# 🛠 Configuration Reference

## Environment Variables

Copy `.env.example` to `.env` to override defaults (all have sensible defaults out of the box):

```bash
cp .env.example .env
```

| Variable | Description | Default |
|----------|-------------|---------|
| `DATABASE_URL` | PostgreSQL connection URL | `postgresql+asyncpg://sussed:sussed_dev_password@localhost:5432/sussed` |
| `SCRAPE_RATE_LIMIT` | Max requests per second | `1.0` |
| `USER_AGENT` | HTTP User-Agent header | Mozilla/5.0 ... |
| `OPENAI_API_KEY` | OpenAI API key (for LLM analysis) | — |
| `ANTHROPIC_API_KEY` | Anthropic API key (for LLM analysis) | — |
| `LOG_LEVEL` | Logging level | `INFO` |

## Hunt Search Config (YAML)

The `hunt` command uses a YAML config file to define your search. Generate an example with:

```bash
uv run sussed hunt --generate-config
```

### Top-level fields

| Field | Description | Default |
|-------|-------------|---------|
| `name` | Name for your search | `My Dream Home Search` |
| `description` | Optional notes | — |
| `notes_for_agent` | Free-form notes for the AI to consider | — |
| `preferred_districts` | Ranked list of preferred districts (first = best) | — |
| `avoid_districts` | Districts to avoid or heavily penalize | — |
| `known_bad_locations` | Known problematic streets/areas to penalize | Cejl, Zábrdovice, Bratislavská, ... |

### `criteria` — What you're looking for

| Field | Description | Default |
|-------|-------------|---------|
| `city` | City to search in | `Brno` |
| `districts` | Specific districts to include | any |
| `exclude_districts` | Districts to avoid | — |
| `apartment_types` | Types like `[2+kk, 2+1, 3+kk]` | any |
| `property_type` | `apartment`, `house`, `cottage`, or `garden` | `apartment` |
| `listing_type` | `sale` or `rent` | `sale` |
| `min_price` | Minimum price in CZK | — |
| `max_price` | Maximum price in CZK | — |
| `max_price_per_m2` | Max price per m² (key metric!) | — |
| `min_area_m2` | Minimum usable area in m² (plot area for gardens) | — |
| `max_area_m2` | Maximum area in m² | — |
| `min_plot_size_m2` | Minimum plot size for cottages/gardens when known | — |
| `min_indoor_area_m2` | Minimum indoor/living area for cottages when known | — |
| `min_floor` | Minimum floor (0 = ground) | — |
| `max_floor` | Maximum floor | — |
| `avoid_ground_floor` | Skip ground floor listings | `false` |
| `avoid_top_floor` | Skip top floor listings (potential roof issues) | `false` |
| `require_parking` | Must have parking/garage | `false` |
| `require_balcony` | Must have balcony/loggia/terrace | `false` |
| `require_elevator` | Building must have elevator | `false` |
| `require_electricity` | Cottage/garden must have electricity | `false` |
| `require_water` | Cottage/garden must have water | `false` |
| `require_fenced` | Garden/cottage plot must be fenced | `false` |
| `reject_panel_building` | No panel buildings (panelák) | `false` |
| `reject_ground_floor` | Hard reject ground floor | `false` |
| `min_photos` | Min photos (few = hiding something) | `3` |
| `require_floor_plan` | Must have floor plan | `false` |
| `exclude_description_keywords` | Comma-separated keywords to auto-reject | — |
| `max_listing_age` | `day`, `week`, `month`, or number of days | all |

### `scoring` — Bonus/penalty modifiers

| Field | Description | Default |
|-------|-------------|---------|
| `bonus_new_building` | Bonus points for new construction | `100` |
| `bonus_reconstruction` | Bonus for recently reconstructed | `50` |
| `bonus_very_good_condition` | Bonus for very good condition | `40` |
| `penalty_no_parking` | Penalty when parking is required but missing | `-100` |
| `penalty_no_balcony` | Penalty when balcony is required but missing | `-100` |
| `penalty_no_elevator` | Penalty when elevator is required but missing | `-100` |
| `penalty_panel` | Penalty for panel buildings | `-100` |
| `penalize_description_keywords` | Custom keywords to penalize (e.g., `{dražba: -80}`) | — |
| `bonus_description_keywords` | Custom keywords to reward (e.g., `{garáž v ceně: 40}`) | — |

### `output` — How to display results

| Field | Description | Default |
|-------|-------------|---------|
| `mode` | `best`, `trash`, `gems`, `sus`, or `all` | `best` |
| `limit` | Number of results to show (1-100) | `10` |
| `include_analysis` | Include AI analysis in output | `true` |
| `format` | `table`, `json`, or `markdown` | `table` |
| `save_to_file` | Save output to this file path | — |

### `runner` — Hunt runner behavior

| Field | Description | Default |
|-------|-------------|---------|
| `max_listings_to_process` | Max listings to analyze per run | `50` |
| `fetch_descriptions` | Fetch missing descriptions from API | `true` |
| `enrich_top_n` | How many top candidates to enrich (1-2000) | `5` |
| `skip_already_scored` | Skip listings that already have a score | `true` |
| `use_llm` | Use LLM for description analysis | `true` |
| `llm_provider` | `anthropic` (Claude) or `openai` (GPT) | `anthropic` |
| `llm_model` | Specific model ID (default: claude-haiku-4-5 / gpt-4o-mini) | auto |
| `llm_analyze_top_n` | How many top listings to LLM-analyze (1-50) | `5` |
| `poa_evaluation_mode` | How to evaluate 1 Kč listings: `description_only`, `skip`, `estimate` | `description_only` |
| `auto_scrape` | Scrape before processing | `false` |
| `scrape_max_pages` | Max pages to scrape if auto_scrape enabled | `5` |

## Property type examples

For apartment hunts: see [`../search_config.yaml`](../search_config.yaml) or [`../simple_config.yaml`](../simple_config.yaml).
For cottage hunts: see [`../cottage_config.yaml`](../cottage_config.yaml) (uses [`sussed-cottage-review` skill](../../.copilot/sussed-plugin/skills/sussed-cottage-review/SKILL.md)).
For garden hunts: see [`../garden_config.yaml`](../garden_config.yaml) (uses [`sussed-garden-review` skill](../../.copilot/sussed-plugin/skills/sussed-garden-review/SKILL.md)).
