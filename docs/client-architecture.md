# Client architecture (app/)

Static PWA, no framework, ES modules, built with Vite 6 + vite-plugin-pwa (injectManifest).
Node 22.11 on the dev machine (Vite 8 needs ≥22.12, so Vite 6.4.1 is pinned). Russian UI.
All libraries are bundled locally; no CDN at runtime.

```
app/
  index.html               app shell (one page, 4 screens)
  vite.config.js           PWA plugin (injectManifest, registerType 'prompt'), base './'
  src/
    main.js                boot: city choice (picker), logging, storage.persist(), load pack, build index, route screens
    sw.js                  service worker: precache shell (Workbox), runtime cache for data/tiles
    log/log.js             ring buffer logger (IndexedDB, 2000 entries), console mirror
    store/cities.js        city registry, per-city storage names, chosen city, packLang(), tilesInfo()
    store/db.js            idb-keyval wrappers: zone, settings, history, saved queries, notes (per city)
    store/pack.js          manifest/pack download, sha256 check, atomic switch, offline load (per city)
    search/bitset.js       Bitset (Uint32Array): and/or/not/andNot/count/forEach/clone
    search/normalize.js    skeleton(), kaToLat(), kaToRu(), normRu(), stripType()
    search/names.js        primaryName(), secondaryName(), ruLabel(), nameVariants(), areaRu() for ka/ru packs
    search/index.js        buildIndex(pack) -> Index (see below)
    search/houses.js       parseHouseQuery(), house matching, probable numbers
    dsl/lexer.js           tokenize(text) -> tokens with positions
    dsl/parser.js          parse(text) -> {ast} | {error}
    dsl/eval.js            evaluate(ast, index, ctx) -> result (docs/dsl.md "Output")
    dsl/explain.js         explain(ast) -> Russian sentence
    dsl/complete.js        complete(text, cursor, index, ctx) -> suggestions
    zone/geo.js            decodePolyline, haversine, pointInPolygon, clipLengthInside, bearings, grid
    zone/masks.js          computeZoneMasks(index, polygon, opts) -> ZoneMasks
    zone/code.js           encodeZone(polygon) <-> decodeZone(code)  (short share code)
    zone/draw.js           freehand drawing overlay (Pointer Events + canvas + simplify-js)
    zone/edit.js           Terra Draw polygon/select modes on MapLibre (tap vertices, edit)
    map/map.js             MapLibre + PMTiles basemap (offline), result/house/zone layers
    ui/*.js, ui/styles.css screens: search, map, zone, readiness (+ log, about)
  public/
    data/                  Tbilisi pack (docs/pack-format.md); data/khamovniki/ — Moscow pack
    tiles/                 tbilisi.pmtiles, khamovniki.pmtiles, fonts/, sprites/ (shared)
    icons/, manifest.webmanifest
```

## Core interfaces (implemented by the logic layer, consumed by the UI)

```js
// store/pack.js
loadPack({ onProgress }) -> Promise<Pack>          // from Cache Storage / network, verifies sha256
checkForUpdate() -> Promise<{available, version}>   // cache:'no-store', 3 s timeout
// Pack = { manifest, core, geom, houses, areas, eponyms }  (geom/houses/areas may arrive later:
// loadPack resolves after core+eponyms; pack.ready is a Promise for the rest)

// search/index.js
buildIndex(pack) -> Index
Index = {
  n,                         // number of features
  pack,
  names: string[][],         // per feature: all name variants (raw)
  skel:  string[],           // per feature: joined skeletons separated by '|'
  kindBits: Map<code, Bitset>, groupBits: Map<'street*'|'lane*'|..., Bitset>,
  districtBits: Bitset[], placeBits: Bitset[],
  familyOf: Int32Array, membersOfFamily: Int32Array[],
  eponymOf: Int32Array,      // -1 if unknown
  catBits: Map<taxId, Bitset>, kindAliasBits: Map<alias, Bitset>,
  adj: { offsets: Int32Array, nbr: Int32Array, xi: Int32Array },   // CSR over crossings
  geometry(i) -> Array<Array<[lat, lon]>>   // lazy decode + cache
  houses(i)  -> Array<HouseRow>
}

// dsl
parse(text) -> { ast } | { error: {pos, len, message, suggestions} }
evaluate(ast, index, ctx) -> { bits, count, order, explain, warnings, timeMs }
ctx = { zone: ZoneMasks | null, me: {lat, lon} | null, mapCenter: {lat, lon} | null }
complete(text, cursor, index, ctx) -> [{label, insert, detail}]

// zone
computeZoneMasks(index, polygon /* [[lat, lon], ...] */, { rule: 'default'|'touch'|'whole' })
  -> ZoneMasks = { polygon, bbox, areaKm2,
                   featDefault: Bitset, featTouch: Bitset, featWhole: Bitset,
                   lenInside: Float32Array,
                   crossingIn: Uint8Array,              // per crossing index
                   houseIn(featureIdx, row) -> boolean, // or precomputed
                   counts: { features, houses } }
encodeZone(polygon) -> string ; decodeZone(string) -> polygon
```

## Implementation notes (24/10/2026, as built)

- `Bitset`: `and/or/andNot/xor/not` return a new set; `iand/ior/iandNot/ixor/inot` mutate in place;
  also `has/get/set/count/forEach/toArray/first/intersects` and an iterator.
- `Index` extras used by the DSL: `textBits, nameBits, resolveKind, resolveWho, resolveArea,
  resolveDistrict, occupationBits, eponymBits, oldEponymBits, familyClosure, crossingBits,
  nearBits, nearPointBits, suggest` (MiniSearch), `warmup()` (runs on idle in the browser).
- `index.houses(i)` returns raw pack rows `[raw, n, lit, sub, lat, lon, n_to?, corp?]`.
- `adj` has `mask` (direction mask of the street at that crossing); one entry per crossing point,
  so a neighbour can repeat.
- House semantics: a range "3-5" covers only its own parity side; `=3` excludes ranges and
  korpus rows; `дом:3а` also matches "3ა/1".
- `ZoneMasks` extras: `featRule, contains, forRule, housesInside, refreshHouses, recompute`;
  `counts.houses = null` until houses.json is loaded.
- `decodeZone` throws an Error with a Russian message; `tryDecodeZone` returns null.
- `store/pack.js`: `loadPack` downloads data files on first run; tiles via
  `downloadPack({includeTiles: true})` (skips files with an unchanged sha256, atomic switch);
  also `checkForUpdate, packStatus, removePack, tileEntries`.
- Service worker: never precache `data/*.json` or `tiles/*` with Workbox; answer them from the
  `pack-v<version>` cache via `caches.match`, and serve HTTP Range requests for `.pmtiles`
  from the cached file.
- UI modules (`app/src/ui/`): state, dom, format, feature, dsledit, query-input, chips, search,
  card, router, theme, header, mapctl, mapscreen, zonectl, zonescreen, ready, assets, update,
  logscreen, about. `app/src/map/`: map.js (layers), basemap.js (PMTiles from Cache Storage →
  File → FileSource, else network, else plain background with our streets), geomutil.js (area,
  +50 m buffer, self-intersection repair, union of districts).
- Service worker routing (as built): `data/*` and `tiles/*` → `caches.match` over all caches,
  network fallback; `.pmtiles` Range → 206 from the cached file; requests with `no-store`/`reload`
  go network-first; glyphs/sprites not in the pack go to runtime cache `basemap-assets-v1`.
- Places (`place*` kinds): a street card lists places at its houses ("д. 10 — Музей …", tap →
  place card) using the 9th element of house rows; a place card shows its address (tap → street)
  and no house grid or crossings; result rows of places show kind and "street, house".
- Lazy chunks: MapLibre + PMTiles + Protomaps style (~300 KB gzip) and Terra Draw (~35 KB gzip)
  load when the map or zone editing opens; the start bundle is ~100 KB gzip.

## Cities (added 10/11/2026)

The app serves several districts ("районы"), each with its own pack. Registry `store/cities.js`:

```js
CITIES = [
  { id: 'tbilisi', title: 'Тбилиси', sub: 'Грузия', dataDir: './data/', lang: 'ka' },
  { id: 'khamovniki', title: 'Москва. Хамовники', sub: 'Россия', dataDir: './data/khamovniki/', lang: 'ru' },
]
```

- Choice: `?city=<id>` in the URL (deep links, e2e; remembered, then removed from the address bar),
  else the localStorage mirror `quchebi.city` (read synchronously before the first paint; the inline
  script in index.html sets `data-city` / `data-need-city` on `<html>`), else the full-screen picker
  `ui/picker.js` ("Выберите район": every city with its offline status — installed version and size
  from the pack pointer, or the size of the network pack when online). The choice is stored in the
  global IndexedDB setting `city` and the mirror. The first choice boots in place; a later switch
  (city chip in the top bar, row "Район" on "Готовность", block "Район" on "О приложении") stores the
  new choice and reloads the page, so every module-level cache belongs to one city.
- Pack language: `manifest.city.lang`, else the registry `lang` (main.js sets `pack.lang`, `pack.city`);
  `packLang(pack)` / `state.packLanguage()`. `'ru'` packs keep the primary Russian name in `ka`
  (no `ru`): names always go through `search/names.js` (no empty or repeated second line; sorting by
  the Russian name), house numbers are Moscow-style (docs/dsl.md "Houses"), `свой:` reads "местный".
- Basemap: `tilesInfo(manifest)` -> `{path, file, bbox, center, minzoom, maxzoom}` from the current
  pack's `manifest.tiles` (`basemap`, `bbox` [w, s, e, n], `center` [lon, lat, zoom]); map.js takes
  the initial view and pan limits (bbox + 0.15°/0.1°) from it, or from the extent of the data when
  absent. basemap.js loads `tiles/<city>.pmtiles`; fonts and sprites are shared. Distance grids
  (`PointGrid`) are centred on the data, never on a fixed city.

## Storage

Tbilisi keeps the storage names of the single-city app (an installed app keeps its pack, tiles and
zone); every other city uses suffixed names (`store/cities.js`: `pointerKey`, `cachePrefix`,
`cityKey`, `cityLsKey`).

| what | Tbilisi | other city (`<id>`) | notes |
|---|---|---|---|
| app shell | Cache Storage (Workbox precache) | same | update only after user confirms (`registerType: 'prompt'`), never in "game mode" |
| pack + tiles | Cache Storage `pack-v<version>`, pointer `pack:current` | `pack-<id>-v<version>`, `pack:current:<id>` | sha256 per file, atomic pointer switch; cleanup deletes only caches of the same city (`cacheOwner()`: `pack-<word>-v…` is never Tbilisi's); identical files (shared fonts/sprites, same URL + sha256) are reused from any city's cache |
| zone | IndexedDB `zone` + localStorage `quchebi.zone` | `zone:<id>` + `quchebi.zone.<id>` | `{polygon, createdAt, mode, name}` |
| zoneSkipped, zoneEnabled | settings keys `zoneSkipped`, `zoneEnabled` | `zoneSkipped:<id>`, `zoneEnabled:<id>` | `db.getCitySetting()` |
| history, saved queries, notes | IndexedDB `history`, `saved`, `notes` | `history:<id>`, `saved:<id>`, `notes:<id>` | |
| chosen city | settings `city` + localStorage `quchebi.city` | (global) | |
| theme, game mode, airplane test | settings, localStorage `quchebi.theme` | (global) | |
| logs | IndexedDB ring buffer | (global) | screen "Журнал" copies them |

The service worker answers `data/**` (incl. `data/<id>/*.json`) and `tiles/**` (incl. Range requests for
`tiles/<id>.pmtiles`) from any cache via `caches.match`; URLs differ per city.

`navigator.storage.persist()` at start; readiness screen shows shell/pack/tiles/zone/persist
status and suggests an airplane-mode test. iOS: install to Home Screen is required (7-day eviction).

## UX rules
- Search field and chips at the bottom (thumb reach); input font-size 16 px; `autocapitalize=off`,
  `autocorrect=off`, `spellcheck=false`, `enterkeyhint=search`.
- Chips append DSL tokens; the DSL string is the source of truth (two-way highlight).
- Explanation line + count above results; results grouped by family.
- Tap targets ≥ 44 px; never `text-transform: uppercase` on Georgian text.
- The current district is always visible (city chip at the left of the top bar, one tap -> picker);
  the brand plate "ქუჩები / Quchebi" stays the same in every city.
- Choosers with a single option are hidden (Moscow: one district -> no "Районы" segment on the zone
  screen, no districts in the "Район" picker); type chips and quick buttons appear only for kinds the
  loaded pack has objects of.
- `core.f[].note` is shown as plain text under the names in the object card.
- Navigation after closing a sheet waits for the sheet's history step (`dom.whenHistorySettled`),
  otherwise "На карте" in the card was undone by the pending `history.back()`.
- Light high-contrast theme for sunlight; dark theme follows the system.
- Map: rotation and pitch disabled; `overscroll-behavior: none` on the drawing overlay.
