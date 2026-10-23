import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import {
  decodePolyline, encodePolyline, haversine, bearing, bearingSector, compassBit, COMPASS, directionMask,
  pointInPolygon, clipLengthInside, PreparedPolygon, polygonAreaKm2, circlePolygon, PointGrid, densify,
  lineLength, pointLineDistance, bboxOf, toRings, isClosedRing,
} from '../../app/src/zone/geo.js';

const V = JSON.parse(readFileSync(new URL('../vectors/polyline.json', import.meta.url), 'utf8'));
const near = (a, b, eps, msg) => assert.ok(Math.abs(a - b) <= eps, `${msg || ''} ${a} vs ${b} (eps ${eps})`);

// 1 km x 1 km square around Sololaki (approximate: 1 km = 1/111.195 deg lat)
const LAT = 41.69, LON = 44.80;
const dLat = 1000 / 111195.08, dLon = 1000 / (111195.08 * Math.cos(LAT * Math.PI / 180));
const SQUARE = [[LAT, LON], [LAT + dLat, LON], [LAT + dLat, LON + dLon], [LAT, LON + dLon]];

test('polyline vectors: decode and encode', () => {
  for (const c of V.cases) {
    const pts = decodePolyline(c.encoded, V.precision);
    assert.equal(pts.length, c.points.length, c.name);
    pts.forEach((p, i) => { near(p[0], c.points[i][0], 1e-9, c.name); near(p[1], c.points[i][1], 1e-9, c.name); });
    assert.equal(encodePolyline(c.points, V.precision), c.encoded, c.name);
  }
  assert.deepEqual(decodePolyline(''), []);
  assert.throws(() => decodePolyline('_p~iF~ps|U_ulLnnqC_mqNvxq`'), /truncated/);
  assert.throws(() => decodePolyline('ab cd'), /bad char/);
});

test('haversine', () => {
  near(haversine([0, 0], [1, 0]), 111195.08, 0.5, '1 deg lat');
  near(haversine(LAT, LON, LAT + dLat, LON), 1000, 0.01);
  near(haversine({ lat: LAT, lon: LON }, { lat: LAT, lon: LON + dLon }), 1000, 1);
  near(haversine([41.70095, 44.79381], [41.69947, 44.79689]), 304.6, 1.5, 'Rustaveli segment');
  assert.equal(haversine([LAT, LON], [LAT, LON]), 0);
});

test('bearings and sectors', () => {
  near(bearing([LAT, LON], [LAT + 0.01, LON]), 0, 1e-6);
  near(bearing([LAT, LON], [LAT, LON + 0.01]), 90, 0.01);
  near(bearing([LAT, LON], [LAT - 0.01, LON]), 180, 1e-6);
  near(bearing([LAT, LON], [LAT, LON - 0.01]), 270, 0.01);
  assert.equal(bearingSector(0), 0);
  assert.equal(bearingSector(350), 0);
  assert.equal(bearingSector(22), 0);
  assert.equal(bearingSector(23), 1);
  assert.equal(bearingSector(90), 2);
  assert.equal(bearingSector(-90), 6);
  assert.equal(compassBit('в'), 2);
  assert.equal(compassBit('СЗ'), 7);
  assert.equal(compassBit('sw'), 5);
  assert.equal(compassBit('x'), -1);
  assert.equal(Object.keys(COMPASS).length, 16);
});

test('directionMask: through street and T-junction', () => {
  const through = [[LAT, LON - 0.002], [LAT, LON + 0.002]]; // W-E
  assert.equal(directionMask([through], [LAT, LON]), (1 << 2) | (1 << 6));
  const stub = [[LAT, LON], [LAT + 0.002, LON]]; // starts at the crossing, goes north
  assert.equal(directionMask([stub], [LAT, LON]), 1 << 0);
  assert.equal(directionMask([stub], [LAT + 0.01, LON]), 0, 'point far from the line');
  const tiny = [[LAT - 0.00002, LON], [LAT + 0.002, LON]]; // 2 m stub to the south is ignored
  assert.equal(directionMask([tiny], [LAT, LON]), 1 << 0);
  const ring = [[LAT, LON], [LAT + 0.002, LON], [LAT + 0.002, LON + 0.002], [LAT, LON + 0.002], [LAT, LON]];
  assert.equal(directionMask([ring], [LAT + 0.001, LON]), (1 << 0) | (1 << 4), 'ring side: both ways');
});

test('pointInPolygon with holes and multipolygon parts', () => {
  assert.ok(pointInPolygon([LAT + dLat / 2, LON + dLon / 2], SQUARE));
  assert.ok(!pointInPolygon([LAT - 0.001, LON + dLon / 2], SQUARE));
  const hole = [[LAT + dLat * 0.4, LON + dLon * 0.4], [LAT + dLat * 0.6, LON + dLon * 0.4], [LAT + dLat * 0.6, LON + dLon * 0.6], [LAT + dLat * 0.4, LON + dLon * 0.6]];
  const withHole = [SQUARE, hole];
  assert.ok(!pointInPolygon([LAT + dLat / 2, LON + dLon / 2], withHole), 'in the hole');
  assert.ok(pointInPolygon([LAT + dLat * 0.2, LON + dLon * 0.2], withHole));
  const far = SQUARE.map(([a, b]) => [a + 0.1, b]);
  assert.ok(pointInPolygon([LAT + 0.1 + dLat / 2, LON + dLon / 2], [SQUARE, far]), 'second outer ring');
  const closed = [...SQUARE, SQUARE[0]];
  assert.ok(pointInPolygon([LAT + dLat / 2, LON + dLon / 2], closed), 'closed ring accepted');
  assert.ok(!new PreparedPolygon([]).contains(LAT, LON), 'empty polygon');
});

test('clipLengthInside: cut by edges, midpoints tested', () => {
  const y = LAT + dLat / 2;
  // horizontal line from 500 m west of the square to 500 m east: 1000 m of 2000 m inside
  const line = [[y, LON - dLon / 2], [y, LON + dLon * 1.5]];
  const r = clipLengthInside(line, SQUARE);
  near(r.total, 2000, 1);
  near(r.inside, 1000, 1);
  near(r.share, 0.5, 0.001);
  // through the hole: 1000 - 200 = 800 m
  const hole = [[LAT + dLat * 0.4, LON + dLon * 0.4], [LAT + dLat * 0.6, LON + dLon * 0.4], [LAT + dLat * 0.6, LON + dLon * 0.6], [LAT + dLat * 0.4, LON + dLon * 0.6]];
  near(clipLengthInside(line, [SQUARE, hole]).inside, 800, 1);
  // multi-vertex line entering and leaving twice (concave zone)
  const U = [[LAT, LON], [LAT + dLat, LON], [LAT + dLat, LON + dLon * 0.4], [LAT + dLat * 0.3, LON + dLon * 0.4],
    [LAT + dLat * 0.3, LON + dLon * 0.6], [LAT + dLat, LON + dLon * 0.6], [LAT + dLat, LON + dLon], [LAT, LON + dLon]];
  const y2 = LAT + dLat * 0.5;
  near(clipLengthInside([[y2, LON - dLon], [y2, LON + dLon * 0.5], [y2, LON + dLon * 2]], U).inside, 800, 1);
  // several parts
  const r2 = clipLengthInside([line, line], SQUARE);
  near(r2.inside, 2000, 2);
  // fully outside, fully inside
  assert.equal(clipLengthInside([[LAT - 0.01, LON], [LAT - 0.02, LON]], SQUARE).inside, 0);
  const inside = [[LAT + dLat * 0.1, LON + dLon * 0.1], [LAT + dLat * 0.9, LON + dLon * 0.1]];
  near(clipLengthInside(inside, SQUARE).share, 1, 1e-9);
  // line along the border: either 0 or full, never NaN
  const border = clipLengthInside([[LAT, LON], [LAT, LON + dLon]], SQUARE);
  assert.ok(Number.isFinite(border.inside));
});

test('polygonAreaKm2 / circlePolygon', () => {
  near(polygonAreaKm2(SQUARE), 1, 0.002);
  const hole = [[LAT + dLat * 0.4, LON + dLon * 0.4], [LAT + dLat * 0.6, LON + dLon * 0.4], [LAT + dLat * 0.6, LON + dLon * 0.6], [LAT + dLat * 0.4, LON + dLon * 0.6]];
  near(polygonAreaKm2([SQUARE, hole]), 0.96, 0.003);
  near(polygonAreaKm2([[SQUARE, hole], [SQUARE.map(([a, b]) => [a + 0.1, b])]]), 1.96, 0.01, 'multipolygon');
  const c = circlePolygon([LAT, LON], 300);
  assert.ok(isClosedRing(c));
  near(polygonAreaKm2(c), Math.PI * 0.09, 0.006);
  c.slice(0, -1).forEach((p) => near(haversine(p, [LAT, LON]), 300, 0.5));
  assert.equal(polygonAreaKm2([]), 0);
});

test('lineLength, densify, distances, bbox, toRings', () => {
  const l = [[LAT, LON], [LAT + dLat, LON]];
  near(lineLength(l), 1000, 0.01);
  const d = densify(l, 25);
  assert.ok(d.length === 41 || d.length === 42, String(d.length));
  for (let i = 0; i + 1 < d.length; i++) assert.ok(haversine(d[i], d[i + 1]) <= 25.01);
  near(pointLineDistance([LAT + dLat / 2, LON + dLon / 10], l), 100, 0.2);
  near(pointLineDistance([LAT + dLat * 2, LON], l), 1000, 0.5, 'beyond the end');
  assert.deepEqual(bboxOf(SQUARE), [LAT, LON, LAT + dLat, LON + dLon]);
  assert.equal(toRings(SQUARE).length, 1);
  assert.equal(toRings([SQUARE, SQUARE]).length, 2);
});

test('PointGrid equals brute force', () => {
  let s = 12345;
  const rnd = () => { s = (s * 1103515245 + 12345) & 0x7fffffff; return s / 0x7fffffff; };
  const pts = Array.from({ length: 3000 }, () => [41.68 + rnd() * 0.04, 44.78 + rnd() * 0.05]);
  const g = new PointGrid(100);
  pts.forEach((p, i) => g.add(p[0], p[1], i));
  g.build();
  assert.equal(g.size, 3000);
  for (let q = 0; q < 30; q++) {
    const c = [41.68 + rnd() * 0.04, 44.78 + rnd() * 0.05];
    const r = 50 + rnd() * 400;
    const got = new Set();
    g.forEachWithin(c[0], c[1], r, (id, dist) => { got.add(id); near(dist, haversine(c, pts[id]), Math.max(0.5, dist * 0.002)); });
    const exp = new Set(pts.map((p, i) => [p, i]).filter(([p]) => haversine(c, p) <= r * 0.999).map(([, i]) => i));
    for (const i of exp) assert.ok(got.has(i), `missing ${i}`);
    for (const i of got) assert.ok(haversine(c, pts[i]) <= r * 1.002, `extra ${i}`);
  }
  assert.equal(g.idsWithin([[0, 0]], 100).size, 0);
  assert.equal(new PointGrid().build().size, 0);
});
