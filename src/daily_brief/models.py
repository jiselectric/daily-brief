from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Article:
    """A raw article pulled from a source, before any AI processing."""
    source_id: str
    source_name: str
    source_weight: float
    title: str
    url: str
    summary: str = ""
    published: datetime | None = None
    topics: list[str] = field(default_factory=list)
    score: float = 0.0
    cluster_id: str | None = None

    def fingerprint(self) -> str:
        return self.url.split("?")[0].rstrip("/").lower()


@dataclass
class Cluster:
    """A group of articles covering the same event."""
    cluster_id: str
    articles: list[Article]
    primary_topic: str
    score: float

    @property
    def lead(self) -> Article:
        return max(self.articles, key=lambda a: a.source_weight)


@dataclass
class WrittenStory:
    """A Claude-generated story for the daily brief."""
    cluster_id: str
    headline: str
    short_summary: str           # ~150 words for skim
    deep_dive_html: str | None   # 10-15min read; only top deep_dives stories
    sources: list[dict]          # [{name, url, title}]
    topic: str
    is_deep_dive: bool
