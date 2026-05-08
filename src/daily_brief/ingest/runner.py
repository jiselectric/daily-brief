from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from ..config import Settings, Sources
from ..models import Article
from .hackernews import fetch_hackernews
from .nyt import fetch_nyt_topstories
from .rss import fetch_rss

log = logging.getLogger(__name__)


def gather_articles(sources: Sources, settings: Settings) -> list[Article]:
    articles: list[Article] = []

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(fetch_rss, src) for src in sources.rss]
        for fut in as_completed(futures):
            try:
                articles.extend(fut.result())
            except Exception as e:
                log.warning("RSS fetch failed: %s", e)

    for api in sources.api:
        try:
            if api.adapter == "nyt":
                articles.extend(fetch_nyt_topstories(api, settings.nyt_api_key or ""))
            elif api.adapter == "hackernews":
                articles.extend(fetch_hackernews(api))
            else:
                log.warning("Unknown adapter: %s", api.adapter)
        except Exception as e:
            log.warning("API fetch %s failed: %s", api.id, e)

    log.info("Total articles ingested: %d", len(articles))
    return articles
