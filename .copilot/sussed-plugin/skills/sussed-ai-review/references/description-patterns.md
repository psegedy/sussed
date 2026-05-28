# Czech Description Patterns

Reference for extracting structured facts from Czech listing descriptions in the `sussed-ai-review` skill.

Read the **full** `description` text from the prepared payload before scoring — Czech listing descriptions often contain important facts that are absent from `detail_items`. Use explicit evidence; do not guess.

Extracted facts are informational only. **Do not modify the score based on parking cost** or treat extracted fields as a separate scoring rubric. The scoring rubric in `references/ideal-profile.md` still governs.

## Parking extraction rules

Populate `parking_price` and `parking_included` from description text when parking is mentioned:

| Description pattern | Review fields |
|---|---|
| `parkovací stání v ceně`, `parking v ceně`, `garáž v ceně` | `parking_included: true`, `parking_price: 0` or `null` |
| `parkovací stání: 295.000 Kč`, `garážové stání za příplatek 350 000 Kč` | `parking_price: 295000`, `parking_included: false` |
| Monthly parking fee, e.g. `parkování 2 000 Kč/měsíc` | `hidden_costs: {"parking_monthly": 2000}` |
| No parking mentioned at all | `parking_included: null`, `parking_price: null` |

Parking purchase prices are integer CZK. Monthly parking fees belong in `hidden_costs.parking_monthly` as integer CZK. These values explain the listing; they do **not** mechanically raise or lower the rating.

### Parking surcharges are normal in Czech listings

Especially in developer projects, parking is sold separately. `"parkovací stání za 350 000 Kč extra"` is **not** suspicious by itself:

- Still extract `parking_price` and set `parking_included: false`.
- Mention the surcharge in `score_reason` for transparent total-cost math.
- Do **not** add normal parking surcharges to `red_flags`.
- Add only a low-priority `yellow_flags` note if the surcharge is unusually expensive (>500,000 CZK).
- Do not lower the score purely because parking is extra. Score the total economics and listing quality, not the fact that Czech parking is sold separately like a tiny concrete DLC.

## Optional extracted facts

Put additional description-derived facts inside `raw_review.extracted` as a free-form object:

- `renovation_year` (int): last reconstruction year from text like `po rekonstrukci 2023`, `rekonstrukce 2021`.
- `building_type` (string): `panel`, `brick`, `new`, or `mixed` from `panelový dům`, `cihlový dům`, `novostavba`, or mixed evidence.
- `monthly_fees_czk` (int): HOA/SVJ/service fees from `poplatky 5000 Kč`, `SVJ`, or `fond oprav`.
- `cellar_included` (bool): true from `sklep`; false only when explicitly absent.
- `elevator` (bool): true from `výtah`; false only when explicitly no elevator or the description clearly says the building lacks one.
- `available_from` (string): ISO date if clear, otherwise natural text from `volné od 1.7.2026`, `ihned`.
- `condition` (string): `new`, `renovated`, `good`, or `needs_work` from the description tone and explicit condition claims.
- `orientation` (string): cardinal directions from text like `okna na jih`, `JZ`.

## Common Czech description keywords

| Czech | English / signal |
|---|---|
| `po rekonstrukci` / `kompletní rekonstrukce` | renovated |
| `novostavba` | new build |
| `cihla` / `cihlový` | brick (preferred over panel) |
| `panel` / `panelák` | panel building |
| `sklep` | cellar |
| `výtah` | elevator |
| `lodžie` / `balkon` / `terasa` | outdoor space |
| `parkovací stání` / `garáž` / `parking` | parking |
| `poplatky` / `SVJ` / `fond oprav` | HOA fees |
| `volné od` | available from |
| `francouzská okna` | French windows ⭐ |
| `vlastní zahrada` | private garden |
