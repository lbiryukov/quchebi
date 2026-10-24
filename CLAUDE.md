# Quchebi — project instructions

Offline PWA for the Runcity game in Tbilisi: search of streets, toponyms and landmarks (ka/ru/lat)
with a query DSL, eponym categories, house numbers, crossings, name history and a finger-drawn
competition zone. Live: https://lbiryukov.github.io/quchebi/ (GitHub Pages, deployed by
`.github/workflows/pages.yml` on every push to `main` that touches `app/`).
Plan: `docs/plans/quchebi-plan.html`. Contracts: `docs/pack-format.md`, `docs/dsl.md`,
`docs/client-architecture.md`, `migrations/001_init.sql`.

## Layout
- `pipeline/` — Python data pipeline (uv). Stages write to `data/runcity.sqlite`; export writes the
  pack to `app/public/data/`. Logging via `pipeline.log.get_logger` → `logs/pipeline.log`.
  - `names/` name parsing, families, transliteration, house numbers (shared with JS via vectors)
  - `osm/` PBF import: areas, features, landmarks (`place*` kinds), feature_area, crossings, houses
  - `history/` former names: encyclopedia 2008 (AcadNusx PDF), ka/ru Wikipedia, OSM, addresses
  - `wikidata/`, `enrich/` eponyms: QIDs, rules, matching, facts, dossiers, LLM batches, merge, review
  - `export/`, `validate/` pack writer, invariants, golden SQL counts, coverage report
  - `tiles/` Protomaps basemap extract, glyphs, sprites → `app/public/tiles/`
- `app/` — static PWA (Vite 6, no framework). Logic in `app/src/{search,dsl,zone,store,log}`,
  UI in `app/src/{ui,map}`, service worker `app/src/sw.js`, e2e in `app/e2e/`.
- `tests/` — pytest (`tests/*.py`), node:test (`tests/js/*.test.mjs`), shared vectors
  (`tests/vectors/*.json`) that Python and JS must both satisfy, acceptance set of real Runcity
  riddles (`tests/acceptance/`).
- `.claude/agents/` — LLM subagents: `eponym-classifier`, `eponym-disambiguator`, `eponym-verifier`,
  `name-qa` (Haiku, classification only); `history-extractor` (Opus, extraction).
- `data/` (gitignored) — raw downloads, SQLite, LLM batches/results, build reports.
  `temp_scripts/prototypes/` — research prototypes (reference only).

## Rules
- Python: uv only, Python 3.13, `[tool.uv] exclude-newer = "2026-05-01T00:00:00Z"`. Run with `uv run`.
- Never create, copy or edit `.env` files.
- Feature/area ids change on every OSM rebuild; caches and manual decisions are keyed by
  `family.key`, QID or text. A full OSM rebuild deletes `name_history` (cascade): always run
  `history all` and `enrich prepare`/`merge` after it.
- Encyclopedia 2008, Wikipedia, Runcity riddles: store facts only, never full texts in the DB,
  the pack or the repository.
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
