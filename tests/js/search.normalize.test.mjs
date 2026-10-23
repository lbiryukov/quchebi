import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { skeleton, kaToRu, kaToLat, normRu, stripType, parseName, ordinalOf, editDistance, kaLower } from '../../app/src/search/normalize.js';

const V = JSON.parse(readFileSync(new URL('../vectors/translit.json', import.meta.url), 'utf8'));

test('skeleton vectors (shared with Python)', () => {
  for (const c of V.skeleton) assert.equal(skeleton(c.in), c.out, `skeleton(${c.in})`);
});

test('ka_to_ru vectors (shared with Python)', () => {
  for (const c of V.ka_to_ru) assert.equal(kaToRu(c.in), c.out, `kaToRu(${c.in})`);
});

test('skeleton edge cases', () => {
  assert.equal(skeleton(''), '');
  assert.equal(skeleton(null), '');
  assert.equal(skeleton('---'), '');
  assert.equal(skeleton('Ლ'), skeleton('ლ'), 'Mtavruli');
  assert.equal(skeleton('ДЖАНСУГ'), 'jansug');
  assert.equal(skeleton('Ёлка'), skeleton('елка'));
  assert.equal(skeleton('Фалиашвили'), skeleton('ფალიაშვილი'));
  assert.equal(skeleton('Важа-Пшавела'), skeleton('ვაჟა-ფშაველა'));
  assert.equal(skeleton('ჱ'), '', 'archaic letters are not in the skeleton table (Python parity)');
});

test('kaToLat national romanisation', () => {
  assert.equal(kaToLat('ჭავჭავაძე'), 'chavchavadze');
  assert.equal(kaToLat('ილია ჭავჭავაძის', true), 'Ilia Chavchavadzis');
  assert.equal(kaToLat('ქუჩა 26'), 'kucha 26');
  assert.equal(kaToLat('ᲛᲔᲤᲔ'), 'mepe');
  assert.equal(kaLower('ᲛᲔᲤᲔ Abc'), 'მეფე Abc');
});

test('kaToRu keeps non-Georgian characters and handles word-initial e', () => {
  assert.equal(kaToRu('ერეკლე II'), 'эрекле II');
  assert.equal(kaToRu('ჯანსუღ'), 'джансуг');
  assert.equal(kaToRu('Abc-ე'), 'Abc-э');
});

test('normRu', () => {
  assert.equal(normRu('  Ёлка   «Новая»  '), 'елка новая');
  assert.equal(normRu('генерал–полковник'), 'генерал-полковник');
  assert.equal(normRu(''), '');
});

test('stripType / parseName', () => {
  assert.deepEqual(parseName('ლადო ასათიანის II შესახვევი'), { base: 'ლადო ასათიანის', kind: 'შესახვევი', ord: 2 });
  assert.deepEqual(parseName('2-й переулок Ладо Асатиани'), { base: 'Ладо Асатиани', kind: 'переулок', ord: 2 });
  assert.deepEqual(parseName('Lado Asatiani 2nd Lane'), { base: 'Lado Asatiani', kind: 'lane', ord: 2 });
  assert.equal(stripType('улица Ираклия II'), 'Ираклия II', 'regnal number is not an ordinal');
  assert.equal(stripType('Lado Asatiani II Lane'), 'Lado Asatiani');
  assert.equal(stripType('Метехский мост'), 'Метехский');
  assert.equal(stripType('площадь 26 Мая'), '26 Мая');
  assert.equal(stripType('Lado Asatiani Dead End'), 'Lado Asatiani');
  assert.equal(stripType('Парк'), 'Парк', 'never strips everything');
  assert.equal(stripType('სოლოლაკის აღმართი'), 'სოლოლაკის');
});

test('ordinalOf and editDistance', () => {
  assert.equal(ordinalOf('III'), 3);
  assert.equal(ordinalOf('IV'), 4);
  assert.equal(ordinalOf('1-я'), 1);
  assert.equal(ordinalOf('3rd'), 3);
  assert.equal(ordinalOf('მე-2'), 2);
  assert.equal(ordinalOf('1-ლი'), 1);
  assert.equal(ordinalOf('abc'), 0);
  assert.equal(editDistance('рйон', 'район'), 1);
  assert.equal(editDistance('ab', 'ba'), 1, 'transposition');
  assert.equal(editDistance('kitten', 'sitting'), 3);
  assert.ok(editDistance('aaaaaa', 'b', 2) > 2);
});

test('skeleton performance: 40k names (sanity bound, generous for parallel test runs)', () => {
  const names = [];
  for (let i = 0; i < 10000; i++) names.push('ლადო ასათიანის II შესახვევი', '2-й переулок Ладо Асатиани', 'Lado Asatiani 2nd Lane', 'ცინამძღვრიშვილის ქუჩა');
  const t0 = performance.now();
  let s = 0;
  for (const nm of names) s += skeleton(nm).length;
  const ms = performance.now() - t0;
  assert.ok(s > 0);
  assert.ok(ms < 400, `took ${ms.toFixed(1)} ms`);
});
