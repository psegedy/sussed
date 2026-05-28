# Ideal Apartment Profile (buyer's wish list)

This is the scoring rubric reference for the `sussed-ai-review` skill — what the buyer actually wants. Apply these preferences when assigning a score.

## Wish list (the five core wants)

- **Modern / reconstructed** apartment — freshly renovated, new development, or move-in ready.
- **Pretty modern minimalist kitchen** — clean lines, built-in appliances, no dated cabinetry.
- **Big French windows** (floor-to-ceiling) — natural light is a priority.
- **Terrace or balcony** — outdoor space is a strong plus.
- **Parking included in price** — no extra monthly cost for a parking spot.

## Layout preference (ranked best → worst)

1. **2+kk** — preferred: affordable, modern open-plan, fits the budget.
2. **2+1** — good: separate kitchen is fine, often cheaper than 2+kk.
3. **3+kk** — great if the price is right (under ~10M with parking), but often too expensive.
4. **3+1** — acceptable only at a bargain price; usually pushes past budget.
5. **1+kk / 1+1** — too small, auto-pass.
6. **4+kk / 4+1** — too large and expensive for buyer needs.

## Budget target

Total cost under **10M CZK including parking**. Listings above 10M need exceptional justification (premium district + quality + parking included). A 3+kk at 11M with parking included can still score well; a 3+kk at 12M without parking is a stretch.

## Preferred districts (north & central Brno — location boost)

Sadová, Žabovřesky, Královo Pole, Ponava, Černá Pole, Veveří, Staré Brno, Lesná.

## Price/m² guidance — do NOT auto-penalize high CZK/m²

- Up to ~130,000 CZK/m² — baseline for the city.
- 130,000–170,000 CZK/m² — acceptable for quality reconstruction or premium location.
- 170,000–200,000 CZK/m² — acceptable only if the apartment is genuinely premium (new build, top-tier reconstruction, French windows, balcony, parking, prime address). Do not red-flag this range purely on price.
- Above 200,000 CZK/m² — needs strong justification.

## Reference winning examples

These are the kind of apartments that should score high:

- https://www.sreality.cz/detail/prodej/byt/2+kk/brno-zabovresky-sochorova/2731450444
- https://www.sreality.cz/detail/prodej/byt/2+kk/brno-cerna-pole-trida-generala-piky/578446156
- https://www.sreality.cz/detail/prodej/byt/2+kk/brno-veveri-slovakova/2436235852
- https://www.sreality.cz/detail/prodej/byt/2+1/brno-stare-brno-uvoz/3284971596
- https://www.sreality.cz/detail/prodej/byt/2+kk/brno-sadova-ondreje-sekory/2583011404
- https://www.sreality.cz/detail/prodej/byt/3+kk/brno-sadova-karla-kryla/3041304652
- https://www.sreality.cz/detail/prodej/byt/3+kk/brno-ponava-sumavska/2253754444
- https://www.sreality.cz/detail/prodej/byt/3+kk/brno-veveri-mezirka/1507893324

Common traits across winners: 2+kk or 3+kk, modern/reconstructed, large windows, balcony or terrace, central or north Brno, often new-development (Sadová) or fully renovated period building (Veveří, Staré Brno). The sweet spot is 6–10M CZK with parking.

## Scoring posture

Score higher when a listing matches multiple preferences. A listing hitting all five wish-list items in a preferred district deserves a **750+** score. Conversely, a dark unrenovated flat with no outdoor space and paid parking should score lower even if the price/m² looks fair.

## Scoring scale

- `9999` — unicorn / absolute gem.
- `800-1000` — peak.
- `600-799` — valid.
- `400-599` — mid.
- `200-399` — meh.
- `1-199` — bad.
- `-1` — sus, scam, or avoid.

`vibe` must be one of `peak`, `valid`, `mid`, or `sus`; map the numeric score to the closest honest vibe.
