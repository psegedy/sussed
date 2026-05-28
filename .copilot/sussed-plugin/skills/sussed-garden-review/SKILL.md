---
name: sussed-garden-review
description: Use when reviewing, scoring, re-scoring, vibe-checking, or analyzing saved sussed garden/zahrada/zahrádka plot listings; not apartments, houses, cottages/chatas, chalupas, or residential buildings.
---

# Sussed Garden Listing Review

## Overview

Review saved `sussed` garden listings with the coding agent as the reasoning and vision reviewer, and the `sussed` CLI as the only read/prepare/validate/save path. `sussed enrich` pre-warms the per-listing photo cache; `sussed review prepare` only reads from that cache and never downloads anything itself.

**Core principle:** gardens are their own beast. A `zahrada`/`zahrádka` is judged as a plot for leisure gardening, storage, utilities, access, and legal sanity — not as an apartment, house, or cottage. Reason from prepared evidence only. Do not invent missing facts, skip available photos, or persist reviews outside `sussed review save`.

## When to Use

Use when the user asks to:

- Review, score, re-score, vibe-check, or analyze saved `sussed` garden / allotment listings.
- Judge Czech `zahrada`, `zahrádka`, `zahrádková osada`, garden plot, allotment colony, or gardening-purpose land listings.
- Compare plot size, utilities, ownership, access, structures, photos, hidden costs, or legal red flags.
- Save a garden AI review result back to the `sussed` database.

Do **not** use for apartments, houses, commercial land, farms, vineyards as businesses, cottages (`chata`), cabins, chalets (`chalupa`), or anything marketed for residential/recreational living. A garden may include a small `chatka` or shed; that does not make it a cottage review. If the listing sells the building as the main thing, use the appropriate non-garden skill instead.

## Detailed references

Heavy reference content lives in sibling files. Load them on demand:

- **What the buyer wants + scoring scale** → [references/ideal-profile.md](references/ideal-profile.md)
- **Czech garden keywords + extracted facts** → [references/description-patterns.md](references/description-patterns.md)
- **Review JSON schema + field rules** → [references/review-schema.md](references/review-schema.md)
- **Reusable helpers from apartment review** → [../sussed-ai-review/scripts/README.md](../sussed-ai-review/scripts/README.md) (`summarize_prepared.py`, `make_review.py`, `batch_save.sh` work for any property type)

## Workflow

**First step in any new shell or sub-agent:** `cd` into the Python project directory (`sussed/` inside the repo or worktree) before running anything below. Every `uv run sussed ...` command below assumes this CWD.

1. **Refresh + score with hunt (always start here).** `sussed hunt -c garden_config.yaml --scrape` scrapes fresh listings, applies the user's search config, and writes a `quick_score` into `ai_analysis` for every match. Without this, `review candidates` has nothing fresh to surface.
   - After editing scoring weights, add `--rescore` to re-score the existing catalog under the new rules.

2. **Check the queue.** `uv run sussed review status`

3. **List candidates** ranked by recency and quick score:
   ```bash
   uv run sussed review candidates --limit 20 --max-age-days 7 --min-quick-score 450 --recent -p garden
   ```
   Prefer `--max-age-days 7` for fresh inventory; drop it only if the fresh queue runs dry. The `-p garden` flag is **required** — without it, you'd review apartments by mistake.

4. **Prepare** a selected listing — use the **first 8 hex chars** of the UUID as prefix (no dashes):
   ```bash
   uv run sussed review prepare abcdef12 --output .sussed/image-cache/abcdef12-prepared.json
   ```
   For batches: `uv run sussed review prepare-batch -n 50 --max-age-days 7 --min-quick-score 450 --recent -p garden`.

5. **Inspect the prepared JSON.** Read `title`, `description`, `detail_items`, `features`, `raw_labels`, `price_history`, `image_urls`, `image_paths`, `input_hash`, and price-drop signals. Gardens do not have meaningful `apartment_type`, `floor`, or `elevator` expectations.

6. **Inspect every cached image** in `image_paths`. Photos are the only way to catch the killers the description hides. For each image, classify it first:
   - **Aerial/cadastral map** — look for motorway/highway (D1, D2), railway tracks and overhead lines, airport runways, high-voltage pylons (VN/VVN), industrial complexes, flood-zone river bends. Within ~200 m of any of these, the plot is impaired.
   - **Real on-site photo** — verify fences, access path, shed/chatka condition, slope, neighbors, overgrowth, mud/water clues. Confirm the listing text matches what you see.
   - **CGI / AI-generated** — render style, impossible lighting, mismatched seasons. Trust nothing from these alone.

   If `image_paths` is empty, add a `yellow_flags` entry: `"Photo inspection unavailable: no local image paths in prepared payload."` If every image is a map or cadastral view with no real plot photo, add: `"Photos are maps/cadastral only — no on-site evidence."`

7. **Inspect plot signals.** Use [references/description-patterns.md](references/description-patterns.md) and [references/ideal-profile.md](references/ideal-profile.md) to assess plot size, water, electricity, fencing, structure, ownership, access, build limits, flood risk, and hidden costs. Set `usable_area_m2` to the plot area when known; do not run apartment-style area inflation math.

8. **Write the review JSON** matching [references/review-schema.md](references/review-schema.md). Copy `input_hash` exactly. `score_reason` MUST end with the listing URL in square brackets.

9. **Validate** before saving: `uv run sussed review validate <review-path>`

10. **Save through the CLI:**
    ```bash
    uv run sussed review save abcdef12 --input .sussed/image-cache/abcdef12-review.json
    ```

## Rules

- Always run `sussed review prepare` before scoring; table/listing output alone is insufficient.
- Inspect every local image path in `image_paths` when present.
- If no image paths are available, continue text-only and add a yellow flag.
- Use `null` for unknown facts; do not invent utilities, ownership, plot area, hidden costs, access, condition, or photo quality.
- The listing URL is **mandatory** in `score_reason` (square brackets, at the end) — applies to every review including avoid/sus ones.
- Save only through `sussed review save`; never edit PostgreSQL directly, patch Python code to persist, or treat `ai_analysis` manual writes as acceptable.
- Copy `input_hash` from the prepared payload exactly; do not create a new hash.
- If saving fails validation, fix the JSON and rerun `sussed review save` rather than bypassing the CLI.
- Keep garden reviews distinct from cottage reviews: a small `chatka`, `bouda`, or `kůlna` is a storage/comfort feature, not proof of residential cottage value.
- `hidden_costs` values must be `int | null`; never store strings like `"5k/year"`.

## Batch Reviews

- Use `prepare-batch` to fetch many candidates in one call, then review them in parallel.
- Dispatch sub-agents in **batches of 5–7 listings**; keep to **3–4 parallel agents** to avoid stalling.
- Sub-agents must include the rubric inline or load it from `references/ideal-profile.md` themselves; they cannot rely on parent context.
- Helper scripts at `../sussed-ai-review/scripts/`:
  - `summarize_prepared.py <prepared.json>` — compact human digest, garden-aware (water/electricity/ownership/fence) when `property_category == "garden"`.
  - `make_review.py skeleton <prepared.json> --out <review.json>` — emit a valid review stub pre-filled with `input_hash`, URL-bearing `score_reason`, `usable_area_m2`, and `reviewer_name`. The reviewer fills in score / vibe / flags / summary.
  - `make_review.py validate <review.json>` — local schema check (same rules as `uv run sussed review validate`).
  - `batch_save.sh <dir>` — `sussed review save` every `*-review.json` in a directory.
- For metadata-only garden listings, score from title, price, plot area, locality, ownership clues, and flags. Keep confidence ≤ 0.5 and explain missing evidence in `score_reason`.

## Viewing results

```bash
uv run sussed review picks -p garden                 # top garden picks only
uv run sussed review picks -p garden --all           # include unreviewed
uv run sussed review picks -p garden --min-score 700 # only high scorers
uv run sussed review picks -p garden -f json         # JSON output
```

## Common Mistakes

| Mistake | Correct behavior |
|---|---|
| Treating a garden with `chatka` as a cottage | Review as garden unless the building is clearly the main sold asset. |
| Applying apartment criteria like floor, elevator, layout, or usable flat area | Ignore them; judge plot size, utilities, ownership, access, and structure. |
| Running apartment area-recalc | Do not. `usable_area_m2` means plot area for gardens. |
| Skipping the aerial/cadastral map check | Always classify each photo (map vs on-site vs CGI). Maps reveal motorway, railway, airport, power-line, and flood adjacency that the description hides. |
| Guessing water or electricity from greenery/photos | Use explicit evidence; otherwise `null` plus a yellow flag. |
| Ignoring ownership because the plot looks cute | `pronájem pozemku` and vague `družstevní` terms can tank the score. |
| Treating shared water as equal to own well | Shared water is a yellow flag, not the same as `vlastní studna`. |
| Forgetting the URL in `score_reason` | Every review must end `score_reason` with `[https://...]`. |
| Putting strings in `hidden_costs` | Values must be `int | null`, e.g. `{"annual_lease": 6000}`. |
| Writing a `recommendation` longer than 40 chars | Use short tags like `CONSIDER`, `AVOID`, `INSPECT IN PERSON`. The CLI will reject longer values. |
| Saving via SQL or manual JSONB updates | Save only with `uv run sussed review save ... --input ...`. |
| Using full UUID with dashes as prefix | Use first 8 hex chars only, e.g. `5627933d`. |
