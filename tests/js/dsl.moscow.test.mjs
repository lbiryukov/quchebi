// A Russian-language pack (Москва. Хамовники, manifest.city.lang = 'ru') built in memory: primary
// names in `ka` (Russian), no `ru`; new place kinds (stop, house); notes (core.f[].note) and инфо:;
// свой: with eponyms.e[].o = "ru" | "fo"; Moscow house numbers; name helpers; ranking tiers;
// explanations and autocomplete wording. Does not depend on app/public/data/khamovniki.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { buildIndex } from '../../app/src/search/index.js';
import { encodePolyline } from '../../app/src/zone/geo.js';
import { run } from '../../app/src/dsl/eval.js';
import { parse } from '../../app/src/dsl/parser.js';
import { explain } from '../../app/src/dsl/explain.js';
import { complete } from '../../app/src/dsl/complete.js';
import { dslData, TIER, placeRuKindName } from '../../app/src/dsl/data.js';
import { primaryName, secondaryName, ruLabel, nameLines, nameVariants, areaRu } from '../../app/src/search/names.js';
import { QUIET_LOG, loadIndex } from '../fixtures/dsl_mini/load.mjs';

const e5 = (x) => Math.round(x * 1e5);

const KINDS = [
  { code: 'street', ka: '', ru: 'улица', syn: [], group: 'street*' },
  { code: 'lane', ka: '', ru: 'переулок', syn: [], group: 'lane*' },
  { code: 'square', ka: '', ru: 'площадь', syn: [], group: '' },
  { code: 'landmark', ka: '', ru: 'место', syn: ['места', 'достопримечательность'], group: 'place*' },
  { code: 'museum', ka: '', ru: 'музей', syn: ['галерея'], group: 'place*' },
  { code: 'plaque', ka: '', ru: 'доска', syn: ['мемдоска'], group: 'place*' },
  { code: 'stop', ka: '', ru: 'остановка', syn: ['остановки', 'автобус'], group: 'place*' },
  { code: 'house', ka: '', ru: 'здание', syn: ['особняк', 'наследие', 'окн'], group: 'place*' },
];
const K = Object.fromEntries(KINDS.map((k, i) => [k.code, i]));

// [key, name, kind, extra, line [[lat, lon], ...]]
const FEATURES = [
  ['tolstogo', 'улица Льва Толстого', 'street', { fam: 0, en: 'Lva Tolstogo Street', hn: 6 }, [[55.7330, 37.5870], [55.7300, 37.5800]]],
  ['neo1', '1-й Неопалимовский переулок', 'lane', { fam: 1, o: 1 }, [[55.7420, 37.5800], [55.7410, 37.5760]]],
  ['neo2', '2-й Неопалимовский переулок', 'lane', { fam: 1, o: 2 }, [[55.7430, 37.5810], [55.7420, 37.5770]]],
  ['palme', 'улица Улофа Пальме', 'street', { fam: 2 }, [[55.7200, 37.5600], [55.7210, 37.5650]]],
  ['unknown', 'улица Неизвестного', 'street', { fam: 3 }, [[55.7250, 37.5700], [55.7260, 37.5750]]],
  ['zubovskaya', 'Зубовская площадь', 'square', {}, [[55.7365, 37.5900], [55.7370, 37.5910]]],
  ['museum', 'Музей-усадьба Л. Н. Толстого', 'museum', { par: 0, en: 'Leo Tolstoy Estate Museum', note: '1882, усадьба; объект культурного наследия' }, [[55.7318, 37.5855]]],
  ['stop', 'Улица Льва Толстого', 'stop', { par: 0, note: 'Автобусы: м3, 15, 255' }, [[55.7325, 37.5862]]],
  ['house', 'Доходный дом Толстого', 'house', { par: 0, note: '1911, арх. Н. Д. Струков; объект культурного наследия' }, [[55.7310, 37.5840]]],
  ['plaque', 'Лев Толстой', 'plaque', { par: 0, note: 'Здесь жил Лев Николаевич Толстой' }, [[55.7319, 37.5856]]],
];
export const F = Object.fromEntries(FEATURES.map((x, i) => [x[0], i]));

function buildPack() {
  const f = [];
  const g = [];
  for (const [, name, kind, extra, line] of FEATURES) {
    const lats = line.map((p) => p[0]);
    const lons = line.map((p) => p[1]);
    f.push({
      ka: name, k: K[kind], len: line.length > 1 ? 400 : 0, c: [e5(lats[0]), e5(lons[0])],
      bb: [e5(Math.min(...lats)), e5(Math.min(...lons)), e5(Math.max(...lats)), e5(Math.max(...lons))],
      hn: 0, d: [0], ...extra,
    });
    g.push([encodePolyline(line)]);
  }
  f[F.neo1].p = [0];
  f[F.neo2].p = [0];
  const core = {
    v: 1,
    kinds: KINDS,
    districts: [{ ka: 'район Хамовники', en: 'Khamovniki' }],
    places: [{ ka: 'Девичье поле', kind: 'neighbourhood', parent: 0, c: [e5(55.742), e5(37.578)] }],
    f,
    fam: [{ ka: 'Льва Толстого', e: 0 }, { ka: 'Неопалимовский' }, { ka: 'Улофа Пальме', e: 1 }, { ka: 'Неизвестного', e: 2 }],
    x: [], xp: [], xd: [], near: [],
  };
  const eponyms = {
    v: 1,
    taxonomy: [{ id: 'lit', ru: 'Литература', aliases: ['литература'], children: [{ id: 'lit.writer', ru: 'писатель', aliases: ['писатель'] }] }],
    kinds: { person: ['персона'] },
    e: [
      { ru: 'Лев Толстой', en: 'Leo Tolstoy', k: 'person', cat: ['lit.writer'], occ: ['писатель'], g: 'm', b: 1828, d: 1910, o: 'ru' },
      { ru: 'Улоф Пальме', en: 'Olof Palme', k: 'person', cat: [], occ: ['политик'], g: 'm', b: 1927, d: 1986, o: 'fo' },
      { ru: 'Неизвестный', k: 'person', cat: [] },
    ],
  };
  const houses = {
    v: 1,
    h: {
      [String(F.tolstogo)]: [
        ['12', 12, '', '', e5(55.732), e5(37.586)],
        ['12А', 12, 'а', '', e5(55.7321), e5(37.5861)],
        ['12 к2', 12, '', '', e5(55.7322), e5(37.5862), null, 'к2'],
        ['12 к2 с1', 12, '', '', e5(55.7323), e5(37.5863), null, 'к2 с1'],
        ['16', 16, '', '', e5(55.7315), e5(37.5850), null, null, F.museum],
        ['5/7 с2', 5, '', '7', e5(55.7329), e5(37.5868), null, 'с2'],
      ],
    },
    place: {},
  };
  return {
    manifest: { v: 1, version: 'test-ru', built: '10/11/2026', city: { id: 'khamovniki', title: 'Москва. Хамовники', lang: 'ru' }, files: {} },
    core, eponyms, houses, geom: { v: 1, prec: 5, g }, areas: { v: 1, districts: [{ poly: [] }], places: [{ poly: [] }] },
  };
}

const pack = buildPack();
const index = buildIndex(pack, { log: QUIET_LOG });
const names = (r) => [...r.order].map((i) => FEATURES[i][0]);
const set = (r) => names(r).sort();
const q = (text, ctx = {}) => {
  const r = run(text, index, ctx);
  assert.ok(!r.error, `${text}: ${r.error && r.error.message}`);
  return r;
};

test('the pack language comes from manifest.city', () => {
  assert.equal(dslData(index).lang, 'ru');
  const center = dslData(index).center;
  assert.ok(Math.abs(center.lat - 55.73) < 0.05 && Math.abs(center.lon - 37.58) < 0.05, 'distance grids centred on the data');
});

test('search by Russian name; secondary names never empty or duplicated', () => {
  assert.ok(names(q('толстого')).includes('tolstogo'));
  assert.ok(names(q('толстого')).includes('house'));
  assert.deepEqual(names(q('льва толстого')).slice(0, 1), ['tolstogo'], 'the whole name of the street comes first');
  assert.deepEqual(set(q('неопалимовский')), ['neo1', 'neo2']);
  assert.deepEqual(set(q('тип:переулок №:2')), ['neo2']);
  const f = pack.core.f;
  assert.equal(primaryName(f[F.tolstogo]), 'улица Льва Толстого');
  assert.equal(secondaryName(f[F.tolstogo], 'ru'), 'Lva Tolstogo Street');
  assert.equal(secondaryName(f[F.neo1], 'ru'), '', 'no English name: no second line');
  assert.equal(secondaryName({ ka: 'улица Х', ru: 'улица Х' }, 'ru'), '', 'ru equal to ka is not repeated');
  assert.equal(ruLabel(f[F.neo1]), '1-й Неопалимовский переулок');
  assert.deepEqual(nameLines(f[F.zubovskaya], 'ru'), { primary: 'Зубовская площадь', secondary: '' });
  assert.deepEqual(nameVariants({ ka: 'улица Х', ru: 'улица Х', en: 'X Street' }, 'ru').map((v) => v.label), ['название', 'en']);
  assert.deepEqual(nameVariants({ ka: 'ქუჩა', ru: 'улица', en: 'Street' }, 'ka').map((v) => [v.label, v.main]), [['ka', true], ['ru', false], ['en', false]]);
  assert.equal(areaRu(pack.core.districts[0], 'ru'), 'район Хамовники');
  assert.equal(areaRu({ ka: 'ვაკე', en: 'Vake' }, 'ka'), 'Vake');
  assert.equal(placeRuKindName(f[F.museum], KINDS[K.museum]), null, 'a Russian primary name needs no "музей <en>" alias');
});

test('name order uses the Russian primary name', () => {
  assert.deepEqual(names(q('тип:улица сорт:имя')), ['tolstogo', 'unknown', 'palme']);
});

test('new place kinds: тип: by name and synonyms, low ranking tier, never streets', () => {
  assert.deepEqual(names(q('тип:остановка')), ['stop']);
  assert.deepEqual(names(q('тип:автобус')), ['stop']);
  assert.deepEqual(names(q('тип:здание')), ['house']);
  assert.deepEqual(names(q('тип:особняк')), ['house']);
  assert.deepEqual(set(q('тип:место*')), ['house', 'museum', 'plaque', 'stop']);
  const d = dslData(index);
  assert.equal(d.tier[F.museum], TIER.place);
  assert.equal(d.tier[F.stop], TIER.minorPlace);
  assert.equal(d.tier[F.house], TIER.minorPlace);
  assert.equal(d.tier[F.plaque], TIER.minorPlace);
  assert.ok(!d.streetish.has(F.stop) && !d.streetish.has(F.house));
  const r = names(q('толстого'));
  assert.ok(r.indexOf('museum') < r.indexOf('stop'), 'main place before a stop');
  assert.ok(r.indexOf('tolstogo') < r.indexOf('stop'));
});

test('инфо: searches notes (skeleton substring), есть / нет; bare words do not', () => {
  assert.deepEqual(names(q('инфо:струков')), ['house']);
  assert.deepEqual(names(q('info:"арх. Н. Д. Струков"')), ['house']);
  assert.deepEqual(set(q('note:наследия')), ['house', 'museum']);
  assert.deepEqual(names(q('инфо:255')), ['stop']);
  assert.deepEqual(set(q('инфо:есть')), ['house', 'museum', 'plaque', 'stop']);
  assert.equal(q('инфо:нет').count, FEATURES.length - 4);
  assert.deepEqual(names(q('инфо:/струк/')), ['house']);
  assert.equal(q('струков').count, 0, 'a bare word does not search notes');
  assert.deepEqual(names(q('тип:место* инфо:автобусы')), ['stop']);
  assert.equal(q('инфо:струков').explain, 'Объекты с пометкой «струков» — весь город');
  assert.equal(q('инфо:есть').explain, 'Объекты с пометкой — весь город');
  assert.match(q('-инфо:нет тип:здание').explain, /кроме: объекты без пометки/);
});

test('свой: means "local" (o present and not "fo") for a Moscow pack', () => {
  assert.deepEqual(names(q('свой:да')), ['tolstogo']);
  assert.deepEqual(names(q('свой:нет')), ['palme']);
  assert.deepEqual(names(q('свой:местный')), ['tolstogo']);
  assert.equal(q('свой:да').explain, 'Объекты (местный) — весь город');
  assert.equal(q('свой:нет').explain, 'Объекты (иностранец) — весь город');
  assert.match(q('тип:улица -свой:да').explain, /не местный/);
  const s = complete('свой:', 5, index, {});
  assert.deepEqual(s.map((x) => x.label), ['да — местный', 'нет — иностранец']);
  assert.deepEqual(s.map((x) => x.detail), ['1', '1']);
});

test('свой: wording stays Georgian for a Tbilisi pack', () => {
  const tb = loadIndex();
  const r = run('свой:да', tb, {});
  assert.ok(!r.error);
  assert.match(r.explain, /грузин/);
  assert.equal(explain(parse('свой:нет').ast, { lang: 'ka' }), 'Объекты (иностранец)');
  assert.equal(explain(parse('-свой:да').ast, { lang: 'ka' }), 'Объекты (не грузин)');
});

test('Moscow house numbers in queries', () => {
  assert.deepEqual(names(q('дом:12')), ['tolstogo']);
  assert.deepEqual(names(q('дом:12к2с1')), ['tolstogo']);
  assert.deepEqual(names(q('дом:12стр1')), ['tolstogo']);
  assert.deepEqual(names(q('дом:12а')), ['tolstogo']);
  assert.deepEqual(names(q('дом:12a')), ['tolstogo'], 'Latin letter');
  assert.equal(q('дом:12с2').count, 0);
  assert.equal(q('дом:12б').count, 0);
  assert.deepEqual(names(q('дом:5/7с2')), ['tolstogo']);
  assert.deepEqual(names(q('дом:=12')), ['tolstogo']);
  assert.equal(q('дом:=5').count, 0);
  assert.equal(q('дом:12к2с1').explain, 'Объекты с домом 12 корп. 2, стр. 1 — весь город');
  const bad = run('дом:12х1', index, {});
  assert.ok(bad.error);
  assert.match(bad.error.message, /12к2/);
  // an AST parsed without the language is re-read for the pack when evaluated
  const r = run('дом:12к2', index, {});
  assert.equal(r.count, 1);
  const forms = complete('дом:', 4, index, {}).map((x) => x.insert);
  assert.ok(forms.includes('12к2') && forms.includes('12с1'));
});

test('autocomplete: инфо is offered for packs with notes, kinds come from the pack', () => {
  assert.ok(complete('ин', 2, index, {}).some((s) => s.insert === 'инфо:'));
  const tb = loadIndex();
  assert.ok(!complete('и', 1, tb, {}).some((s) => s.insert === 'инфо:'), 'Tbilisi pack has no notes');
  assert.ok(complete('инфо:', 5, index, {}).some((s) => s.insert === 'есть'));
  assert.ok(complete('тип:ост', 7, index, {}).some((s) => s.insert === 'остановка'));
  assert.ok(!complete('тип:ост', 7, tb, {}).some((s) => s.insert === 'остановка'), 'Tbilisi pack has no stops');
});

test('districts and places: район:/в: work with Russian primary names', () => {
  assert.equal(q('район:хамовники').count, FEATURES.length);
  assert.deepEqual(set(q('в:"девичье поле"')), ['neo1', 'neo2']);
  assert.ok(complete('в:дев', 5, index, {}).some((s) => s.label === 'Девичье поле'));
});

test('рядом: distances are right at Moscow latitude', () => {
  // the plaque is ~10 m from the museum; the stop ~90 m; the house ~110 m
  assert.deepEqual(set(q('рядом:(тип:музей)~50 тип:место*')), ['museum', 'plaque']);
  assert.ok(names(q('рядом:(тип:музей)~100 тип:остановка')).includes('stop'));
});
