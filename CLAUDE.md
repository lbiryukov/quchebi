# Quchebi — project instructions

Offline PWA for the Runcity game: search of streets, toponyms and landmarks (ka/ru/lat) with a
query DSL, eponym categories, house numbers, crossings, name history and a finger-drawn competition
zone. Two districts ("cities"), chosen in the app at entry: **Tbilisi** (default) and **Moscow,
Khamovniki** (`khamovniki`, Russian-language pack). Live: https://lbiryukov.github.io/quchebi/ (GitHub Pages, deployed by
`.github/workflows/pages.yml` on every push to `main` that touches `app/`).
Plan: `docs/plans/quchebi-plan.html`. Contracts: `docs/pack-format.md`, `docs/dsl.md`,
`docs/client-architecture.md`, `migrations/001_init.sql`.

## Layout
- `pipeline/` — Python data pipeline (uv). The city is chosen with `QUCHEBI_CITY=<id>` (default
  `tbilisi`; registry `pipeline/config.py: CITIES`). Tbilisi: stages write to `data/runcity.sqlite`,
  export writes the pack to `app/public/data/`. Other cities: `data/<id>/runcity.sqlite` and
  `app/public/data/<id>/`. Logging via `pipeline.log.get_logger` → `logs/pipeline.log`.
  - `names/` name parsing, families, transliteration, house numbers (shared with JS via vectors);
    `parse_ru.py` + `houses.parse_house_ru` for Russian-language cities
  - `osm/` PBF import: areas, features, landmarks (`place*` kinds), feature_area, crossings, houses
  - `history/` former names: encyclopedia 2008 (AcadNusx PDF), ka/ru Wikipedia, OSM, addresses
  - `wikidata/`, `enrich/` eponyms: QIDs, rules, matching, facts, dossiers, LLM batches, merge, review
  - `export/`, `validate/` pack writer, invariants, golden SQL counts, coverage report
  - `tiles/` Protomaps basemap extract, glyphs, sprites → `app/public/tiles/` (`<city>.pmtiles`;
    fonts and sprites are shared, a district build never re-downloads them)
  - `ru/` reference stage of Russian-language cities (instead of `history/` + `enrich/`): Wikidata
    SPARQL, Russian Wikipedia wikitext, Wikivoyage heritage lists, LLM batches, merge
- `app/` — static PWA (Vite 6, no framework). Logic in `app/src/{search,dsl,zone,store,log}`,
  UI in `app/src/{ui,map}`, service worker `app/src/sw.js`, e2e in `app/e2e/`.
- `tests/` — pytest (`tests/*.py`), node:test (`tests/js/*.test.mjs`), shared vectors
  (`tests/vectors/*.json`) that Python and JS must both satisfy, acceptance set of real Runcity
  riddles (`tests/acceptance/`).
- `.claude/agents/` — LLM subagents: `eponym-classifier`, `eponym-disambiguator`, `eponym-verifier`,
  `name-qa` (Haiku, classification only); `history-extractor` (Opus, extraction). Moscow:
  `street-researcher` (Opus, extraction from street articles), `ru-eponym-classifier` (Haiku).
- `data/` (gitignored) — raw downloads, SQLite, LLM batches/results, build reports.
  `temp_scripts/prototypes/` — research prototypes (reference only).

## Rules
- Python: uv only, Python 3.13, `[tool.uv] exclude-newer = "2026-05-01T00:00:00Z"`. Run with `uv run`.
- Never create, copy or edit `.env` files.
- Feature/area ids change on every OSM rebuild; caches and manual decisions are keyed by
  `family.key`, QID or text. A full OSM rebuild deletes `name_history` (cascade): always run
  `history all` and `enrich prepare`/`merge` after it.
- Encyclopedia 2008, Wikipedia, Wikivoyage, Runcity riddles: store facts only, never full texts in
  the DB, the pack or the repository.
- The Tbilisi pack (`app/public/data/*.json`, `app/public/tiles/tbilisi.pmtiles`, fonts, sprites)
  cannot be rebuilt without the local `data/` of the dev machine: never touch it from a session
  that only builds another city.
- From cloud sessions Geofabrik and Overpass refuse connections and the MediaWiki `api.php`
  answers 429: use the OSM France extract (configured for `khamovniki`), raw wikitext
  (`index.php?action=raw`) and Wikidata SPARQL (`pipeline/ru/sources.py` does this).
- Subagent model policy: Opus for complex work, Haiku only for classification.
- Dates in user-facing text: DD/MM/YYYY. No emoji.
- Node is 22.11 on the dev machine: keep Vite 6.x (Vite 8 needs ≥22.12). `.npmrc` sets
  `legacy-peer-deps=true` (npm 10.9 peer-resolution bug). `node --test` needs a glob, not a folder.
- Push as lbiryukov (the default gh account on this machine is different).

## Full data rebuild (order matters)
1. `uv run python -m pipeline.cli osm --offline` — OSM + landmarks (~1 min with cached PBF)
2. `uv run python -m pipeline.history all` — former names (cached sources)
3. `uv run python -m pipeline.enrich prepare` — Wikidata facts, rules, matching, LLM batches
4. Run the Haiku subagents on `data/enrich/batches/{classifier,verifier,name_qa}/*.jsonl`
   (instructions in `.claude/agents/*.md`; results go to `data/enrich/results/<role>/`)
5. `uv run python -m pipeline.enrich merge`, then
   `uv run python -m pipeline.enrich batches --role disambiguator --role verifier`, run those
   subagents, `merge` again, `uv run python -m pipeline.enrich calibrate --apply`, `merge`,
   `uv run python -m pipeline.enrich review` (→ `data/build/review.html`)
6. `uv run python -m pipeline.export` and `uv run python -m pipeline.validate`

## Moscow, Khamovniki rebuild (`export QUCHEBI_CITY=khamovniki` first; order matters)
1. `uv run python -m pipeline.cli osm` — OSM import from the OSM France Moscow extract (~15 s cached)
2. `uv run python -m pipeline.tiles` — basemap `app/public/tiles/khamovniki.pmtiles` (reuses fonts/sprites)
3. `uv run python -m pipeline.ru fetch` — Wikidata, street articles, heritage lists (cached in `data/khamovniki/raw/ref`)
4. `uv run python -m pipeline.ru batches --role researcher`, run the Opus `street-researcher`
   subagents on `data/khamovniki/enrich/batches/researcher/r*.md` (results → `.../results/researcher/`)
5. `uv run python -m pipeline.ru batches --role classifier`, run the Haiku `ru-eponym-classifier`
   subagents on `.../batches/classifier/c*.json`
6. `uv run python -m pipeline.ru merge` (matches full-name eponyms to Wikidata by exact label: needs
   the network once, then cached), then `uv run python -m pipeline.export` and `pipeline.validate`
A full OSM rebuild deletes the features created by step 6: always run `ru merge` after it.

## Commands (verified)
- `uv sync` — install Python deps
- `uv run pytest -q` — Python tests
- `node --test "tests/js/*.test.mjs"` — JS tests (from the project root)
- `node tests/acceptance/run_acceptance.mjs` — acceptance on real Runcity riddles (target ≥ 90%)
- `uv run python -m pipeline.cli osm --offline`
- `uv run python -m pipeline.history all`
- `uv run python -m pipeline.enrich prepare|merge|calibrate --apply|review`
- `uv run python -m pipeline.export`
- `uv run python -m pipeline.validate`
- `uv run python -m pipeline.tiles` — rebuild the offline basemap
- `cd app && npm install`
- `cd app && npm run build`
- `cd app && npx playwright test` — e2e (iPhone 14 WebKit + Pixel 7 Chromium)
