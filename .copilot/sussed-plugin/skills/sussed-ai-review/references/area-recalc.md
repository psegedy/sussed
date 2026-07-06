# Recalculating Usable Area & True Price/m² 📐

Reference for the `sussed-ai-review` skill area-correction step.

Many sellers inflate the advertised total area by lumping in **terrace, balcony, loggia, cellar, or garden**. The portal's `price_per_m2` can therefore be misleadingly low — but don't go full conspiracy board for normal-sized flats.

## When to trigger

Attempt this correction **only when the total advertised area is clearly oversized for the apartment type**:

| Type | Suspicious if total advertised area is |
|---|---|
| `1+kk` | >35 m² |
| `1+1` | >45 m² |
| `2+kk` | >65 m² |
| `2+1` | >65 m² |
| `3+kk` | >90 m² |
| `3+1` | >100 m² |
| `4+kk` / `4+1` | >120 m² |

If the total is within the normal range, **do not** flag area or punish missing room-by-room m² breakdowns. Leave `usable_area_m2` as `null` unless the prepared payload explicitly gives `Užitná plocha` / `Podlahová plocha`.

## Step 1 — Find separate area mentions

When the total exceeds the threshold, look in both `description` and `detail_items` for separate non-living-space areas:

| Czech label | What it is | Exclude from usable? |
|---|---|---|
| `Terasa` | Terrace | ✅ yes |
| `Balkon` | Balcony | ✅ yes |
| `Lodžie` | Loggia | ✅ yes |
| `Sklep` | Cellar | ✅ yes |
| `Plocha zahrady` | Garden | ✅ yes |
| `Garáž` / `Parkovací stání` | Parking | ✅ yes |
| `Užitná plocha` | Usable/living area | ❌ this IS the answer if present |
| `Podlahová plocha` | Floor area (living) | ❌ this IS the answer if present |

`detail_items` entries look like `{"name": "Terasa", "value": "32", "type": "area"}`.

## Step 1b — Use the floor-plan diagram when you have one

If a floor-plan image is available (often the **last** photo in `image_paths`), read the labeled room areas straight off it — this is the most reliable usable-area source, better than a single advertised number:

- **Sum the indoor rooms** (obývací pokoj / kuchyně, ložnice, pokoj, předsíň, koupelna, WC, komora, technická místnost) → that sum is `usable_area_m2`.
- **Exclude** lodžie / balkon / terasa / sklep / garáž / parkovací stání.
- If the summed indoor total differs from the advertised `Podlahová plocha` / `Užitná plocha` by more than ~3 m², note the discrepancy in `yellow_flags` and trust the diagram sum.

Worked (Sadová 2+kk, 9,490,000 Kč, advertised 70 m²): předsíň 10 + WC 2.5 + koupelna 5.1 + obývací 33.9 + ložnice 13.9 = **65.4 m² usable** (lodžie 14.4 excluded). True price/m² = 9,490,000 / 65.4 = **145,100 Kč** vs advertised 135,600 Kč — a ~7% correction: a yellow flag, not a red one, and easily offset when parking is included in the price.

## Step 2 — Compute `usable_area_m2`

Priority order:

1. **Only act when total area exceeds the threshold** for the apartment type.
2. **Prefer an explicit `Užitná plocha` / `Podlahová plocha`** value from `detail_items` — that's the seller's own usable-area number.
3. **Otherwise subtract** balcony + terrace + loggia + cellar + garden + parking from the advertised total area when those separate m² values are present.
4. **If you can't tell** (no separate areas mentioned and no explicit usable value), leave `usable_area_m2` as `null` — don't guess.

## Step 3 — Call out the correction in your review

When the corrected area changes the price/m² meaningfully (>5%), make it visible:

- Add to `yellow_flags`: `"Advertised 97 m² includes 32 m² terrace; real usable area ~65 m²"`
- Add to `score_reason`: `"True price/m² ≈ 132,800 Kč (advertised 85,360 Kč). Inflated area hides ~55% premium per usable m²."`
- If `parking_price` is known, factor it in too: `true_price_per_m2 = (price_czk + parking_price) / usable_area_m2`

## Step 4 — Worked example

Listing: `8,280,000 Kč`, advertised `97 m²`, `Terasa 32 m²` in `detail_items`, parking `350,000 Kč` extra.

- `usable_area_m2` = 97 − 32 = **65**
- Advertised price/m² = 8,280,000 / 97 = 85,360 Kč
- True price/m² = (8,280,000 + 350,000) / 65 = **132,769 Kč**
- That's a **+55%** correction — add a `yellow_flags` entry, explain the true math, and score based on the corrected economics. It is not automatically a `red_flags` entry.
