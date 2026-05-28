# Ideal Garden Plot Profile (buyer's wish list)

This is the scoring rubric reference for `sussed-garden-review`. Apply these preferences when assigning a score to Czech garden / allotment plot listings.

## Core wants

A strong garden listing has most of these:

- **300-1000 m² plot** — enough room to garden, relax, store tools, and not spend every weekend fighting a jungle.
- **Water on the plot** — `vlastní studna`, water connection, or reliable community water. Own well is best.
- **Electricity hookup** — connected electricity or a realistic, priced connection path.
- **Fenced** — `oplocené`, `oplocení`, hedge, or clear boundary.
- **Basic structure** — `chatka`, `bouda`, `kůlna`, greenhouse, or shelter for tools and rain survival.
- **Personal ownership** — `osobní vlastnictví` beats cooperative shares or lease arrangements.
- **Within ~30 minutes of the city** — easy enough for spontaneous after-work gardening.
- **Quiet, civilized surroundings** — decent neighbors, calm colony, not chaos next to a highway.

## Scoring scale

- `9999` — unicorn / absolute gem: owned, ideal size, water, electricity, fenced, structure, great access, peaceful setting, fair price.
- `800-1000` — peak: hits nearly all wants; any trade-off is minor and explicit.
- `600-799` — valid: good usable garden with one or two acceptable compromises.
- `400-599` — mid: workable but missing important comforts or clarity.
- `200-399` — meh: inconvenient, under-equipped, legally awkward, or overpriced.
- `1-199` — bad: major practical problems, weak evidence, or too much future work.
- `-1` — sus, scam, legal mess, auction/execution trap, or avoid.

`vibe` must be one of `peak`, `valid`, `mid`, or `sus`; map the numeric score to the closest honest vibe.

## Red flags

These can crush the score unless the listing gives unusually clear mitigation:

- `pronájem pozemku`, annual lease only, or unclear right to use the land.
- Cooperative-only ownership (`družstevní`) without clear share terms, transfer rules, or buyback path.
- No water (`bez vody`) or no realistic water source.
- No electricity and no viable hookup (`bez možnosti elektřiny`).
- More than ~1 hour from the target city.
- Flood zone: `záplavová oblast`, repeated flooding, riverbank risk without mitigation.
- Legal disputes, unclear cadastre, `bez katastru`, easement chaos, access disputes.
- `exekuce`, `dražba`, forced sale, or `pouze hotovost` financing pressure.
- Building restrictions that defeat the intended use, e.g. no shed, no utility hookup, no access.

## Yellow flags

These are not instant death, but they need transparency in `yellow_flags` and `score_reason`:

- Shared water (`společná voda`, colony well) instead of own source.
- Generator-only electricity or “possible connection” with no price/timeline.
- Unfenced or unclear boundary.
- No shed/chatka/structure.
- Overgrown plot requiring major cleanup.
- Cooperative ownership with decent but incomplete terms.
- Car access nearby but not directly to the plot.
- Tiny plot under 300 m² or monster plot over 1000 m² unless price/use case justifies it.

## Highlights

Score higher when evidence supports:

- Own well (`vlastní studna`) or reliable water on the plot.
- Electricity at the plot (`elektřina na pozemku`, `přípojka elektřiny`).
- Recent structure renovation or dry, usable storage.
- Mature fruit trees (`vzrostlé ovocné stromy`), fertile soil, greenhouse, vines.
- South-facing slope (`jižní svah`), sunny and not too steep.
- Flat/usable terrain (`rovinatý pozemek`) and good access by car.
- Personal ownership with clean cadastre and no legal bullshit.
