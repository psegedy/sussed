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

## Detailed references

Heavy reference content lives in sibling files. Load them on demand:

- **What the buyer wants + scoring scale** → [references/ideal-profile.md](references/ideal-profile.md)
- **Czech keywords + parking extraction** → [references/description-patterns.md](references/description-patterns.md)
- **Usable-area recalc rules + worked example** → [references/area-recalc.md](references/area-recalc.md)
- **Review JSON schema + field rules** → [references/review-schema.md](references/review-schema.md)
- **Reusable Python helpers** → [scripts/README.md](scripts/README.md) (`summarize_prepared.py`, `make_review.py`, `batch_save.sh`)

## Workflow

Run from the Python project directory (`sussed/` inside the repo/worktree).

1. **Refresh + score with hunt (always start here).** `sussed hunt -c search_config.yaml --scrape` scrapes fresh listings, applies the user's search config, and writes a `quick_score` into `ai_analysis` for every match. Without this, `review candidates` has nothing fresh to surface.
   - After editing scoring weights, add `--rescore` to re-score the existing catalog under the new rules.

2. **Check the queue.** `uv run sussed review status`

3. **List candidates** ranked by recency and quick score (already filtered by your hunt config):
   ```bash
   uv run sussed review candidates --limit 20 --max-age-days 7 --min-quick-score 450 --recent
   ```
   Always prefer `--max-age-days 7` for fresh inventory; drop the filter only if the fresh queue runs dry.

4. **Prepare** a selected listing — use the **first 8 hex chars** of the UUID as prefix (no dashes):
   ```bash
   uv run sussed review prepare abcdef12 --output .sussed/image-cache/abcdef12-prepared.json
   ```
   For batches: `uv run sussed review prepare-batch -n 50 --max-age-days 7 --min-quick-score 450 --recent`.

5. **Inspect the prepared JSON.** Read `description`, `detail_items`, `features`, `price_history`, `image_urls`, `image_paths`, `input_hash`, and the top-level price-drop signals: `initial_price`, `original_price`, `price_dropped_to_poa`. When `price_dropped_to_poa` is `true`, the seller switched from a real price to POA — flag in `yellow_flags`/`red_flags` and call it out in `score_reason`.

6. **Inspect every cached image** in `image_paths`. Do not infer photo quality from `image_count`, filenames, or URLs alone. If no image paths are available, add a `yellow_flags` entry such as `"Photo inspection unavailable: no local image paths in prepared payload."`

7. **Recalculate usable area & true price/m²** only when the total advertised area exceeds the apartment-type threshold (see [references/area-recalc.md](references/area-recalc.md)). Missing per-room m² breakdowns are normal and are NOT a flag.

8. **Write the review JSON** matching [references/review-schema.md](references/review-schema.md). Copy `input_hash` exactly. `score_reason` MUST end with the listing URL in square brackets.

9. **Validate** before saving: `python3 scripts/make_review.py validate <review-path>`

10. **Save through the CLI:**
    ```bash
    uv run sussed review save abcdef12 --input .sussed/image-cache/abcdef12-review.json
    ```

## Rules

- Always run `sussed review prepare` before scoring; table/listing output alone is insufficient.
- Inspect every local image path in `image_paths` when present.
- If no image paths are available, continue text-only and add a yellow flag.
- Use `null` for unknown facts; do not invent parking, usable area, hidden costs, condition, or photo quality.
- The listing URL is **mandatory** in `score_reason` (square brackets, at the end) — applies to every review including avoid/sus ones.
- Save only through `sussed review save`; never edit PostgreSQL directly, patch Python code to persist, or treat `ai_analysis` manual writes as acceptable.
- Copy `input_hash` from the prepared payload exactly; do not create a new hash.
- If saving fails validation, fix the JSON and rerun `sussed review save` rather than bypassing the CLI.

## Batch Reviews

- Use `prepare-batch` to fetch many candidates in one call, then review them in parallel.
- Dispatch sub-agents in **batches of 5–7 listings**; keep to **3–4 parallel agents** to avoid stalling and rate-limit failures.
- For metadata-only listings (empty descriptions, no images, null features), score quickly from title/price/area/district/layout with yellow flags. A single agent can rip through 20+ via `scripts/make_review.py` + `scripts/batch_save.sh`.

## Viewing results

```bash
uv run sussed review picks                       # top reviewed picks
uv run sussed review picks --all                 # include unreviewed
uv run sussed review picks -d "Královo Pole"     # filter by district
uv run sussed review picks --min-score 700       # only high scorers
uv run sussed review picks -f json               # JSON output
```

## Common Mistakes

| Mistake | Correct behavior |
|---|---|
| Reviewing only `listings` or candidate table output | Run `sussed review prepare` and review the prepared payload. |
| Skipping photos under time pressure | Inspect every `image_paths` file before scoring. |
| Inferring photo quality from `image_count` | Use photo observations only after inspecting local images; otherwise yellow-flag unavailable photos. |
| Prepared payload has no image paths | Run `sussed enrich` first to populate `.sussed/image-cache/<listing-id>/`, then re-run `sussed review prepare`. |
| Guessing parking, usable area, or hidden costs | Use `null` and explain uncertainty in `yellow_flags`. |
| Treating a normal Czech parking surcharge as a red flag | Surcharges are standard — see [references/description-patterns.md](references/description-patterns.md). Yellow flag only if >500,000 CZK. |
| Flagging "area inflation" on a normal-sized flat | Only trigger area recalc when total exceeds the type threshold — see [references/area-recalc.md](references/area-recalc.md). |
| Forgetting the URL in `score_reason` | Every review must end `score_reason` with `[https://...]`. |
| Saving via SQL, code edits, or manual JSONB updates | Save only with `uv run sussed review save ... --input ...`. |
| Forgetting or inventing `input_hash` | Copy `input_hash` exactly from the prepared JSON. |
| Using full UUID with dashes as prefix | Use first 8 hex chars only, e.g. `5627933d` not `5627933d-ef8c-...`. |
| Putting strings in `hidden_costs` | Values must be `int \| null`. Use `{"parking": 2000}` not `{"parking": "2000 CZK"}`. |
| Launching 10+ parallel review agents | Keep to 3–4 parallel agents with 5–7 listings each. |
| Trusting listing descriptions at face value | Always verify claims against photos. "Designer" can mean garish. Score what you see, not what they say. |
| Using `../search_config.yaml` for hunt config | The config is at `search_config.yaml` inside the `sussed/` project directory. |
