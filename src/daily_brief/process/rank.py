from __future__ import annotations

import logging
from datetime import datetime, timezone
from urllib.parse import urlparse

from ..config import Interests
from ..models import Article, Cluster

log = logging.getLogger(__name__)


# Coarse publisher mapping — group subdomains/feeds under one outlet so the
# per-publisher cap treats them as one source.
_PUBLISHER_BY_DOMAIN_HINT = {
    "bloomberg.com": "Bloomberg",
    "nytimes.com": "NYT",
    "nyt.com": "NYT",
    "wsj.com": "WSJ",
    "ft.com": "FT",
    "economist.com": "Economist",
    "reuters.com": "Reuters",
    "bbc.co.uk": "BBC",
    "bbc.com": "BBC",
    "ycombinator.com": "Hacker News",
    "axios.com": "Axios",
    "theverge.com": "The Verge",
    "techcrunch.com": "TechCrunch",
    "arstechnica.com": "Ars Technica",
    "ap.org": "AP",
    "apnews.com": "AP",
    "foreignaffairs.com": "Foreign Affairs",
    "foreignpolicy.com": "Foreign Policy",
    "lawfaremedia.org": "Lawfare",
    "warontherocks.com": "War on the Rocks",
    "politico.com": "Politico",
}


def publisher_of(article: Article) -> str:
    host = (urlparse(article.url).netloc or "").lower()
    for prefix in ("www.", "m.", "feeds.", "rss."):
        if host.startswith(prefix):
            host = host[len(prefix):]
    for domain, name in _PUBLISHER_BY_DOMAIN_HINT.items():
        if host == domain or host.endswith("." + domain):
            return name
    # Hosted blog platforms — the subdomain is the actual publisher.
    for hosted in ("substack.com", "ghost.io", "medium.com"):
        if host.endswith("." + hosted):
            sub = host[: -(len(hosted) + 1)]
            return sub.replace("-", " ").title()
    parts = host.split(".")
    if len(parts) >= 2:
        return parts[-2].title()
    return article.source_name


def _freshness(published: datetime | None) -> float:
    if not published:
        return 0.5
    if published.tzinfo is None:
        published = published.replace(tzinfo=timezone.utc)
    age_h = (datetime.now(timezone.utc) - published).total_seconds() / 3600
    if age_h < 0:
        return 1.0
    if age_h < 6:
        return 1.0
    if age_h < 24:
        return 0.85
    if age_h < 48:
        return 0.6
    if age_h < 96:
        return 0.35
    return 0.15


def _keyword_boost(text: str, boosts: list[str], excludes: list[str]) -> float:
    text_l = text.lower()
    boost = sum(0.15 for kw in boosts if kw.lower() in text_l)
    penalty = sum(0.5 for kw in excludes if kw.lower() in text_l)
    return min(boost, 0.6) - penalty


def score_clusters(clusters: list[Cluster], interests: Interests) -> list[Cluster]:
    for c in clusters:
        topic_weight = interests.topics.get(c.primary_topic, 0.4)
        cross_source_bonus = min(0.3, 0.1 * (len({a.source_id for a in c.articles}) - 1))
        max_authority = max(a.source_weight for a in c.articles)
        avg_freshness = sum(_freshness(a.published) for a in c.articles) / len(c.articles)
        text_blob = " ".join(f"{a.title} {a.summary}" for a in c.articles)
        kw = _keyword_boost(text_blob, interests.boost_keywords, interests.exclude_keywords)
        c.score = (
            (topic_weight * 1.5)
            + (max_authority * 1.0)
            + cross_source_bonus
            + (avg_freshness * 0.8)
            + kw
        )
    clusters.sort(key=lambda c: c.score, reverse=True)
    return clusters


def select_for_brief(
    clusters: list[Cluster],
    interests: Interests,
) -> tuple[list[Cluster], list[Cluster], list[Cluster]]:
    """Returns (deep_dive_clusters, short_summary_clusters, headline_only_clusters).

    Selection enforces a priority cap: at most `priority_cap` fraction of the
    written articles (deep + short) come from `priority_topics`. The remainder
    is filled from non-priority topics to keep editorial diversity. If the
    non-priority pool is too thin, the shortfall falls back to priority.
    """
    cfg = interests.brief
    n_total = cfg.deep_dives + cfg.short_summaries
    n_head = cfg.headlines_only

    priority = set(interests.priority_topics)
    cap_pct = max(0.0, min(1.0, interests.priority_cap))
    max_priority = int(round(n_total * cap_pct))
    min_diverse = n_total - max_priority

    priority_clusters = [c for c in clusters if c.primary_topic in priority]
    diverse_clusters = [c for c in clusters if c.primary_topic not in priority]

    # Per-publisher cap — no single outlet takes more than `pub_cap` slots in
    # the final brief. Prevents Bloomberg dominance when one outlet has many
    # feeds and others are missing.
    pub_cap = max(1, int(round(n_total * 0.20)))  # 3/15 at default settings
    log.info("Per-publisher cap: %d articles per outlet", pub_cap)

    def _pick(pool: list[Cluster], target: int, pub_counts: dict[str, int]) -> list[Cluster]:
        picked: list[Cluster] = []
        for c in pool:
            if len(picked) >= target:
                break
            pub = publisher_of(c.lead)
            if pub_counts.get(pub, 0) >= pub_cap:
                continue
            picked.append(c)
            pub_counts[pub] = pub_counts.get(pub, 0) + 1
        return picked

    pub_counts: dict[str, int] = {}
    chosen_priority = _pick(priority_clusters, max_priority, pub_counts)
    chosen_diverse = _pick(diverse_clusters, min_diverse, pub_counts)

    # Backfill from priority if diverse pool was thin.
    if len(chosen_diverse) < min_diverse:
        shortfall = min_diverse - len(chosen_diverse)
        already = {c.cluster_id for c in chosen_priority}
        backfill = _pick(
            [c for c in priority_clusters if c.cluster_id not in already],
            shortfall,
            pub_counts,
        )
        chosen_priority += backfill

    selected = chosen_priority + chosen_diverse
    selected.sort(key=lambda c: c.score, reverse=True)
    selected = selected[:n_total]

    # Promote top-scored clusters to deep dives, with per-topic cap of 2 so
    # a single topic can't take >2 of the 5 deep slots.
    deep: list[Cluster] = []
    used_topics: dict[str, int] = {}
    max_per_topic = 2
    for c in selected:
        if len(deep) >= cfg.deep_dives:
            break
        topic = c.primary_topic
        if used_topics.get(topic, 0) >= max_per_topic:
            continue
        deep.append(c)
        used_topics[topic] = used_topics.get(topic, 0) + 1

    deep_ids = {c.cluster_id for c in deep}
    shorts = [c for c in selected if c.cluster_id not in deep_ids][: cfg.short_summaries]
    short_ids = {c.cluster_id for c in shorts}

    remaining = [c for c in clusters if c.cluster_id not in deep_ids and c.cluster_id not in short_ids]
    heads = remaining[:n_head]

    log.info(
        "Selected: %d priority / %d diverse (cap %.0f%%)",
        sum(1 for c in selected if c.primary_topic in priority),
        sum(1 for c in selected if c.primary_topic not in priority),
        cap_pct * 100,
    )
    return deep, shorts, heads
