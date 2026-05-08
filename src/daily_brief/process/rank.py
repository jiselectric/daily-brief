from __future__ import annotations

import logging
from datetime import datetime, timezone

from ..config import Interests
from ..models import Cluster

log = logging.getLogger(__name__)


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
    """Returns (deep_dive_clusters, short_summary_clusters, headline_only_clusters)."""
    cfg = interests.brief
    n_deep = cfg.deep_dives
    n_short = cfg.short_summaries
    n_head = cfg.headlines_only

    # Topic balancing for deep dives so AI/tech doesn't dominate.
    deep: list[Cluster] = []
    used_topics: dict[str, int] = {}
    max_per_topic = 2

    for c in clusters:
        if len(deep) >= n_deep:
            break
        topic = c.primary_topic
        if used_topics.get(topic, 0) >= max_per_topic:
            continue
        deep.append(c)
        used_topics[topic] = used_topics.get(topic, 0) + 1

    deep_ids = {c.cluster_id for c in deep}
    remaining = [c for c in clusters if c.cluster_id not in deep_ids]
    shorts = remaining[:n_short]
    short_ids = {c.cluster_id for c in shorts}
    heads = [c for c in remaining if c.cluster_id not in short_ids][:n_head]

    return deep, shorts, heads
