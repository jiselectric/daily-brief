from __future__ import annotations

import logging
import re
from collections import defaultdict

from rapidfuzz import fuzz

from ..models import Article, Cluster

log = logging.getLogger(__name__)

_STOPWORDS = {
    "the", "a", "an", "of", "in", "to", "and", "for", "on", "at", "by", "with",
    "is", "as", "from", "that", "this", "it", "be", "are", "was", "were", "or",
    "but", "not", "have", "has", "had", "will", "can", "could", "would", "should",
    "may", "might", "do", "does", "did", "say", "says", "said", "after", "before",
    "new", "us", "u.s.", "u.s",
}


def _normalize(title: str) -> set[str]:
    cleaned = re.sub(r"[^a-z0-9 ]", " ", title.lower())
    return {w for w in cleaned.split() if w and w not in _STOPWORDS and len(w) > 2}


def _title_similarity(a: str, b: str) -> float:
    return fuzz.token_set_ratio(a.lower(), b.lower()) / 100.0


def cluster_articles(articles: list[Article], similarity_threshold: float = 0.62) -> list[Cluster]:
    """Greedy clustering by title similarity. O(n²) but fine for n<500."""
    clusters: list[list[Article]] = []
    cluster_keysets: list[set[str]] = []

    for article in articles:
        keys = _normalize(article.title)
        placed = False
        for i, ckeys in enumerate(cluster_keysets):
            if not ckeys or not keys:
                continue
            overlap = len(keys & ckeys) / max(1, min(len(keys), len(ckeys)))
            if overlap >= 0.5 or _title_similarity(article.title, clusters[i][0].title) >= similarity_threshold * 100 / 100:
                clusters[i].append(article)
                cluster_keysets[i] = ckeys | keys
                placed = True
                break
        if not placed:
            clusters.append([article])
            cluster_keysets.append(keys)

    out: list[Cluster] = []
    for idx, group in enumerate(clusters):
        topic = _primary_topic(group)
        cluster_id = f"c{idx:04d}"
        for a in group:
            a.cluster_id = cluster_id
        out.append(
            Cluster(
                cluster_id=cluster_id,
                articles=group,
                primary_topic=topic,
                score=0.0,
            )
        )
    log.info("Clustered %d articles → %d clusters", len(articles), len(out))
    return out


def _primary_topic(articles: list[Article]) -> str:
    counts: dict[str, float] = defaultdict(float)
    for a in articles:
        for t in a.topics:
            counts[t] += a.source_weight
    if not counts:
        return "general"
    return max(counts.items(), key=lambda kv: kv[1])[0]
