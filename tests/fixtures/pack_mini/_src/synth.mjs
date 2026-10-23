// Synthetic pack of real size (for benchmarks): ~5600 features, ~89k vertices, ~40k houses,
// ~8700 crossings, ~2100 eponyms over the Tbilisi bbox. Deterministic (seeded PRNG).
import { encodePolyline } from '../../../../app/src/zone/geo.js';
import { kaToRu, kaToLat } from '../../../../app/src/search/normalize.js';
import { readMini } from './load.mjs';

function mulberry32(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const SYL = ['ა', 'ბა', 'გი', 'და', 'ვე', 'ზო', 'თა', 'ია', 'კა', 'ლი', 'მე', 'ნო', 'პა', 'რი', 'სა', 'ტუ', 'ფი', 'ქე',
  'ღო', 'ყა', 'შვი', 'ჩი', 'ცე', 'ძე', 'წყა', 'ჭა', 'ხი', 'ჯა', 'ჰე', 'ლა', 'ძის', 'შვილის'];

export function makeSyntheticPack({ features = 5600, vertices = 89000, houses = 40000, crossings = 8700, eponyms = 2100, seed = 7 } = {}) {
  const rnd = mulberry32(seed);
  const mini = readMini('core.json');
  const kinds = mini.kinds;
  const ep = readMini('eponyms.json');
  const leafCats = [];
  for (const root of ep.taxonomy) for (const c of root.children || []) leafCats.push(c.id);
  const word = () => { let s = ''; const k = 2 + Math.floor(rnd() * 3); for (let i = 0; i < k; i++) s += SYL[Math.floor(rnd() * SYL.length)]; return s; };
  const E = [];
  for (let e = 0; e < eponyms; e++) {
    const ka = word() + ' ' + word();
    E.push({ ka, ru: kaToRu(ka), en: kaToLat(ka, true), k: rnd() < 0.8 ? 'person' : 'toponym',
      cat: [leafCats[Math.floor(rnd() * leafCats.length)]], occ: ['поэт', 'инженер', 'хормейстер', 'художник'].slice(0, 1 + Math.floor(rnd() * 3)),
      g: rnd() < 0.9 ? 'm' : 'f', b: 1700 + Math.floor(rnd() * 250), d: 1800 + Math.floor(rnd() * 200), o: rnd() < 0.8 ? 'ge' : 'fo',
      desc: 'синтетический эпоним', src: 'synth', conf: 0.5, ai: 0 });
  }
  const famCount = Math.floor(features * 0.7);
  const fam = [];
  for (let j = 0; j < famCount; j++) { const e = Math.floor(rnd() * eponyms); fam.push({ ka: E[e].ka + 'ის', ru: E[e].ru, e }); }
  const f = [], g = [];
  const perFeature = vertices / features;
  const lineKinds = [0, 0, 0, 0, 1, 5, 6, 9, 9, 10, 11];
  for (let i = 0; i < features; i++) {
    const fm = Math.floor(rnd() * famCount);
    const k = lineKinds[Math.floor(rnd() * lineKinds.length)];
    const nv = Math.max(2, Math.round(perFeature * (0.4 + rnd() * 1.2)));
    let lat = 41.65 + rnd() * 0.15, lon = 44.70 + rnd() * 0.20;
    const pts = [[lat, lon]];
    let dir = rnd() * Math.PI * 2;
    for (let v = 1; v < nv; v++) {
      dir += (rnd() - 0.5) * 0.6;
      lat += Math.cos(dir) * 25 / 111195; lon += Math.sin(dir) * 25 / 83000;
      pts.push([Math.round(lat * 1e5) / 1e5, Math.round(lon * 1e5) / 1e5]);
    }
    let a = Infinity, b = Infinity, c = -Infinity, d = -Infinity;
    for (const p of pts) { a = Math.min(a, p[0]); b = Math.min(b, p[1]); c = Math.max(c, p[0]); d = Math.max(d, p[1]); }
    const base = fam[fm].ka;
    const ka = base + ' ' + kinds[k].ka;
    f.push({ ka, ru: kinds[k].ru + ' ' + fam[fm].ru, en: kaToLat(base, true) + ' Street', k, fam: fm, d: [Math.floor(rnd() * 10)],
      p: [Math.floor(rnd() * 60)], len: Math.round(25 * (nv - 1)), c: [Math.round(pts[0][0] * 1e5), Math.round(pts[0][1] * 1e5)],
      bb: [a, b, c, d].map((x) => Math.round(x * 1e5)), hn: 0,
      ...(rnd() < 0.1 ? { old: [{ ka: word() + 'ის ქუჩა', ru: 'улица ' + word(), from: 1930, to: 1990 }] } : {}),
      ...(rnd() < 0.3 ? { alt: [kaToLat(base, true) + ' St'] } : {}) });
    g.push([encodePolyline(pts)]);
  }
  const h = {};
  for (let r = 0; r < houses; r++) {
    const i = Math.floor(rnd() * features);
    const n = 1 + Math.floor(rnd() * 120);
    const lat = f[i].c[0] + Math.floor((rnd() - 0.5) * 100), lon = f[i].c[1] + Math.floor((rnd() - 0.5) * 100);
    (h[i] || (h[i] = [])).push([String(n), n, '', '', lat, lon]);
    f[i].hn++;
  }
  const x = [], xp = [], xd = [];
  for (let t = 0; t < crossings; t++) {
    const p = Math.floor(rnd() * features);
    let q = Math.floor(rnd() * features);
    if (q === p) q = (q + 1) % features;
    x.push(Math.min(p, q), Math.max(p, q));
    xp.push(f[p].c[0], f[p].c[1]);
    xd.push(1 << Math.floor(rnd() * 8), 1 << Math.floor(rnd() * 8));
  }
  const districts = Array.from({ length: 10 }, (_, i) => ({ ka: 'რაიონი ' + i, ru: 'Район ' + i, en: 'District ' + i }));
  const places = Array.from({ length: 60 }, (_, i) => ({ ka: 'ადგილი ' + i, ru: 'Место ' + i, en: 'Place ' + i, kind: 'neighbourhood',
    parent: i % 10, c: [4170000 + i * 100, 4480000 + i * 100], km2: 0.3 }));
  const core = { v: 1, kinds, districts, places, f, fam, x, xp, xd, near: [] };
  const pack = { manifest: { v: 1, version: 'synthetic', files: {} }, core,
    eponyms: { v: 1, taxonomy: ep.taxonomy, kinds: ep.kinds, e: E },
    geom: { v: 1, prec: 5, g }, houses: { v: 1, h, place: {} }, areas: { v: 1, districts: [], places: [] } };
  pack.ready = Promise.resolve(pack);
  return pack;
}

/** Zone polygon: an irregular blob of `n` vertices with radius r metres around (lat, lon). */
export function blobZone(lat, lon, r, n = 120, seed = 3) {
  const rnd = mulberry32(seed);
  const pts = [];
  for (let i = 0; i < n; i++) {
    const a = (2 * Math.PI * i) / n;
    const rr = r * (0.75 + 0.25 * Math.sin(3 * a) + 0.1 * (rnd() - 0.5));
    pts.push([lat + Math.cos(a) * rr / 111195, lon + Math.sin(a) * rr / 83000]);
  }
  return pts;
}
