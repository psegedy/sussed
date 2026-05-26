---
name: sussed-ai-review
description: Use when reviewing, scoring, re-scoring, vibe-checking, or analyzing saved sussed real-estate listings with Copilot CLI or Claude Code
---

# Sussed AI Listing Review

## Overview

Review saved `sussed` listings with the coding agent as the reasoning and vision reviewer, and the `sussed` CLI as the only read/prepare/validate/save path. `sussed enrich` pre-warms the per-listing photo cache; `sussed review prepare` only reads from that cache and never downloads anything itself.

**Core principle:** reason from prepared evidence only. Do not invent missing facts, skip available photos, or persist reviews outside `sussed review save`.

## When to Use

Use when the user asks to:
- Review, score, re-score, vibe-check, or analyze saved `sussed` listings.
- Compare listing text, structured details, photos, hidden costs, or inflated area claims.
- Save an AI review result back to the `sussed` database.

Do not use for general code review, scraper debugging, README edits, or implementing new features.

## Ideal Apartment Profile

When scoring, weigh these preferences (the buyer's wish list):

- **Modern / reconstructed** apartment — freshly renovated, new development, or move-in ready.
- **Pretty modern minimalist kitchen** — clean lines, built-in appliances, no dated cabinetry.
- **Big French windows** (floor-to-ceiling) — natural light is a priority.
- **Terrace or balcony** — outdoor space is a strong plus.
- **Parking included in price** — no extra monthly cost for a parking spot.

**Layout preference (ranked):**
1. **2+kk** — preferred: affordable, modern open-plan, fits the budget.
2. **2+1** — good: separate kitchen is fine, often cheaper than 2+kk.
3. **3+kk** — great if the price is right (under ~10M with parking), but often too expensive.
4. **3+1** — acceptable only at a bargain price; usually pushes past budget.
5. **1+kk / 1+1** — too small, auto-pass.
6. **4+kk / 4+1** — too large and expensive for buyer needs.

**Budget target:** Total cost under **10M CZK including parking**. Listings above 10M need exceptional justification (premium district + quality + parking included). A 3+kk at 11M with parking included can still score well; a 3+kk at 12M without parking is a stretch.

**Preferred districts (north & central Brno):**
Sadová, Žabovřesky, Královo Pole, Ponava, Černá Pole, Veveří, Staré Brno, Lesná. These deserve a location boost.

**Price/m² guidance — do NOT auto-penalize high CZK/m²:**
- Up to ~130,000 CZK/m² — baseline for the city.
- 130,000–170,000 CZK/m² — acceptable for quality reconstruction or premium location.
- 170,000–200,000 CZK/m² — acceptable only if the apartment is genuinely premium (new build, top-tier reconstruction, French windows, balcony, parking, prime address). Do not red-flag this range purely on price.
- Above 200,000 CZK/m² — needs strong justification.

Reference winning examples (these are the kind of apartments that should score high):
- `https://www.sreality.cz/detail/prodej/byt/2+kk/brno-zabovresky-sochorova/2731450444`
- `https://www.sreality.cz/detail/prodej/byt/2+kk/brno-cerna-pole-trida-generala-piky/578446156`
- `https://www.sreality.cz/detail/prodej/byt/2+kk/brno-veveri-slovakova/2436235852`
- `https://www.sreality.cz/detail/prodej/byt/2+1/brno-stare-brno-uvoz/3284971596`
- `https://www.sreality.cz/detail/prodej/byt/2+kk/brno-sadova-ondreje-sekory/2583011404`
- `https://www.sreality.cz/detail/prodej/byt/3+kk/brno-sadova-karla-kryla/3041304652`
- `https://www.sreality.cz/detail/prodej/byt/3+kk/brno-ponava-sumavska/2253754444`
- `https://www.sreality.cz/detail/prodej/byt/3+kk/brno-veveri-mezirka/1507893324`

Common traits across winners: 2+kk or 3+kk, modern/reconstructed, large windows, balcony or terrace, central or north Brno, often new-development (Sadová) or fully renovated period building (Veveří, Staré Brno). The sweet spot is 6–10M CZK with parking.

Score higher when a listing matches multiple preferences. A listing hitting all five wish-list items in a preferred district deserves a 750+ score. Conversely, a dark unrenovated flat with no outdoor space and paid parking should score lower even if the price/m² looks fair.

## Description Analysis

Read the **full** `description` text from the prepared payload before scoring, not only the title, structured details, or candidate table. Czech listing descriptions often contain important facts that are absent from `detail_items`.

Extract structured facts from the description when the text supports them. Use explicit evidence; do not guess. These extracted facts are informational only. **Do not modify the score based on parking cost** or treat extracted fields as a separate scoring rubric. The scoring rubric in **Ideal Apartment Profile** still governs.

### Parking extraction rules

Populate `parking_price` and `parking_included` from description text when parking is mentioned:

| Description pattern | Review fields |
|---|---|
| `parkovací stání v ceně`, `parking v ceně`, `garáž v ceně` | `parking_included: true`, `parking_price: 0` or `null` |
| `parkovací stání: 295.000 Kč`, `garážové stání za příplatek 350 000 Kč` | `parking_price: 295000`, `parking_included: false` |
| Monthly parking fee, e.g. `parkování 2 000 Kč/měsíc` | `hidden_costs: {"parking_monthly": 2000}` |
| No parking mentioned at all | `parking_included: null`, `parking_price: null` |

Parking purchase prices are integer CZK. Monthly parking fees belong in `hidden_costs.parking_monthly` as integer CZK. These values explain the listing; they do **not** mechanically raise or lower the rating.

### Optional extracted facts

Put additional description-derived facts inside `raw_review.extracted` as a free-form object:

- `renovation_year` (int): last reconstruction year from text like `po rekonstrukci 2023`, `rekonstrukce 2021`.
- `building_type` (string): `panel`, `brick`, `new`, or `mixed` from `panelový dům`, `cihlový dům`, `novostavba`, or mixed evidence.
- `monthly_fees_czk` (int): HOA/SVJ/service fees from `poplatky 5000 Kč`, `SVJ`, or `fond oprav`.
- `cellar_included` (bool): true from `sklep`; false only when explicitly absent.
- `elevator` (bool): true from `výtah`; false only when explicitly no elevator or the description clearly says the building lacks one.
- `available_from` (string): ISO date if clear, otherwise natural text from `volné od 1.7.2026`, `ihned`.
- `condition` (string): `new`, `renovated`, `good`, or `needs_work` from the description tone and explicit condition claims.
- `orientation` (string): cardinal directions from text like `okna na jih`, `JZ`.

### Common Czech description patterns

- `po rekonstrukci` / `kompletní rekonstrukce` → renovated
- `novostavba` → new build
- `cihla` / `cihlový` → brick (preferred over panel)
- `panel` / `panelák` → panel building
- `sklep` → cellar
- `výtah` → elevator
- `lodžie` / `balkon` / `terasa` → outdoor space
- `parkovací stání` / `garáž` / `parking` → parking
- `poplatky` / `SVJ` / `fond oprav` → HOA fees
- `volné od` → available from
- `francouzská okna` → French windows ⭐
- `vlastní zahrada` → private garden

## Workflow

Run from the Python project directory (`sussed/` inside the repo/worktree).

### 0. Freshness pre-flight (do this every session)

Before reviewing, ensure the catalog has been freshly hunted **today** with your config. If not, scrape and re-score now so brand-new listings (last 7 days) are scored and picked up by review candidates:

```bash
# Check the freshest first_seen_at across active listings — if it's not today, run hunt.
uv run sussed review status

# Refresh: scrape new listings + score with hunt config (idempotent; safe to re-run).
uv run sussed hunt -c ../search_config.yaml --scrape
```

If a hunt config doesn't exist yet at `../search_config.yaml` (repo root), generate one with `uv run sussed hunt --generate-config` and tune it before running.

### 1. Check review queue status

```bash
uv run sussed review status
```

### 2. List candidates — prefer newly added listings (last 7 days)

Recently added listings are usually more interesting than month-old ones (fresh inventory, not yet picked over). Always pass `--max-age-days 7` first; only widen if the queue is empty.

```bash
uv run sussed review candidates --limit 10 --max-age-days 7
```

For **rescoring the top hunt picks**, filter by quick score and sort by recency:

```bash
# Show latest 100 listings where hunt quick score >= 450
uv run sussed review candidates --limit 100 --min-quick-score 450 --recent
```

> ℹ️ **Hunt and AI review share `ai_analysis`** but are now safe to interleave: once `ai_reviewed_at` is set, `sussed hunt` no longer overwrites the rich AI analysis — it only refreshes `vibe_check` and appends `_hunt_score` / `_hunt_scored_at` keys into the existing dict.

3. Prepare one selected listing. Use the **first 8 hex characters** of the UUID as the prefix (no dashes). Photos must already be cached by `sussed enrich`; this command reads from `.sussed/image-cache/<listing-id>/` and never downloads:

   ```bash
   uv run sussed review prepare abcdef12 --output .sussed/image-cache/abcdef12-prepared.json
   ```

   > **Prefix format:** The CLI validates that the listing ID prefix contains only hex characters (`[0-9a-fA-F]`), 4–36 chars long. Full UUIDs with dashes are rejected. Use the first 8 characters of the UUID, e.g. `5627933d` not `5627933d-ef8c-4068-a91f-9f4724038726`.

4. Read the prepared JSON. Inspect `description`, `detail_items`, `features`, `price_history`, `image_urls`, `image_paths`, and `input_hash`.

5. If `image_paths` contains local paths, inspect **every** local image path before scoring. Do not infer photo quality from `image_count`, filenames, or URLs alone.

6. If no local image paths are available, continue text-only and add a `yellow_flags` entry such as `"Photo inspection unavailable: no local image paths in prepared payload."`

7. Write a review result JSON matching the schema below. Copy `input_hash` exactly from the prepared payload.

8. Save only through the CLI:

   ```bash
   uv run sussed review save abcdef12 --input .sussed/image-cache/abcdef12-review.json
   ```

## Review JSON Schema

Use valid JSON. Unknown facts must be `null`, not guesses.

```json
{
  "score": 700,
  "vibe": "valid",
  "confidence": 0.8,
  "recommendation": "CONSIDER",
  "score_reason": "Clear reason for the score.",
  "summary": "One-sentence summary.",
  "red_flags": [],
  "yellow_flags": [],
  "highlights": [],
  "hidden_costs": {"parking": null, "parking_monthly": null},
  "parking_price": null,
  "parking_included": null,
  "usable_area_m2": null,
  "photo_observations": [],
  "raw_review": {
    "extracted": {
      "renovation_year": null,
      "building_type": null,
      "monthly_fees_czk": null,
      "cellar_included": null,
      "elevator": null,
      "available_from": null,
      "condition": null,
      "orientation": null
    }
  },
  "reviewer_name": "sussed-ai-review",
  "reviewer_model": "copilot-cli",
  "reviewer_session": "current-session-or-null",
  "input_hash": "copy-from-prepared-payload"
}
```

Required fields: `score`, `vibe`, `confidence`, `recommendation`, `score_reason`, `summary`, `reviewer_name`, and `input_hash`. Optional unknown values should still be included as `null` when relevant.

> **`hidden_costs` values must be `int | null`**, not strings. Example: `{"parking": 2000, "broker_commission": 295000}` or `{"parking": null}`. The Pydantic model rejects string values like `"295000 CZK (5%)"`.

> **`--image-limit` defaults to 5.** The `prepare` command caches at most 5 images by default. If a listing has 20+ photos, only the first 5 are available for inspection. Use `--image-limit 20` to cache more.

## Scoring

- `9999`: unicorn / absolute gem.
- `800-1000`: peak.
- `600-799`: valid.
- `400-599`: mid.
- `200-399`: meh.
- `1-199`: bad.
- `-1`: sus, scam, or avoid.

`vibe` must be one of `peak`, `valid`, `mid`, or `sus`; map the numeric score to the closest honest vibe.

## Review Criteria

- Compare price and price/m² against area, district, condition, and visible quality.
- Extract parking price and whether parking is included only when the prepared evidence states it.
- For `usable_area_m2`, count living space only; exclude cellar, balcony, loggia, terrace, garden, garage, and parking.
- Check whether photos support or contradict the description. **Be skeptical of listing descriptions** — they often oversell. "Designer reconstruction" can mean garish colors and questionable taste. Let the photos speak; if the kitchen is ugly, the radiator is painted purple, or the concrete ceiling looks unfinished, say so regardless of how the description frames it.
- Note visible condition, light, layout, finishes, floor plans, damp/damage, staging, or AI-generated-looking artifacts.
- Flag renovation needs, ground/basement drawbacks, noisy or weak location, vague marketing, auctions, foreclosure, cooperative ownership, cash-only terms, hidden costs, and mismatched claims.
- Reward transparent costs, useful floor plans, good condition, natural light, storage, balcony/loggia, parking, elevator, and strong location.

## Rules

- Always run `sussed review prepare` before scoring; table/listing output alone is insufficient.
- Inspect every local image path in `image_paths` when present.
- If no image paths are available, continue text-only and add a yellow flag.
- Use `null` for unknown facts; do not invent parking, usable area, hidden costs, condition, or photo quality.
- Save only through `sussed review save`; never edit PostgreSQL directly, patch Python code to persist, or treat `ai_analysis` manual writes as acceptable.
- Copy `input_hash` from the prepared payload exactly; do not create a new hash.
- If saving fails validation, fix the JSON and rerun `sussed review save` rather than bypassing the CLI.

## Viewing Results

After reviews are saved, view scored picks without re-downloading or re-scoring:

```bash
# Show top AI-reviewed picks
uv run sussed review picks

# Include unreviewed listings too
uv run sussed review picks --all

# Filter by district
uv run sussed review picks -d "Královo Pole"

# Only high scorers
uv run sussed review picks --min-score 700

# JSON output
uv run sussed review picks -f json
```

## Batch Review Workflow

For reviewing many listings at once:

1. **Batch prepare** (focus on fresh listings): `uv run sussed review prepare-batch -n 50 --max-age-days 7` (prepares top 50 candidates first seen in the last week)
   - Or prepare one by ID: `uv run sussed review prepare <prefix> -o .sussed/image-cache/<prefix>-prepared.json`
2. Review each prepared JSON (read text, inspect images, write review JSON).
3. Save each: `uv run sussed review save <prefix> --input .sussed/image-cache/<prefix>-review.json`
4. View results: `uv run sussed review picks`

The `prepare-batch` command accepts `--count/-n`, `--city`, `--image-limit`, `--max-age-days`, `--min-quick-score`, `--recent`, and `--stale-after-days` options. Always prefer `--max-age-days 7` for fresh inventory; drop the filter only if the fresh queue runs dry. For top-hunt rescoring: `prepare-batch -n 100 --min-quick-score 450 --recent`.

When using a coding agent for batch reviews, dispatch parallel sub-agents (each handling **5–7 listings**) to maximize throughput. Avoid launching more than 3–4 parallel agents to prevent rate-limit failures. Agents with 10+ listings tend to stall — keep batches small.

**Efficiency tip — metadata-only listings:** Many listings come back with empty descriptions (length ≤ 3), no images, and null features. These are metadata-only and can be scored quickly from title/price/area/district/layout with yellow flags. Don't waste agent time waiting for photos that don't exist. A single agent can handle 20+ metadata-only listings rapidly by generating review JSONs in bulk via a Python script.

**Efficiency tip — direct Python scripting:** For large batches, generate all review JSONs with a single Python script (reading `input_hash` from each `*-prepared.json`), then save them in a shell loop. This is faster than dispatching sub-agents when most listings are metadata-only.

**Hunt config location:** The config is at `search_config.yaml` inside the `sussed/` project dir (not `../search_config.yaml`).

## Common Mistakes

| Mistake | Correct behavior |
|---|---|
| Reviewing only `listings` or candidate table output | Run `sussed review prepare` and review the prepared payload. |
| Skipping photos under time pressure | Inspect every `image_paths` file before scoring. |
| Inferring photo quality from `image_count` | Use photo observations only after inspecting local images; otherwise yellow-flag unavailable photos. |
| Prepared payload has no image paths | Run `sussed enrich` first to populate `.sussed/image-cache/<listing-id>/`, then re-run `sussed review prepare`. |
| Guessing parking, usable area, or hidden costs for completeness | Use `null` and explain uncertainty in `yellow_flags`. |
| Saving via SQL, code edits, or manual JSONB updates | Save only with `uv run sussed review save ... --input ...`. |
| Forgetting or inventing `input_hash` | Copy `input_hash` exactly from the prepared JSON. |
| Using full UUID with dashes as prefix | Use first 8 hex chars only, e.g. `5627933d` not `5627933d-ef8c-...`. |
| Putting strings in `hidden_costs` | Values must be `int \| null`. Use `{"parking": 2000}` not `{"parking": "2000 CZK"}`. |
| Launching 10+ parallel review agents | Keep to 3–4 parallel agents with 5–7 listings each to avoid stalling and rate-limit failures. |
| Trusting listing descriptions at face value | Always verify claims against photos. "Designer" can mean garish. Score what you see, not what they say. |
| Spending agent time on metadata-only listings | Listings with empty descriptions and no photos can be batch-scored via Python script — no need for full agent review. |
| Using `../search_config.yaml` for hunt config | The config is at `search_config.yaml` inside the `sussed/` project directory. |
