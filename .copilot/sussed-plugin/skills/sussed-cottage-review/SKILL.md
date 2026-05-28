---
name: sussed-cottage-review
description: Use when reviewing, scoring, re-scoring, vibe-checking, or analyzing cottage/chata/chalupa listings in sussed; do not use for apartments or non-cottage property reviews
---

# Sussed Cottage Listing Review

## Overview

Review saved `sussed` cottage listings with the coding agent as the reasoning and vision reviewer, and the `sussed` CLI as the only read/prepare/validate/save path. `sussed enrich` pre-warms the per-listing photo cache; `sussed review prepare` only reads from that cache and never downloads anything itself.

**Core principle:** reason from prepared evidence only. Do not invent missing utilities, access, plot ownership, photo quality, or legal status. Save reviews only through `sussed review save`.

## When to Use

Use when the user asks to:
- Review, score, re-score, vibe-check, or analyze saved cottage/chata/chalupa listings.
- Compare cottage text, structured details, photos, hidden costs, plot/utility/access signals, or ownership weirdness.
- Save an AI review result for a cottage back to the `sussed` database.

Do not use for apartments, ordinary flats, general code review, scraper debugging, README edits, or implementing new features. If the listing is an apartment with a garden, use the apartment review skill instead; don't cosplay it as a cottage.

## Detailed references

Heavy reference content lives in sibling files. Load them on demand:

- **What the buyer wants + scoring scale** → [references/ideal-profile.md](references/ideal-profile.md)
- **Czech cottage keywords + extraction rules** → [references/description-patterns.md](references/description-patterns.md)
- **Review JSON schema + cottage field rules** → [references/review-schema.md](references/review-schema.md)
- **Reusable helpers** → [../sussed-ai-review/scripts/README.md](../sussed-ai-review/scripts/README.md) (`summarize_prepared.py`, `make_review.py`, `batch_save.sh` work for any property type)

## Workflow

Run from the Python project directory (`sussed/` inside the repo/worktree).

1. **Refresh + score with hunt (always start here).** `sussed hunt -c search_config.yaml --scrape` scrapes fresh listings, applies the user's search config, and writes a `quick_score` into `ai_analysis` for every match.
   - After editing scoring weights, add `--rescore` to re-score the existing catalog under the new rules.

2. **Check the queue.** `uv run sussed review status`

3. **List candidates** ranked by recency and quick score:
   ```bash
   uv run sussed review candidates --limit 20 --max-age-days 30 --min-quick-score 450 --recent
   ```
   Cottage inventory moves slower than apartments, so `--max-age-days 30` is reasonable; tighten it for hot searches.

4. **Prepare** a selected listing — use the **first 8 hex chars** of the UUID as prefix (no dashes):
   ```bash
   uv run sussed review prepare abcdef12 --output .sussed/image-cache/abcdef12-prepared.json
   ```
   For batches: `uv run sussed review prepare-batch -n 50 --max-age-days 30 --min-quick-score 450 --recent`.

5. **Inspect the prepared JSON.** Read `description`, `detail_items`, `features`, `price_history`, `image_urls`, `image_paths`, `input_hash`, and the top-level price-drop signals: `initial_price`, `original_price`, `price_dropped_to_poa`. If `price_dropped_to_poa` is true, flag the POA switch in `yellow_flags`/`red_flags` and `score_reason`.

6. **Inspect every cached image** in `image_paths`. Do not infer condition from `image_count`, filenames, or URLs alone. If no image paths are available, add a `yellow_flags` entry such as `"Photo inspection unavailable: no local image paths in prepared payload."`

7. **Inspect plot/utility signals.** Extract land size, land ownership, electricity, water source, sewage, road access, heating, legal status/kolaudace, and obvious condition issues. Cottages report plot size and indoor m² separately; do **not** run apartment area-recalc logic. Set `usable_area_m2` only to indoor living/usable area when explicit.

8. **Write the review JSON** matching [references/review-schema.md](references/review-schema.md). Copy `input_hash` exactly. `score_reason` MUST end with the listing URL in square brackets.

9. **Validate** before saving. From this skill directory use `../sussed-ai-review/scripts/make_review.py`; from the Python project directory run:
   ```bash
   python3 ../.copilot/sussed-plugin/skills/sussed-ai-review/scripts/make_review.py validate <review-path>
   ```

10. **Save through the CLI:**
    ```bash
    uv run sussed review save abcdef12 --input .sussed/image-cache/abcdef12-review.json
    ```

## Rules

- Always run `sussed review prepare` before scoring; table/listing output alone is insufficient.
- Inspect every local image path in `image_paths` when present.
- If no image paths are available, continue text-only and add a yellow flag.
- Use `null` for unknown facts; do not invent utilities, legal status, road access, plot size, usable area, hidden costs, or photo quality.
- The listing URL is **mandatory** in `score_reason` (square brackets, at the end) for every review, including avoid/sus ones.
- Save only through `sussed review save`; never edit PostgreSQL directly, patch Python code to persist, or treat `ai_analysis` manual writes as acceptable.
- Copy `input_hash` from the prepared payload exactly; do not create a new hash.
- If saving fails validation, fix the JSON and rerun validation/save rather than bypassing the CLI.
- Set `parking_price` and `parking_included` to `null` unless paid parking is explicitly mentioned; normal cottage parking on the plot is not a parking surcharge.
- Leased land, missing winter access, no electricity, no water, mold, damp, asbestos, or legal uncertainty are not “rustic charm.” Call that shit out.

## Batch Reviews

- Use `prepare-batch` to fetch many candidates in one call, then review in parallel.
- Dispatch sub-agents in **batches of 5–7 listings**; keep to **3–4 parallel agents** to avoid stalling and rate-limit failures.
- For metadata-only listings, score from title/price/area/location with explicit uncertainty flags. Use `../sussed-ai-review/scripts/make_review.py` and `../sussed-ai-review/scripts/batch_save.sh` to avoid hand-writing broken JSON.

## Viewing results

```bash
uv run sussed review picks                       # top reviewed picks
uv run sussed review picks --all                 # include unreviewed
uv run sussed review picks --min-score 700       # only high scorers
uv run sussed review picks -f json               # JSON output
```

## Common Mistakes

| Mistake | Correct behavior |
|---|---|
| Using this for apartments | Use `sussed-ai-review`; cottage rules are different. |
| Reviewing only candidate table output | Run `sussed review prepare` and review the prepared payload. |
| Skipping photos under time pressure | Inspect every `image_paths` file before scoring. |
| Guessing electricity, water, sewage, access, or legal status | Use `null` and explain uncertainty in `yellow_flags`. |
| Treating free outdoor parking as paid parking | Leave `parking_price`/`parking_included` null unless paid parking is mentioned. |
| Applying apartment area inflation math | For cottages, track indoor `usable_area_m2` and plot size separately. |
| Missing `pronájem pozemku` | Leased land is a major ownership risk; flag it. |
| Ignoring winter access | Seasonal access can tank year-round usability. |
| Forgetting the URL in `score_reason` | Every review must end `score_reason` with `[https://...]`. |
| Saving via SQL, code edits, or manual JSONB updates | Save only with `uv run sussed review save ... --input ...`. |
| Forgetting or inventing `input_hash` | Copy `input_hash` exactly from the prepared JSON. |
| Using full UUID with dashes as prefix | Use first 8 hex chars only, e.g. `5627933d`. |
| Putting strings in `hidden_costs` | Values must be `int | null`, never `"5000 CZK"`. |
