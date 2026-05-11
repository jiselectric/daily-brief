from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..config import Settings
from ..models import Article, WrittenStory

log = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent / "templates"

_env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=select_autoescape(["html", "j2"]),
)

# Display order for topic sections — mirrors Economist/WSJ/NYT editorial flow.
TOPIC_ORDER = [
    "world", "politics",
    "economics", "markets", "business",
    "ai", "technology", "startups",
    "science", "opinion",
]
TOPIC_LABELS = {
    "world": "World",
    "politics": "Politics",
    "economics": "Economics",
    "markets": "Markets",
    "business": "Business",
    "ai": "AI",
    "technology": "Technology",
    "startups": "Startups",
    "science": "Science",
    "opinion": "Opinion",
}


def _group_by_topic(stories: list[WrittenStory]) -> list[tuple[str, list[WrittenStory]]]:
    buckets: dict[str, list[WrittenStory]] = {}
    for s in stories:
        buckets.setdefault(s.topic, []).append(s)
    # Inside each bucket, deep dives first.
    for items in buckets.values():
        items.sort(key=lambda s: (not s.is_deep_dive,))
    return [(TOPIC_LABELS.get(t, t.title()), buckets[t]) for t in TOPIC_ORDER if t in buckets]


def render_email(
    *,
    deep_stories: list[WrittenStory],
    short_stories: list[WrittenStory],
    headline_articles: list[Article],
    tldr: list[str],
    timezone_name: str,
    run_stats: str,
    deep_dive_base_url: str | None = None,
) -> tuple[str, str]:
    template = _env.get_template("email.html.j2")
    date_str = datetime.now(ZoneInfo(timezone_name)).strftime("%A, %B %d, %Y")
    grouped = _group_by_topic(deep_stories + short_stories)
    html = template.render(
        date_str=date_str,
        timezone=timezone_name,
        grouped=grouped,
        headlines=headline_articles,
        tldr=tldr,
        run_stats=run_stats,
        deep_dive_base_url=deep_dive_base_url,
    )
    subject = f"Daily Brief — {date_str}"
    return subject, html


def send_email_resend(settings: Settings, subject: str, html: str) -> bool:
    if not settings.resend_api_key or not settings.to_email:
        log.info("Email: skipping (RESEND_API_KEY or TO_EMAIL missing)")
        return False
    try:
        r = httpx.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {settings.resend_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "from": settings.resend_from,
                "to": [settings.to_email],
                "subject": subject,
                "html": html,
            },
            timeout=30.0,
        )
        r.raise_for_status()
        log.info("Email: sent → %s", settings.to_email)
        return True
    except httpx.HTTPStatusError as e:
        log.error("Email: failed %s — %s", e.response.status_code, e.response.text)
        return False
    except Exception as e:
        log.error("Email: failed — %s", e)
        return False
