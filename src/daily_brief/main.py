from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from anthropic import Anthropic

from . import storage
from .config import DATA_DIR, ROOT, load_interests, load_settings, load_sources
from .deliver.discord import send_discord
from .deliver.email import render_email, send_email_resend
from .deliver.pages import write_static_pages
from .ingest import gather_articles
from .models import Article
from .process.dedupe import cluster_articles
from .process.rank import score_clusters, select_for_brief
from .process.summarize import write_stories
from .process.tldr import generate_tldr


def setup_logging(verbose: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )
    # Tame noisy libs.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("anthropic").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def filter_unseen(articles: list[Article]) -> list[Article]:
    fresh: list[Article] = []
    rows: list[tuple[str, str, str, str]] = []
    for a in articles:
        fp = a.fingerprint()
        if storage.already_seen(fp):
            continue
        fresh.append(a)
        rows.append((fp, a.source_id, a.title, a.url))
    storage.mark_seen_batch(rows)
    return fresh


def run(*, dry_run: bool, skip_send: bool) -> int:
    log = logging.getLogger("daily_brief")
    storage.init()
    storage.prune_seen(days=30)

    settings = load_settings()
    sources = load_sources()
    interests = load_interests()

    run_id = storage.start_run()
    error: str | None = None

    try:
        log.info("=== Ingestion ===")
        articles = gather_articles(sources, settings)
        articles = filter_unseen(articles)
        log.info("After dedup vs history: %d fresh articles", len(articles))

        if not articles:
            log.warning("No fresh articles — exiting")
            storage.finish_run(run_id, 0, 0, delivered=False, error="no fresh articles")
            return 0

        log.info("=== Clustering ===")
        clusters = cluster_articles(articles)
        clusters = score_clusters(clusters, interests)

        deep_clusters, short_clusters, head_clusters = select_for_brief(clusters, interests)
        log.info("Selected: %d deep / %d short / %d headlines",
                 len(deep_clusters), len(short_clusters), len(head_clusters))

        if dry_run:
            log.info("=== DRY RUN — selected clusters ===")
            for tier, group in [("DEEP", deep_clusters), ("SHORT", short_clusters), ("HEAD", head_clusters)]:
                for c in group:
                    log.info("[%s %.2f] %s — %s (%d sources)", tier, c.score, c.primary_topic, c.lead.title, len(c.articles))
            storage.finish_run(run_id, len(articles), len(clusters), delivered=False)
            return 0

        log.info("=== Writing stories with Claude ===")
        deep_stories, short_stories = write_stories(settings, deep_clusters, short_clusters)
        if not deep_stories and not short_stories:
            raise RuntimeError("Claude produced zero stories")

        log.info("=== Generating TL;DR ===")
        client = Anthropic(api_key=settings.anthropic_api_key)
        tldr = generate_tldr(client, settings.anthropic_model, deep_stories + short_stories)

        headline_articles = [c.lead for c in head_clusters]

        out_dir = ROOT / "out"
        deep_dive_base_url = os.environ.get("DEEP_DIVE_BASE_URL")  # set by GH Pages workflow

        log.info("=== Writing static pages ===")
        write_static_pages(out_dir, deep_stories, short_stories, settings.timezone)

        run_stats = (
            f"{len(articles)} articles · {len(clusters)} clusters · "
            f"{len(deep_stories)} deep dives · {len(short_stories)} shorts"
        )

        if skip_send:
            log.info("--skip-send: not delivering")
            log.info("Static pages: %s", out_dir)
            storage.finish_run(run_id, len(articles), len(clusters), delivered=False)
            return 0

        log.info("=== Delivering ===")
        subject, html = render_email(
            deep_stories=deep_stories,
            short_stories=short_stories,
            headline_articles=headline_articles,
            tldr=tldr,
            timezone_name=settings.timezone,
            run_stats=run_stats,
            deep_dive_base_url=deep_dive_base_url,
        )
        email_ok = send_email_resend(settings, subject, html)
        discord_ok = send_discord(
            settings,
            deep_stories=deep_stories,
            short_stories=short_stories,
            headline_articles=headline_articles,
            tldr=tldr,
            deep_dive_base_url=deep_dive_base_url,
        )
        delivered = email_ok or discord_ok

        # Save the rendered email locally too.
        (out_dir / "latest.html").write_text(html, encoding="utf-8")

        storage.finish_run(run_id, len(articles), len(clusters), delivered=delivered)
        log.info("Done. delivered=%s email=%s discord=%s", delivered, email_ok, discord_ok)
        return 0 if delivered else 1

    except Exception as e:
        error = str(e)
        log.exception("Run failed")
        storage.finish_run(run_id, 0, 0, delivered=False, error=error)
        return 2


def main() -> int:
    parser = argparse.ArgumentParser(description="Daily personalized news brief")
    parser.add_argument("--dry-run", action="store_true", help="Ingest + cluster + rank, but don't call Claude or deliver")
    parser.add_argument("--skip-send", action="store_true", help="Generate stories but don't email/Discord")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    setup_logging(args.verbose)
    return run(dry_run=args.dry_run, skip_send=args.skip_send)


if __name__ == "__main__":
    sys.exit(main())
