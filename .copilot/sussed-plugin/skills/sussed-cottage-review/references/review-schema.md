# Cottage Review JSON Schema

Reference for the JSON payload accepted by `sussed review save`. Cottage reviews use the same Pydantic source of truth as apartments: `ReviewResultInput`. Use valid JSON. Unknown facts must be `null`, not guesses.

```json
{
  "score": 700,
  "vibe": "valid",
  "confidence": 0.8,
  "recommendation": "CONSIDER",
  "score_reason": "Solid legal cottage with own plot, connected electricity, well, septic, and year-round access. [https://www.sreality.cz/detail/prodej/chata/example/1234567890]",
  "summary": "One-sentence cottage review summary.",
  "red_flags": [],
  "yellow_flags": [],
  "highlights": [],
  "hidden_costs": {
    "electricity_connection": null,
    "septic_emptying": null,
    "well_maintenance": null,
    "winter_road_clearing": null
  },
  "parking_price": null,
  "parking_included": null,
  "usable_area_m2": null,
  "photo_observations": [],
  "raw_review": {
    "extracted": {
      "plot_size_m2": null,
      "electricity_connected": null,
      "water_source": null,
      "sewage": null,
      "road_access": null,
      "legalized": null,
      "building_type": null,
      "heating": null,
      "land_ownership": null
    }
  },
  "reviewer_name": "sussed-cottage-review",
  "reviewer_model": "copilot-cli",
  "reviewer_session": "current-session-or-null",
  "input_hash": "copy-from-prepared-payload"
}
```

## Required fields

`score`, `vibe`, `confidence`, `recommendation`, `score_reason`, `summary`, `reviewer_name`, and `input_hash`. Optional unknown values should still be included as `null` when relevant.

## Mandatory URL in score_reason

`score_reason` MUST end with the listing URL in square brackets so every saved review is directly verifiable without a separate DB lookup. This applies to every cottage review: peak forest gems, mid weekend cabins, sus legal disasters, the whole damn lineup.

Example: `"score_reason": "Own fenced 920 m² plot, electricity connected, well and septic present, but winter access is unclear. [https://www.sreality.cz/detail/prodej/chata/.../1234567890]"`

## Field-level notes

- `score` — `-1`, `0-1000`, or `9999` (unicorn). Map to `vibe` honestly: see [ideal-profile.md](./ideal-profile.md).
- `vibe` — one of `peak`, `valid`, `mid`, `sus`.
- `confidence` — float in `[0, 1]`. Lower when photos, description, legal status, access, or utilities are unclear.
- `recommendation` — short verb-y phrase: `BUY`, `CONSIDER`, `INVESTIGATE`, `AVOID`, etc. (max 40 chars).
- `hidden_costs` — values must be `int | null`, never strings. Use `{"septic_emptying": 4000}` not `{"septic_emptying": "4000 CZK/year"}`. Cottage common costs include `electricity_connection`, `septic_emptying`, `well_maintenance`, and `winter_road_clearing`.
- `parking_price` / `parking_included` — usually not relevant for cottages. Set both to `null` unless paid parking is explicitly mentioned. Outdoor parking on the plot is a highlight, not a paid parking field.
- `usable_area_m2` — indoor living/usable area only. Use explicit `obytná plocha`, `užitná plocha`, or clearly stated indoor m². Exclude veranda, terrace, shed, woodshed, cellar, garage, and plot/garden.
- `raw_review.extracted.plot_size_m2` — land plot size, separate from `usable_area_m2`.
- `raw_review.extracted.electricity_connected` — boolean only when explicit.
- `raw_review.extracted.water_source` — `"studna"`, `"vodovod"`, `"none"`, or `null`.
- `raw_review.extracted.sewage` — `"kanalizace"`, `"septik"`, `"jímka"`, `"čistírna odpadních vod"`, `"none"`, or `null`.
- `raw_review.extracted.road_access` — `"year-round"`, `"seasonal"`, or `null`.
- `raw_review.extracted.legalized` — boolean only when legal status/kolaudace/cadastre evidence is explicit.
- `input_hash` — copy character-for-character from the prepared payload. Don't generate.

## Validation

Use the existing helper before saving:

```bash
python3 ../.copilot/sussed-plugin/skills/sussed-ai-review/scripts/make_review.py validate <review-path>
```

The validator checks required fields, score range, confidence range, valid vibe, integer/null `hidden_costs`, and the URL rule for `score_reason`.
