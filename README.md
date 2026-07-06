# 🕵️‍♂️ sussed

> **Because life is too short to live in a mid apartment.** 👨‍🍳🔥

Imagine being **delulu** enough to manually refresh real estate portals in 2026. **Skill issue.** `sussed` is an AI-powered real estate agent that susses out the market so you don't have to. The name comes from the Slovak word **"sused"** (neighbor), because we find your future neighbors before they even know you're moving in. It crawls the **sus** listings, parses the data, and alerts you when it finds a deal that actually **ate**.

## ✨ Why `sussed`?

* **Main Character Energy:** Why look for a home when the home can find you? 💅
* **Zero Mid Listings:** Our agent filters out the "cozy studios" (closets) so you only see the **W** deals.
* **Sussing the Sused:** We analyze the neighborhood vibes so you don't end up living next to an NPC.
* **No Crumbs Left:** High-speed parsing ensures you're the first to the viewing. **Fr fr.**

## 👨‍🍳 The Recipe (How it works)

- **The Sniff:** The agent scrolls through portals like it's on TikTok, looking for new drops.
- **The Suss:** AI translates "vibrant neighborhood" to "loud AF" and "lots of potential" to "this place is falling apart."
- **The Glow Up:** You get a clean notification only when a deal is valid.

Four property types are supported out of the box: **apartments**, **houses**, **cottages** (chata/chalupa), and **garden plots** (zahrada/zahrádka). Each has its own AI review skill + example hunt config.

## 🚀 Quick Start

**Prerequisites:** Python 3.14+, [uv](https://github.com/astral-sh/uv), and Docker/Podman for PostgreSQL.

```bash
git clone https://github.com/yourusername/sussed.git
cd sussed/sussed

docker compose up -d         # fire up the DB
uv sync                      # install deps
uv run sussed db init        # create tables
uv run sussed scrape -c brno -m 5   # grab some listings
uv run sussed hunt -c search_config.yaml --scrape   # score them
uv run sussed feed --open    # browse the best picks in a slick web feed
```

## 📚 Docs

- **[CLI reference](sussed/docs/cli.md)** — every command, every flag, with examples (`scrape`, `listings`, `hunt`, `drops`, `dedup`, `enrich`, `review`, `service`, `feed`, `url`, `db`)
- **[Configuration reference](sussed/docs/configuration.md)** — environment variables + the full YAML schema for hunt configs (criteria, scoring, output, runner)
- **Example configs** — [`sussed/search_config.yaml`](sussed/search_config.yaml) (apartments), [`sussed/cottage_config.yaml`](sussed/cottage_config.yaml), [`sussed/garden_config.yaml`](sussed/garden_config.yaml), [`sussed/simple_config.yaml`](sussed/simple_config.yaml)
- **AI review skills** — invoke from Copilot CLI or Claude Code (no LLM API key needed; the coding agent IS the reviewer)
  - `sussed-ai-review` for apartments
  - `sussed-cottage-review` for chata/chalupa
  - `sussed-garden-review` for zahrada/zahrádka

## 📊 Data Sources

- **sreality.cz** — Czech Republic's largest real estate portal (free JSON API, no scraping needed)

## 🗂 Project Structure

Heads up: the repo root and the Python project are **both** named `sussed/` (run all `uv run sussed …` commands from the inner one).

```
sussed/                              # repo root
├── README.md                        # you are here
├── .copilot/sussed-plugin/skills/   # AI review skills (sussed-ai-review, -cottage-, -garden-)
└── sussed/                          # the Python project — run commands from here
    ├── src/sussed/                  # cli · scrapers · hunt · review · models · db · config
    ├── docs/                        # cli.md + configuration.md
    ├── scripts/                     # init-db.sql
    ├── tests/                       # pytest suite
    ├── *_config.yaml                # search / cottage / garden / simple hunt configs
    ├── docker-compose.yml           # PostgreSQL
    ├── .env.example
    └── pyproject.toml · uv.lock
```

## 🤝 Contributing

If you want to add more rizz to the scrapers or improve the parsing logic, feel free to open a PR. Don't be mid-contribute.

## License

MIT
