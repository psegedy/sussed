# 👨‍🍳 sussed CLI reference

All `sussed` commands run from inside the `sussed/` Python project directory, prefixed with `uv run`.

> **Quick discovery:** `uv run sussed --help` lists every command. `uv run sussed <command> --help` shows flags for any single command.

## Scraping Listings

```bash
# Scrape Brno apartments for sale
uv run sussed scrape -c brno

# Scrape with limit and verbose output
uv run sussed scrape -c brno -m 5 -v

# Scrape rentals instead of sales
uv run sussed scrape -c brno -t rent

# Scrape houses instead of apartments
uv run sussed scrape -c brno -p house

# Scrape cottages or garden plots
uv run sussed scrape -c brno -p cottage
uv run sussed scrape -c brno -p garden

# Scrape only listings from the last day/week/month
uv run sussed scrape -c brno -a day
uv run sussed scrape -c brno -a week
uv run sussed scrape -c brno -a month
```

| Flag | Description | Default |
|------|-------------|---------|
| `-c, --city` | City to scrape (brno, praha, ostrava) | brno |
| `-t, --type` | Listing type: sale or rent | sale |
| `-p, --property` | Property type: apartment, house, cottage, or garden | apartment |
| `-a, --age` | Filter by listing age: day, week, or month | all |
| `-m, --max-pages` | Maximum pages to scrape | all |
| `-v, --verbose` | Enable debug logging | false |

## Viewing Listings

```bash
# Show listings (table format)
uv run sussed listings

# Limit results
uv run sussed listings --limit 10

# Filter by apartment type
uv run sussed listings --type 2+kk

# Filter by max price
uv run sussed listings --max-price 5000000

# Export as Markdown (for AI analysis)
uv run sussed listings --format md

# Export to file
uv run sussed listings --format md --output listings.md
```

| Flag | Description | Default |
|------|-------------|---------|
| `-c, --city` | Filter by city | all |
| `-t, --type` | Filter by apartment type (2+kk, 3+1, etc.) | all |
| `--max-price` | Maximum price in CZK | none |
| `-l, --limit` | Number of results | 20 |
| `-f, --format` | Output format: table or md | table |
| `-o, --output` | Write to file instead of stdout | stdout |

## AI Reviewing Saved Listings

`sussed` prepares saved DB listings for review by Copilot CLI or Claude Code without storing an LLM API key in the app. The coding agent acts as the LLM (and vision) reviewer; `sussed` just persists structured results.

Run `sussed enrich` first — it fetches descriptions **and** pre-warms the photo cache under `.sussed/image-cache/<listing-id>/`. `sussed review prepare` reads only from that cache and never downloads photos itself.

```bash
# Pre-warm descriptions + photo cache (rate limited, be patient)
uv run sussed enrich --limit 10 --image-limit 5

# See queue health (counts of pending/reviewed listings)
uv run sussed review status

# Get smart review candidates (ranked by priority)
uv run sussed review candidates --limit 5

# Prepare one listing (reads cached photos from .sussed/image-cache/)
uv run sussed review prepare abcdef12 --output .sussed/image-cache/abcdef12-prepared.json

# Validate the AI-produced review JSON before saving (no DB write)
uv run sussed review validate .sussed/image-cache/abcdef12-review.json

# Save a structured AI review produced by an AI review skill
uv run sussed review save abcdef12 --input .sussed/image-cache/abcdef12-review.json
```

In Copilot CLI or Claude Code, invoke the right skill to run this loop end-to-end:

| Property type | Skill |
|---|---|
| Apartments | `sussed-ai-review` |
| Cottages (chata/chalupa) | `sussed-cottage-review` |
| Garden plots (zahrada/zahrádka) | `sussed-garden-review` |

Each skill uses the authenticated coding agent as the LLM/vision reviewer and `sussed` as the persistence layer — so no LLM API key ever lives inside the app.

## Autonomous Hunt Mode 🎯

The `hunt` command scores and ranks listings based on a YAML config file. It runs heuristic scoring on all listings, optionally enriches top candidates with descriptions, and can use an LLM for deeper analysis.

```bash
# Generate an example config file
uv run sussed hunt --generate-config

# Run with your config
uv run sussed hunt -c my_search.yaml

# Scrape fresh data first, then hunt (recommended!)
uv run sussed hunt -c my_search.yaml --scrape

# Hunt cottages or garden plots with example configs
uv run sussed hunt -c cottage_config.yaml --scrape
uv run sussed hunt -c garden_config.yaml --scrape

# Show top 5 best picks
uv run sussed hunt -c my_search.yaml --best 5

# Show trash/sus listings
uv run sussed hunt -c my_search.yaml --trash 10

# Show only gems (score >= 900)
uv run sussed hunt --gems

# Re-score everything from scratch
uv run sussed hunt --rescore

# Save results as JSON
uv run sussed hunt --best 10 -f json -s results.json
```

`sussed` supports four property types: apartments, houses, cottages (chata/chalupa), and garden plots (zahrada/zahrádka). Apartment, cottage, and garden hunts each have their own AI review skill and example config.

| Flag | Description | Default |
|------|-------------|---------|
| `-c, --config` | Path to search config YAML | search_config.yaml |
| `-b, --best` | Show top N highest scored | config default |
| `-t, --trash` | Show bottom N (overpriced/sus) | — |
| `-g, --gems` | Show only gems (score >= 900) | false |
| `-f, --format` | Output format: table, json, markdown | table |
| `-s, --save` | Save results to file | stdout |
| `-r, --rescore` | Re-score all listings | false |
| `--scrape` | Scrape fresh data before hunting | false |
| `-p, --scrape-pages` | Max pages to scrape | 5 |
| `-v, --verbose` | Enable debug logging | false |
| `--generate-config` | Generate example config and exit | — |

See [configuration.md](configuration.md) for the full YAML schema.

## Scheduled Service 🕐

Set up a daily "set it and forget it" job that scrapes fresh listings, AI-reviews the promising ones via Copilot CLI, writes a report, and pings you with a desktop notification. Uses **launchd** on macOS and a **systemd user timer** on Linux — no root, no cron.

```bash
# Install with defaults (runs daily at 10:00)
uv run sussed service install

# Pick your own time (24h HH:MM) and config
uv run sussed service install --time 07:30 --config search_config.yaml

# Is it alive? Last run? Recent reports?
uv run sussed service status

# Rip it out (keeps your logs + reports in ~/.sussed/)
uv run sussed service uninstall
```

Run `install` from the project directory (where `pyproject.toml` lives). Each day the service:

1. Runs `sussed hunt --scrape` (fresh listings + quick scores)
2. Runs `copilot` non-interactively to review new apartments with the `sussed-ai-review` skill
3. Writes a report to `~/.sussed/results/YYYY-MM-DD-daily-report.md`
4. Fires a desktop notification with the pick count

| Flag | Description | Default |
|------|-------------|---------|
| `-t, --time` | Daily run time (24h HH:MM) | 10:00 |
| `-c, --config` | Path to search config YAML | search_config.yaml |

**Missed a day?** If your machine was off at the scheduled time, the job catches up on next boot/login (systemd `Persistent=true`; launchd `RunAtLoad` + a schedule-aware guard) — but never runs twice in a day.

**Logs:** `~/.sussed/service.log` (plus the system journal on Linux / `~/.sussed/launchd.log` on macOS).

> ⚠️ **Security note:** step 2 runs `copilot` non-interactively over **scraped** listing text, which is untrusted. A malicious listing could attempt prompt injection. Permissions are scoped (no network/URL access, pinned working dir), but only point this at real-estate portals you trust.

## Price Drops 📉

Show every active listing that has had a price decrease, sorted by most-recent drop. Catches both regular decreases AND the sneaky "switched to POA" case where the seller hides the new price.

```bash
# All recent drops (default 20)
uv run sussed drops

# Only drops to POA / 1 Kč (seller hiding new price)
uv run sussed drops --to-poa

# Last 7 days, 2+kk only, in Brno
uv run sussed drops --days 7 --type 2+kk --city brno
```

| Flag | Description | Default |
|------|-------------|---------|
| `-l, --limit` | Max listings to show | 20 |
| `-d, --days` | Only drops in last N days | all |
| `-c, --city` | Filter by city | all |
| `-t, --type` | Filter by apartment type | all |
| `--to-poa` | Only listings that dropped to POA | false |

## Instagram-style Feed 📸

Generate a **single self-contained HTML file** of the best listings — a visual, shareable
alternative to the terminal output. No server, no API: the data is read from the database
and embedded in the page, and all filtering/sorting happens client-side in the browser.

Two tabs:
- **🏆 AI Picks** — AI-reviewed listings ranked by review score.
- **🆕 Fresh** — recently-listed listings (within `--fresh-days`) ranked by *effective*
  score: the AI review score when reviewed, otherwise the cheap `hunt` quick-score. This
  surfaces strong-but-not-yet-reviewed listings too.

Each listing renders as one Instagram-style post: photo carousel, price with price-change,
listing dates, AI summary, pros/cons, and a link to the original sreality listing.

```bash
# Best picks + fresh from the last week → sussed-feed.html
uv run sussed feed

# Open it in the browser as soon as it's generated
uv run sussed feed --open

# Apartments in one district, custom output path
uv run sussed feed -p apartment -d "Královo Pole" -o brno.html

# Widen the net: include unreviewed listings in AI Picks, 30-day fresh window
uv run sussed feed --all --fresh-days 30

# Only high scorers, more posts per tab
uv run sussed feed --min-score 700 --limit 100
```

| Flag | Description | Default |
|------|-------------|---------|
| `-o, --output` | Path to write the HTML file | sussed-feed.html |
| `-l, --limit` | Max posts per tab | 50 |
| `--fresh-days` | Age window (days) for the Fresh tab | 7 |
| `-m, --min-score` | Minimum effective score (both tabs) | — |
| `-d, --district` | Filter by district (fuzzy) | all |
| `-p, --property-type` | apartment, house, cottage, or garden | all |
| `--all` | Include unreviewed listings in AI Picks | false |
| `--title` | Page title | sussed · best picks |
| `--open/--no-open` | Open the file in a browser when done | no-open |

> **Note:** on the AI Picks tab, `--min-score` only matches listings that have a full AI
> review (unreviewed listings have no `ai_score`). The Fresh tab filters on the effective
> score, so it includes quick-scored listings too.

Open the generated file directly in any browser, or serve the folder with
`python -m http.server` and browse to it. The page is fully offline-capable except for
the listing photos (hotlinked from sreality's CDN) and web fonts.

## Getting Listing URLs

```bash
# Get URL by listing ID (supports partial IDs)
uv run sussed url c17c0eb1
```

## Database Management

```bash
# Initialize database tables
uv run sussed db init

# Check database connection
uv run sussed db status
```

## Other Commands

```bash
uv run sussed version    # Show version
uv run sussed --help     # Show help
```
