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

TOPIC_ORDER = [
    "world", "politics",
    "economics", "markets", "business",
    "ai", "technology", "startups",
    "science", "opinion",
]
TOPIC_LABELS = {
    "world": "🌍 World",
    "politics": "🏛️ Politics",
    "economics": "📈 Economics",
    "markets": "💹 Markets",
    "business": "💼 Business",
    "ai": "🤖 AI",
    "technology": "💻 Technology",
    "startups": "🚀 Startups",
    "science": "🔬 Science",
    "opinion": "✍️ Opinion",
}


def _short_block(story: WrittenStory, deep_dive_base_url: str | None) -> str:
    sources = " · ".join(f"[{s['name']}]({s['url']})" for s in story.sources[:4])
    body = textwrap.shorten(story.short_summary, width=900, placeholder=" …")
    out = f"**{story.headline}**\n{body}\n_{sources}_"
    if story.is_deep_dive and deep_dive_base_url:
        out += f"\n→ [Deep dive]({deep_dive_base_url}/{story.cluster_id}.html)"
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


def _post(webhook_url: str, content: str, thread_name: str | None = None) -> bool:
    """Post to a Discord webhook. If thread_name is set, the post creates (or posts into) that thread."""
    payload: dict = {
        "content": content,
        "allowed_mentions": {"parse": []},
    }
    url = webhook_url
    if thread_name:
        # First post creates the thread; subsequent posts to the same thread need ?thread_id=.
        # Webhook trick: passing thread_name creates a forum/thread post. For text channels with
        # threads enabled, thread_name on first post creates a new thread.
        payload["thread_name"] = thread_name
    try:
        r = httpx.post(url, json=payload, timeout=20.0)
        if r.status_code == 429:
            retry = float(r.headers.get("Retry-After", "1"))
            time.sleep(retry + 0.5)
            r = httpx.post(url, json=payload, timeout=20.0)
        r.raise_for_status()
        return True
    except httpx.HTTPStatusError as e:
        log.error("Discord: %s — %s", e.response.status_code, e.response.text[:200])
        return False
    except Exception as e:
        log.error("Discord: post failed — %s", e)
        return False


def _post_thread_followup(webhook_url: str, thread_id: str, content: str) -> bool:
    """Post a follow-up message into an existing thread by id."""
    params = {"thread_id": thread_id}
    try:
        r = httpx.post(
            webhook_url,
            params=params,
            json={"content": content, "allowed_mentions": {"parse": []}},
            timeout=20.0,
        )
        if r.status_code == 429:
            retry = float(r.headers.get("Retry-After", "1"))
            time.sleep(retry + 0.5)
            r = httpx.post(webhook_url, params=params,
                           json={"content": content}, timeout=20.0)
        r.raise_for_status()
        return True
    except httpx.HTTPStatusError as e:
        log.error("Discord thread: %s — %s", e.response.status_code, e.response.text[:200])
        return False
    except Exception as e:
        log.error("Discord thread post failed — %s", e)
        return False


def _post_and_get_thread_id(webhook_url: str, content: str, thread_name: str) -> str | None:
    """Post a new thread-creating message, return the new thread's id."""
    try:
        r = httpx.post(
            webhook_url,
            params={"wait": "true"},  # return the created message info
            json={
                "content": content,
                "thread_name": thread_name,
                "allowed_mentions": {"parse": []},
            },
            timeout=20.0,
        )
        if r.status_code == 429:
            retry = float(r.headers.get("Retry-After", "1"))
            time.sleep(retry + 0.5)
            r = httpx.post(
                webhook_url, params={"wait": "true"},
                json={"content": content, "thread_name": thread_name}, timeout=20.0,
            )
        r.raise_for_status()
        data = r.json()
        # When ?wait=true and thread_name is used, message.id == thread_id.
        thread_id = data.get("channel_id") or data.get("id")
        return str(thread_id) if thread_id else None
    except httpx.HTTPStatusError as e:
        log.error("Discord thread create: %s — %s", e.response.status_code, e.response.text[:200])
        return None
    except Exception as e:
        log.error("Discord thread create failed — %s", e)
        return None


def _send_tldr(webhook_url: str, date_str: str, tldr: list[str]) -> bool:
    if not tldr:
        return False
    content = f"# 📰 Daily Brief — {date_str}\n\n**TL;DR**\n" + "\n".join(f"• {line}" for line in tldr)
    posted = False
    for chunk in _chunk(content):
        if _post(webhook_url, chunk):
            posted = True
            time.sleep(0.5)
    return posted


def _send_brief_thread(
    webhook_url: str,
    date_str: str,
    deep_stories: list[WrittenStory],
    short_stories: list[WrittenStory],
    headline_articles: list[Article],
    deep_dive_base_url: str | None,
) -> bool:
    # Bucket all stories by topic.
    by_topic: dict[str, list[WrittenStory]] = {}
    for s in deep_stories + short_stories:
        by_topic.setdefault(s.topic, []).append(s)
    for items in by_topic.values():
        items.sort(key=lambda s: (not s.is_deep_dive,))

    thread_name = f"Daily Brief — {date_str}"

    # Build the kickoff message (something short to start the thread).
    kickoff_lines = [f"# 📰 {thread_name}", ""]
    for topic in TOPIC_ORDER:
        if topic in by_topic:
            count = len(by_topic[topic])
            kickoff_lines.append(f"{TOPIC_LABELS[topic]} — {count} {'story' if count == 1 else 'stories'}")
    if deep_dive_base_url:
        kickoff_lines.append("")
        kickoff_lines.append(f"📚 [Full archive]({deep_dive_base_url})")
    kickoff = "\n".join(kickoff_lines)

    thread_id = _post_and_get_thread_id(webhook_url, kickoff, thread_name)
    if not thread_id:
        log.warning("Discord brief: thread creation failed; falling back to single posts")
        # Fallback: post each section as a top-level message in the channel.
        for topic in TOPIC_ORDER:
            if topic not in by_topic:
                continue
            section = f"## {TOPIC_LABELS[topic]}\n\n" + "\n\n".join(
                _short_block(s, deep_dive_base_url) for s in by_topic[topic]
            )
            for chunk in _chunk(section):
                _post(webhook_url, chunk)
                time.sleep(0.5)
        return True

    # Post one section per topic into the thread.
    for topic in TOPIC_ORDER:
        if topic not in by_topic:
            continue
        section_blocks = "\n\n".join(_short_block(s, deep_dive_base_url) for s in by_topic[topic])
        section = f"## {TOPIC_LABELS[topic]}\n\n{section_blocks}"
        for chunk in _chunk(section):
            _post_thread_followup(webhook_url, thread_id, chunk)
            time.sleep(0.5)

    # Skim list at the end of the thread.
    if headline_articles:
        lines = [f"• [{h.title}]({h.url}) — {h.source_name}" for h in headline_articles]
        skim = "## 👀 Skim\n\n" + "\n".join(lines)
        for chunk in _chunk(skim):
            _post_thread_followup(webhook_url, thread_id, chunk)
            time.sleep(0.5)

    log.info("Discord: posted thread %s (%s) with %d topics",
             thread_id, thread_name, len(by_topic))
    return True


def send_discord(
    settings: Settings,
    *,
    deep_stories: list[WrittenStory],
    short_stories: list[WrittenStory],
    headline_articles: list[Article],
    tldr: list[str],
    deep_dive_base_url: str | None = None,
) -> bool:
    """Posts to two Discord webhooks: TLDR (top-level message) and BRIEF (one thread per day).

    Falls back to the legacy single DISCORD_WEBHOOK_URL if the new two-webhook vars aren't set —
    in that case it posts everything to the legacy channel.
    """
    date_str = datetime.now(ZoneInfo(settings.timezone)).strftime("%a · %b %d, %Y")
    tldr_url = settings.discord_webhook_tldr
    brief_url = settings.discord_webhook_brief
    legacy_url = settings.discord_webhook_url

    if not (tldr_url or brief_url or legacy_url):
        log.info("Discord: skipping (no webhooks configured)")
        return False

    delivered = False

    if tldr_url:
        if _send_tldr(tldr_url, date_str, tldr):
            delivered = True
            log.info("Discord: TL;DR posted to dedicated channel")
    if brief_url:
        if _send_brief_thread(brief_url, date_str, deep_stories, short_stories,
                              headline_articles, deep_dive_base_url):
            delivered = True
            log.info("Discord: brief thread posted")

    # Backwards-compat path: legacy single webhook.
    if not (tldr_url or brief_url) and legacy_url:
        log.info("Discord: falling back to legacy DISCORD_WEBHOOK_URL")
        if _send_tldr(legacy_url, date_str, tldr):
            delivered = True
        if _send_brief_thread(legacy_url, date_str, deep_stories, short_stories,
                              headline_articles, deep_dive_base_url):
            delivered = True

    return delivered
