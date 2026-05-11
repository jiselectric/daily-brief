from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field

load_dotenv()

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)


class RSSSource(BaseModel):
    id: str
    name: str
    url: str
    weight: float = 1.0
    topics: list[str] = Field(default_factory=list)


class APISource(BaseModel):
    id: str
    name: str
    adapter: str
    weight: float = 1.0
    sections: list[str] = Field(default_factory=list)
    min_score: int | None = None
    limit: int | None = None


class Sources(BaseModel):
    rss: list[RSSSource]
    api: list[APISource]


class BriefConfig(BaseModel):
    deep_dives: int = 5
    short_summaries: int = 10
    headlines_only: int = 15


class Interests(BaseModel):
    topics: dict[str, float]
    priority_topics: list[str] = Field(default_factory=list)
    priority_cap: float = 0.80
    boost_keywords: list[str] = Field(default_factory=list)
    exclude_keywords: list[str] = Field(default_factory=list)
    brief: BriefConfig = Field(default_factory=BriefConfig)


class Settings(BaseModel):
    anthropic_api_key: str
    anthropic_model: str = "claude-sonnet-4-6"
    nyt_api_key: str | None = None
    resend_api_key: str | None = None
    resend_from: str = "Daily Brief <onboarding@resend.dev>"
    to_email: str | None = None
    discord_webhook_url: str | None = None     # legacy single-channel webhook
    discord_webhook_tldr: str | None = None    # #daily-tldr channel
    discord_webhook_brief: str | None = None   # #daily-brief channel (threads created per day)
    timezone: str = "America/Los_Angeles"


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open() as f:
        return yaml.safe_load(f)


def load_sources() -> Sources:
    return Sources(**_load_yaml(CONFIG_DIR / "sources.yaml"))


def load_interests() -> Interests:
    return Interests(**_load_yaml(CONFIG_DIR / "interests.yaml"))


def load_settings() -> Settings:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")
    return Settings(
        anthropic_api_key=key,
        anthropic_model=os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6"),
        nyt_api_key=os.environ.get("NYT_API_KEY") or None,
        resend_api_key=os.environ.get("RESEND_API_KEY") or None,
        resend_from=os.environ.get("RESEND_FROM", "Daily Brief <onboarding@resend.dev>"),
        to_email=os.environ.get("TO_EMAIL") or None,
        discord_webhook_url=os.environ.get("DISCORD_WEBHOOK_URL") or None,
        discord_webhook_tldr=os.environ.get("DISCORD_WEBHOOK_TLDR") or None,
        discord_webhook_brief=os.environ.get("DISCORD_WEBHOOK_BRIEF") or None,
        timezone=os.environ.get("TIMEZONE", "America/Los_Angeles"),
    )
