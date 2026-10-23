import { test } from 'node:test';
import assert from 'node:assert/strict';
import { encodeZone, decodeZone, tryDecodeZone, extractZoneCode, simplifyRing, ZONE_CODE_PREFIX } from '../../app/src/zone/code.js';
import { polygonAreaKm2, haversine } from '../../app/src/zone/geo.js';
import { blobZone } from '../fixtures/pack_mini/_src/synth.mjs';

// "Old town + Sololaki + Mtatsminda" style zone, 12 vertices
const ZONE12 = [[41.6975, 44.7902], [41.6990, 44.7985], [41.6962, 44.8061], [41.6930, 44.8105], [41.6896, 44.8129],
  [41.6862, 44.8121], [41.6849, 44.8077], [41.6856, 44.8013], [41.6869, 44.7957], [41.6893, 44.7899],
  [41.6921, 44.7861], [41.6952, 44.7866]];

test('round trip of a small zone is exact to 1e-5', () => {
  const code = encodeZone(ZONE12);
  assert.ok(code.startsWith(ZONE_CODE_PREFIX));
  assert.ok(code.length < 120, `code length ${code.length}`);
  const back = decodeZone(code);
  assert.equal(back.length, 12);
  back.forEach((p, i) => { assert.ok(Math.abs(p[0] - ZONE12[i][0]) < 1e-9); assert.ok(Math.abs(p[1] - ZONE12[i][1]) < 1e-9); });
  // closed input ring gives the same code
  assert.equal(encodeZone([...ZONE12, ZONE12[0]]), code);
  // rings input: only the outer ring is encoded
  assert.equal(encodeZone([ZONE12, ZONE12.slice(0, 4)]), code);
});

test('freehand zone is simplified to <= 60 vertices with little area change', () => {
  const free = blobZone(41.69, 44.80, 1200, 500);
  const s = simplifyRing(free, 60);
  assert.ok(s.length <= 60 && s.length >= 3, `vertices ${s.length}`);
  const code = encodeZone(free);
  const back = decodeZone(code);
  assert.ok(back.length <= 60);
  const a0 = polygonAreaKm2(free), a1 = polygonAreaKm2(back);
  assert.ok(Math.abs(a1 - a0) / a0 < 0.03, `area ${a0} -> ${a1}`);
  assert.ok(code.length < 420, `code length ${code.length}`);
});

test('decoding tolerates messenger noise', () => {
  const code = encodeZone(ZONE12);
  const body = code.slice(3);
  const variants = [
    code,
    `  ${code}  `,
    code.slice(0, 20) + '\n' + code.slice(20, 41) + '\r\n  ' + code.slice(41),
    code.slice(0, 10) + '​' + code.slice(10) + '﻿',
    `Зона на завтра: ${code}`,
    `«${code}»`,
    `"${code}"`,
    `Код зоны:\n${code}\nдо встречи!`,
    'z1.' + body,
    code.slice(0, 15) + ' ' + code.slice(15),
  ];
  for (const v of variants) {
    const back = decodeZone(v);
    assert.equal(back.length, 12, JSON.stringify(v));
    assert.ok(haversine(back[0], ZONE12[0]) < 1);
  }
  assert.equal(extractZoneCode('nothing here'), null);
});

test('invalid codes throw Russian errors; tryDecodeZone returns null', () => {
  assert.throws(() => decodeZone('hello'), /Z1/);
  assert.throws(() => decodeZone('Z1.'), /пустой/);
  assert.throws(() => decodeZone('Z1.wkn}FkhmpG'), /меньше трёх/);
  assert.throws(() => decodeZone('Z1.' + encodeZone(ZONE12).slice(3, -1)), /повреждён|меньше/);
  const far = encodeZone([[41.69, 44.80], [41.70, 44.80], [-30, 120]]);
  assert.throws(() => decodeZone(far), /далеко/);
  assert.equal(tryDecodeZone('garbage'), null);
  assert.ok(Array.isArray(tryDecodeZone(encodeZone(ZONE12))));
  assert.throws(() => encodeZone([[1, 1], [1, 2]]), /три вершины/);
});
