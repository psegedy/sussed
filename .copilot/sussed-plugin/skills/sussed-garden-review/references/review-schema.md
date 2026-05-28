# Garden Review JSON Schema

Reference for the JSON payload accepted by `sussed review save` when using `sussed-garden-review`.

The source of truth is the same Pydantic model as apartment reviews: `ReviewResultInput`. Use valid JSON. Unknown facts must be `null`, not guesses.

```json
{
  "score": 700,
  "vibe": "valid",
  "confidence": 0.8,
  "recommendation": "CONSIDER",
  "score_reason": "Owned 520 m² fenced garden with well, electricity, and usable shed; shared access needs checking. [https://www.sreality.cz/detail/prodej/pozemek/zahrada/brno/123]",
  "summary": "Solid owned garden with core utilities and one access caveat.",
  "red_flags": [],
  "yellow_flags": [],
  "highlights": [],
  "hidden_costs": {
    "cooperative_membership_fee": null,
    "annual_lease": null,
    "electricity_connection": null,
    "water_connection": null
  },
  "parking_price": null,
  "parking_included": null,
  "usable_area_m2": 520,
  "photo_observations": [],
  "raw_review": {
    "extracted": {
      "plot_size_m2": 520,
      "ownership_type": "osobní",
      "water_source": "studna",
      "electricity_connected": true,
      "fenced": true,
      "has_structure": true,
      "building_allowed": null
    }
  },
  "reviewer_name": "sussed-garden-review",
  "reviewer_model": "copilot-cli",
  "reviewer_session": "current-session-or-null",
  "input_hash": "copy-from-prepared-payload"
}
```

## Required fields

`score`, `vibe`, `confidence`, `recommendation`, `score_reason`, `summary`, `reviewer_name`, and `input_hash`. Optional unknown values should still be included as `null` when relevant.

## Mandatory URL in score_reason

`score_reason` MUST end with the listing URL in square brackets so every saved review is directly verifiable without a separate DB lookup. This applies to all reviews: gems, mids, sus garbage, and boring-but-valid dirt rectangles.

Correct ending:

```json
"score_reason": "Owned fenced garden with well and electricity; cooperative fee still unknown. [https://www.sreality.cz/detail/prodej/pozemek/zahrada/brno/123]"
```

## Field-level notes

- `score` — `-1`, `0-1000`, or `9999` (unicorn). Map to `vibe` honestly; see [ideal-profile.md](./ideal-profile.md).
- `vibe` — one of `peak`, `valid`, `mid`, or `sus`.
- `confidence` — float in `[0, 1]`. Lower it when photos, ownership, utilities, or description are missing.
- `recommendation` — short verb-y phrase: `BUY`, `CONSIDER`, `INVESTIGATE`, `AVOID`, etc. (max 40 chars).
- `hidden_costs` — **values must be `int | null`**, never strings. Common garden keys: `cooperative_membership_fee`, `annual_lease`, `electricity_connection`, `water_connection`, `cleanup_cost`, `legal_fees`.
- `parking_price` / `parking_included` — almost always `null` for gardens. Set them only if a formal paid parking space is explicitly part of the deal.
- `usable_area_m2` — interpret as **plot area in m²**. Use the listing's `area_m2` directly when known. Gardens do not need apartment-style inflated-area correction.
- `photo_observations` — record visible evidence: fence, shed/chatka condition, slope, access road, overgrowth, water/electric clues, neighboring plots.
- `input_hash` — copy character-for-character from the prepared payload. Do not generate.

## Garden-specific raw_review.extracted fields

Put garden facts under `raw_review.extracted` as a free-form object accepted by the Pydantic model:

- `plot_size_m2` (`int | null`) — advertised plot area.
- `ownership_type` (`"osobní" | "družstevní" | "pronájem" | null`) — use explicit wording only.
- `water_source` (`"studna" | "vodovod" | "shared" | "none" | null`) — source from text/detail items.
- `electricity_connected` (`bool | null`) — true only for explicit hookup/connection.
- `fenced` (`bool | null`) — true/false only with text or photo evidence.
- `has_structure` (`bool | null`) — any `chatka`, shed, greenhouse, pergola, or shelter.
- `building_allowed` (`bool | null`) — whether building/placing a shed is explicitly possible.

## Validation

Use the built-in CLI validator before saving:

```bash
uv run sussed review validate <review-path>
```

The helper validates score range, confidence range, valid vibe, integer/null `hidden_costs`, and the mandatory URL rule. It does not know whether your garden facts are true — that part is on you, chief.
