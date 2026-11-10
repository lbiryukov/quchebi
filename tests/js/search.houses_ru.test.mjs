// Moscow house numbers (packs with lang 'ru'): дом: parsing and row matching against the shared
// vectors tests/vectors/house_query_ru.json; Tbilisi parsing (lang 'ka') stays as it was.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { parseHouseQuery, houseRowMatches, matchHouses, corpTokens, normLetterRu } from '../../app/src/search/houses.js';
import { parseHouseValue, describeHouse } from '../../app/src/dsl/housequery.js';

const V = JSON.parse(readFileSync(new URL('../vectors/house_query_ru.json', import.meta.url), 'utf8'));

test('parse vectors (lang ru)', () => {
  for (const c of V.parse) {
    const q = parseHouseQuery(c.q, { lang: 'ru' });
    if (c.error) { assert.ok(q.error, `${c.q} must be an error`); continue; }
    assert.ok(!q.error, `${c.q}: ${q.error}`);
    assert.equal(q.kind, c.kind, c.q);
    for (const k of ['n', 'lit', 'sub', 'corp']) if (k in c) assert.equal(q[k], c[k], `${c.q}.${k}`);
  }
});

test('match vectors (lang ru)', () => {
  for (const c of V.match) {
    const q = parseHouseQuery(c.q, { lang: 'ru' });
    assert.ok(!q.error, `${c.q}: ${q.error}`);
    const got = V.rows.map((r, k) => (houseRowMatches(r, q) ? k : -1)).filter((k) => k >= 0);
    if (q.kind === 'range') {
      assert.deepEqual(V.rows.map((r, k) => (matchHouses([r], q) ? k : -1)).filter((k) => k >= 0), c.rows, c.q);
    } else assert.deepEqual(got, c.rows, c.q);
  }
});

test('helpers: building tokens and Cyrillic letters', () => {
  assert.deepEqual(corpTokens('к2с1'), ['к2', 'с1']);
  assert.deepEqual(corpTokens('корп.2 стр.1'), ['к2', 'с1']);
  assert.deepEqual(corpTokens(''), []);
  assert.equal(corpTokens('кв5'), null);
  assert.equal(normLetterRu('A'), 'а');
  assert.equal(normLetterRu('Б'), 'б');
  assert.equal(normLetterRu('ё'), 'е');
});

test('Tbilisi parsing is unchanged: letters go to Georgian, к2 is not a korpus', () => {
  assert.equal(parseHouseQuery('3а').lit, 'ა');
  assert.equal(parseHouseQuery('3а', { lang: 'ka' }).lit, 'ა');
  assert.ok(parseHouseQuery('12к2').error);
  assert.equal(parseHouseQuery('16 корп. 3').kind, 'corp');
});

test('DSL value and explanation', () => {
  assert.ok(parseHouseValue('12к2', 'ka').error);
  const r = parseHouseValue('12к2с1', 'ru');
  assert.ok(!r.error);
  assert.equal(describeHouse(r.q), 'с домом 12 корп. 2, стр. 1');
  assert.equal(describeHouse(parseHouseValue('12а', 'ru').q), 'с домом 12а');
  assert.match(parseHouseValue('12х1', 'ru').error, /12к2/);
});
