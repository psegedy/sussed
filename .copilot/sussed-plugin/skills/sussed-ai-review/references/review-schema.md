# Review JSON Schema

Reference for the JSON payload accepted by `sussed review save`. Used by the `sussed-ai-review` skill.

Use valid JSON. Unknown facts must be `null`, not guesses.

```json
{
  "score": 700,
  "vibe": "valid",
  "confidence": 0.8,
  "recommendation": "CONSIDER",
  "score_reason": "Clear reason for the score. [https://www.sreality.cz/detail/...]",
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

## Required fields

`score`, `vibe`, `confidence`, `recommendation`, `score_reason`, `summary`, `reviewer_name`, and `input_hash`. Optional unknown values should still be included as `null` when relevant.

## Mandatory URL in score_reason

`score_reason` MUST end with the listing URL in square brackets so every saved review is directly verifiable without a separate DB lookup. This applies to all reviews: top picks, mid-tier, sus, boring-but-valid, the whole damn lineup.

Example: `"score_reason": "Pisárky novostavba with Špilberk view, parking +400k extra. [https://www.sreality.cz/detail/prodej/byt/2+kk/brno-pisarky-porici/4118380620]"`

## Field-level notes

- `score` — `-1`, `0-1000`, or `9999` (unicorn). Map to `vibe` honestly: see [ideal-profile.md](./ideal-profile.md).
- `vibe` — one of `peak`, `valid`, `mid`, `sus`.
- `confidence` — float in `[0, 1]`. Lower when you couldn't inspect photos or description is missing.
- `recommendation` — short verb-y phrase: `BUY`, `CONSIDER`, `INVESTIGATE`, `AVOID`, etc. (max 40 chars).
- `hidden_costs` — **values must be `int | null`**, never strings. Example: `{"parking": 2000, "broker_commission": 295000}` or `{"parking": null}`. The Pydantic model rejects `"295000 CZK (5%)"`.
- `parking_price` / `parking_included` — see [description-patterns.md](./description-patterns.md) for extraction rules.
- `usable_area_m2` — only set when the corrected area calculation actually applies; see [area-recalc.md](./area-recalc.md). Leave `null` for normal-sized flats.
- `input_hash` — copy character-for-character from the prepared payload. Don't generate.

## Validation

Use `scripts/make_review.py validate <path>` before saving to catch missing required fields, URL absence in `score_reason`, wrong score range, or string values in `hidden_costs`.

## Operational notes

- **`--image-limit` defaults to 5.** `sussed review prepare` caches at most 5 images by default. If a listing has 20+ photos, only the first 5 are available for inspection. Run `sussed enrich --image-limit 20` to cache more before preparing.
