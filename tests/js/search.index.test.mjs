import { test } from 'node:test';
import assert from 'node:assert/strict';
import { buildIndex, SHAPE } from '../../app/src/search/index.js';
import { Bitset } from '../../app/src/search/bitset.js';
import { compassBit } from '../../app/src/zone/geo.js';
import { loadMiniPack } from '../fixtures/pack_mini/_src/load.mjs';
import { makeSyntheticPack } from '../fixtures/pack_mini/_src/synth.mjs';

const arr = (b) => (b ? Array.from(b.toArray()) : null);
const idx = buildIndex(loadMiniPack());

test('basic fields on pack_mini', () => {
  assert.equal(idx.n, 26);
  assert.equal(idx.kinds.length, 26);
  assert.equal(idx.names[0][0], 'ლადო ასათიანის ქუჩა');
  assert.ok(idx.skel[0].includes('ladoasatiani'));
  assert.equal(idx.ord[2], 2);
  assert.equal(idx.par[2], 0);
  assert.equal(idx.par[0], -1);
  assert.equal(idx.hn[0], 11);
  assert.equal(idx.len[0] > 600, true);
  assert.equal(idx.displayName(11), 'река Кура');
  assert.equal(idx.all.count(), 26);
});

test('text search on skeletons: ka, ru, latin, old names', () => {
  assert.deepEqual(arr(idx.textBits('асати')), [0, 1, 2, 3, 4]);
  assert.deepEqual(arr(idx.textBits('ასათ')), [0, 1, 2, 3, 4]);
  assert.deepEqual(arr(idx.textBits('ASATI')), [0, 1, 2, 3, 4]);
  assert.deepEqual(arr(idx.textBits('ленин')), [6], 'old name');
  assert.deepEqual(arr(idx.textBits('leselidze')), [8], 'old latin name');
  assert.deepEqual(arr(idx.textBits('kura')), [11], 'alt name');
  assert.deepEqual(arr(idx.textBits('мтквари')), [11]);
  assert.equal(idx.textBits('--'), null);
  assert.deepEqual(arr(idx.oldTextBits('ленин')), [6]);
  assert.deepEqual(arr(idx.oldTextBits('асати')), []);
});

test('nameBits on base names and full names; textRank', () => {
  assert.deepEqual(arr(idx.nameBits('Ладо Асатиани', 'exact')), [0, 1, 2, 3, 4]);
  assert.deepEqual(arr(idx.nameBits('шесахвеви', 'suffix')), [1, 2]);
  assert.deepEqual(arr(idx.nameBits('ვაჟა', 'prefix')), []);
  assert.deepEqual(arr(idx.nameBits('пушкин', 'prefix')), [20, 24].filter((i) => idx.baseSkel[i].some((b) => b.startsWith('pushkin'))));
  const s = 'ladoasatiani';
  assert.equal(idx.textRank(0, s), 0);
  assert.equal(idx.textRank(0, 'lado'), 1);
  assert.equal(idx.textRank(0, 'asatiani'), 2);
  assert.equal(idx.textRank(6, 'lenin'), 3);
  assert.equal(idx.textRank(6, 'zzz'), 4);
  assert.deepEqual(idx.regexNames(6), ['თავისუფლების მოედანი', 'площадь Свободы', 'ლენინის მოედანი', 'площадь Ленина', 'ერევნის მოედანი', 'Эриванская площадь']);
});

test('kinds, groups and synonyms', () => {
  assert.deepEqual(arr(idx.resolveKind('переулок').bits), [1, 2]);
  assert.deepEqual(arr(idx.resolveKind('переулок*').bits), [1, 2, 3, 4]);
  assert.deepEqual(arr(idx.resolveKind('lane*').bits), [1, 2, 3, 4]);
  assert.deepEqual(arr(idx.resolveKind('съезд').bits), [4], 'synonym');
  assert.deepEqual(arr(idx.resolveKind('сад').bits), [20]);
  const st = idx.resolveKind('улица*');
  assert.equal(st.group, 'street*');
  assert.ok(st.codes.includes('avenue') && st.codes.includes('ascent'));
  assert.deepEqual(arr(st.bits), [0, 5, 7, 8, 9, 16, 17, 18, 19, 22, 23, 24]);
  assert.deepEqual(arr(idx.resolveKind('улица').bits), [0, 7, 8, 9, 16, 17, 19, 22, 23, 24]);
  assert.deepEqual(arr(idx.resolveKind('ქუჩა').bits), arr(idx.resolveKind('улица').bits));
  assert.deepEqual(arr(idx.resolveKind('микрорайон').bits), [14, 15, 25]);
  assert.equal(idx.resolveKind('улиза'), null);
  assert.ok(idx.suggestKind('улиза').includes('улица'));
  assert.deepEqual(arr(idx.groupBits.get('water*')), [11]);
  assert.ok(idx.kindNames().some((k) => k.name === 'переулок*' && k.count === 4));
});

test('districts and places', () => {
  assert.deepEqual(idx.resolveDistrict('мтац'), [0]);
  assert.deepEqual(idx.resolveDistrict('krts'), [1]);
  assert.deepEqual(idx.resolveDistrict('ისნ'), [2]);
  const a = idx.resolveArea('сололаки');
  assert.deepEqual(a.places, [0]);
  assert.ok(a.bits.has(3), 'dead end is in Sololaki');
  assert.ok(!a.bits.has(4), 'passage is outside');
  assert.equal(idx.resolveArea('нигде'), null);
  assert.ok(idx.suggestArea('сололак').includes('Сололаки'));
  assert.ok(idx.districtBits[1].has(9));
});

test('families and eponyms', () => {
  assert.deepEqual(Array.from(idx.membersOfFamily[0]), [0, 1, 2, 3, 4]);
  assert.deepEqual(arr(idx.familyClosure(Bitset.fromIndices(26, [2]))), [0, 1, 2, 3, 4]);
  assert.deepEqual(arr(idx.familyClosure(Bitset.fromIndices(26, [11]))), [11], 'no family keeps itself');
  assert.equal(idx.eponymOf[0], 0);
  assert.equal(idx.eponymOf[23], -1, 'family without eponym');
  assert.equal(idx.eponymOf[11], -1, 'no family');
  assert.deepEqual(arr(idx.eponymBits((e) => e.g === 'f')), [16]);
  assert.deepEqual(arr(idx.eponymBits((e) => e.o === 'fo')), [20, 24]);
  assert.deepEqual(arr(idx.eponymBits((e) => e.b <= 1300)), [5, 17]);
  assert.deepEqual(arr(idx.oldEponymBits((e) => (e.cat || []).includes('power.revolutionary'))), [6]);
  assert.deepEqual(arr(idx.oldEponymBits((e) => (e.occ || []).includes('генерал-полковник'))), [8]);
});

test('кто: resolution order (taxonomy id, alias, kind alias, occupation)', () => {
  const poets = [0, 1, 2, 3, 4, 5, 7, 20, 22, 24];
  assert.deepEqual(arr(idx.resolveWho('lit.poet').bits), poets);
  assert.equal(idx.resolveWho('lit.poet').via, 'taxonomy');
  assert.deepEqual(arr(idx.resolveWho('поэт').bits), poets);
  assert.deepEqual(arr(idx.resolveWho('поэтесса').bits), poets);
  assert.equal(idx.resolveWho('поэт').via, 'alias');
  assert.deepEqual(arr(idx.resolveWho('литература').bits), [...poets, 16].sort((a, b) => a - b), 'root includes children');
  assert.deepEqual(arr(idx.resolveWho('lit').bits), [...poets, 16].sort((a, b) => a - b));
  assert.deepEqual(arr(idx.resolveWho('царь').bits), [9, 17]);
  const persons = idx.resolveWho('персона');
  assert.equal(persons.via, 'kind');
  assert.ok(persons.bits.has(19), 'persons_multi counts as персона');
  assert.ok(!persons.bits.has(10), 'toponym is not a person');
  assert.deepEqual(arr(idx.resolveWho('топоним').bits), [10, 18]);
  const occ = idx.resolveWho('дипломат');
  assert.equal(occ.via, 'occupation');
  assert.deepEqual(arr(occ.bits), [22]);
  assert.deepEqual(arr(idx.resolveWho('деятельн').bits), [16], 'inner word of a multi-word occupation');
  assert.equal(idx.resolveWho('хормейстер'), null);
  assert.ok(idx.suggestWho('поет').includes('поэт'));
  assert.ok(idx.whoValues().some((w) => w.name === 'поэт' && w.count === 10));
});

test('crossings CSR, masks and crossingBits', () => {
  const { offsets, nbr, xi, mask } = idx.adj;
  assert.equal(offsets.length, 27);
  assert.equal(offsets[26], 2 * idx.cross.count);
  const of24 = Array.from(nbr.slice(offsets[24], offsets[25]));
  assert.deepEqual(of24.filter((x) => x === 20).length, 2, 'Pushkin St crosses the garden twice');
  for (let f = 0; f < 26; f++) {
    for (let k = offsets[f]; k < offsets[f + 1]; k++) {
      const t = xi[k];
      const isA = idx.cross.a[t] === f;
      assert.ok(isA || idx.cross.b[t] === f);
      assert.equal(mask[k], isA ? idx.cross.maskA[t] : idx.cross.maskB[t]);
      assert.equal(nbr[k], isA ? idx.cross.b[t] : idx.cross.a[t]);
    }
  }
  const square = Bitset.fromIndices(26, [6]);
  assert.deepEqual(arr(idx.crossingBits(square)), [5, 7, 8, 24]);
  assert.deepEqual(arr(idx.crossingBits(square, { dirBit: compassBit('юз') })), [7, 24]);
  assert.deepEqual(arr(idx.crossingBits(square, { crossingOk: () => false })), []);
  const lanesOfAsatiani = idx.crossingBits(Bitset.fromIndices(26, [0]), { dirBit: compassBit('n') });
  assert.deepEqual(arr(lanesOfAsatiani), [1, 4, 19], 'leave Asatiani to the north');
  assert.deepEqual(Array.from(idx.near.nbr.slice(idx.near.offsets[2], idx.near.offsets[3])), [16]);
});

test('lazy geometry / houses / areas attach after pack.ready', async () => {
  const pack = loadMiniPack({ late: false });
  const ix = buildIndex(pack);
  assert.equal(ix.hasGeom, false);
  assert.deepEqual(ix.geometry(0), []);
  assert.deepEqual(ix.houses(0), []);
  assert.equal(ix.shape(0), SHAPE.LINE, 'len > 0 -> line before geometry');
  assert.equal(ix.shape(12), SHAPE.UNKNOWN);
  assert.equal(ix.districtRings(0), null);
  assert.equal(ix.placeRings(2).length, 1, 'circle even before areas');
  let changed = 0;
  ix.onChange(() => { changed++; });
  // representative-point grid works before geometry
  assert.ok(ix.nearPointBits(0.0 + ix.cLat[12], ix.cLon[12], 50).has(12));
  await pack.finish();
  await ix.ready;
  assert.equal(changed, 1);
  assert.equal(ix.hasGeom && ix.hasHouses && ix.hasAreas, true);
  assert.deepEqual(ix.geometry(0), [[[41.6902, 44.793], [41.6905, 44.798], [41.6908, 44.801]]]);
  assert.equal(ix.houses(0).length, 11);
  assert.equal(ix.placeHouses(2).length, 2);
  assert.equal(ix.shape(0), SHAPE.LINE);
  assert.equal(ix.shape(6), SHAPE.POLYGON);
  assert.equal(ix.shape(13), SHAPE.POLYGON);
  assert.equal(ix.shape(12), SHAPE.POINT);
  assert.equal(ix.shape(25), SHAPE.POINT);
  assert.equal(ix.districtRings(0).length, 1);
  assert.equal(ix.placeRings(1).length, 2, 'outer ring + hole');
  assert.equal(ix.placeRings(2)[0].length, 33, 'place without polygon -> 300 m circle');
});

test('near: grid over densified geometry', () => {
  const metro = idx.resolveKind('метро').bits;
  const near300 = idx.nearBits(metro, 300);
  for (const i of [5, 6, 7, 8, 12, 20, 24, 4]) assert.ok(near300.has(i), `${idx.displayName(i)} within 300 m of metro`);
  assert.ok(!near300.has(0), 'Asatiani ends 311 m from the metro');
  assert.ok(!near300.has(11), 'river is far');
  assert.ok(!idx.nearBits(metro, 300, { includeSelf: false }).has(12));
  const none = idx.nearBits(metro, 300, { inside: () => false });
  assert.equal(none.count(), 0, 'source restricted to the zone');
  const me = idx.nearPointBits(41.6905, 44.7980, 30);
  assert.deepEqual(arr(me), [0, 7], 'Asatiani x Tabidze crossing; area rings are far from this point');
  assert.ok(idx.distanceTo(0, 41.6905, 44.7980) < 5);
});

test('fuzzy suggestions (minisearch)', () => {
  const s = idx.suggest('asatiany');
  assert.ok(s.length > 0 && [0, 1, 2, 3, 4].includes(s[0].i));
  assert.equal(idx.suggest('руставел')[0].i, 5);
  assert.deepEqual(idx.suggest(''), []);
});

test('nameRank sorts by ru name', () => {
  const order = Array.from({ length: 26 }, (_, i) => i).sort((a, b) => idx.nameRank[a] - idx.nameRank[b]);
  assert.equal(idx.displayName(order[0]), '1-й переулок Ладо Асатиани');
  assert.equal(idx.displayName(order[25]), 'улица Читадзе');
});

test('buildIndex on a pack of real size < 300 ms', () => {
  const pack = makeSyntheticPack();
  let vertices = 0;
  for (const g of pack.geom.g) for (const s of g) vertices += s.length; // encoded chars, sanity only
  assert.equal(pack.core.f.length, 5600);
  assert.ok(vertices > 100000);
  const times = [];
  let ix;
  for (let r = 0; r < 5; r++) { const t0 = performance.now(); ix = buildIndex(pack); times.push(performance.now() - t0); }
  times.sort((a, b) => a - b);
  console.log(`# buildIndex real-size: min ${times[0].toFixed(1)} ms, median ${times[2].toFixed(1)} ms`);
  // min of 5 = the uncontended cost (test files and other processes run in parallel)
  assert.ok(times[0] < 300, `min ${times[0].toFixed(1)} ms`);
  const t1 = performance.now();
  const b = ix.textBits('შვილ');
  const tText = performance.now() - t1;
  assert.ok(b.count() > 0);
  assert.ok(tText < 20, `textBits ${tText.toFixed(1)} ms`);
  const t2 = performance.now();
  ix.warmup();
  console.log(`# warmup (base names, name order, near grid): ${(performance.now() - t2).toFixed(1)} ms`);
});
