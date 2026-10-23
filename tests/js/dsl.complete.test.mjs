// complete(): keys by prefix, values of enumerated keys with counts in the zone, recursion
// into subqueries, house-number forms, replacement ranges.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { loadIndex, QUIET_LOG } from '../fixtures/dsl_mini/load.mjs';
import { computeZoneMasks } from '../../app/src/zone/masks.js';
import { complete, contextAt } from '../../app/src/dsl/complete.js';
import { run } from '../../app/src/dsl/eval.js';

const index = loadIndex();
const zone = computeZoneMasks(index, [[41.6880, 44.7960], [41.6960, 44.7960], [41.6960, 44.8030], [41.6880, 44.8030]], { log: QUIET_LOG });
const at = (text, ctx = {}, cursor = text.length) => complete(text, cursor, index, ctx);
const labels = (s) => s.map((x) => x.label);
const inserts = (s) => s.map((x) => x.insert);

test('keys by prefix with descriptions and replacement range', () => {
  const s = at('ти');
  assert.deepEqual(inserts(s), ['тип:']);
  assert.equal(s[0].from, 0);
  assert.equal(s[0].to, 2);
  assert.ok(s[0].detail.length > 0);
  assert.deepEqual(inserts(at('д')), ['дом:', 'домов:', 'длина:', 'd:'].filter((x) => inserts(at('д')).includes(x)));
  assert.ok(inserts(at('д')).includes('дом:') && inserts(at('д')).includes('домов:') && inserts(at('д')).includes('длина:'));
  assert.equal(at('').length, 19, 'all keys on empty input');
  assert.deepEqual(inserts(at('n')), ['n:', 'near:', 'no:']);
  assert.deepEqual(labels(at('n')), ['n: (имя)', 'near: (рядом)', 'no: (№)']);
  assert.deepEqual(at('qqq'), []);
});

test('keys after operators, parentheses and in the middle of the text', () => {
  assert.deepEqual(inserts(at('кто:царь | к')), ['кто:']);
  assert.deepEqual(inserts(at('тип:улица -к')), ['кто:']);
  assert.deepEqual(inserts(at('(ти')), ['тип:']);
  const s = complete('ти кто:поэт', 2, index, {});
  assert.deepEqual(inserts(s), ['тип:']);
  assert.equal(s[0].to, 2);
  const mid = complete('тип:пер кто:поэт', 7, index, {});
  assert.equal(mid[0].from, 4);
  assert.equal(mid[0].to, 7);
  const inside = complete('тип:переулок кто:поэт', 6, index, {});
  assert.equal(inside[0].to, 12, 'word under the cursor is replaced whole');
});

test('kind values with counts; groups; current zone', () => {
  const s = at('тип:пер');
  assert.deepEqual(inserts(s), ['переулок*', 'переулок']);
  assert.equal(s[0].detail, String(run('тип:переулок*', index, {}).count));
  assert.equal(s[1].detail, String(run('тип:переулок зона:выкл', index, {}).count));
  const z = at('тип:пер', { zone });
  assert.equal(z[1].detail, String(run('тип:переулок', index, { zone }).count));
  assert.notEqual(z[1].detail, s[1].detail);
  assert.ok(inserts(at('тип:')).includes('улица*'));
  assert.deepEqual(inserts(at('тип:переулок,т')), ['тупик', 'тоннель']);
  assert.ok(inserts(at('тип:переулок, ')).length > 0, 'space after a comma');
  assert.equal(at('тип:переулок,т')[0].from, 13);
});

test('кто: values: taxonomy labels first, duplicates collapsed, occupations by prefix', () => {
  const s = at('кто:по');
  assert.equal(s[0].insert, 'поэт');
  assert.ok(!inserts(s).includes('поэтесса'), 'alias with the same meaning is collapsed');
  assert.equal(s[0].detail, String(run('кто:поэт', index, {}).count));
  assert.deepEqual(inserts(at('кто:хор')), ['хормейстер']);
  assert.equal(at('кто:хор')[0].detail, '1');
  assert.ok(labels(at('кто:лит')).includes('литература (вся группа)'));
  const multi = at('кто:общ');
  assert.ok(multi.some((x) => x.insert === '"общественный деятель"'), 'values with spaces are quoted');
});

test('area values', () => {
  assert.deepEqual(inserts(at('в:сол')), ['сололаки']);
  assert.equal(at('в:сол')[0].label, 'Сололаки');
  assert.deepEqual(inserts(at('район:')).sort(), ['исани', 'крцаниси', 'мтацминда']);
  assert.equal(at('район:мтац')[0].detail, String(run('район:мтацминда', index, {}).count));
  assert.ok(labels(at('в:')).includes('Мтацминда (район)'));
});

test('fixed values: пол, свой, зона, сорт, век, №', () => {
  assert.deepEqual(inserts(at('пол:')), ['м', 'ж']);
  assert.equal(at('пол:ж')[0].detail, String(run('пол:ж', index, {}).count));
  assert.deepEqual(inserts(at('свой:')).sort(), ['да', 'нет']);
  const zs = at('зона:', { zone });
  assert.deepEqual(inserts(zs).sort(), ['вкл', 'выкл', 'задевает', 'целиком']);
  assert.equal(zs[0].insert, 'выкл', 'sorted by count: the whole city first');
  assert.equal(zs.find((x) => x.insert === 'целиком').detail, String(zone.featWhole.count()));
  assert.equal(zs.find((x) => x.insert === 'выкл').detail, String(index.n));
  assert.deepEqual(inserts(at('сорт:р')), ['расстояние']);
  const v = at('век:1');
  assert.ok(inserts(v).includes('19'));
  assert.equal(v.find((x) => x.insert === '12').detail, String(run('век:12', index, {}).count));
  assert.deepEqual(inserts(at('№:')), ['1', '2', '3', '4', '5']);
});

test('inside subqueries: recursive keys and values; было:( only eponym keys', () => {
  assert.deepEqual(inserts(at('x:(ти')), ['тип:']);
  assert.deepEqual(inserts(at('x:(тип:ул')), ['улица*', 'улица']);
  assert.deepEqual(inserts(at('рядом:(тип:метро) x:((кт')), ['кто:']);
  assert.deepEqual(inserts(at('было:(')), ['имя:', 'кто:', 'био:', 'век:', 'пол:', 'свой:']);
  assert.deepEqual(at('было:(ти'), []);
  assert.deepEqual(at('было:(кто:поэт) тип:ул').length, 2, 'outside было:( again all keys');
  assert.ok(!inserts(at('x:(зо')).includes('зона:'), 'directives are not offered inside subqueries');
  assert.deepEqual(inserts(at('x:(тип:сквер)~')), ['с', 'св', 'в', 'юв', 'ю', 'юз', 'з', 'сз']);
  assert.deepEqual(inserts(at('x:(тип:сквер)~ю')), ['юв', 'ю', 'юз']);
  assert.deepEqual(inserts(at('рядом:я~')), ['100', '200', '300', '500', '1000']);
  assert.deepEqual(inserts(at('рядом:(тип:метро)~3')), ['300']);
  assert.deepEqual(inserts(at('x:')), ['(']);
  assert.deepEqual(inserts(at('рядом:')), ['(', 'я']);
});

test('house forms', () => {
  assert.deepEqual(inserts(at('дом:')), ['есть', 'нет', '3', '=3', '3а', '3/16', '1-9', '3?']);
  assert.deepEqual(inserts(at('дом:12')), ['12', '12а', '12?', '12-22']);
  assert.deepEqual(inserts(at('дом:=12')), ['=12']);
  assert.deepEqual(inserts(at('дом:н')), ['нет']);
});

test('no suggestions inside quotes or regexes; never throws', () => {
  assert.deepEqual(at('"26 ма'), []);
  assert.deepEqual(at('/ab'), []);
  for (const q of ['(((', ')))', 'тип:::', '~~', ',,', 'x:(a)~в~', '"', '|||', 'дом:=']) {
    assert.doesNotThrow(() => at(q), q);
  }
});

test('contextAt reports the mode', () => {
  assert.equal(contextAt('тип:', 4).mode, 'value');
  assert.equal(contextAt('тип:', 4).key.id, 'type');
  assert.equal(contextAt('ти', 2).mode, 'key');
  assert.equal(contextAt('x:(a)~', 6).mode, 'tilde');
  assert.equal(contextAt('было:(кто:', 10).inOld, true);
  assert.equal(contextAt('x:(кто:', 7).sub, 'cross');
});
