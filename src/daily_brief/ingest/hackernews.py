from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx

from ..config import APISource
from ..models import Article

log = logging.getLogger(__name__)

HN_TOPSTORIES = "https://hacker-news.firebaseio.com/v0/topstories.json"
HN_ITEM = "https://hacker-news.firebaseio.com/v0/item/{id}.json"


def fetch_hackernews(source: APISource) -> list[Article]:
    min_score = source.min_score or 200
    limit = source.limit or 30
    out: list[Article] = []
    with httpx.Client(timeout=20.0) as client:
        try:
            ids = client.get(HN_TOPSTORIES).json()[:80]
        except Exception as e:
            log.warning("HN: top stories fetch failed: %s", e)
            return []
        for item_id in ids:
            try:
                item = client.get(HN_ITEM.format(id=item_id)).json()
            except Exception:
                continue
            if not item or item.get("score", 0) < min_score:
                continue
            title = item.get("title")
            url = item.get("url") or f"https://news.ycombinator.com/item?id={item_id}"
            if not title:
                continue
            ts = item.get("time")
            published = (
                datetime.fromtimestamp(ts, tz=timezone.utc) if ts else None
            )
            out.append(
                Article(
                    source_id="hackernews",
                    source_name="Hacker News",
                    source_weight=source.weight,
                    title=title.strip(),
                    url=url,
                    summary=f"HN score {item.get('score', 0)} · {item.get('descendants', 0)} comments",
                    published=published,
                    topics=["technology"],  # Claude will reclassify to ai/startups/science where appropriate
                )
            )
            if len(out) >= limit:
                break
    log.info("HN: %d articles passed threshold (score≥%d)", len(out), min_score)
    return out
