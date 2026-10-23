# Quchebi — project instructions

Offline PWA for the Runcity game in Tbilisi: search of streets and toponyms (ka/ru/lat) with a
query DSL, eponym categories, house numbers, crossings and a finger-drawn competition zone.
Plan: `docs/plans/quchebi-plan.html`. Contracts: `docs/pack-format.md`, `docs/dsl.md`,
`docs/client-architecture.md`, `migrations/001_init.sql`.

## Layout
- `pipeline/` — Python data pipeline (uv). Stages write to `data/runcity.sqlite`; export writes the
  pack to `app/public/data/`. Logging via `pipeline.log.get_logger` → `logs/pipeline.log`.
- `app/` — static PWA (Vite 6, no framework). Logic in `app/src/{search,dsl,zone,store,log}`,
  UI in `app/src/{ui,map}`.
- `tests/` — pytest (`tests/*.py`), node:test (`tests/js/*.test.mjs`), shared vectors
  (`tests/vectors/*.json`) that Python and JS must both satisfy.
- `.claude/agents/` — classification subagents (Haiku) used by the enrichment stage.
- `data/` (gitignored) — raw downloads, SQLite, LLM batches/results. `temp_scripts/prototypes/` —
  research prototypes (reference only).

## Rules
- Python: uv only, Python 3.13, `[tool.uv] exclude-newer = "2026-05-01T00:00:00Z"`. Run with `uv run`.
- Never create, copy or edit `.env` files.
- Feature/area ids change on every OSM rebuild; caches and manual decisions are keyed by
  `family.key`, QID or text.
- Encyclopedia 2008 and Wikipedia: store facts only (years, former names, links), never full
  texts in the DB or the pack.
- Dates in user-facing text: DD/MM/YYYY. No emoji.
- Node is 22.11 on the dev machine: keep Vite 6.x (Vite 8 needs ≥22.12). `.npmrc` sets
  `legacy-peer-deps=true` (npm 10.9 peer-resolution bug).

## Commands (verified)
- `uv sync` — install Python deps
- `uv run pytest` — Python tests
- `cd app && npm install` — install JS deps
