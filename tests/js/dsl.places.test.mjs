// Places (kind group place*) in text search: ranking against streets, the fuzzy street fallback
// when exact hits are only places, Russian kind words for places without a Russian name, and
// the UI helpers for the address of a place / the places of a street (houses.json 9th element).
// Fixture: tests/fixtures/dsl_mini + synthetic places built here.
import { test, describe } from 'node:test';
import assert from 'node:assert/strict';
import { loadPack, F, QUIET_LOG } from '../fixtures/dsl_mini/load.mjs';
import { buildIndex } from '../../app/src/search/index.js';
import { encodePolyline } from '../../app/src/zone/geo.js';
import { run } from '../../app/src/dsl/eval.js';
import { dslData, placeRuKindName, loanForm, TIER } from '../../app/src/dsl/data.js';
import { state } from '../../app/src/ui/state.js';
import { isPlace, placeAddress, placeAddressText, streetPlaces, resetCaches } from '../../app/src/ui/feature.js';

const e5 = (x) => Math.round(x * 1e5);

const PLACE_KINDS = [
  { code: 'landmark', ka: '', ru: 'место', syn: ['места', 'достопримечательность'], group: 'place*' },
  { code: 'museum', ka: 'მუზეუმი', ru: 'музей', syn: ['галерея'], group: 'place*' },
  { code: 'monument', ka: 'ძეგლი', ru: 'памятник', syn: ['монумент', 'бюст'], group: 'place*' },
  { code: 'plaque', ka: 'დაფა', ru: 'доска', syn: ['мемдоска'], group: 'place*' },
  { code: 'university', ka: 'უნივერსიტეტი', ru: 'университет', syn: ['вуз'], group: 'place*' },
];

// [key, kind, ka, ru, en, parKey, lat, lon]
const PLACES = [
  ['tabMonument', 'monument', 'გალაკტიონ ტაბიძე', 'Галактион Табидзе', 'Galaktion Tabidze', 'tabidze', 41.6914, 44.7990],
  ['tabPlaque', 'plaque', 'გალაკტიონ ტაბიძე', 'Галактион Табидзе', 'Galaktion Tabidze', 'tabidze', 41.6915, 44.7991],
  ['tabUniversity', 'university', 'გალაკტიონ ტაბიძის სახელობის უნივერსიტეტი, კორპუსი 2',
    'Университет имени Галактиона Табидзе, корпус 2', 'Galaktion Tabidze University, Building 2', 'tabidze', 41.6916, 44.7992],
  ['illusions', 'museum', 'ილუზიების მუზეუმი', 'Илузиебис Музеуми', 'Museum of Illusions', 'asatiani', 41.6904, 44.7970],
  ['apollonPlaque', 'plaque', 'აპოლონ ქუთათელაძე', 'Аполлон Кутателадзе', 'Apollon Kutateladze', null, 41.6930, 44.7960],
];

function placesPack() {
  const pack = loadPack();
  const core = pack.core;
  const k0 = core.kinds.length;
  core.kinds.push(...PLACE_KINDS);
  const kindIdx = Object.fromEntries(core.kinds.map((k, i) => [k.code, i]));
  const keys = { ...F };
  // a street whose Georgian/Russian spelling has one "l": a Latin "Apollon" misses it exactly
  const addFeature = (key, f, pts) => {
    const i = core.f.length;
    core.f.push(f);
    pack.geom.g[i] = [encodePolyline(pts)];
    keys[key] = i;
    return i;
  };
  addFeature('apolonStreet', {
    ka: 'აპოლონ ქუთათელაძის ქუჩა', ru: 'улица Аполона Кутателадзе', en: 'Apolon Kutateladze Street', k: kindIdx.street,
    d: [], p: [], len: 140, c: [e5(41.6931), e5(44.7962)], hn: 0,
  }, [[41.6931, 44.7955], [41.6931, 44.7969]]);
  for (const [key, kind, ka, ru, en, par, lat, lon] of PLACES) {
    const f = { ka, ru, en, k: kindIdx[kind], d: [], p: [], len: 0, c: [e5(lat), e5(lon)], hn: 0 };
    if (par) f.par = keys[par];
    addFeature(key, f, [[lat, lon]]);
  }
  // the museum's building carries house 8 of улица Ладо Асатиани (9th element = place index)
  const h = pack.houses.h[String(F.asatiani)];
  h.push(['8', 8, '', '', e5(41.6904), e5(44.7970), null, null, keys.illusions]);
  core.f[F.asatiani].hn = h.length;
  pack.manifest.counts.features = core.f.length;
  assert.equal(core.kinds[k0].code, 'landmark');
  return { pack, keys };
}

const { pack, keys: K } = placesPack();
const index = buildIndex(pack, { log: QUIET_LOG });
const byIdx = new Map(Object.entries(K).map(([k, v]) => [v, k]));
const order = (q) => {
  const r = run(q, index, {});
  assert.ok(!r.error, `${q}: ${r.error && r.error.message}`);
  return [...r.order].map((i) => byIdx.get(i) ?? String(i));
};
const res = (q) => run(q, index, {});

describe('ranking: streets before places at equal relevance', () => {
  test('a person\'s name: the street first, main places before plaques and university buildings', () => {
    const o = order('galaktion tabidze');
    assert.equal(o[0], 'tabidze', o.join(','));
    const pos = (k) => o.indexOf(k);
    assert.ok(pos('tabMonument') > pos('tabidze'));
    assert.ok(pos('tabMonument') < pos('tabPlaque'), o.join(','));
    assert.ok(pos('tabMonument') < pos('tabUniversity'), o.join(','));
    assert.deepEqual(order('tabidze').slice(0, 1), ['tabidze']);
    assert.deepEqual(order('Табидзе').slice(0, 1), ['tabidze']);
  });
  test('tiers of the data cache', () => {
    const d = dslData(index);
    assert.equal(d.tier[K.tabidze], TIER.street);
    assert.equal(d.tier[K.freedom], TIER.street, 'a square ranks with streets');
    assert.equal(d.tier[K.metekhi], TIER.other, 'a bridge');
    assert.equal(d.tier[K.asatiani1], TIER.ordinal, 'a numbered lane');
    assert.equal(d.tier[K.tabMonument], TIER.place);
    assert.equal(d.tier[K.tabPlaque], TIER.minorPlace);
    assert.equal(d.tier[K.tabUniversity], TIER.minorPlace);
    assert.ok(d.streetish.has(K.apolonStreet) && !d.streetish.has(K.apollonPlaque) && !d.streetish.has(K.metekhi));
  });
  test('the whole name beats a word of it, a Georgian genitive counts as the name', () => {
    // "ladoasatiani" is the base of the street and of its lanes: the street first, its lanes after
    const o = order('lado asatiani');
    assert.equal(o[0], 'asatiani', o.join(','));
    assert.equal(o.length, 5);
    // პუშკინის = pushkin + is: a whole word of the name; the street before сквер Пушкина (other objects)
    assert.deepEqual(order('pushkin'), ['pushkin', 'pushkinSq']);
  });
  test('without text: places after other objects; сорт:имя keeps the plain name order', () => {
    const o = order('тип:улица,памятник в:мтацминда | тип:памятник');
    assert.equal(o[o.length - 1], 'tabMonument', o.join(','));
    const byName = order('тип:памятник,улица сорт:имя');
    assert.equal(byName[0], 'tabMonument', 'Галактион before улица ...');
    assert.equal(order('тип:памятник,улица').at(-1), 'tabMonument');
  });
});

describe('fuzzy street fallback when exact hits are only places', () => {
  test('a Latin spelling that only a plaque has exactly still finds the street', () => {
    const r = res('apollon kutateladze');
    const o = [...r.order].map((i) => byIdx.get(i));
    assert.deepEqual([...o].sort(), ['apollonPlaque', 'apolonStreet']);
    assert.ok(r.warnings.some((w) => w.startsWith('нечёткое совпадение среди улиц: «apollon» ≈ ')), JSON.stringify(r.warnings));
    assert.deepEqual(order('apollon тип:улица'), ['apolonStreet']);
    assert.deepEqual(order('"Apollon Kutateladze"').sort(), ['apollonPlaque', 'apolonStreet']);
  });
  test('the warning only when fuzzy streets reach the result; no fallback when a street matches exactly', () => {
    const r = res('apollon тип:доска');
    assert.deepEqual([...r.order].map((i) => byIdx.get(i)), ['apollonPlaque']);
    assert.deepEqual(r.warnings, []);
    const t = res('Tabidze');
    assert.deepEqual(t.warnings, [], 'the street has an exact hit');
    assert.ok(!t.order.includes(K.apolonStreet));
    const sub = res('x:(apollon)');
    assert.ok(sub.warnings.some((w) => w.startsWith('нечёткое совпадение среди улиц')), 'subquery: always shown');
  });
});

describe('Russian kind words for places without a Russian name', () => {
  test('тип:музей иллюзий finds "Museum of Illusions"', () => {
    const r = res('тип:музей иллюзий');
    assert.deepEqual([...r.order].map((i) => byIdx.get(i)), ['illusions']);
    assert.ok(r.warnings[0].startsWith('нечёткое совпадение: «иллюзий» ≈ «Illusions»'), r.warnings[0]);
    assert.deepEqual(order('музей иллюзий'), ['illusions']);
    assert.ok(order('музей').includes('illusions'), 'the kind word alone');
    assert.ok(order('памятник галактион').includes('tabMonument'));
    assert.deepEqual(order('иллюзиями тип:улица'), [], 'only places are matched by English words');
  });
  test('search-only name: transliterated or missing ru only; dictionary words, else the kind', () => {
    const kinds = Object.fromEntries(PLACE_KINDS.map((k) => [k.code, k]));
    assert.equal(placeRuKindName({ ka: 'ილუზიების მუზეუმი', ru: 'Илузиебис Музеуми', en: 'Museum of Illusions' }, kinds.museum),
      'музей Museum of Illusions');
    assert.equal(placeRuKindName({ ka: 'ილუზიების მუზეუმი', en: 'Museum of Illusions' }, kinds.museum), 'музей Museum of Illusions');
    assert.equal(placeRuKindName({ ka: 'X', ru: 'Музей иллюзий', en: 'Museum of Illusions' }, kinds.museum), null, 'real Russian name');
    assert.equal(placeRuKindName({ ka: 'გალაკტიონ ტაბიძე', ru: 'Галактион Табидзе', en: 'Galaktion Tabidze' }, kinds.plaque),
      'доска Galaktion Tabidze');
    assert.equal(placeRuKindName({ ka: 'ნარიყალა', en: 'Narikala Fortress' }, kinds.landmark), 'крепость Narikala Fortress');
    assert.equal(placeRuKindName({ ka: 'ნარიყალა', en: 'Narikala' }, kinds.landmark), null, 'generic kind: no "место"');
    assert.equal(placeRuKindName({ ka: 'ქუჩა', en: 'Street' }, { code: 'street', ru: 'улица', group: 'street*' }), null);
    assert.equal(loanForm('illiuzii'), 'ilusi');
  });
});

describe('UI: address of a place and places of a street', () => {
  state.pack = pack;
  state.index = index;
  resetCaches();
  test('place card address from the house row (9th element)', () => {
    assert.ok(isPlace(K.illusions) && !isPlace(K.asatiani));
    assert.deepEqual(placeAddress(K.illusions), { street: K.asatiani, houses: ['8'] });
    assert.equal(placeAddressText(K.illusions), 'улица Ладо Асатиани, 8');
    assert.deepEqual(placeAddress(K.tabMonument), { street: K.tabidze, houses: [] }, 'par without a house');
    assert.equal(placeAddress(K.apollonPlaque), null);
  });
  test('street card lists its places: by house number first, then the rest', () => {
    assert.deepEqual(streetPlaces(K.asatiani), [{ p: K.illusions, house: '8' }]);
    assert.deepEqual(streetPlaces(K.tabidze).map((x) => x.p).sort((a, b) => a - b),
      [K.tabMonument, K.tabPlaque, K.tabUniversity].sort((a, b) => a - b));
    assert.deepEqual(streetPlaces(K.rustaveli), []);
  });
});
