# sussed AI Review Helper Scripts

Reusable helpers for the `sussed-ai-review` skill live here. They use only the
Python standard library or Bash, so future reviewers can run them from any
session without importing the sussed package.

## `summarize_prepared.py`

Prints a compact digest of prepared review payloads. It shows the listing ID
prefix, title, URL, price, price-drop signals, key features, cached photo count,
description preview, and full `input_hash`.

```bash
python3 summarize_prepared.py ../../../../../sussed/.sussed/image-cache/2e6afbfb-prepared.json
python3 summarize_prepared.py --all ../../../../../sussed/.sussed/image-cache
```

Prepared files should stay in `sussed/.sussed/image-cache/`, usually as
`<prefix>-prepared.json`.

## `make_review.py`

Builds and validates schema-compliant review JSON payloads.

Importable helpers:

- `build_review(...)` returns a complete review dict with safe defaults.
- `validate_review(review)` returns a list of validation errors.
- `dump_review(review, output_path)` writes pretty JSON with UTF-8 text.

CLI:

```bash
# Validate a finished review
python3 make_review.py validate reviews/2e6afbfb-review.json

# Emit a schema-valid stub from a prepared payload; fill in score/vibe/flags later
python3 make_review.py skeleton .sussed/image-cache/2e6afbfb-prepared.json
python3 make_review.py skeleton .sussed/image-cache/2e6afbfb-prepared.json \
    --out reviews/2e6afbfb-review.json --reviewer-name sussed-garden-review
```

The validator checks score range, confidence range, valid vibe, integer/null
`hidden_costs`, and the skill rule that `score_reason` includes the listing URL.
The skeleton seeds `input_hash`, URL-bearing `score_reason`, `usable_area_m2`,
and `reviewer_name` so the reviewer only fills in judgement fields.

## `batch_save.sh`

Saves every `*-review.json` file in a directory through the CLI:

```bash
cd /Users/psegedy/git/sussed/sussed
../.copilot/sussed-plugin/skills/sussed-ai-review/scripts/batch_save.sh reviews
```

The script extracts the first eight characters from each filename and runs:

```bash
uv run sussed review save <prefix> --input <file>
```

Review JSON files can live anywhere convenient, such as `sussed/reviews/` or a
session-specific directory. Save only through `sussed review save`; do not patch
PostgreSQL or `ai_analysis` directly.

## Workflow Fit

1. Run `sussed hunt -c search_config.yaml --scrape`.
2. Pull candidates with `sussed review candidates ...`.
3. Prepare payloads with `sussed review prepare-batch ...`.
4. Use `summarize_prepared.py` to scan each prepared JSON.
5. Use `make_review.py` from an ad hoc script or REPL to write reviews.
6. Validate each review with `make_review.py validate`.
7. Run `batch_save.sh` from the `sussed/` Python project directory.
