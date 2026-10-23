import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { parseHouse, parseHouseQuery, matchHouses, probable, houseRowMatches, houseMatchDetail, rangeContains, normLetter, sortHouseRows } from '../../app/src/search/houses.js';
import { readMini } from '../fixtures/pack_mini/_src/load.mjs';

const V = JSON.parse(readFileSync(new URL('../vectors/house_numbers.json', import.meta.url), 'utf8'));
const H = readMini('houses.json').h;
const rowsOf = (i) => H[String(i)] || [];
const FEATURES = Object.keys(H).map(Number);
const match = (value, inside) => {
  const q = parseHouseQuery(value);
  assert.ok(!q.error, `query ${value}: ${q.error}`);
  return FEATURES.filter((i) => matchHouses(rowsOf(i), q, inside)).sort((a, b) => a - b);
};

test('parseHouse vectors (shared with the pipeline)', () => {
  for (const c of V.cases) assert.deepEqual(parseHouse(c.raw), c.parts, `parseHouse(${JSON.stringify(c.raw)})`);
});

test('normLetter maps Latin/Cyrillic/Mtavruli to Georgian', () => {
  assert.equal(normLetter('a'), 'ა');
  assert.equal(normLetter('А'), 'ა');
  assert.equal(normLetter('ბ'), 'ბ');
  assert.equal(normLetter('в'), 'ვ');
  assert.equal(normLetter('Ა'), 'ა');
  assert.equal(normLetter('x'), 'x');
});

test('parseHouseQuery', () => {
  assert.deepEqual(parseHouseQuery('3'), { n: 3, lit: '', sub: '', corp: '', probable: false, kind: 'any' });
  assert.equal(parseHouseQuery('=3').kind, 'exact');
  for (const v of ['3а', '3ა', '3a', '3A']) assert.deepEqual([parseHouseQuery(v).kind, parseHouseQuery(v).lit], ['lit', 'ა'], v);
  assert.deepEqual([parseHouseQuery('3/16').kind, parseHouseQuery('3/16').sub], ['sub', '16']);
  assert.deepEqual(parseHouseQuery('1-9'), { kind: 'range', from: 1, to: 9, probable: false });
  assert.equal(parseHouseQuery('3?').probable, true);
  assert.equal(parseHouseQuery(' 3 ? ').probable, true);
  assert.equal(parseHouseQuery('есть').kind, 'has');
  assert.equal(parseHouseQuery('нет').kind, 'none');
  assert.equal(parseHouseQuery('16 корп. 3').kind, 'corp');
  assert.ok(parseHouseQuery('abc').error);
  assert.ok(parseHouseQuery('').error);
  assert.ok(parseHouseQuery('=1-9').error);
});

test('rangeContains respects the side of the street', () => {
  assert.ok(rangeContains(3, 5, 3) && rangeContains(3, 5, 5));
  assert.ok(!rangeContains(3, 5, 4), '3-5 is the odd side');
  assert.ok(rangeContains(2, 5, 3), 'mixed parity covers all');
  assert.ok(!rangeContains(3, 5, 7));
});

test('дом:3 / =3 / 3а / 3/16 / 1-9 / 4 on pack_mini', () => {
  // 0 Asatiani (3, 3ა, 3/16), 8 Apkhazi (3), 9 Erekle II (3ა/1), 17 Gorgasali (3), 19 Zubalashvili (3-5), 22 Besiki (3), 24 Pushkin (3)
  assert.deepEqual(match('3'), [0, 8, 9, 17, 19, 22, 24]);
  assert.deepEqual(match('=3'), [0, 8, 17, 22, 24]);
  assert.deepEqual(match('3а'), [0, 9]);
  assert.deepEqual(match('3ა'), [0, 9]);
  assert.deepEqual(match('3/16'), [0]);
  assert.deepEqual(match('3ა/1'), [9]);
  assert.deepEqual(match('4'), [0, 2, 7]);
  assert.deepEqual(match('5'), [0, 7, 17, 19, 24]);
  assert.deepEqual(match('16 корп. 3'), [8]);
  assert.deepEqual(match('29'), [8], 'list "27;29" gives two rows');
  assert.deepEqual(match('30-40'), [5, 8, 9]);
});

test('дом:3? adds features where 3 is probably there', () => {
  // 7 Tabidze has 1 and 5, 16 Gabashvili has 1 and 7; 5 Rustaveli has odd 13 and 37 only (no number below 3)
  assert.deepEqual(match('3?'), [0, 7, 8, 9, 16, 17, 19, 22, 24]);
  assert.ok(probable(rowsOf(16), 5));
  assert.ok(!probable(rowsOf(5), 3));
  assert.ok(!probable(rowsOf(0), 3), 'present -> not "probable"');
  assert.ok(!probable(rowsOf(5), 11), 'no odd number below 11');
  assert.ok(probable(rowsOf(5), 21), 'odd 13 below and 37 above');
});

test('has / none and zone restriction', () => {
  assert.equal(matchHouses([], parseHouseQuery('нет')), true);
  assert.equal(matchHouses(rowsOf(0), parseHouseQuery('есть')), true);
  const westOf = (lon) => (row) => row[5] / 1e5 < lon;
  // only rows of Asatiani west of 44.7945: numbers 1, 2 (and nothing else)
  const inside = westOf(44.7945);
  const q3 = parseHouseQuery('3');
  assert.equal(matchHouses(rowsOf(0), q3, inside), false);
  assert.equal(matchHouses(rowsOf(0), parseHouseQuery('1'), inside), true);
  assert.equal(matchHouses(rowsOf(0), parseHouseQuery('есть'), () => false), false);
  assert.equal(matchHouses(rowsOf(0), parseHouseQuery('нет'), () => false), true);
});

test('houseMatchDetail and houseRowMatches', () => {
  const d = houseMatchDetail(rowsOf(0), parseHouseQuery('3'));
  assert.deepEqual(d.matched.map((k) => rowsOf(0)[k][0]), ['3', '3ა', '3/16']);
  assert.equal(d.probable, false);
  const p = houseMatchDetail(rowsOf(7), parseHouseQuery('3?'));
  assert.deepEqual(p, { matched: [], probable: true });
  assert.ok(houseRowMatches(['3-5', 3, '', '', 0, 0, 5], parseHouseQuery('5')));
  assert.ok(!houseRowMatches(['3-5', 3, '', '', 0, 0, 5], parseHouseQuery('=3')));
  assert.ok(houseRowMatches(['16 კორპ. 3', 16, '', '', 0, 0, null, '3'], parseHouseQuery('16')));
});

test('sortHouseRows', () => {
  const s = sortHouseRows(rowsOf(0)).map((r) => r[0]);
  assert.deepEqual(s.slice(0, 5), ['1', '2', '3', '3/16', '3ა']);
});
