from __future__ import annotations

import logging

from anthropic import Anthropic
from tenacity import retry, stop_after_attempt, wait_exponential

from ..models import WrittenStory

log = logging.getLogger(__name__)

TLDR_SYSTEM = """You are writing a TL;DR for the top of a daily brief. Each bullet is ONE sentence, declarative, with specific actors and numbers. No filler verbs. No meta-framing ("This is about..."). Submit via the publish_tldr tool."""

TLDR_TOOL = {
    "name": "publish_tldr",
    "description": "Submit a 5-bullet TL;DR.",
    "input_schema": {
        "type": "object",
        "properties": {
            "bullets": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 3,
                "maxItems": 7,
                "description": "Each bullet is one declarative sentence.",
            },
        },
        "required": ["bullets"],
    },
}


@retry(stop=stop_after_attempt(2), wait=wait_exponential(min=1, max=8))
def generate_tldr(client: Anthropic, model: str, stories: list[WrittenStory], n: int = 5) -> list[str]:
    if not stories:
        return []
    digest = "\n\n".join(
        f"Story {i+1} ({s.topic}): {s.headline}\n{s.short_summary}"
        for i, s in enumerate(stories[:8])
    )
    user = f"Write a {n}-bullet TL;DR (one sentence each) covering the most important items below.\n\n{digest}"
    try:
        resp = client.messages.create(
            model=model,
            max_tokens=500,
            system=TLDR_SYSTEM,
            messages=[{"role": "user", "content": user}],
            tools=[TLDR_TOOL],
            tool_choice={"type": "tool", "name": TLDR_TOOL["name"]},
        )
        for block in resp.content:
            if getattr(block, "type", None) == "tool_use" and block.name == TLDR_TOOL["name"]:
                bullets = block.input.get("bullets", [])
                return [str(b) for b in bullets[:n]]
    except Exception as e:
        log.warning("TL;DR generation failed: %s — falling back to headlines", e)
    return [s.headline for s in stories[:n]]
