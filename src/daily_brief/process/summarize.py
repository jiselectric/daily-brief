from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor

from anthropic import Anthropic
from tenacity import retry, stop_after_attempt, wait_exponential

from ..config import Settings
from ..models import Cluster, WrittenStory

log = logging.getLogger(__name__)


VALID_TOPICS = {
    "world", "politics", "economics", "markets",
    "business", "ai", "technology", "startups",
    "science", "opinion",
}


VOICE_RULES = """VOICE — read this twice:

You are the journalist. You are not summarizing other journalists. The byline on this piece is yours.

PROHIBITED phrases:
- "According to..."
- "X reports that..."
- "X says..."
- "X notes..."
- "X writes..."
- "The New York Times reports..."
- "WSJ reported..."
- Any framing that makes the claim sound borrowed.

REQUIRED voice:
- Declarative. State the fact. The citation carries the attribution.
- Example WRONG: "According to the WSJ, Anthropic raised $1 billion at a $61 billion valuation."
- Example RIGHT: "Anthropic raised $1 billion at a $61 billion valuation¹."

Disagreement between sources:
- WRONG: "WSJ said the round was led by Lightspeed, but NYT said it was Salesforce Ventures."
- RIGHT: "Lightspeed led the round¹, though another account names Salesforce Ventures as lead².
"""


TOPIC_TAXONOMY = """Topic classification — pick EXACTLY ONE from this list. Use the rules below to choose:

- "world": geopolitics, foreign policy, international relations, conflicts, cross-border affairs.
  Examples: Russia-Ukraine, China-Taiwan tensions, EU policy, BRICS summit, Middle East conflict.

- "politics": US domestic politics, elections, policy fights, Congress, executive actions, court rulings on policy.
  Examples: Trump tariff policy, Senate confirmation, SCOTUS ruling on abortion, primary results.

- "economics": macro, monetary policy, fiscal policy, central banks, inflation, trade, employment data, GDP.
  Examples: Fed rate decision, CPI release, ECB minutes, jobless claims, recession indicators.

- "markets": equities, bonds, FX, commodities, sector moves, market structure.
  Examples: S&P 500 drops 2%, 10Y yield curve, oil crosses $80, IPO market, ETF flows.

- "business": corporate news, M&A, industry analysis, earnings — non-tech.
  Examples: Boeing 737 troubles, Disney CEO succession, JPMorgan earnings, McDonald's China expansion.

- "ai": AI is the LEAD subject. The story would not exist without the AI angle.
  Examples: Nvidia earnings, OpenAI/Anthropic news, AI safety regulation, AI model release, AI lab funding.

- "technology": tech that is NOT AI-led. Hardware, software, platforms, cyber, infra.
  Examples: Apple iOS release, TSMC fab in Arizona, Meta VR push, ransomware on hospitals, cloud outage.

- "startups": VC, funding rounds, founder news, startup ecosystem mechanics (regardless of vertical).
  Examples: Sequoia raises new fund, YC batch demo day, Series A trends, founder dispute, exit news.

- "science": research, climate, health, biotech, basic science breakthroughs.
  Examples: NIH Alzheimer's trial, IPCC report, CRISPR result, ITER fusion milestone, vaccine news.

- "opinion": explicit op-eds, leader columns, By Invitation, editorial board pieces.
  Examples: Economist Leaders, NYT Op-Ed, WSJ Opinion piece, FT Lex column.
  NOTE: Analyst blogs like Stratechery, Marginal Revolution, Import AI go in their CONTENT topic
  (technology, economics, ai respectively), NOT opinion. "Opinion" is reserved for institutional
  editorial content explicitly framed as opinion.

If a story spans multiple topics, choose by primary READ INTENT — where would a reader naturally look for this?
A Nvidia earnings story is "ai" (not "business" or "markets") because AI is what makes it interesting.
A TSMC-Arizona-CHIPS-Act story is "technology" (not "politics") because the tech industry impact is the lead.
A Fed rate decision affecting markets is "economics" (not "markets") — the policy is the news.
A bond selloff with no policy trigger is "markets"."""


CITATION_RULES = """CITATIONS — strict format:

You will be given N source articles, numbered 1..N. After every non-trivial claim, place a Unicode superscript citation matching the source number(s) you drew the claim from.

Use these characters EXACTLY (not <sup> tags, not [1], not ^1):
1 → ¹    2 → ²    3 → ³    4 → ⁴    5 → ⁵    6 → ⁶    7 → ⁷    8 → ⁸    9 → ⁹

For two sources: "...the figure¹².  "  (just concatenate)
Use one citation per claim minimum; multi-source for cross-confirmed facts.
Place superscript immediately after the claim, before the period: "X happened¹."
Do not invent citation numbers beyond the N sources you were given.
"""


SHORT_SYSTEM = f"""You are an editor for a sophisticated daily brief modeled on The Economist, FT Alphaville, and Stratechery.

{VOICE_RULES}

{CITATION_RULES}

For each story cluster, write a ~150-word skim summary that:
- Opens with the most important fact (numbers, dates, named actors), not a meta sentence.
- Embeds 3–6 inline citations matching the source numbers.
- Notes contradictions if sources disagree, using the disagreement pattern above.
- No filler ("looks at", "discusses", "explores").

Return ONLY valid JSON in this shape (no markdown fences):
{{
  "headline": "string under 90 chars, declarative not clickbait",
  "short_summary": "string (~150 words, plain text with Unicode superscripts ¹²³ for citations, NO markdown, NO source list — citations only)",
  "topic": "world" | "politics" | "economics" | "markets" | "business" | "ai" | "technology" | "startups" | "science" | "opinion"
}}

{TOPIC_TAXONOMY}"""


DEEP_SYSTEM = f"""You are writing a deep-dive analytical piece in the voice of an Economist or WSJ staff writer.

{VOICE_RULES}

{CITATION_RULES}

Quality bar:
- Lead with the news. No throat-clearing.
- Quantify everything with numbers drawn from the sources, citing them.
- Provide context: what changed, why now, who's affected, second-order effects.
- Where sources disagree, surface the disagreement via citations, not "X said Y".
- End with a "What to watch" section (<h3>): 2-3 concrete bullets — actual signals to track in the coming weeks, with specific names/numbers/dates.

Length: 1200-1700 words.

HTML formatting rules for deep_dive_html:
- Use <p>, <h3>, <ul>, <li>, <strong>, <em>
- For citations, use <sup>1</sup>, <sup>2</sup>, etc. (NOT Unicode superscripts in HTML — use the tag form)
- Do NOT include <html>, <head>, <body>, or any source list — those are rendered by the page template.

Return ONLY valid JSON (no markdown fences):
{{
  "headline": "string under 90 chars",
  "short_summary": "string ~150 words plain text with Unicode superscripts ¹²³ for citations, for the email skim section",
  "deep_dive_html": "string (HTML body with <sup>N</sup> citations inline)",
  "topic": "world" | "politics" | "economics" | "markets" | "business" | "ai" | "technology" | "startups" | "science" | "opinion"
}}

{TOPIC_TAXONOMY}"""


def _cluster_brief(cluster: Cluster) -> str:
    """Compact text representation of a cluster for the prompt, with numbered sources."""
    lines = [f"PRIMARY TOPIC HINT (from source metadata): {cluster.primary_topic}", ""]
    lines.append(f"You have {len(cluster.articles)} sources, numbered 1..{len(cluster.articles)}.")
    lines.append("Cite using Unicode superscripts ¹²³⁴⁵⁶⁷⁸⁹ in short_summary, and <sup>N</sup> in deep_dive_html.\n")
    for i, a in enumerate(cluster.articles, 1):
        lines.append(f"--- Source {i}: {a.source_name} ---")
        lines.append(f"Title: {a.title}")
        if a.summary:
            lines.append(f"Excerpt: {a.summary}")
        lines.append(f"URL: {a.url}")
        lines.append("")
    return "\n".join(lines)


def _extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            return json.loads(match.group(0))
        raise


def _validate_topic(raw: str | None, fallback: str) -> str:
    if not raw:
        return fallback if fallback in VALID_TOPICS else "other"
    raw = raw.strip().lower()
    if raw in VALID_TOPICS:
        return raw
    if fallback in VALID_TOPICS:
        return fallback
    return "other"


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=15))
def _call_claude(client: Anthropic, model: str, system: str, user: str, max_tokens: int) -> str:
    resp = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    parts = [b.text for b in resp.content if getattr(b, "type", None) == "text"]
    return "".join(parts)


def write_short(client: Anthropic, model: str, cluster: Cluster) -> WrittenStory | None:
    try:
        raw = _call_claude(client, model, SHORT_SYSTEM, _cluster_brief(cluster), max_tokens=700)
        data = _extract_json(raw)
    except Exception as e:
        log.warning("short summary failed for %s: %s", cluster.cluster_id, e)
        return None
    topic = _validate_topic(data.get("topic"), cluster.primary_topic)
    return WrittenStory(
        cluster_id=cluster.cluster_id,
        headline=data.get("headline", cluster.lead.title)[:120],
        short_summary=data.get("short_summary", ""),
        deep_dive_html=None,
        sources=[
            {"name": a.source_name, "url": a.url, "title": a.title, "index": i}
            for i, a in enumerate(cluster.articles, 1)
        ],
        topic=topic,
        is_deep_dive=False,
    )


def write_deep(client: Anthropic, model: str, cluster: Cluster) -> WrittenStory | None:
    try:
        raw = _call_claude(client, model, DEEP_SYSTEM, _cluster_brief(cluster), max_tokens=5000)
        data = _extract_json(raw)
    except Exception as e:
        log.warning("deep dive failed for %s: %s", cluster.cluster_id, e)
        return None
    topic = _validate_topic(data.get("topic"), cluster.primary_topic)
    return WrittenStory(
        cluster_id=cluster.cluster_id,
        headline=data.get("headline", cluster.lead.title)[:120],
        short_summary=data.get("short_summary", ""),
        deep_dive_html=data.get("deep_dive_html", ""),
        sources=[
            {"name": a.source_name, "url": a.url, "title": a.title, "index": i}
            for i, a in enumerate(cluster.articles, 1)
        ],
        topic=topic,
        is_deep_dive=True,
    )


def write_stories(
    settings: Settings,
    deep_clusters: list[Cluster],
    short_clusters: list[Cluster],
) -> tuple[list[WrittenStory], list[WrittenStory]]:
    client = Anthropic(api_key=settings.anthropic_api_key)
    model = settings.anthropic_model

    log.info("Writing %d deep dives and %d short summaries via %s",
             len(deep_clusters), len(short_clusters), model)

    deep_stories: list[WrittenStory] = []
    short_stories: list[WrittenStory] = []

    with ThreadPoolExecutor(max_workers=4) as pool:
        deep_futs = {pool.submit(write_deep, client, model, c): c for c in deep_clusters}
        short_futs = {pool.submit(write_short, client, model, c): c for c in short_clusters}
        for fut in deep_futs:
            try:
                story = fut.result()
                if story:
                    deep_stories.append(story)
            except Exception as e:
                log.warning("deep future failed: %s", e)
        for fut in short_futs:
            try:
                story = fut.result()
                if story:
                    short_stories.append(story)
            except Exception as e:
                log.warning("short future failed: %s", e)

    log.info("Wrote %d deep dives, %d short summaries", len(deep_stories), len(short_stories))
    return deep_stories, short_stories
