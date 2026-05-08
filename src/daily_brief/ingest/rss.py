from __future__ import annotations

import logging
from datetime import datetime, timezone

import feedparser
from bs4 import BeautifulSoup

from ..config import RSSSource
from ..models import Article

log = logging.getLogger(__name__)


def _strip_html(text: str) -> str:
    if not text:
        return ""
    return BeautifulSoup(text, "lxml").get_text(" ", strip=True)


def _parse_date(entry) -> datetime | None:
    for attr in ("published_parsed", "updated_parsed"):
        val = getattr(entry, attr, None) or entry.get(attr)
        if val:
            try:
                return datetime(*val[:6], tzinfo=timezone.utc)
            except (TypeError, ValueError):
                continue
    return None


def fetch_rss(source: RSSSource, limit: int = 40) -> list[Article]:
    log.info("RSS: fetching %s", source.name)
    parsed = feedparser.parse(source.url, request_headers={"User-Agent": "daily-brief/0.1"})
    if parsed.bozo and not parsed.entries:
        log.warning("RSS: %s failed to parse: %s", source.name, parsed.bozo_exception)
        return []

    out: list[Article] = []
    for entry in parsed.entries[:limit]:
        url = getattr(entry, "link", None)
        title = getattr(entry, "title", None)
        if not url or not title:
            continue
        summary = _strip_html(getattr(entry, "summary", "") or getattr(entry, "description", ""))
        out.append(
            Article(
                source_id=source.id,
                source_name=source.name,
                source_weight=source.weight,
                title=title.strip(),
                url=url,
                summary=summary[:1000],
                published=_parse_date(entry),
                topics=list(source.topics),
            )
        )
    log.info("RSS: %s → %d articles", source.name, len(out))
    return out
