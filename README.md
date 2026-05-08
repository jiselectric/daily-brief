# Daily Brief

Personalized daily news brief — pulls from RSS + NYT + Hacker News, clusters stories across sources, generates Stratechery-style synthesized analysis with Claude, and delivers to your email and Discord at 8am PT.

## What you get

- **TL;DR** — 5 single-sentence bullets at the top
- **Deep dives** (5×) — 1200–1700 word multi-source analytical pieces, hosted as static pages on GitHub Pages
- **Short summaries** (10×) — ~150-word skims for email/Discord
- **Skim list** (15×) — bare headline + source + link

Topic mix favors politics, economics, business, AI, and technology, balanced so AI/tech doesn't dominate.

## v0 architecture (this repo)

```
RSS + NYT API + HN  →  cluster by title similarity  →  rank by topic/freshness/authority
                                                              ↓
                                          Claude writes deep dives + short summaries
                                                              ↓
                                  Email (Resend)  ·  Discord (webhook)  ·  GitHub Pages
```

Runs on a free GitHub Actions cron. SQLite (`data/brief.db`) holds dedup state and is committed back to the repo each run.

What's coming next:
- **v1** — Claude tool-use for data investigation (FRED / BLS / SEC EDGAR / World Bank) and chart generation
- **v1.5** — Email-forwarding inbox for paywalled subscriber newsletters (NYT/WSJ/Economist)
- **v2** — 👍/👎 feedback loop in Discord

---

## Setup

### 1. Local dev

```bash
cd daily-brief
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
cp .env.example .env
# fill in .env with your keys
```

### 2. API keys you need

| Service | Why | Where to get | Cost |
|---|---|---|---|
| **Anthropic** | Claude writes the briefs | https://console.anthropic.com → API Keys | ~$40–80/mo at full volume |
| **NYT Developer** | Top Stories API | https://developer.nytimes.com → register app, enable "Top Stories API" | Free |
| **Resend** | Email delivery | https://resend.com → API Keys | Free tier covers 3k emails/mo |
| **Discord webhook** | Discord delivery | Discord channel → Edit Channel → Integrations → Webhooks → New | Free |

For Resend's free tier you can send from `onboarding@resend.dev` without verifying a domain — fine for personal use. To send from your own domain, verify it in the Resend dashboard.

### 3. Run locally

```bash
# Sanity check — ingest + cluster, no Claude calls, no delivery
python -m daily_brief.main --dry-run -v

# Generate stories + write static pages, but don't actually send
python -m daily_brief.main --skip-send -v

# Full run (sends to email + Discord)
python -m daily_brief.main -v
```

The static deep-dive pages are written to `out/`. Open `out/index.html` in a browser.

### 4. Deploy the cron

1. Create a new GitHub repo and push this code.
2. Repo → Settings → Secrets and variables → Actions → **Secrets**:
   - `ANTHROPIC_API_KEY`
   - `NYT_API_KEY`
   - `RESEND_API_KEY`
   - `TO_EMAIL` (the address you want to receive the brief)
   - `DISCORD_WEBHOOK_URL`
3. Same screen → **Variables** (optional):
   - `ANTHROPIC_MODEL` — defaults to `claude-sonnet-4-6`
   - `RESEND_FROM` — defaults to `Daily Brief <onboarding@resend.dev>`
   - `DEEP_DIVE_BASE_URL` — set this to `https://<your-username>.github.io/<repo-name>` after enabling Pages (step 4) so "Read full analysis →" links work
4. Repo → Settings → Pages → Source: **GitHub Actions**
5. Test it: Actions tab → "Daily Brief" → Run workflow → set `dry_run=true` first.

The workflow runs at 15:00 and 16:00 UTC daily, but a sentinel check ensures it only proceeds when the Pacific local clock reads 8am — handles DST automatically.

### 5. Customize

- `config/interests.yaml` — topic weights, boost/exclude keywords, deep-dive count
- `config/sources.yaml` — add/remove RSS feeds, adjust source authority weights

---

## Cost estimate

At default settings (5 deep dives + 10 short summaries + 1 TL;DR per day):
- ~150k input tokens + ~30k output tokens per day with Claude Sonnet 4.6
- ≈ **$1.30/day → $40/mo** for AI calls
- Hosting: $0 (GitHub Actions, GitHub Pages, Resend free tier)

If costs come in higher than expected, set `ANTHROPIC_MODEL=claude-haiku-4-5-20251001` for short summaries to drop ~70% — but the deep dives lose noticeable quality.

## Troubleshooting

- **No fresh articles**: the seen-articles dedup is too aggressive. Delete `data/brief.db` to reset.
- **Email didn't arrive**: check Resend dashboard for delivery logs; the free `onboarding@resend.dev` sender often hits spam — verify your own domain.
- **Discord post truncated**: messages are auto-chunked at 1900 chars; check for rate-limit logs.
- **Cron didn't fire**: GitHub Actions disables schedules on inactive repos after 60 days. Push any commit to re-enable.
