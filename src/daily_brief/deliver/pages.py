from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from ..models import WrittenStory

log = logging.getLogger(__name__)


PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{headline}</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  body {{ font-family: Charter, 'Source Serif Pro', Georgia, serif; max-width: 720px; margin: 0 auto; padding: 32px 20px; color: #1a1a1a; line-height: 1.65; }}
  h1 {{ font-size: 32px; line-height: 1.2; margin-bottom: 6px; }}
  h3 {{ font-size: 19px; margin-top: 28px; }}
  .meta {{ color: #888; font-size: 14px; margin-bottom: 24px; }}
  .topic {{ display: inline-block; font-size: 11px; text-transform: uppercase; letter-spacing: 1.5px; color: #b8651a; margin-bottom: 8px; font-weight: 600; }}
  .summary {{ font-size: 18px; color: #444; font-style: italic; margin-bottom: 32px; padding-left: 16px; border-left: 3px solid #b8651a; }}
  sup {{ font-size: 0.75em; color: #b8651a; padding: 0 1px; }}
  sup a {{ color: #b8651a; text-decoration: none; font-weight: 600; }}
  sup a:hover {{ text-decoration: underline; }}
  .references {{ margin-top: 48px; padding-top: 24px; border-top: 1px solid #ddd; font-size: 14px; }}
  .references h3 {{ font-size: 13px; text-transform: uppercase; letter-spacing: 1.2px; color: #888; margin-top: 0; }}
  .references ol {{ padding-left: 24px; color: #444; }}
  .references li {{ margin-bottom: 8px; }}
  .references a {{ color: #1a1a1a; }}
  a {{ color: #b8651a; }}
  .back {{ display: inline-block; margin-bottom: 24px; font-size: 13px; color: #888; text-decoration: none; }}
  blockquote {{ border-left: 3px solid #ddd; padding-left: 16px; margin-left: 0; color: #555; }}
</style>
</head>
<body>
<a class="back" href="./">← all briefs</a>
<div class="topic">{topic}</div>
<h1>{headline}</h1>
<div class="meta">{date_str}</div>
<div class="summary">{short_summary}</div>

{deep_html}

<div class="references">
<h3>References</h3>
<ol>{reference_items}</ol>
</div>
</body>
</html>
"""

INDEX_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Daily Brief — {date_str}</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  body {{ font-family: Charter, 'Source Serif Pro', Georgia, serif; max-width: 720px; margin: 0 auto; padding: 32px 20px; color: #1a1a1a; }}
  h1 {{ border-bottom: 2px solid #1a1a1a; padding-bottom: 8px; }}
  h2 {{ font-size: 13px; text-transform: uppercase; letter-spacing: 1.2px; color: #888; margin-top: 32px; }}
  ul {{ list-style: none; padding: 0; }}
  li {{ padding: 12px 0; border-bottom: 1px solid #eee; }}
  a {{ color: #1a1a1a; text-decoration: none; font-size: 18px; }}
  a:hover {{ color: #b8651a; }}
</style>
</head>
<body>
<h1>Daily Brief — {date_str}</h1>
{sections}
</body>
</html>
"""


def _wrap_sup_with_links(html: str, sources: list[dict]) -> str:
    """Convert <sup>N</sup> to <sup><a href="#ref-N">N</a></sup> linking to footnotes."""
    if not html:
        return ""
    import re
    def repl(m: re.Match) -> str:
        n = m.group(1)
        try:
            idx = int(n)
        except ValueError:
            return m.group(0)
        if 1 <= idx <= len(sources):
            return f'<sup><a href="#ref-{idx}">{idx}</a></sup>'
        return m.group(0)
    return re.sub(r"<sup>\s*(\d+)\s*</sup>", repl, html)


def _reference_items(sources: list[dict]) -> str:
    """Generate <li id="ref-N"> items in source order."""
    lines = []
    for i, s in enumerate(sources, 1):
        title = s.get("title", "")
        name = s.get("name", "")
        url = s.get("url", "#")
        lines.append(
            f'<li id="ref-{i}"><a href="{url}">{title}</a> — <em>{name}</em></li>'
        )
    return "\n".join(lines)


def write_static_pages(
    out_dir: Path,
    deep_stories: list[WrittenStory],
    short_stories: list[WrittenStory],
    timezone_name: str,
) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now(ZoneInfo(timezone_name)).strftime("%A, %B %d, %Y")
    written: list[Path] = []

    for story in deep_stories:
        if not story.deep_dive_html:
            continue
        deep_html = _wrap_sup_with_links(story.deep_dive_html, story.sources)
        html = PAGE_TEMPLATE.format(
            headline=story.headline,
            topic=story.topic,
            date_str=date_str,
            short_summary=story.short_summary,
            deep_html=deep_html,
            reference_items=_reference_items(story.sources),
        )
        path = out_dir / f"{story.cluster_id}.html"
        path.write_text(html, encoding="utf-8")
        written.append(path)
        log.info("Wrote %s", path)

    # Build an index grouped by topic.
    by_topic: dict[str, list[WrittenStory]] = {}
    for s in deep_stories + short_stories:
        by_topic.setdefault(s.topic, []).append(s)

    section_lines: list[str] = []
    for topic in [
        "world", "politics",
        "economics", "markets", "business",
        "ai", "technology", "startups",
        "science", "opinion",
    ]:
        items = by_topic.get(topic, [])
        if not items:
            continue
        section_lines.append(f"<h2>{topic}</h2>\n<ul>")
        for s in items:
            if s.is_deep_dive and s.deep_dive_html:
                section_lines.append(f'<li><a href="{s.cluster_id}.html">{s.headline}</a></li>')
            else:
                # short summaries don't have their own page; link to first source
                source_url = s.sources[0]["url"] if s.sources else "#"
                section_lines.append(f'<li><a href="{source_url}">{s.headline}</a></li>')
        section_lines.append("</ul>")

    index = INDEX_TEMPLATE.format(date_str=date_str, sections="\n".join(section_lines))
    (out_dir / "index.html").write_text(index, encoding="utf-8")
    written.append(out_dir / "index.html")
    return written
