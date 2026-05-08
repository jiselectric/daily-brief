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
  body {{ font-family: Charter, Georgia, serif; max-width: 720px; margin: 0 auto; padding: 32px 20px; color: #1a1a1a; line-height: 1.65; }}
  h1 {{ font-size: 32px; line-height: 1.2; margin-bottom: 6px; }}
  h3 {{ font-size: 19px; margin-top: 28px; }}
  .meta {{ color: #888; font-size: 14px; margin-bottom: 24px; }}
  .topic {{ display: inline-block; font-size: 11px; text-transform: uppercase; letter-spacing: 1.5px; color: #b8651a; margin-bottom: 8px; font-weight: 600; }}
  .summary {{ font-size: 18px; color: #444; font-style: italic; margin-bottom: 32px; padding-left: 16px; border-left: 3px solid #b8651a; }}
  .sources {{ margin-top: 48px; padding-top: 24px; border-top: 1px solid #ddd; }}
  .sources h3 {{ font-size: 13px; text-transform: uppercase; letter-spacing: 1.2px; color: #888; }}
  .sources ul {{ list-style: none; padding: 0; }}
  .sources li {{ margin-bottom: 8px; font-size: 14px; }}
  .sources a {{ color: #1a1a1a; }}
  a {{ color: #b8651a; }}
  .back {{ display: inline-block; margin-bottom: 24px; font-size: 13px; color: #888; text-decoration: none; }}
</style>
</head>
<body>
<a class="back" href="./">← all briefs</a>
<div class="topic">{topic}</div>
<h1>{headline}</h1>
<div class="meta">{date_str}</div>
<div class="summary">{short_summary}</div>

{deep_html}

<div class="sources">
<h3>Sources</h3>
<ul>{source_items}</ul>
</div>
</body>
</html>
"""

INDEX_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Daily Brief Archive</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  body {{ font-family: Charter, Georgia, serif; max-width: 720px; margin: 0 auto; padding: 32px 20px; color: #1a1a1a; }}
  h1 {{ border-bottom: 2px solid #1a1a1a; padding-bottom: 8px; }}
  ul {{ list-style: none; padding: 0; }}
  li {{ padding: 12px 0; border-bottom: 1px solid #eee; }}
  a {{ color: #1a1a1a; text-decoration: none; font-size: 18px; }}
  a:hover {{ color: #b8651a; }}
  .topic {{ display: inline-block; font-size: 10px; text-transform: uppercase; letter-spacing: 1.2px; color: #888; margin-right: 8px; }}
</style>
</head>
<body>
<h1>Daily Brief — {date_str}</h1>
<ul>
{items}
</ul>
</body>
</html>
"""


def write_static_pages(
    out_dir: Path,
    deep_stories: list[WrittenStory],
    timezone_name: str,
) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now(ZoneInfo(timezone_name)).strftime("%A, %B %d, %Y")
    written: list[Path] = []

    for story in deep_stories:
        if not story.deep_dive_html:
            continue
        source_items = "\n".join(
            f'<li><a href="{s["url"]}">{s["name"]}</a> — {s["title"]}</li>'
            for s in story.sources
        )
        html = PAGE_TEMPLATE.format(
            headline=story.headline,
            topic=story.topic,
            date_str=date_str,
            short_summary=story.short_summary,
            deep_html=story.deep_dive_html,
            source_items=source_items,
        )
        path = out_dir / f"{story.cluster_id}.html"
        path.write_text(html, encoding="utf-8")
        written.append(path)
        log.info("Wrote %s", path)

    items = "\n".join(
        f'<li><span class="topic">{s.topic}</span>'
        f'<a href="{s.cluster_id}.html">{s.headline}</a></li>'
        for s in deep_stories if s.deep_dive_html
    )
    index = INDEX_TEMPLATE.format(date_str=date_str, items=items)
    (out_dir / "index.html").write_text(index, encoding="utf-8")
    written.append(out_dir / "index.html")
    return written
