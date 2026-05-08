from __future__ import annotations

import logging
import textwrap
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

from ..config import Settings
from ..models import Article, WrittenStory

log = logging.getLogger(__name__)

DISCORD_LIMIT = 1900  # leaves headroom under 2000


def _fmt_topic(topic: str) -> str:
    return f"`{topic}`"


def _short_block(story: WrittenStory) -> str:
    sources = " · ".join(f"[{s['name']}]({s['url']})" for s in story.sources[:4])
    body = textwrap.shorten(story.short_summary, width=900, placeholder=" …")
    return (
        f"{_fmt_topic(story.topic)} **{story.headline}**\n"
        f"{body}\n"
        f"_{sources}_"
    )


def _deep_block(story: WrittenStory, deep_dive_base_url: str | None) -> str:
    sources = " · ".join(f"[{s['name']}]({s['url']})" for s in story.sources[:5])
    body = textwrap.shorten(story.short_summary, width=900, placeholder=" …")
    out = (
        f"{_fmt_topic(story.topic)} **{story.headline}**\n"
        f"{body}\n"
        f"_{sources}_"
    )
    if deep_dive_base_url:
        out += f"\n→ [Full analysis]({deep_dive_base_url}/{story.cluster_id}.html)"
    return out


def _chunk(message: str) -> list[str]:
    chunks: list[str] = []
    while message:
        if len(message) <= DISCORD_LIMIT:
            chunks.append(message)
            break
        cut = message.rfind("\n", 0, DISCORD_LIMIT)
        if cut == -1:
            cut = DISCORD_LIMIT
        chunks.append(message[:cut])
        message = message[cut:].lstrip("\n")
    return chunks


def _post(webhook_url: str, content: str) -> bool:
    try:
        r = httpx.post(
            webhook_url,
            json={"content": content, "allowed_mentions": {"parse": []}},
            timeout=20.0,
        )
        if r.status_code == 429:
            retry = float(r.headers.get("Retry-After", "1"))
            time.sleep(retry + 0.5)
            r = httpx.post(webhook_url, json={"content": content}, timeout=20.0)
        r.raise_for_status()
        return True
    except Exception as e:
        log.error("Discord: post failed — %s", e)
        return False


def send_discord(
    settings: Settings,
    *,
    deep_stories: list[WrittenStory],
    short_stories: list[WrittenStory],
    headline_articles: list[Article],
    tldr: list[str],
    deep_dive_base_url: str | None = None,
) -> bool:
    if not settings.discord_webhook_url:
        log.info("Discord: skipping (DISCORD_WEBHOOK_URL missing)")
        return False

    date_str = datetime.now(ZoneInfo(settings.timezone)).strftime("%A · %b %d, %Y")
    sections: list[str] = []

    header = f"# 📰 Daily Brief — {date_str}\n"
    if tldr:
        header += "\n**TL;DR**\n" + "\n".join(f"• {line}" for line in tldr)
    sections.append(header)

    if deep_stories:
        sections.append("## Deep Dives\n\n" + "\n\n".join(
            _deep_block(s, deep_dive_base_url) for s in deep_stories
        ))

    if short_stories:
        sections.append("## What Else to Know\n\n" + "\n\n".join(
            _short_block(s) for s in short_stories
        ))

    if headline_articles:
        lines = [f"• [{h.title}]({h.url}) — {h.source_name}" for h in headline_articles]
        sections.append("## Skim\n\n" + "\n".join(lines))

    posted_any = False
    for section in sections:
        for chunk in _chunk(section):
            if _post(settings.discord_webhook_url, chunk):
                posted_any = True
                time.sleep(0.6)  # gentle pacing
    if posted_any:
        log.info("Discord: posted")
    return posted_any
