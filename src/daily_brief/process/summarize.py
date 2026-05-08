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


SHORT_SYSTEM = """You are an editor for a personalized daily brief. Your audience is a sophisticated reader who values rigorous, source-grounded analysis (think Stratechery, FT Alphaville, The Economist).

For each story cluster, write a tight ~150-word skim summary that:
- Opens with the most important fact, not a meta sentence about "this story is about..."
- Names specific actors, numbers, and dates from the source material
- Notes where sources agree or diverge if multiple are present
- Avoids hype, filler, and vague verbs ("looks at", "discusses")

Return ONLY valid JSON in this shape:
{
  "headline": "string (under 90 chars, declarative not clickbait)",
  "short_summary": "string (~150 words, plain text, no markdown)"
}
"""

DEEP_SYSTEM = """You are writing a deep-dive analytical brief for a sophisticated reader. The audience is the kind of person who reads The Economist, Stratechery, and FT Alphaville.

Quality bar:
- Lead with the news; do not warm up.
- Synthesize across the provided sources. Where they agree, state it once. Where they disagree, name the disagreement and which source holds which view.
- Quantify everything you can. Use numbers from the sources verbatim.
- Provide context: what changed, why now, who's affected, what the second-order effects are.
- Cite inline as [SourceName] after each non-trivial claim.
- End with a "What to watch" section: 2-3 bullets on the actual signals to track.

Length target: 1200-1700 words (10-15 minute read).

Return ONLY valid JSON:
{
  "headline": "string (under 90 chars)",
  "short_summary": "string (~150 words for the email skim section)",
  "deep_dive_html": "string (HTML body — use <h3>, <p>, <ul>, <li>, <strong>; do NOT include <html>, <head>, or <body> tags; cite sources inline as [SourceName])"
}
"""


def _cluster_brief(cluster: Cluster) -> str:
    """Compact text representation of a cluster for the prompt."""
    lines = [f"PRIMARY TOPIC: {cluster.primary_topic}", ""]
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
        # Try to extract first {...} block.
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            return json.loads(match.group(0))
        raise


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
        raw = _call_claude(
            client, model, SHORT_SYSTEM, _cluster_brief(cluster), max_tokens=600
        )
        data = _extract_json(raw)
    except Exception as e:
        log.warning("short summary failed for %s: %s", cluster.cluster_id, e)
        return None
    return WrittenStory(
        cluster_id=cluster.cluster_id,
        headline=data.get("headline", cluster.lead.title)[:120],
        short_summary=data.get("short_summary", ""),
        deep_dive_html=None,
        sources=[
            {"name": a.source_name, "url": a.url, "title": a.title}
            for a in cluster.articles
        ],
        topic=cluster.primary_topic,
        is_deep_dive=False,
    )


def write_deep(client: Anthropic, model: str, cluster: Cluster) -> WrittenStory | None:
    try:
        raw = _call_claude(
            client, model, DEEP_SYSTEM, _cluster_brief(cluster), max_tokens=4500
        )
        data = _extract_json(raw)
    except Exception as e:
        log.warning("deep dive failed for %s: %s", cluster.cluster_id, e)
        return None
    return WrittenStory(
        cluster_id=cluster.cluster_id,
        headline=data.get("headline", cluster.lead.title)[:120],
        short_summary=data.get("short_summary", ""),
        deep_dive_html=data.get("deep_dive_html", ""),
        sources=[
            {"name": a.source_name, "url": a.url, "title": a.title}
            for a in cluster.articles
        ],
        topic=cluster.primary_topic,
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
