// DSL test fixture: tests/fixtures/pack_mini (by search-core) plus a few hand-made features
// needed by the golden examples of docs/dsl.md (хормейстер, дирижёр + old name «Крылова»,
// a scientist, an old-name eponym who built the Rioni HPP, five consonants in a row,
// Рижский сквер with a street leaving east, улица Чаргали, the Vazha-Pshavela family).
// Everything is built in memory from pack_mini, so the fixture follows pack_mini changes.
//
//   import { loadPack, loadIndex, F } from '../fixtures/dsl_mini/load.mjs';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { buildIndex } from '../../../app/src/search/index.js';
import { encodePolyline, lineLength, bboxOf } from '../../../app/src/zone/geo.js';

const HERE = dirname(fileURLToPath(import.meta.url));
const MINI = join(HERE, '..', 'pack_mini');
const e5 = (x) => Math.round(x * 1e5);

export const QUIET_LOG = { debug() {}, info() {}, warn() {}, error() {} };

function readMini() {
  const pack = {};
  for (const f of ['manifest', 'core', 'geom', 'houses', 'areas', 'eponyms']) {
    pack[f] = JSON.parse(readFileSync(join(MINI, `${f}.json`), 'utf8'));
  }
  return pack;
}

// Extra eponyms (appended to eponyms.e); keys are local names used below.
const EXTRA_EPONYMS = {
  kakhidze: { ka: 'ჯანსუღ კახიძე', ru: 'Джансуг Кахидзе', en: 'Jansug Kakhidze', k: 'person', cat: ['art.composer'],
    occ: ['дирижёр', 'хормейстер', 'композитор'], g: 'm', b: 1935, d: 2002, o: 'ge', desc: 'грузинский дирижёр и композитор', src: 'test', conf: 0.95, ai: 0 },
  krylov: { ka: 'ივან კრილოვი', ru: 'Иван Крылов', en: 'Ivan Krylov', k: 'person', cat: ['lit.writer'],
    occ: ['баснописец'], g: 'm', b: 1769, d: 1844, o: 'fo', desc: 'русский баснописец', src: 'test', conf: 0.9, ai: 0 },
  muskhelishvili: { ka: 'ნიკო მუსხელიშვილი', ru: 'Николай Мусхелишвили', en: 'Niko Muskhelishvili', k: 'person', cat: ['sci.engineer'],
    occ: ['математик', 'механик'], g: 'm', b: 1891, d: 1976, o: 'ge', desc: 'грузинский математик и механик', src: 'test', conf: 0.95, ai: 0 },
  aleksandrov: { ka: 'ივან ალექსანდროვი', ru: 'Иван Александров', en: 'Ivan Aleksandrov', k: 'person', cat: ['sci.engineer'],
    occ: ['инженер-гидротехник'], g: 'm', b: 1875, d: 1936, o: 'fo', desc: 'инженер-энергетик, руководил строительством Рионской ГЭС', src: 'test', conf: 0.85, ai: 1 },
  vazha: { ka: 'ვაჟა-ფშაველა', ru: 'Важа-Пшавела', en: 'Vazha-Pshavela', k: 'person', cat: ['lit.poet', 'lit.writer'],
    occ: ['поэт', 'писатель'], g: 'm', b: 1861, d: 1915, o: 'ge', desc: 'грузинский поэт и писатель', src: 'test', conf: 0.99, ai: 0 },
  riga: { ka: 'რიგა', ru: 'Рига', en: 'Riga', k: 'toponym', cat: ['place.foreign_city'], desc: 'столица Латвии', src: 'test', conf: 0.9, ai: 0 },
};

// Extra families: [key, ka, ru, eponymKey | null]
const EXTRA_FAMILIES = [
  ['kakhidze', 'ჯანსუღ კახიძის', 'Джансуга Кахидзе', 'kakhidze'],
  ['muskhelishvili', 'ნიკო მუსხელიშვილის', 'Мусхелишвили', 'muskhelishvili'],
  ['vazha', 'ვაჟა-ფშაველას', 'Важа-Пшавела', 'vazha'],
  ['riga', 'რიგის', 'Рижский', 'riga'],
  ['tsinamdz', 'ცინამძღვრიშვილის', 'Цинамдзгвришвили', null],
  ['chargali', 'ჩარგლის', 'Чаргали', null],
];

// Extra features. Coordinates are in Vake (north-west of pack_mini, outside all its areas).
const EXTRA_FEATURES = [
  { key: 'kakhidze', ka: 'ჯანსუღ კახიძის ქუჩა', ru: 'улица Джансуга Кахидзе', en: 'Jansug Kakhidze Street', kind: 'street', fam: 'kakhidze',
    line: [[41.7150, 44.7600], [41.7150, 44.7660]],
    old: [{ ka: 'კრილოვის ქუჩა', ru: 'улица Крылова', lat: 'Krylov Street', from: 1940, to: 1991, e: 'krylov', src: 'test' }],
    houses: [['2', 2, '', ''], ['24', 24, '', ''], ['26', 26, '', '']] },
  { key: 'muskhelishvili', ka: 'ნიკო მუსხელიშვილის ქუჩა', ru: 'улица Мусхелишвили', en: 'Niko Muskhelishvili Street', kind: 'street', fam: 'muskhelishvili',
    line: [[41.7135, 44.7660], [41.7135, 44.7700]],
    old: [{ ka: 'ალექსანდროვის ქუჩა', ru: 'улица Александрова', lat: 'Aleksandrov Street', from: 1937, to: 1990, e: 'aleksandrov', src: 'test' }],
    houses: [['1', 1, '', ''], ['2', 2, '', ''], ['3ა', 3, 'ა', '']] },
  { key: 'vazha', ka: 'ვაჟა-ფშაველას გამზირი', ru: 'проспект Важа-Пшавела', en: 'Vazha-Pshavela Avenue', kind: 'avenue', fam: 'vazha',
    line: [[41.7120, 44.7600], [41.7120, 44.7700]], houses: [['16', 16, '', ''], ['24', 24, '', '']] },
  { key: 'vazha1', ka: 'ვაჟა-ფშაველას I შესახვევი', ru: '1-й переулок Важа-Пшавела', en: 'Vazha-Pshavela 1st Lane', kind: 'lane', o: 1, fam: 'vazha', par: 'vazha',
    line: [[41.7120, 44.7650], [41.7132, 44.7650]], houses: [] },
  { key: 'vazhaDead', ka: 'ვაჟა-ფშაველას ჩიხი', ru: 'тупик Важа-Пшавела', en: 'Vazha-Pshavela Dead End', kind: 'dead_end', fam: 'vazha', par: 'vazha',
    line: [[41.7120, 44.7680], [41.7110, 44.7680]], houses: [] },
  { key: 'riga', ka: 'რიგის სკვერი', ru: 'Рижский сквер', en: 'Riga Square', kind: 'garden', fam: 'riga', area: true,
    line: [[41.7102, 44.7617], [41.7108, 44.7617], [41.7108, 44.7623], [41.7102, 44.7623], [41.7102, 44.7617]], houses: [] },
  { key: 'tsinamdz', ka: 'ცინამძღვრიშვილის ქუჩა', ru: 'улица Цинамдзгвришвили', en: 'Tsinamdzgvrishvili Street', kind: 'street', fam: 'tsinamdz',
    line: [[41.7105, 44.7623], [41.7105, 44.7680]], houses: [['16', 16, '', ''], ['18', 18, '', '']] },
  { key: 'chargali', ka: 'ჩარგლის ქუჩა', ru: 'улица Чаргали', en: 'Chargali Street', kind: 'street', fam: 'chargali',
    line: [[41.7100, 44.7620], [41.7160, 44.7620]], houses: [['16', 16, '', '']] },
];

// Extra crossings: [keyA, keyB, lat, lon, maskA, maskB]. Masks: bit 0 N, 1 NE, 2 E, 3 SE, 4 S, 5 SW, 6 W, 7 NW.
const N = 1, E = 4, S = 16, W = 64;
const EXTRA_CROSSINGS = [
  ['vazha', 'vazha1', 41.7120, 44.7650, E | W, N],
  ['vazha', 'vazhaDead', 41.7120, 44.7680, E | W, S],
  ['vazha', 'chargali', 41.7120, 44.7620, E | W, N | S],
  ['kakhidze', 'chargali', 41.7150, 44.7620, E | W, N | S],
  ['riga', 'tsinamdz', 41.7105, 44.7623, N | S, E],
  ['riga', 'chargali', 41.7102, 44.7620, E | W, N | S],
];

/** Feature indices of the extra features by key, plus pack_mini features used in tests. */
export const F = {
  // pack_mini (see tests/fixtures/pack_mini/_src/build.mjs)
  asatiani: 0, asatiani1: 1, asatiani2: 2, asatianiDead: 3, asatianiPass: 4, rustaveli: 5, freedom: 6,
  tabidze: 7, apkhazi: 8, erekle: 9, metekhi: 10, kura: 11, metro: 12, mtatsminda: 13, sololaki: 14,
  kala: 15, gabashvili: 16, gorgasali: 17, sololakiAsc: 18, zubalashvili: 19, pushkinSq: 20, rike: 21,
  besiki: 22, chitadze: 23, pushkin: 24, avlabari: 25,
};

/** pack_mini + extras, as a fresh object (safe to mutate). */
export function loadPack() {
  const pack = readMini();
  const core = pack.core;
  const ep = pack.eponyms;
  const kindIdx = Object.fromEntries(core.kinds.map((k, i) => [k.code, i]));
  const eIdx = {};
  for (const [key, e] of Object.entries(EXTRA_EPONYMS)) { eIdx[key] = ep.e.length; ep.e.push(e); }
  const famIdx = {};
  for (const [key, ka, ru, e] of EXTRA_FAMILIES) {
    famIdx[key] = core.fam.length;
    const fm = { ka, ru };
    if (e) fm.e = eIdx[e];
    core.fam.push(fm);
  }
  const base = core.f.length;
  EXTRA_FEATURES.forEach((x, k) => { F[x.key] = base + k; });
  for (const x of EXTRA_FEATURES) {
    const i = F[x.key];
    const bb = bboxOf(x.line);
    const mid = x.line[Math.floor(x.line.length / 2)];
    const f = {
      ka: x.ka, ru: x.ru, en: x.en, k: kindIdx[x.kind], fam: famIdx[x.fam], d: [], p: [],
      len: x.area ? 0 : Math.round(lineLength(x.line)),
      c: [e5(mid[0]), e5(mid[1])], bb: bb.map(e5), hn: x.houses.length,
    };
    if (x.o) f.o = x.o;
    if (x.par) f.par = F[x.par];
    if (x.old) f.old = x.old.map((o) => ({ ...o, e: eIdx[o.e] }));
    core.f.push(f);
    pack.geom.g[i] = [encodePolyline(x.line)];
    if (x.houses.length) {
      // house points spread along the line
      const [a, b] = [x.line[0], x.line[x.line.length - 1]];
      pack.houses.h[String(i)] = x.houses.map((r, k) => {
        const t = (k + 1) / (x.houses.length + 1);
        return [...r, e5(a[0] + (b[0] - a[0]) * t + 0.00005), e5(a[1] + (b[1] - a[1]) * t)];
      });
    }
  }
  for (const [ka, kb, lat, lon, ma, mb] of EXTRA_CROSSINGS) {
    let a = F[ka]; let b = F[kb]; let mA = ma; let mB = mb;
    if (a > b) { [a, b] = [b, a]; [mA, mB] = [mB, mA]; }
    core.x.push(a, b);
    core.xp.push(e5(lat), e5(lon));
    core.xd.push(mA, mB);
  }
  pack.manifest.counts.features = core.f.length;
  return pack;
}

/** Index over loadPack() (quiet logger). */
export function loadIndex() {
  return buildIndex(loadPack(), { log: QUIET_LOG });
}
