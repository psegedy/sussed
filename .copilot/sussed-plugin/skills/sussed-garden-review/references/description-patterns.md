# Czech Garden Description Patterns

Reference for extracting structured facts from Czech garden / allotment plot listing descriptions in `sussed-garden-review`.

Read the **full** `description` and `detail_items` before scoring. Use explicit evidence; do not guess from vibes, greenery, or realtor poetry.

## Plot types

- `zahrada`, `zahrádka`
- `zahrádková osada`, `zahrádkářská osada`
- `kolonie`, `zahrádkářská kolonie`
- `pozemek určený k zahrádkářským účelům`
- `rekreační zahrada`, `zahradní pozemek`

Do not confuse these with cottages (`chata`, `chalupa`) where the building is the primary asset.

## Structures

- `chatka` — small garden cabin/shed; useful, but not automatically a cottage.
- `bouda` — shed.
- `kůlna` — tool shed.
- `přístřešek` — shelter.
- `skleník` — greenhouse.
- `pergola` — pergola / covered sitting area.

## Utilities (good)

- `elektřina`, `elektřina na pozemku`
- `přípojka elektřiny`, `elektrická přípojka`
- `voda na pozemku`
- `vlastní studna`
- `obecní vodovod`, `vodovodní přípojka`

## Utilities (bad or missing)

- `bez elektřiny`
- `bez vody`
- `společná voda`, `společná studna`
- `dovoz vody`, `nutný dovoz vody`
- `generátor`, `centrála` — generator-only electricity
- `bez možnosti elektřiny`

## Ownership types

- `osobní vlastnictví` — best; owned land.
- `družstevní` — worse; cooperative share terms matter.
- `podíl v družstvu`, `členský podíl` — cooperative share, not straightforward ownership.
- `pronájem pozemku` — worst for buying; leased land.
- `pacht`, `nájemní smlouva` — lease-like arrangement.
- `bezúplatný převod` — transfer; inspect terms carefully.

## Land, plants, and boundaries

- `oplocené`, `oplocení`, `živý plot` — fenced/bounded.
- `úrodná půda`, `úrodná zemina` — fertile soil.
- `ovocné stromy`, `vzrostlé stromy` — mature trees.
- `vinná réva`, `vinice` — vines.
- `slunný pozemek`, `jižní svah` — good sun exposure.
- `rovinatý pozemek` — easier gardening and access.

## Access and parking

- `příjezd autem`, `příjezdová cesta`
- `parkování`, `možnost parkování`
- `nedaleko cesty`, `přístup z obecní komunikace`

For gardens, `parking_price` and `parking_included` are usually `null` unless a formal paid parking spot is explicitly sold.

## Red flag phrases

- `pouze hotovost`
- `bez katastru`
- `investiční příležitost` when details are vague
- `dražba`, `exekuce`
- `záplavová oblast`
- `bez možnosti stavby`
- `bez možnosti elektřiny`
- `právní vady`, `věcné břemeno`, `sporný přístup`
- `ve vlastnictví státu`, `pozemek města` — state/municipal land

## Transport, power, and noise adjacency

Czech listings rarely admit these. Cross-check the aerial / cadastral images and any GPS coordinates against a map.

- `u dálnice`, `D1`, `D2`, `silnice I. třídy` — motorway / main road.
- `u trati`, `železniční trať`, `koridor` — railway, often electrified.
- `letiště`, `Tuřany` — airport (Brno-Tuřany flight path covers the SE Brno-venkov band).
- `vysoké napětí`, `vedení VN`, `VN linka`, `VVN` — high-voltage power lines overhead.
- `průmyslová zóna`, `lom`, `pískovna`, `velkochov` — industrial / mining / livestock neighbour.

The photo classification is more reliable than the text: motorway lanes, catenary masts, airport runways, and pylons are unmistakable from above even when the description stays silent.

## Image-source quality

Some listings provide only cadastral plans or AI-generated lead photos. Distinguish them before scoring:

- Real on-site photo — handheld perspective, weather/season match, no render style.
- Aerial / cadastral — orthophoto or katastrální mapa overlay; great for adjacency checks.
- CGI / AI render — implausible lighting, mismatched seasons, suspiciously perfect chatka.

When no real on-site photo exists, add the `"Photos are maps/cadastral only — no on-site evidence."` yellow flag.

## Phrase → review field mapping

| Common phrase | Review fields |
|---|---|
| `osobní vlastnictví` | `raw_review.extracted.ownership_type: "osobní"`; highlight if clean. |
| `družstevní zahrádka`, `členský podíl` | `ownership_type: "družstevní"`; yellow/red flag depending terms. |
| `pronájem pozemku`, `pacht` | `ownership_type: "pronájem"`; usually red flag; annual fee in `hidden_costs.annual_lease` if known. |
| `vlastní studna` | `water_source: "studna"`; highlight. |
| `obecní vodovod`, `vodovodní přípojka` | `water_source: "vodovod"`; highlight. |
| `společná voda`, `společná studna` | `water_source: "shared"`; yellow flag. |
| `bez vody` | `water_source: "none"`; red flag unless cheap and explicitly acceptable. |
| `elektřina na pozemku`, `přípojka elektřiny` | `electricity_connected: true`; highlight. |
| `bez elektřiny`, `generátor` | `electricity_connected: false`; yellow/red flag by severity. |
| `oploceno`, `oplocený pozemek` | `fenced: true`; highlight. |
| `chatka`, `bouda`, `kůlna`, `skleník` | `has_structure: true`; mention condition from text/photos. |
| `bez možnosti stavby` | `building_allowed: false`; yellow/red flag depending intended use. |
| `stavební povolení možné`, `možnost umístit chatku` | `building_allowed: true`; highlight only if explicit. |
| `záplavová oblast` | red flag; lower confidence if flood risk unclear. |
| `rovinatý`, `jižní svah`, `úrodná půda`, `vzrostlé stromy` | highlights: usable terrain, sun, soil, mature planting. |
