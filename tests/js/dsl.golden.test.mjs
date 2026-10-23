// Golden queries (tests/vectors/dsl_golden.json): they all parse, evaluate without throwing,
// and — when the real pack and SQL counts exist — their counts without a zone match SQL.
//
// The real-pack comparison runs only if both files are present:
//   app/public/data/core.json (+ eponyms/houses/geom/areas.json), written by pipeline export
//   data/build/golden_counts.json, written by the export agent from SQL. Accepted shapes:
//     [{"query": "...", "count": 12}, ...] | {"queries": [{"query", "count"}]} | {"<query>": 12}
//   ("count" may also be called "sql_count" or "expected"; entries with null count are skipped).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { parse } from '../../app/src/dsl/parser.js';
import { run } from '../../app/src/dsl/eval.js';
import { buildIndex } from '../../app/src/search/index.js';
import { loadIndex, QUIET_LOG } from '../fixtures/dsl_mini/load.mjs';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..', '..');
const GOLDEN = JSON.parse(readFileSync(join(ROOT, 'tests', 'vectors', 'dsl_golden.json'), 'utf8'));
const PACK_DIR = join(ROOT, 'app', 'public', 'data');
const COUNTS = join(ROOT, 'data', 'build', 'golden_counts.json');

test('dsl_golden.json is a list of {query, note} and includes all 15 golden examples of docs/dsl.md', () => {
  assert.ok(Array.isArray(GOLDEN));
  for (const g of GOLDEN) {
    assert.equal(typeof g.query, 'string');
    assert.equal(typeof g.note, 'string');
  }
  const spec = readFileSync(join(ROOT, 'docs', 'dsl.md'), 'utf8');
  const block = spec.split('## Golden examples')[1].split('```')[1];
  const examples = block.split('\n').map((s) => s.trim()).filter(Boolean);
  assert.equal(examples.length, 15);
  const queries = new Set(GOLDEN.map((g) => g.query));
  for (const ex of examples) assert.ok(queries.has(ex), `missing golden example ${ex}`);
});

test('every golden query parses', () => {
  for (const g of GOLDEN) {
    const r = parse(g.query, { strict: true });
    assert.ok(!r.error, `${g.query}: ${r.error && r.error.message}`);
  }
});

test('every golden query evaluates on the fixture without throwing', () => {
  const index = loadIndex();
  for (const g of GOLDEN) {
    const r = run(g.query, index, {});
    assert.equal(typeof r.count, 'number', g.query);
    if (r.error) assert.ok(/^неизвестн/.test(r.error.message), `${g.query}: ${r.error.message}`);
  }
});

function readCounts(file) {
  const raw = JSON.parse(readFileSync(file, 'utf8'));
  const list = Array.isArray(raw) ? raw : Array.isArray(raw.queries) ? raw.queries
    : Object.entries(raw).filter(([, v]) => typeof v === 'number').map(([query, count]) => ({ query, count }));
  const out = new Map();
  for (const it of list) {
    const c = it.count ?? it.sql_count ?? it.expected;
    if (typeof it.query === 'string' && typeof c === 'number') out.set(it.query, c);
  }
  return out;
}

function loadRealPack() {
  const pack = {};
  for (const f of ['manifest', 'core', 'eponyms', 'geom', 'houses', 'areas']) {
    const p = join(PACK_DIR, `${f}.json`);
    if (existsSync(p)) pack[f] = JSON.parse(readFileSync(p, 'utf8'));
  }
  return pack;
}

const haveReal = existsSync(join(PACK_DIR, 'core.json')) && existsSync(COUNTS);
test('golden counts without a zone match SQL on the real pack', { skip: haveReal ? false : 'no app/public/data/core.json or data/build/golden_counts.json' }, () => {
  const counts = readCounts(COUNTS);
  assert.ok(counts.size > 0, 'golden_counts.json has no counts');
  const index = buildIndex(loadRealPack(), { log: QUIET_LOG });
  const mismatches = [];
  for (const [query, want] of counts) {
    const r = run(query, index, {});
    if (r.error) mismatches.push(`${query}: error ${r.error.message}`);
    else if (r.count !== want) mismatches.push(`${query}: dsl ${r.count} != sql ${want}`);
  }
  assert.deepEqual(mismatches, []);
});
