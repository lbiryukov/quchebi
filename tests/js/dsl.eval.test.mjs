// Evaluator tests on tests/fixtures/dsl_mini (pack_mini + DSL extras): golden examples of
// docs/dsl.md, plan scenarios, eponym/house/relation semantics, zone behaviour, sorting, errors.
import { test, describe } from 'node:test';
import assert from 'node:assert/strict';
import { loadIndex, loadPack, F, QUIET_LOG } from '../fixtures/dsl_mini/load.mjs';
import { buildIndex } from '../../app/src/search/index.js';
import { computeZoneMasks } from '../../app/src/zone/masks.js';
import { parse } from '../../app/src/dsl/parser.js';
import { evaluate, run, countText, prepare } from '../../app/src/dsl/eval.js';
import { nameSet, names } from './dsl_helpers.mjs';

const index = loadIndex();
const set = (q, ctx = {}) => {
  const r = run(q, index, ctx);
  assert.ok(!r.error, `${q}: ${r.error && r.error.message}`);
  return nameSet(r, F);
};
const sorted = (arr) => [...arr].sort();

// Zone around Freedom Square and the east half of улица Ладо Асатиани.
const ZONE_POLY = [[41.6880, 44.7960], [41.6960, 44.7960], [41.6960, 44.8030], [41.6880, 44.8030], [41.6880, 44.7960]];
const zone = computeZoneMasks(index, ZONE_POLY, { rule: 'default', log: QUIET_LOG });

describe('golden examples (docs/dsl.md), no zone', () => {
  const GOLDEN = [
    ['дом:3', ['apkhazi', 'asatiani', 'besiki', 'erekle', 'gorgasali', 'muskhelishvili', 'pushkin', 'zubalashvili']],
    ['тип:улица* дом:3', ['apkhazi', 'asatiani', 'besiki', 'erekle', 'gorgasali', 'muskhelishvili', 'pushkin', 'zubalashvili']],
    ['тип:переулок x:(тип:улица* кто:поэт)', ['asatiani1', 'asatiani2', 'vazha1']],
    ['кто:хормейстер дом:24', ['kakhidze']],
    ['кто:наука дом:2', ['muskhelishvili']],
    ['кто:дирижёр было:крылов', ['kakhidze']],
    ['было:(био:гэс)', ['muskhelishvili']],
    ['имя:/\\С{5}/', ['tsinamdz']],
    ['x:(тип:сквер рижск)~в дом:16', ['tsinamdz']],
    ['x:(чаргали)', ['kakhidze', 'riga', 'vazha']],
    ['тип:тупик дом:нет в:сололаки', ['asatianiDead']],
    ['кто:писатель пол:ж', ['gabashvili']],
    ['тип:улица -кто:персона', ['chargali', 'chitadze', 'tsinamdz']],
    ['рядом:(тип:метро)~300 тип:переулок*', ['asatianiPass']],
    ['семья:важа-пшавела', ['vazha', 'vazha1', 'vazhaDead']],
  ];
  for (const [q, want] of GOLDEN) test(q, () => assert.deepEqual(set(q), sorted(want)));
});

describe('plan scenarios', () => {
  test('latin from a sign or an address matches old latin names', () => {
    assert.deepEqual(set('leselidze'), ['apkhazi']);
    assert.deepEqual(set('"Leselidze St"'), ['apkhazi']);
  });
  test('heard by ear: ka, ru and latin spellings give the same result', () => {
    for (const q of ['табидзе', 'ტაბიძე', 'tabidze', 'TABIDZE', 'Табидзе']) assert.deepEqual(set(q), ['tabidze'], q);
  });
  test('square in an area with its name history', () => {
    assert.deepEqual(set('в:мтацминда тип:площадь'), ['freedom']);
    assert.deepEqual(set('тип:площадь было:ленин'), ['freedom']);
  });
  test('riddle addresses in Latin: fuzzy fallback when a bare word has no exact hit', () => {
    let r = run('Galaktioni', index, {});
    assert.deepEqual(nameSet(r, F), ['tabidze']);
    assert.ok(r.warnings[0].startsWith('нечёткое совпадение: «Galaktioni» ≈ '), r.warnings[0]);
    r = run('Alexander', index, {});
    assert.deepEqual(nameSet(r, F), ['pushkin'], 'x = ks: Alexander ≈ Aleksandre');
    assert.deepEqual(set('Aleksandre'), ['pushkin']);
    assert.deepEqual(set('Rustavelli'), ['rustaveli']);
    assert.deepEqual(set('"Galaktioni Tabidze St"'), ['tabidze'], 'quoted phrase: fuzzy per word');
    assert.deepEqual(set('Galaktioni Tabidze'), ['tabidze']);
    assert.deepEqual(set('тип:улица Galaktioni'), ['tabidze']);
  });
  test('fuzzy fallback keeps exact semantics and its distance budget', () => {
    const r = run('Tabidze', index, {});
    assert.deepEqual(r.warnings, [], 'exact hits: no fuzzy');
    assert.deepEqual(set('xyzzyq'), []);
    assert.deepEqual(set('Табидзеее'), ['tabidze'], '2 edits on 9 letters');
    assert.deepEqual(set('Тобидзооо'), [], '4 edits on 9 letters is too many');
    assert.deepEqual(set('Кураа'), ['kura'], '1 edit on 5 letters');
    assert.deepEqual(set('Кураб'), ['kura']);
    assert.deepEqual(set('Курбб'), [], '2 edits on 5 letters is too many');
    assert.deepEqual(set('-Galaktioni тип:улица').includes('tabidze'), false, 'fuzzy also under NOT');
  });
  test('plain text searches every name variant including old ones', () => {
    assert.deepEqual(set('эриванская'), ['freedom']);
    assert.deepEqual(set('крылова'), ['kakhidze']);
  });
});

describe('kinds, areas, names', () => {
  test('тип: list = OR, star = group, synonyms and codes', () => {
    assert.deepEqual(set('тип:переулок,тупик'), sorted(['asatiani1', 'asatiani2', 'asatianiDead', 'vazha1', 'vazhaDead']));
    assert.deepEqual(set('тип:переулок*'), sorted(['asatiani1', 'asatiani2', 'asatianiDead', 'asatianiPass', 'vazha1', 'vazhaDead']));
    assert.deepEqual(set('тип:сад'), set('тип:сквер'));
    assert.deepEqual(set('тип:lane'), set('тип:переулок'));
    assert.deepEqual(set('тип:street*'), set('тип:улица*'));
    assert.deepEqual(set('тип:реки'), ['kura']);
    assert.deepEqual(set('тип:метро'), ['metro']);
  });
  test('район: and в: by prefix, ka/ru/en', () => {
    assert.deepEqual(set('в:сололак тип:тупик'), ['asatianiDead']);
    assert.deepEqual(set('в:sololaki тип:тупик'), ['asatianiDead']);
    assert.deepEqual(set('в:სოლოლაკი тип:тупик'), ['asatianiDead']);
    assert.deepEqual(set('район:крцан тип:мост'), ['metekhi']);
    assert.deepEqual(set('район:исани,крцаниси тип:мост'), ['metekhi']);
    assert.deepEqual(set('в:кала тип:улица'), sorted(['apkhazi', 'erekle', 'gorgasali']));
  });
  test('имя: prefix, suffix, exact, substring of current names only', () => {
    assert.deepEqual(set('имя:^ვაჟა'), sorted(['vazha', 'vazha1', 'vazhaDead']));
    assert.deepEqual(set('имя:^шота'), ['rustaveli']);
    assert.deepEqual(set('имя:^чаргали$'), ['chargali']);
    assert.deepEqual(set('имя:кахидзе$'), ['kakhidze']);
    assert.deepEqual(set('имя:крылова'), [], 'old names are not in имя:');
    assert.deepEqual(set('крылова'), ['kakhidze']);
  });
  test('№: ordinal of lanes', () => {
    assert.deepEqual(set('тип:переулок №:2'), ['asatiani2']);
    assert.deepEqual(set('№:1-2'), sorted(['asatiani1', 'asatiani2', 'vazha1']));
    assert.deepEqual(set('№>1'), ['asatiani2']);
  });
  test('регулярные выражения по ka и ru', () => {
    assert.deepEqual(set('/^улица Ч/'), sorted(['chargali', 'chitadze']));
    assert.deepEqual(set('/^ჩ/'), sorted(['chargali', 'chitadze']));
    assert.deepEqual(set('/ленина/'), ['freedom'], 'old ru names are searched');
  });
});

describe('eponyms', () => {
  test('кто: taxonomy id, alias, group alias, kind alias, occupation prefix', () => {
    const poets = set('кто:поэт');
    assert.deepEqual(set('кто:lit.poet'), poets);
    assert.deepEqual(set('кто:поэтесса'), poets);
    assert.ok(set('кто:литература').length > poets.length);
    assert.ok(set('кто:литература').includes('gabashvili'));
    assert.ok(set('кто:персона').includes('zubalashvili'), 'персона includes persons_multi');
    assert.deepEqual(set('кто:топоним'), sorted(['metekhi', 'riga', 'sololakiAsc']));
    assert.deepEqual(set('кто:хорм'), ['kakhidze'], 'occupation prefix');
    assert.deepEqual(set('кто:царь,полководец'), sorted(['apkhazi', 'erekle', 'gorgasali']));
    assert.deepEqual(set('кто:царь | кто:полководец'), set('кто:царь,полководец'));
  });
  test('био: substring of description, occupations and names', () => {
    assert.deepEqual(set('био:"витязь в тигровой"'), ['rustaveli']);
    assert.deepEqual(set('био:баснописец'), []);
    assert.deepEqual(set('было:(био:баснописец)'), ['kakhidze']);
    assert.deepEqual(set('био:/^грузинская/'), ['gabashvili']);
  });
  test('век: overlap of life years', () => {
    assert.deepEqual(set('век:12'), ['rustaveli']);
    assert.deepEqual(set('век:XII'), ['rustaveli']);
    assert.deepEqual(set('век:5-6'), ['gorgasali']);
    assert.ok(set('век:19').includes('pushkin'));
    assert.ok(!set('век:20').includes('pushkin'));
    assert.deepEqual(set('век:<6'), ['gorgasali']);
  });
  test('пол: and свой:', () => {
    assert.deepEqual(set('пол:ж'), ['gabashvili']);
    assert.ok(!set('пол:м').includes('gabashvili'));
    assert.deepEqual(set('свой:нет'), sorted(['pushkin', 'pushkinSq']));
    assert.ok(set('свой:да').includes('rustaveli'));
    assert.ok(!set('свой:да').includes('chitadze'), 'no eponym -> neither да nor нет');
  });
  test('было:(…) uses eponyms of old names, text inside matches the old name', () => {
    assert.deepEqual(set('было:(кто:революционер)'), ['freedom']);
    assert.deepEqual(set('было:(кто:инженер)'), ['muskhelishvili']);
    assert.deepEqual(set('было:(кто:писатель крылов)'), ['kakhidze']);
    assert.deepEqual(set('было:(кто:инженер крылов)'), []);
    assert.deepEqual(set('было:(кто:персона -свой:да)'), sorted(['freedom', 'kakhidze', 'muskhelishvili']));
    assert.deepEqual(set('было:эриван'), ['freedom']);
    assert.deepEqual(set('было:(имя:^эриван)'), ['freedom']);
  });
});

describe('houses', () => {
  test('forms of дом:', () => {
    assert.deepEqual(set('дом:=3'), sorted(['apkhazi', 'asatiani', 'besiki', 'gorgasali', 'pushkin']));
    assert.deepEqual(set('дом:3а'), sorted(['asatiani', 'erekle', 'muskhelishvili']));
    assert.deepEqual(set('дом:3ა'), set('дом:3а'));
    assert.deepEqual(set('дом:3a'), set('дом:3а'));
    assert.deepEqual(set('дом:3/16'), ['asatiani']);
    assert.deepEqual(set('дом:3а/1'), ['erekle']);
    assert.deepEqual(set('дом:20/22'), ['apkhazi']);
    assert.deepEqual(set('дом:29'), ['apkhazi'], 'list "27;29" -> two rows');
    assert.deepEqual(set('дом:4 тип:улица'), sorted(['asatiani', 'tabidze']), '"3-5" covers 3 and 5, not 4');
    assert.deepEqual(set('дом:5 семья:зубалашвили'), ['zubalashvili'], 'range "3-5" contains 5');
    assert.deepEqual(set('дом:30-40'), sorted(['apkhazi', 'erekle', 'rustaveli']));
    assert.deepEqual(set('дом:3,24'), sorted([...set('дом:3'), 'kakhidze', 'vazha']));
  });
  test('дом:3? adds features where 3 is probably there', () => {
    assert.deepEqual(set('дом:3? -дом:3'), sorted(['gabashvili', 'tabidze']));
    assert.ok(!set('дом:3?').includes('rustaveli'), 'even side only: not probable');
  });
  test('дом:есть / дом:нет / домов', () => {
    assert.ok(set('дом:нет').includes('freedom'));
    assert.ok(!set('дом:нет').includes('asatiani'));
    assert.equal(set('дом:есть').length + set('дом:нет').length, index.n);
    assert.deepEqual(set('домов>=6'), sorted(['apkhazi', 'asatiani', 'rustaveli']));
    assert.deepEqual(set('домов=0 тип:улица'), []);
  });
});

describe('relations', () => {
  test('x: with compass sector of the result feature', () => {
    assert.deepEqual(set('x:(тип:сквер)~в'), ['tsinamdz']);
    assert.deepEqual(set('x:(тип:сквер)~св'), ['pushkin']);
    assert.deepEqual(set('x:(тип:сквер)~восток'), ['tsinamdz']);
    assert.deepEqual(set('x:(тип:сквер рижск)'), sorted(['chargali', 'tsinamdz']));
    assert.deepEqual(set('x:чаргали'), set('x:(чаргали)'));
  });
  test('рядом: radius and default 200 m', () => {
    const r300 = set('рядом:(тип:метро)~300');
    const r200 = set('рядом:(тип:метро)');
    assert.ok(r300.length >= r200.length);
    assert.ok(r200.includes('metro'));
    assert.ok(!r200.includes('asatianiPass') && r300.includes('asatianiPass'));
    assert.deepEqual(set('рядом:(тип:метро)~1км'), set('рядом:(тип:метро)~1000'));
  });
  test('рядом:я uses ctx.me, error without geolocation', () => {
    const me = { lat: 41.6934, lon: 44.8014 };
    assert.ok(set('рядом:я~100', { me }).includes('freedom'));
    const r = run('рядом:я~300', index, {});
    assert.equal(r.error.message, 'нет геолокации — нажмите «Где я»');
    assert.equal(r.count, 0);
  });
  test('семья: closure over families', () => {
    assert.deepEqual(set('семья:асатиани'), sorted(['asatiani', 'asatiani1', 'asatiani2', 'asatianiDead', 'asatianiPass']));
    assert.deepEqual(set('семья:(тип:тупик в:сололаки)'), set('семья:асатиани'));
    assert.deepEqual(set('семья:кура'), ['kura'], 'feature without family keeps itself');
  });
  test('NOT is relative to all features; OR and parentheses', () => {
    assert.equal(set('-кто:персона').length + set('кто:персона').length, index.n);
    assert.deepEqual(set('(кто:царь | кто:полководец) -тип:улица'), []);
    assert.deepEqual(set('не тип:улица* тип:переулок*'), set('тип:переулок*'));
  });
  test('длина: without zone', () => {
    assert.deepEqual(set('длина>1000'), sorted(['kura', 'rustaveli']));
    assert.deepEqual(set('длина>500 семья:асатиани'), ['asatiani']);
  });
});

describe('zone', () => {
  const zctx = { zone };
  test('final filter once, houses only inside the zone polygon', () => {
    assert.deepEqual(set('дом:3', zctx), sorted(['apkhazi', 'pushkin']));
    const r = run('дом:3 зона:выкл', index, zctx);
    assert.equal(r.count, 8);
    assert.deepEqual(r.warnings, ['зона выключена']);
    assert.match(r.explain, /весь город \(зона выключена\)$/);
  });
  test('x: subquery without zone, crossing point inside the zone', () => {
    assert.deepEqual(set('тип:переулок x:(тип:улица* кто:поэт)', zctx), ['asatiani2']);
    assert.deepEqual(set('x:(руставели)', zctx), sorted(['apkhazi', 'freedom', 'pushkin', 'tabidze']));
    assert.deepEqual(set('x:(руставели) зона:выкл', zctx), sorted(['besiki', 'chitadze', 'apkhazi', 'freedom', 'pushkin', 'tabidze']));
  });
  test('subqueries ignore the zone inside them (house 3 of the poet street is outside)', () => {
    assert.ok(!set('дом:3', zctx).includes('asatiani'));
    assert.deepEqual(set('тип:переулок x:(тип:улица* дом:3)', zctx), ['asatiani2']);
    assert.deepEqual(set('семья:(дом:3/16)', zctx), sorted(['asatiani', 'asatiani2', 'asatianiDead', 'asatianiPass']));
  });
  test('длина: inside the zone', () => {
    assert.deepEqual(set('длина>500 семья:асатиани', zctx), []);
    assert.deepEqual(set('длина>400 семья:асатиани', zctx), ['asatiani']);
  });
  test('рядом: only parts of the subquery inside the zone count', () => {
    assert.deepEqual(set('рядом:(тип:река)~200', zctx), []);
    assert.ok(set('рядом:(тип:река)~200 зона:выкл', zctx).includes('metekhi'));
  });
  test('zone modes and counts', () => {
    assert.deepEqual(set('дом:3 зона:целиком', zctx), []);
    assert.deepEqual(set('тип:тупик дом:нет', zctx), ['asatianiDead']);
    assert.equal(run('зона:задевает', index, zctx).count, zone.featTouch.count());
    assert.equal(run('зона:целиком', index, zctx).count, zone.featWhole.count());
    assert.equal(run('', index, zctx).count, zone.featDefault.count());
    assert.equal(run('зона:выкл', index, zctx).count, index.n);
    const touchZone = computeZoneMasks(index, ZONE_POLY, { rule: 'touch', log: QUIET_LOG });
    assert.equal(run('', index, { zone: touchZone }).count, touchZone.featTouch.count(), 'zone own rule by default');
    assert.equal(run('зона:вкл', index, { zone: touchZone }).count, touchZone.featTouch.count());
  });
  test('zone requested without a zone -> warning, whole city', () => {
    const r = run('зона:задевает дом:3', index, {});
    assert.equal(r.count, 8);
    assert.deepEqual(r.warnings, ['зона не задана — ищу по всему городу']);
  });
  test('explain carries the zone suffix', () => {
    assert.equal(run('тип:переулок x:(тип:улица* кто:поэт)', index, zctx).explain,
      'Переулки, пересекающие улицы, проспекты и т.п. (кто: поэт) — в зоне');
    assert.equal(run('дом:3 зона:задевает', index, zctx).explain, 'Объекты с домом 3 — задевают зону');
    assert.equal(run('дом:3', index, {}).explain, 'Объекты с домом 3 — весь город');
  });
});

describe('ordering', () => {
  test('relevance for text: exact base name before substring', () => {
    const r = run('чаргали | кахидзе', index, {});
    assert.deepEqual(names(r, F), ['chargali', 'kakhidze']);
    const byName = run('кто:композитор | семья:чаргали', index, {});
    assert.deepEqual(names(byName, F), ['kakhidze', 'chargali'], 'no text -> by name');
  });
  test('сорт:длина, домов, имя, расстояние', () => {
    const len = names(run('тип:улица* сорт:длина', index, {}), F);
    assert.equal(len[0], 'rustaveli');
    const hn = names(run('тип:улица* сорт:домов', index, {}), F);
    assert.equal(hn[0], 'asatiani');
    const me = { lat: 41.69063, lon: 44.80041 }; // house 12 of улица Ладо Асатиани
    const dist = names(run('тип:улица* сорт:расстояние', index, { me }), F);
    assert.equal(dist[0], 'asatiani');
    const centre = names(run('тип:улица* сорт:расстояние', index, { mapCenter: me }), F);
    assert.equal(centre[0], 'asatiani');
    const r = run('тип:улица* сорт:расстояние', index, {});
    assert.ok(r.warnings[0].startsWith('нет точки для расстояния'));
    const nm = names(run('семья:асатиани сорт:имя', index, {}), F);
    assert.deepEqual(nm, ['asatiani1', 'asatiani2', 'asatianiPass', 'asatianiDead', 'asatiani']);
  });
  test('order is an Int32Array with exactly the bits', () => {
    const r = run('дом:3', index, {});
    assert.ok(r.order instanceof Int32Array);
    assert.equal(r.order.length, r.count);
    assert.equal(r.bits.count(), r.count);
    for (const i of r.order) assert.ok(r.bits.has(i));
  });
});

describe('errors and edge cases', () => {
  test('unknown enumerated values give suggestions', () => {
    let r = run('тип:улца', index, {});
    assert.equal(r.error.message, 'неизвестный тип «улца» — может быть: улица?');
    assert.deepEqual(r.error.suggestions, ['улица']);
    assert.equal(r.error.pos, 4);
    r = run('тип:улца*', index, {});
    assert.deepEqual(r.error.suggestions, ['улица*']);
    r = run('кто:поэд', index, {});
    assert.ok(r.error.suggestions.includes('поэт'));
    assert.ok(r.error.message.startsWith('неизвестная категория «поэд»'));
    r = run('в:сололкаи', index, {});
    assert.ok(r.error.suggestions.includes('сололаки'), JSON.stringify(r.error));
    r = run('район:мтацмнда', index, {});
    assert.ok(r.error.suggestions.includes('мтацминда'));
    assert.equal(r.count, 0);
    assert.equal(r.order.length, 0);
  });
  test('parse errors come back from run()', () => {
    const r = run('рйон:мтацминда', index, {});
    assert.equal(r.error.message, 'неизвестный ключ «рйон» — может быть, «район»?');
    assert.equal(r.count, 0);
  });
  test('empty query and directive-only queries mean "everything"', () => {
    assert.equal(run('', index, {}).count, index.n);
    assert.equal(evaluate(null, index, {}).count, index.n);
    assert.equal(run('сорт:длина', index, {}).count, index.n);
  });
  test('evaluate accepts a bare node as well as the root', () => {
    const { ast } = parse('кто:поэт');
    assert.equal(evaluate(ast.expr, index, {}).count, evaluate(ast, index, {}).count);
  });
  test('words without a skeleton are skipped with a warning', () => {
    const r = run('*** тип:мост', index, {});
    assert.equal(r.count, 1);
    assert.ok(r.warnings[0].includes('пропущено'));
  });
  test('auto-closed parenthesis warning is passed through', () => {
    const r = run('x:(тип:сквер рижск', index, {});
    assert.deepEqual(nameSet(r, F), sorted(['chargali', 'tsinamdz']));
    assert.deepEqual(r.warnings, ['не закрыта скобка «(» — закрыта автоматически']);
  });
  test('countText agrees the noun', () => {
    assert.equal(countText(1), '1 объект');
    assert.equal(countText(3), '3 объекта');
    assert.equal(countText(41), '41 объект');
    assert.equal(countText(12), '12 объектов');
  });
  test('prepare() builds caches once', () => {
    assert.equal(typeof prepare(index), 'number');
    assert.ok(prepare(index, { all: true }) >= 0);
    assert.ok(prepare(index, { all: true }) < 50, 'second call is a no-op');
  });
});

describe('before houses.json / geom.json arrive', () => {
  const pack = loadPack();
  delete pack.houses;
  delete pack.geom;
  const bare = buildIndex(pack, { log: QUIET_LOG });
  test('house queries warn and есть/нет fall back to core hn', () => {
    const r = run('дом:3', bare, {});
    assert.equal(r.count, 0);
    assert.deepEqual(r.warnings, ['номера домов ещё загружаются — результат неполный']);
    assert.ok(run('дом:есть', bare, {}).count > 0);
    assert.equal(run('дом:есть', bare, {}).count, run('дом:есть', index, {}).count);
  });
  test('рядом: falls back to representative points', () => {
    const r = run('рядом:(тип:метро)~300', bare, {});
    assert.ok(r.warnings.some((w) => w.startsWith('геометрия ещё загружается')));
    assert.ok(nameSet(r, F).includes('metro'));
  });
});
