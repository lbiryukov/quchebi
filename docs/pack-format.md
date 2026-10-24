# Pack format v1

The pack is what the phone stores: a few JSON files produced by `pipeline/export/` from
`data/runcity.sqlite` and served by the PWA from `app/public/data/`. The client
(`app/src/store/pack.js`) loads `manifest.json` first, then `core.json` (search works
after it), then the rest. Every file has `"v": 1`. All arrays that are "index-aligned"
use the feature index from `core.f` as the key.

Coordinates in compact arrays are integers `round(deg * 1e5)` ("e5"). Geometries are Google
encoded polylines with precision 5 (`pipeline/export/polyline.py` and `app/src/zone/geo.js`
must agree; shared vectors in `tests/vectors/polyline.json`).

## Kinds

Canonical kind codes, in this order (the index is what `core.f[].k` stores; the list itself
is shipped in `core.kinds`, so the client never hardcodes indices).

| code | ka (last word of name) | ru (DSL value) | group |
|---|---|---|---|
| street | ქუჩა | улица | street* |
| avenue | გამზირი | проспект | street* |
| highway | გზატკეცილი | шоссе | street* |
| road | გზა | дорога | street* |
| embankment | სანაპირო | набережная | street* |
| ascent | აღმართი | подъём | street* |
| descent | დაღმართი | спуск | street* |
| alley | ხეივანი | аллея | street* |
| row | რიგი | ряд | street* |
| lane | შესახვევი | переулок | lane* |
| dead_end | ჩიხი | тупик | lane* |
| passage | გასასვლელი | проезд (синоним: съезд) | lane* |
| square | მოედანი | площадь | |
| bridge | ხიდი | мост | |
| tunnel | გვირაბი | тоннель | |
| ravine | ხევი | балка (синоним: ущелье) | |
| garden | სკვერი, ბაღი | сквер (синоним: сад) | |
| park | პარკი | парк | |
| river | მდინარე | река | water* |
| stream | ღელე | ручей | water* |
| canal | არხი | канал | water* |
| lake | ტბა | озеро | water* |
| metro | (station=subway) | метро (синоним: станция) | |
| district | რაიონი | район | area* |
| neighbourhood | (place=suburb/neighbourhood/quarter) | микрорайон (синонимы: квартал, местность) | area* |
| other | | прочее | |
| landmark | (tourism=attraction/viewpoint/zoo, historic=*, artwork, tower) | место (синонимы: места, достопримечательность) | place* |
| museum | მუზეუმი | музей (синоним: галерея) | place* |
| theatre | თეატრი | театр (синонимы: опера, филармония, цирк, концертзал) | place* |
| cinema | კინოთეატრი | кинотеатр (синоним: кино) | place* |
| monument | ძეგლი, მემორიალი, ბიუსტი | памятник (синонимы: монумент, мемориал, статуя, скульптура, бюст) | place* |
| plaque | დაფა | доска (синонимы: мемдоска, табличка, звезда) | place* |
| church | ეკლესია, ტაძარი, მონასტერი | храм (синонимы: церковь, собор, монастырь, базилика, часовня) | place* |
| mosque | მეჩეთი | мечеть | place* |
| synagogue | სინაგოგა | синагога | place* |
| fortress | ციხე, ციხესიმაგრე | крепость (синонимы: замок, цитадель) | place* |
| palace | სასახლე | дворец (синоним: усадьба) | place* |
| bath | აბანო | баня (синоним: бани) | place* |
| stadium | სტადიონი, არენა | стадион (синонимы: арена, спорткомплекс) | place* |
| mall | მოლი | торгцентр (синонимы: тц, молл, универмаг) | place* |
| market | ბაზარი, ბაზრობა | рынок (синоним: базар) | place* |
| station | სადგური, ავტოსადგური | вокзал (синонимы: автовокзал, жд) | place* |
| university | უნივერსიტეტი, აკადემია, ინსტიტუტი | университет (синонимы: вуз, академия, институт, консерватория, колледж) | place* |
| school | სკოლა | школа (синонимы: лицей, гимназия) | place* |
| library | ბიბლიოთეკა | библиотека | place* |
| cemetery | სასაფლაო, პანთეონი | кладбище (синоним: пантеон) | place* |
| factory | ქარხანა, ფაბრიკა | завод (синонимы: фабрика, комбинат) | place* |
| residential | (landuse=residential, building=apartments) | жилкомплекс (синоним: жк) | place* |

`core.kinds[i] = {"code": "street", "ka": "ქუჩა", "ru": "улица", "syn": [], "group": "street*"}`.

Landmark kinds (group `place*`, after `other` so that older indices do not move) are set from OSM
tags by `pipeline/osm/landmarks.py`, never from a street name: their Georgian words are shipped for
the client (search, `тип:`), but `parse_name` does not use them. `тип:место*` (or `тип:place*`)
is the whole group. A landmark is one feature per object (node, building and multipolygon of the
same name within 100 m are merged); small buildings are shipped as points, large areas
(stadiums, cemeteries, markets, ...) as their outline. `par` of a landmark is its street: the street
of `addr:street` (+ `addr:housenumber`), else the nearest street within 50 m. The house row of
that address stays under the street in `houses.json` (the DB flags it `landmark:<kind>`) and carries
the landmark's feature index as its 9th element (see houses.json).

## manifest.json

```json
{
  "v": 1,
  "version": "20261024-1",
  "built": "24/10/2026",
  "osm": "2026-10-23T19:42:11Z",
  "files": {"core.json": {"bytes": 967000, "sha256": "..."}, "...": {}},
  "tiles": {"tbilisi.pmtiles": {"bytes": 21300000, "sha256": "..."}},
  "counts": {"features": 5620, "streets": 5178, "houses": 40576, "crossings": 8700, "eponyms": 2100},
  "attribution": ["© OpenStreetMap contributors, ODbL", "Wikidata, CC0", "..."]
}
```

## core.json

```json
{
  "v": 1,
  "kinds": [ {"code": "street", "ka": "ქუჩა", "ru": "улица", "syn": [], "group": "street*"} ],
  "districts": [ {"ka": "მთაწმინდის რაიონი", "ru": "Мтацминда", "en": "Mtatsminda"} ],
  "places": [ {"ka": "სოლოლაკი", "ru": "Сололаки", "en": "Sololaki", "kind": "neighbourhood",
               "parent": 3, "c": [4169123, 4479456], "km2": 0.49} ],
  "f": [ {
      "ka": "ლადო ასათიანის II შესახვევი",
      "ru": "2-й переулок Ладо Асатиани",
      "en": "Lado Asatiani 2nd Lane",
      "k": 9,
      "o": 2,
      "fam": 412,
      "par": 1033,
      "d": [3],
      "p": [17],
      "len": 71,
      "c": [4169123, 4479456],
      "bb": [4169100, 4479400, 4169150, 4479500],
      "alt": ["Lado Asatiani II Lane"],
      "old": [ {"ka": "...", "ru": "...", "lat": "...", "from": 1934, "to": 1990, "e": 17, "src": "enc2008"} ],
      "hn": 0,
      "q": "Q144086",
      "ar": null
  } ],
  "fam": [ {"ka": "ლადო ასათიანის", "ru": "Ладо Асатиани", "e": 5} ],
  "x":  [0, 2, 0, 7],
  "xp": [4169123, 4479456, 4169200, 4479500],
  "xd": [4, 17, 68, 1],
  "near": [12, 40]
}
```

Field notes:
- `f[i].k` index into `kinds`; `o` ordinal of a lane (absent if none); `fam` index into `fam`
  (absent if no family); `par` parent feature index (lanes → their street); `d`/`p` indices into
  `districts`/`places`; `len` metres (0 for points); `c` representative point e5; `bb` bbox e5
  `[minlat, minlon, maxlat, maxlon]`; `alt` other names in any language (OSM alt_name, official
  NAPR spelling, latin variants from addresses); `old` former names, newest first; `old[].e` index
  into `eponyms.e` (absent if unknown); `hn` count of house numbers; `q` raw OSM etymology QID;
  `ar` for kind district/neighbourhood: index into `districts` or `places` respectively.
- Optional fields are omitted, never `null`, except `ar` may be omitted too.
- `fam[j].e` index into `eponyms.e` (absent if unknown).
- Crossings: pair `t` is `x[2t]`, `x[2t+1]` (feature indices, a < b); its point is
  `xp[2t]`, `xp[2t+1]`; direction masks `xd[2t]` (for a) and `xd[2t+1]` (for b).
  Direction mask bits: 0=N, 1=NE, 2=E, 3=SE, 4=S, 5=SW, 6=W, 7=NW — the sectors (45° each,
  centred on the compass direction) in which that street's geometry leaves the crossing point,
  measured over the first ~30 m. A through street usually has two bits set.
- `near`: flat pairs of features whose ends are 8–25 m apart without a crossing.

## geom.json

```json
{"v": 1, "prec": 5, "g": [ ["encodedPart1", "encodedPart2"], ["..."] ]}
```
`g[i]` = list of encoded polylines of feature `i` (one per connected part). Points are one-vertex
polylines. Polygons (parks, squares mapped as areas) are their outer ring, closed.

## houses.json

```json
{"v": 1, "h": {"1033": [["3ა", 3, "ა", "", 4169123, 4479456], ["3/16", 3, "", "16", 4169130, 4479470]]},
 "place": {"17": [["12", 12, "", "", 4169000, 4479000]]}}
```
Row: `[raw, n, lit, sub, lat_e5, lon_e5]` (+ optional 7th element `n_to` for ranges "3-5",
`corp` as 8th and `lm` as 9th). Keys of `h` are feature indices as strings; keys of `place` are place
indices (addresses that only have `addr:place`). Letters are normalized to Georgian (ა ბ გ დ ე ვ ...).

`lm` (9th element) is the index into `core.f` of the landmark (kind group `place*`) whose OSM object
carries this address (the DB house flag `landmark:<kind>`): `["8", 8, "", "", 4169123, 4479456, null,
null, 6012]` is "д. 8 — Музей истории Тбилиси". Missing `n_to` / `corp` before it are `null`. The
landmark's `par` is usually the feature under whose key the row lies, but not always (its `par` may
come from another address of the same object). The client shows such rows in the street card
(«д. 8 — <place>», tap opens the place) and the address in the place card.

## areas.json

```json
{"v": 1,
 "districts": [ {"poly": [["outerRing", "holeRing"], ["outerRing2"]]} ],
 "places":    [ {"poly": [["outerRing"]]} ]}
```
Index-aligned with `core.districts` / `core.places`. Place without a polygon has `"poly": []`
(the client then uses a 300 m circle around `core.places[i].c`).

## eponyms.json

```json
{"v": 1,
 "taxonomy": [ {"id": "lit", "ru": "Литература", "aliases": ["литература", "литератор"],
                "children": [ {"id": "lit.poet", "ru": "поэт", "aliases": ["поэт", "поэтесса"]} ]} ],
 "kinds": {"person": ["персона", "человек", "люди"], "toponym": ["топоним", "место"],
           "event_date": ["событие", "дата"], "nature": ["природа"], "concept": ["понятие"],
           "group": ["группа"]},
 "e": [ {"ka": "გალაკტიონ ტაბიძე", "ru": "Галактион Табидзе", "en": "Galaktion Tabidze",
         "q": "Q144086", "k": "person", "cat": ["lit.poet"], "occ": ["поэт"],
         "g": "m", "b": 1891, "d": 1959, "o": "ge",
         "desc": "грузинский поэт", "src": "osm", "conf": 0.98, "ai": 0} ]}
```
`ai` = 1 when the categories/description come from an LLM (UI shows the "оценка ИИ" chip).
Optional fields omitted when unknown.

## Conventions fixed during implementation (24/10/2026)

- `core.f[]` always has `ka`, `k`, `len`, `c`, `hn`; `len = 0` for points and polygons.
- `core.places[].parent` is an index into `core.districts`.
- `eponyms.e[].o` is `"ge"` or `"fo"` (omitted when unknown).
- House row with a building/korpus but no range: `[raw, n, lit, sub, lat, lon, null, corp]`;
  a landmark row without range and korpus: `[raw, n, lit, sub, lat, lon, null, null, lm]`.
- The same pair of streets may appear several times in `x` with different crossing points.
- `name_history` rows without `feature_id` or with confidence < 0.5 are not exported.
- `ai = 1` when the eponym or any of its categories comes from an LLM.
- Unreferenced eponyms and families are dropped; indices are re-assigned.
- Size budget: ≤ 1.6 MB gzip for all data files (≈ 1.1 MB streets only; the landmarks layer
  adds ≈ 0.15 MB and eponyms ≈ 0.1 MB).
- `manifest.tiles` = `{basemap, glyphs, sprites, minzoom, maxzoom, bbox, center, bytes,
  files: {"tiles/<path>": {bytes, sha256}}}`, built from `app/public/tiles/tiles-manifest.json`.

## Tiles (separate, large)

`app/public/tiles/tbilisi.pmtiles` (Protomaps basemap extract, z0–15, bbox of Tbilisi),
`app/public/tiles/fonts/<fontstack>/<range>.pbf`, `app/public/tiles/sprites/light.json|png`
(+ `@2x`). Listed under `manifest.tiles` with sha256. Source of truth for tile files is
`app/public/tiles/tiles-manifest.json` written by `pipeline/tiles`; Medium and Italic glyph
ranges that lack Georgian letters are filled from Regular (`glyph_fallback`).
