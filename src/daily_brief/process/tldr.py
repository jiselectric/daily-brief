from __future__ import annotations

import json
import logging
import re

from anthropic import Anthropic
from tenacity import retry, stop_after_attempt, wait_exponential

from ..models import WrittenStory

log = logging.getLogger(__name__)

TLDR_SYSTEM = """You are writing a 5-bullet TL;DR for the top of a daily brief. Each bullet is one sentence, declarative, names specific actors and numbers. No filler verbs. Return ONLY a JSON array of strings.

Example: ["Fed held rates at 5.25-5.50% but signaled three cuts in 2025.", "Nvidia's data-center revenue hit $30.8B, up 154% YoY...", ...]
"""


@retry(stop=stop_after_attempt(2), wait=wait_exponential(min=1, max=8))
def generate_tldr(client: Anthropic, model: str, stories: list[WrittenStory], n: int = 5) -> list[str]:
    if not stories:
        return []
    digest = "\n\n".join(
        f"Story {i+1} ({s.topic}): {s.headline}\n{s.short_summary}"
        for i, s in enumerate(stories[:8])
    )
    user = f"Write a {n}-bullet TL;DR (one sentence each) covering the most important items below.\n\n{digest}"
    resp = client.messages.create(
        model=model, max_tokens=400, system=TLDR_SYSTEM,
        messages=[{"role": "user", "content": user}],
    )
    text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        bullets = json.loads(text)
        if isinstance(bullets, list):
            return [str(b) for b in bullets[:n]]
    except json.JSONDecodeError:
        log.warning("TL;DR: JSON parse failed; falling back")
    return [s.headline for s in stories[:n]]
