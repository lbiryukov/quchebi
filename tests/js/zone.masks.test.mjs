import { test } from 'node:test';
import assert from 'node:assert/strict';
import { buildIndex } from '../../app/src/search/index.js';
import { computeZoneMasks, checkZonePolygon } from '../../app/src/zone/masks.js';
import { pointInPolygon } from '../../app/src/zone/geo.js';
import { loadMiniPack } from '../fixtures/pack_mini/_src/load.mjs';
import { makeSyntheticPack, blobZone } from '../fixtures/pack_mini/_src/synth.mjs';

const arr = (b) => Array.from(b.toArray());
const idx = buildIndex(loadMiniPack());
// Zone over Sololaki: west edge cuts the Sololaki ascent (18), east edge cuts Asatiani (0) and Tabidze (7)
const ZONE = [[41.6878, 44.7928], [41.6932, 44.7928], [41.6932, 44.7990], [41.6878, 44.7990]];

test('default / touch / whole rules on pack_mini, including edge streets', () => {
  const m = computeZoneMasks(idx, ZONE);
  assert.deepEqual(arr(m.featDefault), [0, 1, 2, 7, 13, 14, 16, 19]);
  assert.deepEqual(arr(m.featTouch), [0, 1, 2, 7, 13, 14, 16, 18, 19], 'ascent only touches (about 21 m inside)');
  assert.deepEqual(arr(m.featWhole), [1, 2, 16, 19]);
  assert.equal(m.featRule, m.featDefault);
  assert.equal(m.counts.features, 8);
  assert.equal(m.counts.touch, 9);
  assert.equal(m.counts.whole, 4);
  // lengths inside
  assert.ok(m.lenInside[18] > 15 && m.lenInside[18] < 30, `ascent ${m.lenInside[18]}`);
  assert.ok(m.lenInside[0] > 450 && m.lenInside[0] < 560, `Asatiani ${m.lenInside[0]}`);
  assert.ok(m.lenInside[7] > 200 && m.lenInside[7] < 340, `Tabidze ${m.lenInside[7]} (>= 30 m, < 50%)`);
  assert.ok(m.lenInside[7] < 0.5 * idx.len[7]);
  assert.ok(Math.abs(m.lenInside[1] - idx.len[1]) < 1);
  assert.equal(m.lenInside[3], 0);
  assert.ok(m.areaKm2 > 0.2 && m.areaKm2 < 0.4, `area ${m.areaKm2}`);
  assert.deepEqual(m.bbox, [41.6878, 44.7928, 41.6932, 44.799]);
  assert.ok(m.contains(41.69, 44.795) && !m.contains(41.69, 44.80));
  assert.equal(m.forRule('whole'), m.featWhole);
});

test('crossings and houses inside the zone', () => {
  const m = computeZoneMasks(idx, ZONE);
  const inside = [];
  m.crossingIn.forEach((v, t) => { if (v) inside.push(t); });
  assert.deepEqual(inside, [0, 1, 4, 5, 6, 23]);
  assert.equal(m.counts.crossings, 6);
  // houses: compare with brute force
  let brute = 0;
  for (let i = 0; i < idx.n; i++) for (const r of idx.houses(i)) if (pointInPolygon([r[4] / 1e5, r[5] / 1e5], ZONE)) brute++;
  assert.equal(m.counts.houses, brute);
  const rows = idx.houses(0);
  assert.equal(rows[10][0], '12');
  assert.equal(m.houseIn(0, 10), false, 'house 12 is east of the zone');
  assert.equal(m.houseIn(0, 0), true);
  assert.equal(m.houseIn(0, rows[0]), true, 'row array accepted');
  assert.equal(m.housesInside(0).length, 10);
  assert.equal(m.placeHouseIn(2, 0), false);
});

test('zone with a hole and closed ring input', () => {
  const hole = [[41.6898, 44.7936], [41.6920, 44.7936], [41.6920, 44.7944], [41.6898, 44.7944]];
  const m = computeZoneMasks(idx, [[...ZONE, ZONE[0]], hole]);
  assert.ok(!m.featTouch.has(1), 'lane I lies in the hole');
  assert.equal(m.lenInside[1], 0);
  assert.ok(m.featDefault.has(0));
  assert.equal(m.crossingIn[0], 0, 'Asatiani x lane I crossing is in the hole');
});

test('polygon features intersect; streets ending inside a tiny zone only touch', () => {
  const tiny = [[41.6932, 44.8010], [41.6936, 44.8010], [41.6936, 44.8016], [41.6932, 44.8016]];
  const m = computeZoneMasks(idx, tiny, { rule: 'touch' });
  assert.ok(m.featDefault.has(6), 'zone inside Freedom Square polygon');
  assert.ok(m.featDefault.has(13), 'district polygon');
  assert.ok(!m.featTouch.has(12), 'metro point just outside');
  for (const i of [5, 7, 8, 24]) {
    assert.ok(m.featTouch.has(i), `${idx.displayName(i)} touches`);
    assert.ok(!m.featDefault.has(i), `${idx.displayName(i)} is not "in zone" by the default rule`);
  }
  assert.equal(m.rule, 'touch');
  assert.equal(m.featRule, m.featTouch);
  assert.equal(m.counts.features, m.featTouch.count());
});

test('works before geometry (representative points) and refreshes houses later', async () => {
  const pack = loadMiniPack({ late: false });
  const ix = buildIndex(pack);
  const m = computeZoneMasks(ix, ZONE);
  assert.equal(m.withGeom, false);
  assert.equal(m.counts.houses, null);
  for (const i of [1, 2, 19]) assert.ok(m.featDefault.has(i));
  await pack.finish();
  await ix.ready;
  m.refreshHouses();
  assert.ok(m.counts.houses > 0);
  const m2 = m.recompute();
  assert.equal(m2.withGeom, true);
  assert.deepEqual(arr(m2.featDefault), [0, 1, 2, 7, 13, 14, 16, 19]);
});

test('checkZonePolygon and invalid input', () => {
  assert.equal(checkZonePolygon(ZONE).ok, true);
  assert.equal(checkZonePolygon(ZONE).vertices, 4);
  assert.equal(checkZonePolygon([[1, 1], [1, 2]]).ok, false);
  const tiny = [[41.6932, 44.8010], [41.6933, 44.8010], [41.6933, 44.8011]];
  assert.equal(checkZonePolygon(tiny).warnings.length, 1);
  assert.throws(() => computeZoneMasks(idx, [[1, 1]]), /3 vertices/);
});

test('benchmark: 120-vertex zone over a pack of real size < 100 ms', () => {
  const pack = makeSyntheticPack();
  const ix = buildIndex(pack);
  let vertices = 0;
  const t0 = performance.now();
  for (let i = 0; i < ix.n; i++) for (const p of ix.geometry(i)) vertices += p.length;
  const decodeMs = performance.now() - t0;
  assert.ok(vertices > 80000 && vertices < 100000, `vertices ${vertices}`);
  const zone = blobZone(41.70, 44.80, 1500, 120);
  const city = blobZone(41.725, 44.80, 12000, 120);
  const time = (z) => {
    const ts = [];
    let m;
    for (let r = 0; r < 7; r++) { const t = performance.now(); m = computeZoneMasks(ix, z); ts.push(performance.now() - t); }
    ts.sort((a, b) => a - b);
    return { ms: ts[0], median: ts[3], m }; // min = uncontended cost (tests run in parallel)
  };
  const a = time(zone), b = time(city);
  console.log(`# masks: decode ${decodeMs.toFixed(1)} ms once for ${vertices} vertices; zone 7 km2 min ${a.ms.toFixed(1)} / median ` +
    `${a.median.toFixed(1)} ms (${a.m.counts.features} features, ${a.m.counts.houses} houses); whole city min ${b.ms.toFixed(1)} / ` +
    `median ${b.median.toFixed(1)} ms (${b.m.counts.features} features)`);
  assert.ok(a.ms < 100, `zone masks ${a.ms.toFixed(1)} ms`);
  assert.ok(b.ms < 100, `city masks ${b.ms.toFixed(1)} ms`);
  assert.ok(b.m.counts.features > 4000);
});
