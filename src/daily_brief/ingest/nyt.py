from __future__ import annotations

import logging
from datetime import datetime

import httpx
from dateutil import parser as dateparser

from ..config import APISource
from ..models import Article

log = logging.getLogger(__name__)

NYT_TOPSTORIES = "https://api.nytimes.com/svc/topstories/v2/{section}.json"

_SECTION_TOPICS = {
    "home": ["politics", "world", "business"],
    "world": ["world", "politics"],
    "politics": ["politics"],
    "business": ["business", "economics"],
    "technology": ["technology"],
    "us": ["politics"],
}


def fetch_nyt_topstories(source: APISource, api_key: str, per_section: int = 20) -> list[Article]:
    if not api_key:
        log.info("NYT: skipping (no API key)")
        return []
    sections = source.sections or ["home"]
    out: list[Article] = []
    seen: set[str] = set()
    with httpx.Client(timeout=20.0) as client:
        for section in sections:
            url = NYT_TOPSTORIES.format(section=section)
            try:
                r = client.get(url, params={"api-key": api_key})
                r.raise_for_status()
                data = r.json()
            except Exception as e:
                log.warning("NYT: %s failed: %s", section, e)
                continue
            results = data.get("results", [])[:per_section]
            for item in results:
                link = item.get("url")
                title = item.get("title")
                if not link or not title or link in seen:
                    continue
                seen.add(link)
                published: datetime | None = None
                if item.get("published_date"):
                    try:
                        published = dateparser.parse(item["published_date"])
                    except Exception:
                        pass
                out.append(
                    Article(
                        source_id=f"nyt-api-{section}",
                        source_name=f"New York Times ({section})",
                        source_weight=source.weight,
                        title=title.strip(),
                        url=link,
                        summary=(item.get("abstract") or "")[:1000],
                        published=published,
                        topics=_SECTION_TOPICS.get(section, []),
                    )
                )
            log.info("NYT: %s → %d articles", section, len(results))
    return out
