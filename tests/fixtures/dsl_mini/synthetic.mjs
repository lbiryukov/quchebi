// Synthetic pack of real size (~5 600 features, ~8 700 crossings, ~40 000 house rows,
// ~2 100 eponyms) for DSL performance tests. Deterministic (seeded RNG). Names are random
// Georgian syllables with ru/en transliterations; geometry is random short polylines over Tbilisi.
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { encodePolyline, lineLength, bboxOf } from '../../../app/src/zone/geo.js';
import { kaToRu, kaToLat } from '../../../app/src/search/normalize.js';

const HERE = dirname(fileURLToPath(import.meta.url));
const MINI = join(HERE, '..', 'pack_mini');
const e5 = (x) => Math.round(x * 1e5);

function mulberry32(seed) {
  return () => {
    seed |= 0; seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const SYL = ['ბა', 'გი', 'და', 'ვე', 'ზო', 'თი', 'კა', 'ლა', 'მი', 'ნო', 'პა', 'რე', 'სო', 'ტა', 'ფი', 'ქე', 'ღო', 'ყა',
  'შე', 'ჩი', 'ცა', 'ძე', 'წი', 'ჭა', 'ხო', 'ჯი', 'ჰა', 'ვაჟ', 'ჭავ', 'ძღვ', 'მცხ'];
const OCC = ['поэт', 'писатель', 'художник', 'композитор', 'дирижёр', 'хормейстер', 'инженер', 'врач', 'актёр', 'режиссёр',
  'историк', 'физик', 'математик', 'генерал', 'царь', 'святой', 'меценат', 'купец', 'педагог', 'архитектор'];

/** Build a synthetic pack object {manifest, core, eponyms, geom, houses}. */
export function syntheticPack({ features = 5600, crossings = 8700, houses = 40000, eponyms = 2100, seed = 7 } = {}) {
  const rnd = mulberry32(seed);
  const pick = (arr) => arr[Math.floor(rnd() * arr.length)];
  const mini = JSON.parse(readFileSync(join(MINI, 'core.json'), 'utf8'));
  const miniEp = JSON.parse(readFileSync(join(MINI, 'eponyms.json'), 'utf8'));
  const kinds = mini.kinds;
  const K = Object.fromEntries(kinds.map((k, i) => [k.code, i]));
  const taxIds = [];
  for (const g of miniEp.taxonomy) for (const c of g.children || []) taxIds.push(c.id);

  const word = () => { let s = ''; const n = 2 + Math.floor(rnd() * 3); for (let k = 0; k < n; k++) s += pick(SYL); return s; };
  // eponyms
  const E = [];
  for (let j = 0; j < eponyms; j++) {
    const ka = `${word()} ${word()}ი`;
    const cats = [pick(taxIds)];
    if (rnd() < 0.3) cats.push(pick(taxIds));
    const b = 1100 + Math.floor(rnd() * 880);
    E.push({ ka, ru: kaToRu(ka), en: kaToLat(ka, true), k: rnd() < 0.85 ? 'person' : pick(['toponym', 'concept', 'nature', 'persons_multi']),
      cat: cats, occ: [pick(OCC), pick(OCC)], g: rnd() < 0.08 ? 'f' : 'm', b, d: b + 30 + Math.floor(rnd() * 60),
      o: rnd() < 0.8 ? 'ge' : 'fo', desc: `${pick(OCC)} и ${pick(OCC)}, ${word()}`, src: 'synthetic', conf: 0.9, ai: 0 });
  }
  // families
  const famCount = Math.round(features * 0.42);
  const fam = [];
  for (let j = 0; j < famCount; j++) {
    const ka = `${word()}ის`;
    const fm = { ka, ru: kaToRu(ka) };
    if (rnd() < 0.85) fm.e = Math.floor(rnd() * eponyms);
    fam.push(fm);
  }
  // features
  const plan = [['street', 0.68], ['avenue', 0.03], ['embankment', 0.005], ['ascent', 0.01], ['descent', 0.01], ['lane', 0.15],
    ['dead_end', 0.04], ['passage', 0.01], ['square', 0.015], ['bridge', 0.008], ['garden', 0.01], ['park', 0.005],
    ['river', 0.002], ['stream', 0.004], ['metro', 0.004], ['neighbourhood', 0.012]];
  const F = [];
  const geom = [];
  const famStreets = new Map();
  for (let i = 0; i < features; i++) {
    let r = rnd();
    let code = 'street';
    for (const [c, p] of plan) { if (r < p) { code = c; break; } r -= p; }
    const k = K[code];
    const lat0 = 41.66 + rnd() * 0.12;
    const lon0 = 44.70 + rnd() * 0.18;
    let line;
    if (code === 'metro' || code === 'neighbourhood') line = [[lat0, lon0]];
    else {
      line = [[lat0, lon0]];
      const nv = 3 + Math.floor(rnd() * 10);
      let ang = rnd() * Math.PI * 2;
      for (let v = 1; v < nv; v++) {
        ang += (rnd() - 0.5) * 0.6;
        const step = 30 + rnd() * 60;
        const [pl, po] = line[line.length - 1];
        line.push([pl + (step * Math.cos(ang)) / 111195, po + (step * Math.sin(ang)) / 83000]);
      }
      if (code === 'square' || code === 'garden' || code === 'park') line.push(line[0]);
    }
    const famIdx = Math.floor(rnd() * famCount);
    const fm = fam[famIdx];
    const f = { ka: `${fm.ka} ${kinds[k].ka || 'ობიექტი'}`, ru: `${kinds[k].ru} ${fm.ru}`, en: `${kaToLat(fm.ka, true)} ${kinds[k].code}`,
      k, fam: famIdx, d: [Math.floor(rnd() * 10)], p: rnd() < 0.5 ? [Math.floor(rnd() * 60)] : [] };
    if (code === 'lane' || code === 'dead_end') {
      f.o = 1 + Math.floor(rnd() * 5);
      if (famStreets.has(famIdx)) f.par = famStreets.get(famIdx);
    } else if (code === 'street') famStreets.set(famIdx, i);
    const closed = line.length > 3 && line[0] === line[line.length - 1];
    f.len = line.length > 1 && !closed ? Math.round(lineLength(line)) : 0;
    const mid = line[Math.floor(line.length / 2)];
    f.c = [e5(mid[0]), e5(mid[1])];
    f.bb = bboxOf(line).map(e5);
    if (rnd() < 0.15) {
      const oka = `${word()}ის ქუჩა`;
      const o = { ka: oka, ru: `улица ${kaToRu(oka.split(' ')[0])}`, lat: `${kaToLat(oka.split(' ')[0], true)} Street`, from: 1921, to: 1990, src: 'synthetic' };
      if (rnd() < 0.6) o.e = Math.floor(rnd() * eponyms);
      f.old = [o];
    }
    F.push(f);
    geom.push([encodePolyline(line)]);
  }
  // houses on street-like features
  const h = {};
  const streetLike = F.map((f, i) => (kinds[f.k].group === 'street*' || kinds[f.k].group === 'lane*' ? i : -1)).filter((i) => i >= 0);
  let rows = 0;
  while (rows < houses) {
    const i = pick(streetLike);
    const list = h[String(i)] || (h[String(i)] = []);
    const n = 1 + list.length * (1 + Math.floor(rnd() * 2));
    const lit = rnd() < 0.08 ? pick(['ა', 'ბ', 'გ']) : '';
    const sub = rnd() < 0.05 ? String(1 + Math.floor(rnd() * 20)) : '';
    const raw = `${n}${lit}${sub ? '/' + sub : ''}`;
    list.push([raw, n, lit, sub, F[i].c[0] + Math.floor((rnd() - 0.5) * 60), F[i].c[1] + Math.floor((rnd() - 0.5) * 60)]);
    rows++;
  }
  for (const [i, list] of Object.entries(h)) F[+i].hn = list.length;
  for (const f of F) if (f.hn == null) f.hn = 0;
  // crossings
  const x = [];
  const xp = [];
  const xd = [];
  for (let t = 0; t < crossings; t++) {
    let a = Math.floor(rnd() * features);
    let b = Math.floor(rnd() * features);
    if (a === b) b = (b + 1) % features;
    if (a > b) [a, b] = [b, a];
    x.push(a, b);
    xp.push(F[a].c[0], F[a].c[1]);
    xd.push(1 << Math.floor(rnd() * 8) | 1 << Math.floor(rnd() * 8), 1 << Math.floor(rnd() * 8) | 1 << Math.floor(rnd() * 8));
  }
  const districts = Array.from({ length: 10 }, (_, j) => ({ ka: `${word()}ის რაიონი`, ru: `Район${j}`, en: `District${j}` }));
  const places = Array.from({ length: 60 }, (_, j) => ({ ka: word(), ru: `Место${j}`, en: `Place${j}`, kind: 'neighbourhood', parent: j % 10, c: [4170000, 4480000], km2: 0.5 }));
  const core = { v: 1, kinds, districts, places, f: F, fam, x, xp, xd, near: [] };
  return {
    manifest: { v: 1, version: 'synthetic', counts: { features, crossings, houses: rows, eponyms } },
    core,
    eponyms: { v: 1, taxonomy: miniEp.taxonomy, kinds: miniEp.kinds, e: E },
    geom: { v: 1, prec: 5, g: geom },
    houses: { v: 1, h, place: {} },
  };
}
