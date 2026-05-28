# Czech Cottage Description Patterns

Reference for extracting structured facts from Czech chata/chalupa descriptions. Read the full `description` and `detail_items`; don't infer utilities or ownership from vibes alone.

## Building types

`chata`, `chalupa`, `srub`, `dřevostavba`, `zděná`, `kamenná`.

## Utilities (good)

`elektřina v ceně`, `elektřina zavedena`, `vlastní studna`, `vodovod`, `kanalizace`, `septik`, `čistírna odpadních vod`, `ČOV`, `plyn`.

## Utilities (bad/missing)

`bez elektřiny`, `suchá toaleta`, `bez vody`, `dovoz vody`, `voda není zavedena`, `bez kanalizace`.

## Access

`příjezdová cesta`, `celoročně přístupné`, `parkování u chaty`, `přístup po obecní cestě`, `příjezd po nezpevněné cestě`, `v zimě neprůjezdné`.

## Land

`vlastní pozemek`, `oplocené`, `zahrada`, `les`, `voda na pozemku`, `pozemek v osobním vlastnictví`, `pronájem pozemku`.

## Condition

`po rekonstrukci`, `kompletní rekonstrukce`, `k rekonstrukci`, `nutná rekonstrukce`, `kolaudace`, `kolaudovaná`, `bez kolaudace`, `eternit`, `vlhkost`, `plíseň`.

## Ownership

- `osobní vlastnictví` — good.
- `družstevní` — cooperative; financing and control risk.
- `pronájem pozemku` — leased land; bad unless terms are unusually clear and cheap.
- `bez katastru` — severe legal red flag.

## Heating

`krbová kamna`, `kamna na tuhá paliva`, `elektrické topení`, `plynové topení`, `bez topení`.

## Red flag phrases

`pouze hotovost`, `bez katastru`, `investiční příležitost`, `dražba`, `exekuce`, `černá stavba`, `přístup přes cizí pozemek`.

## Phrase → review fields

| Description pattern | Review fields |
|---|---|
| `vlastní pozemek 850 m²`, `pozemek 850 m2` | `raw_review.extracted.plot_size_m2: 850`; add highlight if owned/usable. |
| `pronájem pozemku`, `pozemek není součástí prodeje` | Add red/yellow flag; `raw_review.extracted.land_ownership: "leased"`. |
| `elektřina zavedena`, `elektřina 230/400V` | `raw_review.extracted.electricity_connected: true`. |
| `bez elektřiny` | `electricity_connected: false`; red flag; possible `hidden_costs.electricity_connection: null`. |
| `vlastní studna` | `water_source: "studna"`; highlight if functional. |
| `vodovod` | `water_source: "vodovod"`. |
| `bez vody`, `dovoz vody` | `water_source: "none"`; yellow/red flag depending severity. |
| `kanalizace` | `sewage: "kanalizace"`; strong highlight. |
| `septik`, `jímka`, `ČOV` | `sewage: "septik"`, `"jímka"`, or `"čistírna odpadních vod"`; add hidden cost if amount stated. |
| `celoročně přístupné` | `road_access: "year-round"`; highlight. |
| `v zimě neprůjezdné`, `pouze pěšky` | `road_access: "seasonal"`; red/yellow flag. |
| `kolaudovaná stavba`, `číslo evidenční` | `legalized: true` when context supports normal legal status. |
| `bez kolaudace`, `bez katastru` | `legalized: false`; red flag, possibly `score: -1`. |
| `suchá toaleta` | Add yellow flag; `sewage: "none"` or leave `null` if unclear. |
| `parkování u chaty`, `stání na pozemku` | Usually no paid parking fields; mention as highlight, leave `parking_price` null. |
| `parkování za 500 Kč/den` | `hidden_costs.parking_daily: 500`; paid parking is unusual for cottages. |

## Extraction discipline

Use exact evidence. If a phrase is absent, leave fields `null`; don't translate a pretty forest photo into “water source: studna.” That's how hallucinated cottage hell begins.
