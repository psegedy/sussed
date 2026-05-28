# 🕵️‍♂️ sussed

> **Because life is too short to live in a mid apartment.** 👨‍🍳🔥

Imagine being **delulu** enough to manually refresh real estate portals in 2026. **Skill issue.** `sussed` is an AI-powered real estate agent that susses out the market so you don't have to. The name comes from the Slovak word **"sused"** (neighbor), because we find your future neighbors before they even know you're moving in. It crawls the **sus** listings, parses the data, and alerts you when it finds a deal that actually **ate**.

## ✨ Why `sussed`?

* **Main Character Energy:** Why look for a home when the home can find you? 💅
* **Zero Mid Listings:** Our agent filters out the "cozy studios" (closets) so you only see the **W** deals.
* **Sussing the Sused:** We analyze the neighborhood vibes so you don't end up living next to an NPC.
* **No Crumbs Left:** High-speed parsing ensures you're the first to the viewing. **Fr fr.**

## �‍🍳 The Recipe (How it works)

- **The Sniff:** The agent scrolls through portals like it's on TikTok, looking for new drops.
- **The Suss:** AI translates "vibrant neighborhood" to "loud AF" and "lots of potential" to "this place is falling apart."
- **The Glow Up:** You get a clean notification (Discord/Telegram/Slack) only when a deal is valid.

## �🚀 Quick Start

### Prerequisites

- Python 3.14+
- [uv](https://github.com/astral-sh/uv) (because `pip` is mid)
- Docker or Podman (for PostgreSQL)

### Installation

```bash
# Clone the repo
git clone https://github.com/yourusername/sussed.git
cd sussed/sussed

# Fire up the database
docker compose up -d

# Install dependencies
uv sync

# Initialize the database
uv run sussed db init
```

## 👨‍🍳 CLI Usage

### Scraping Listings

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

### Viewing Listings

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

### AI Reviewing Saved Listings

`sussed` can prepare saved DB listings for review by Copilot CLI or Claude Code without storing an LLM API key in the app. The coding agent acts as the LLM (and vision) reviewer; `sussed` just persists structured results.

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

# Save a structured AI review produced by the sussed-ai-review skill
uv run sussed review save abcdef12 --input .sussed/image-cache/abcdef12-review.json
```

In Copilot CLI or Claude Code, invoke the right skill to run this loop end-to-end: `sussed-ai-review` for apartments, `sussed-cottage-review` for cottages, or `sussed-garden-review` for garden plots. Each skill uses the authenticated coding agent as the LLM/vision reviewer and `sussed` as the persistence layer — so no LLM API key ever lives inside the app.

### Autonomous Hunt Mode 🎯

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

`sussed` now supports four property types: apartments, houses, cottages (chata/chalupa), and garden plots (zahrada/zahrádka). Apartment, cottage, and garden hunts each have their own AI review skill and example config.

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

### Price Drops 📉

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

### Getting Listing URLs

```bash
# Get URL by listing ID (supports partial IDs)
uv run sussed url c17c0eb1
```

### Database Management

```bash
# Initialize database tables
uv run sussed db init

# Check database connection
uv run sussed db status
```

### Other Commands

```bash
uv run sussed version    # Show version
uv run sussed --help     # Show help
```

## 🛠 Configuration

All settings have sensible defaults out of the box. See [docs/configuration.md](sussed/docs/configuration.md) for the full reference covering environment variables and YAML search config fields.

## 📊 Data Sources

Currently supported:
- **sreality.cz** - Czech Republic's largest real estate portal (free JSON API, no scraping needed!)

## 🗂 Project Structure

```
sussed/
├── src/sussed/
│   ├── cli.py          # CLI commands
│   ├── config.py       # Configuration
│   ├── db/             # Database layer
│   ├── scrapers/       # Scraping modules
│   └── models/         # Pydantic models
├── docker-compose.yml  # PostgreSQL setup
└── pyproject.toml
```

## 🤝 Contributing

If you want to add more rizz to the scrapers or improve the parsing logic, feel free to open a PR. Don't be mid-contribute.

## License

MIT
