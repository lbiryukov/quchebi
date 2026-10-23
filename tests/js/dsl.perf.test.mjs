// DSL performance on an index of real size (synthetic pack: ~5 600 features, ~8 700 crossings,
// ~40 000 houses, ~2 100 eponyms): typical queries must take < 20 ms in Node, with and without
// a zone. Also runs on the real pack (app/public/data) when it is big enough.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { buildIndex } from '../../app/src/search/index.js';
import { computeZoneMasks } from '../../app/src/zone/masks.js';
import { run, prepare } from '../../app/src/dsl/eval.js';
import { complete } from '../../app/src/dsl/complete.js';
import { syntheticPack } from '../fixtures/dsl_mini/synthetic.mjs';
import { QUIET_LOG } from '../fixtures/dsl_mini/load.mjs';

const LIMIT_MS = 20;
const TYPICAL = [
  'дом:3',
  'тип:улица* дом:3',
  'тип:переулок x:(тип:улица* кто:поэт)',
  'кто:хормейстер дом:24',
  'кто:наука дом:2',
  'кто:дирижёр было:ба',
  'было:(био:инженер)',
  'имя:/\\С{5}/',
  'x:(тип:сквер ба)~в дом:16',
  'x:(ბაგი)',
  'тип:тупик дом:нет в:место1',
  'кто:писатель пол:ж',
  'тип:улица -кто:персона',
  'семья:ბაგი',
  'чавч',
  'ვაჟ',
  'кто:царь | кто:полководец',
  'домов>30',
  'длина>1000 сорт:длина',
  'дом:3? тип:улица',
  'век:19 свой:нет',
  'рядом:(тип:метро)~300 тип:переулок*',
  'Galaktioni',
  'Majakovskij Javachishvili',
];
const ME = { lat: 41.72, lon: 44.79 };

function median(xs) { const s = [...xs].sort((a, b) => a - b); return s[Math.floor(s.length / 2)]; }

/**
 * Median of 7 runs per query. The dev machine is shared with other processes, so a query whose
 * median is over the limit is measured again (up to 3 rounds) and the best round counts.
 */
function timeQueries(index, ctx, queries) {
  const out = [];
  for (const q of queries) {
    let best = null;
    for (let round = 0; round < 3; round++) {
      const times = [];
      let res;
      for (let k = 0; k < 7; k++) {
        const t0 = performance.now();
        res = run(q, index, ctx);
        times.push(performance.now() - t0);
      }
      assert.ok(!res.error || /^неизвестн/.test(res.error.message), `${q}: ${res.error && res.error.message}`);
      const cur = { q, ms: median(times), min: Math.min(...times), count: res.count };
      if (!best || cur.ms < best.ms) best = cur;
      if (best.ms < LIMIT_MS / 2) break;
    }
    out.push(best);
  }
  return out;
}

const pack = syntheticPack();
const t0 = performance.now();
const index = buildIndex(pack, { log: QUIET_LOG });
const buildIndexMs = performance.now() - t0;
const t1 = performance.now();
const prepMs = prepare(index);
const prepWallMs = performance.now() - t1;
// zone ≈ 3 km² in the middle of the synthetic city
const zone = computeZoneMasks(index, [[41.71, 44.78], [41.73, 44.78], [41.73, 44.80], [41.71, 44.80]], { log: QUIET_LOG });

test('synthetic pack has real size', () => {
  assert.equal(index.n, 5600);
  assert.equal(index.cross.count, 8700);
  assert.ok(Object.values(pack.houses.h).reduce((s, r) => s + r.length, 0) >= 40000);
  console.log(`# buildIndex ${buildIndexMs.toFixed(1)} ms, DSL caches ${prepMs.toFixed(1)} ms (wall ${prepWallMs.toFixed(1)} ms)`);
  assert.ok(prepWallMs < 1000, `DSL cache build ${prepWallMs} ms`);
  const t2 = performance.now();
  run('Galaktioni', index, {});
  const fuzzyMs = performance.now() - t2;
  console.log(`# first fuzzy query (builds the token index) ${fuzzyMs.toFixed(1)} ms`);
  assert.ok(fuzzyMs < 1000, `fuzzy token index ${fuzzyMs} ms`);
});

test(`typical queries < ${LIMIT_MS} ms without a zone`, () => {
  const res = timeQueries(index, { me: ME }, TYPICAL);
  for (const r of res) console.log(`#   ${r.ms.toFixed(2).padStart(6)} ms  ${String(r.count).padStart(5)}  ${r.q}`);
  const slow = res.filter((r) => r.ms >= LIMIT_MS);
  assert.deepEqual(slow.map((r) => `${r.q}: ${r.ms.toFixed(1)} ms`), []);
});

test(`typical queries < ${LIMIT_MS} ms with a zone`, () => {
  const res = timeQueries(index, { zone, me: ME }, TYPICAL);
  for (const r of res) console.log(`#   ${r.ms.toFixed(2).padStart(6)} ms  ${String(r.count).padStart(5)}  ${r.q} (zone)`);
  const slow = res.filter((r) => r.ms >= LIMIT_MS);
  assert.deepEqual(slow.map((r) => `${r.q}: ${r.ms.toFixed(1)} ms`), []);
});

test('heavy queries stay interactive (best of 7 < 150 ms)', () => {
  const res = timeQueries(index, { me: ME }, ['рядом:(тип:улица)~200', 'тип:улица* сорт:расстояние', 'x:(x:(тип:улица*))', '-чав сорт:имя']);
  for (const r of res) console.log(`#   ${r.ms.toFixed(2).padStart(6)} ms  ${String(r.count).padStart(5)}  ${r.q}`);
  for (const r of res) assert.ok(r.min < 150, `${r.q}: ${r.min} ms`);
});

test(`autocomplete < ${LIMIT_MS} ms`, () => {
  for (const q of ['тип:', 'кто:', 'кто:по', 'в:', 'x:(тип:ул', 'ти', 'дом:1', 'век:']) {
    let best = Infinity;
    for (let round = 0; round < 3 && best >= LIMIT_MS / 2; round++) {
      const times = [];
      for (let k = 0; k < 5; k++) { const t = performance.now(); complete(q, q.length, index, { zone }); times.push(performance.now() - t); }
      best = Math.min(best, median(times));
    }
    console.log(`#   ${best.toFixed(2).padStart(6)} ms  complete ${q}`);
    assert.ok(best < LIMIT_MS, `${q}: ${best} ms`);
  }
});

const REAL = join(dirname(fileURLToPath(import.meta.url)), '..', '..', 'app', 'public', 'data');
const realCore = join(REAL, 'core.json');
const realBig = existsSync(realCore) && JSON.parse(readFileSync(realCore, 'utf8')).f.length > 1000;
test('typical queries on the real pack', { skip: realBig ? false : 'real pack not exported yet' }, () => {
  const p = {};
  for (const f of ['manifest', 'core', 'eponyms', 'geom', 'houses', 'areas']) {
    const file = join(REAL, `${f}.json`);
    if (existsSync(file)) p[f] = JSON.parse(readFileSync(file, 'utf8'));
  }
  const ix = buildIndex(p, { log: QUIET_LOG });
  prepare(ix);
  const res = timeQueries(ix, { me: ME }, TYPICAL);
  for (const r of res) console.log(`#   ${r.ms.toFixed(2).padStart(6)} ms  ${String(r.count).padStart(5)}  ${r.q} (real)`);
  const slow = res.filter((r) => r.ms >= LIMIT_MS);
  assert.deepEqual(slow.map((r) => `${r.q}: ${r.ms.toFixed(1)} ms`), []);
});
