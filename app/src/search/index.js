// In-memory search index over the pack (docs/client-architecture.md "Core interfaces").
// buildIndex(pack) needs only core.json (+ eponyms.json); geom/houses/areas are attached later
// when pack.ready resolves (or by calling index.attach()).
import MiniSearch from 'minisearch';
import { Bitset } from './bitset.js';
import { skeleton, normRu, parseName, editDistance } from './normalize.js';
import { decodePolyline, isClosedRing, circlePolygon, PointGrid, densify } from '../zone/geo.js';
import { log } from '../log/log.js';

const AREA_KINDS = new Set(['square', 'garden', 'park', 'lake', 'district', 'neighbourhood', 'other']);
export const SHAPE = Object.freeze({ UNKNOWN: -1, POINT: 0, LINE: 1, POLYGON: 2 });

const now = () => (typeof performance !== 'undefined' ? performance.now() : Date.now());

function closest(word, candidates, limit = 5, maxDist = 3) {
  const w = normRu(word);
  const scored = [];
  const seen = new Set();
  for (const c of candidates) {
    if (seen.has(c)) continue;
    seen.add(c);
    const cc = normRu(c);
    let d = editDistance(w, cc, maxDist);
    if (cc.startsWith(w) && w.length >= 2) d = Math.min(d, 1);
    if (d <= maxDist) scored.push([d, c]);
  }
  scored.sort((a, b) => a[0] - b[0] || a[1].length - b[1].length || (a[1] < b[1] ? -1 : 1));
  return scored.slice(0, limit).map((x) => x[1]);
}

export class SearchIndex {
  constructor(pack, opts = {}) {
    const t0 = now();
    if (!pack || !pack.core || !Array.isArray(pack.core.f)) throw new Error('buildIndex: pack.core is missing');
    this.pack = pack;
    this.log = opts.log || log;
    const core = pack.core;
    const F = core.f;
    const n = (this.n = F.length);
    this.kinds = core.kinds || [];
    this.districts = core.districts || [];
    this.places = core.places || [];
    this.fam = core.fam || [];
    this.all = Bitset.full(n);
    this._listeners = new Set();

    // ---------------------------------------------------------------- per-feature scalars
    this.kindOf = new Uint8Array(n);
    this.ord = new Int16Array(n);
    this.par = new Int32Array(n).fill(-1);
    this.familyOf = new Int32Array(n).fill(-1);
    this.hn = new Int32Array(n);
    this.len = new Float32Array(n);
    this.cLat = new Float64Array(n);
    this.cLon = new Float64Array(n);
    this.bbox = new Float64Array(4 * n);
    this.names = new Array(n);
    this.skel = new Array(n);
    this.nameSkel = new Array(n);
    this._baseSkel = null; // lazy, see get baseSkel()
    this.oldSkel = new Array(n);
    this.sortKey = new Array(n);

    const kindTypeWords = [];
    for (const k of this.kinds) {
      if (k.ka) kindTypeWords.push(...String(k.ka).split(/[,\s]+/).filter(Boolean));
      if (k.ru) kindTypeWords.push(k.ru);
      for (const s of k.syn || []) kindTypeWords.push(s);
    }
    this._typeWords = kindTypeWords;

    for (let i = 0; i < n; i++) {
      const f = F[i];
      this.kindOf[i] = f.k | 0;
      this.ord[i] = f.o | 0;
      if (f.par !== undefined && f.par !== null) this.par[i] = f.par;
      if (f.fam !== undefined && f.fam !== null) this.familyOf[i] = f.fam;
      this.hn[i] = f.hn | 0;
      this.len[i] = f.len || 0;
      if (f.c) { this.cLat[i] = f.c[0] / 1e5; this.cLon[i] = f.c[1] / 1e5; }
      if (f.bb) for (let j = 0; j < 4; j++) this.bbox[4 * i + j] = f.bb[j] / 1e5;
      const cur = [];
      if (f.ka) cur.push(f.ka);
      if (f.ru) cur.push(f.ru);
      if (f.en) cur.push(f.en);
      if (f.alt) for (const a of f.alt) if (a) cur.push(a);
      const old = [];
      if (f.old) for (const o of f.old) { if (o.ka) old.push(o.ka); if (o.ru) old.push(o.ru); if (o.lat) old.push(o.lat); }
      this.names[i] = cur.concat(old);
      const cs = cur.map(skeleton);
      const os = old.map(skeleton);
      this.nameSkel[i] = cs;
      this.oldSkel[i] = os.join('|');
      this.skel[i] = cs.concat(os).join('|');
      this.sortKey[i] = String(f.ru || f.ka || f.en || '').toLowerCase().replace(/ё/g, 'е');
    }

    // ---------------------------------------------------------------- kinds and groups
    this.kindBits = new Map();
    this.groupBits = new Map();
    this.kindLookup = new Map(); // normRu(name) -> kind code
    this.kinds.forEach((k, idx) => {
      this.kindBits.set(k.code, new Bitset(n));
      if (k.group && !this.groupBits.has(k.group)) this.groupBits.set(k.group, new Bitset(n));
      const names = [k.code, k.ru, ...(k.syn || [])];
      if (k.ka) names.push(...String(k.ka).split(/[,\s]+/).filter(Boolean));
      for (const nm of names) if (nm) { const key = normRu(nm); if (!this.kindLookup.has(key)) this.kindLookup.set(key, k.code); }
      k._idx = idx;
    });
    for (let i = 0; i < n; i++) {
      const k = this.kinds[this.kindOf[i]];
      if (!k) continue;
      this.kindBits.get(k.code).set(i);
      if (k.group) this.groupBits.get(k.group).set(i);
    }

    // ---------------------------------------------------------------- districts, places
    this.districtBits = this.districts.map(() => new Bitset(n));
    this.placeBits = this.places.map(() => new Bitset(n));
    for (let i = 0; i < n; i++) {
      const f = F[i];
      if (f.d) for (const d of f.d) if (this.districtBits[d]) this.districtBits[d].set(i);
      if (f.p) for (const p of f.p) if (this.placeBits[p]) this.placeBits[p].set(i);
    }

    // ---------------------------------------------------------------- families
    const famCount = this.fam.length;
    const famSizes = new Int32Array(famCount);
    for (let i = 0; i < n; i++) if (this.familyOf[i] >= 0 && this.familyOf[i] < famCount) famSizes[this.familyOf[i]]++;
    this.membersOfFamily = Array.from(famSizes, (s) => new Int32Array(s));
    const famFill = new Int32Array(famCount);
    for (let i = 0; i < n; i++) { const fm = this.familyOf[i]; if (fm >= 0 && fm < famCount) this.membersOfFamily[fm][famFill[fm]++] = i; }

    // ---------------------------------------------------------------- eponyms
    this._buildEponyms(pack.eponyms);

    // ---------------------------------------------------------------- crossings (CSR)
    this._buildCrossings(core);

    // ---------------------------------------------------------------- lazy parts
    this.geomData = null; this.housesData = null; this.areasData = null;
    this._geomCache = new Array(n);
    this._shapeCache = new Int8Array(n).fill(-2);
    this._grid = null;
    this._mini = null;
    this._nameRank = null;
    this.attach();
    if (pack.ready && typeof pack.ready.then === 'function') {
      this.ready = pack.ready.then(() => { this.attach(); return this; }, (err) => {
        this.log.error('index', 'pack.ready failed: ' + (err && err.message), { stack: err && err.stack });
        return this;
      });
    } else this.ready = Promise.resolve(this);

    this.buildMs = now() - t0;
    this.log.info('index', `built: ${n} features, ${this.cross.count} crossings in ${this.buildMs.toFixed(1)} ms`);
    // In the browser, precompute the lazy parts when the main thread is idle (opts.warmup=false disables).
    if (opts.warmup !== false && typeof window !== 'undefined') {
      const idle = typeof requestIdleCallback === 'function' ? (cb) => requestIdleCallback(cb, { timeout: 3000 }) : (cb) => setTimeout(cb, 200);
      idle(() => { try { this.warmup(); } catch (e) { this.log.warn('index', 'warmup failed: ' + e.message); } });
      this.ready.then(() => idle(() => { try { if (this.hasGeom) this.pointGrid(); } catch (e) { this.log.warn('index', 'grid warmup failed: ' + e.message); } }));
    }
  }

  /**
   * Skeletons of the base names (current names without kind word / ordinal), per feature.
   * Computed on first use (~50 ms on the real pack); call warmup() in idle time to precompute.
   */
  get baseSkel() {
    if (this._baseSkel) return this._baseSkel;
    const t0 = now();
    const F = this.pack.core.f;
    const out = new Array(this.n);
    for (let i = 0; i < this.n; i++) {
      const f = F[i];
      const bases = [];
      for (const nm of [f.ka, f.ru, f.en, ...(f.alt || [])]) {
        if (!nm) continue;
        const b = skeleton(parseName(nm, this._typeWords).base);
        if (b && !bases.includes(b)) bases.push(b);
      }
      out[i] = bases;
    }
    this._baseSkel = out;
    this.log.debug('index', `base names in ${(now() - t0).toFixed(1)} ms`);
    return out;
  }

  /** Precompute lazy parts (base names, name order, near grid when geometry is loaded). */
  warmup() {
    void this.baseSkel;
    void this.nameRank;
    if (this.hasGeom) this.pointGrid();
    return this;
  }

  // ================================================================== eponyms
  _buildEponyms(ep) {
    const n = this.n;
    ep = ep || { taxonomy: [], kinds: {}, e: [] };
    this.eponyms = ep.e || [];
    const ne = this.eponyms.length;
    this.eponymOf = new Int32Array(n).fill(-1);
    for (let i = 0; i < n; i++) {
      const fm = this.familyOf[i];
      if (fm >= 0 && this.fam[fm] && this.fam[fm].e !== undefined && this.fam[fm].e !== null && this.fam[fm].e < ne) this.eponymOf[i] = this.fam[fm].e;
    }
    const sizes = new Int32Array(ne);
    for (let i = 0; i < n; i++) if (this.eponymOf[i] >= 0) sizes[this.eponymOf[i]]++;
    this.featuresOfEponym = Array.from(sizes, (s) => new Int32Array(s));
    const fill = new Int32Array(ne);
    for (let i = 0; i < n; i++) { const e = this.eponymOf[i]; if (e >= 0) this.featuresOfEponym[e][fill[e]++] = i; }
    // old-name eponyms per feature
    this.oldEponymsOf = new Array(n);
    const F = this.pack.core.f;
    for (let i = 0; i < n; i++) {
      const arr = [];
      if (F[i].old) for (const o of F[i].old) if (o.e !== undefined && o.e !== null && o.e < ne) arr.push(o.e);
      this.oldEponymsOf[i] = arr;
    }

    // taxonomy
    this.taxonomy = new Map();
    const walk = (nodes, parent, depth) => {
      for (const node of nodes || []) {
        if (!node || !node.id) continue;
        this.taxonomy.set(node.id, { id: node.id, ru: node.ru || node.id, aliases: node.aliases || [], parent, depth,
          children: (node.children || []).map((c) => c.id) });
        walk(node.children, node.id, depth + 1);
      }
    };
    walk(ep.taxonomy, null, 0);
    this.taxAliases = new Map(); // normRu(alias) -> Set<taxId>
    const addAlias = (a, id) => { const k = normRu(a); if (!k) return; if (!this.taxAliases.has(k)) this.taxAliases.set(k, new Set()); this.taxAliases.get(k).add(id); };
    for (const node of this.taxonomy.values()) { addAlias(node.ru, node.id); for (const a of node.aliases) addAlias(a, node.id); }

    // catBits with ancestors
    this.catBits = new Map();
    const ancestors = (id) => {
      const out = [];
      let cur = id;
      const guard = new Set();
      while (cur && !guard.has(cur)) {
        guard.add(cur);
        out.push(cur);
        const node = this.taxonomy.get(cur);
        if (node) cur = node.parent;
        else { const dot = cur.lastIndexOf('.'); cur = dot > 0 ? cur.slice(0, dot) : null; }
      }
      return out;
    };
    this.taxAncestors = ancestors;
    for (const id of this.taxonomy.keys()) this.catBits.set(id, new Bitset(n));
    const epCats = this.eponyms.map((e) => {
      const s = new Set();
      for (const c of (e && e.cat) || []) for (const a of ancestors(c)) s.add(a);
      return s;
    });
    for (let i = 0; i < n; i++) {
      const e = this.eponymOf[i];
      if (e < 0) continue;
      for (const c of epCats[e]) {
        if (!this.catBits.has(c)) this.catBits.set(c, new Bitset(n));
        this.catBits.get(c).set(i);
      }
    }

    // eponym kinds
    this.epKindBits = new Map();
    for (let i = 0; i < n; i++) {
      const e = this.eponymOf[i];
      if (e < 0) continue;
      const k = (this.eponyms[e] && this.eponyms[e].k) || 'unknown';
      if (!this.epKindBits.has(k)) this.epKindBits.set(k, new Bitset(n));
      this.epKindBits.get(k).set(i);
    }
    this.kindAliasBits = new Map();
    this.kindAliasCodes = new Map();
    const kinds = ep.kinds || {};
    for (const [code, aliases] of Object.entries(kinds)) {
      const codes = code === 'person' ? ['person', 'persons_multi'] : [code];
      const bits = new Bitset(n);
      for (const c of codes) if (this.epKindBits.has(c)) bits.ior(this.epKindBits.get(c));
      for (const a of [code, ...(aliases || [])]) {
        const k = normRu(a);
        if (!k) continue;
        if (this.kindAliasBits.has(k)) this.kindAliasBits.get(k).ior(bits);
        else this.kindAliasBits.set(k, bits.clone());
        this.kindAliasCodes.set(k, codes);
      }
    }

    // occupations (third level)
    const occ = new Map();
    for (let e = 0; e < ne; e++) {
      const ep1 = this.eponyms[e];
      if (!ep1 || !ep1.occ) continue;
      for (const o of ep1.occ) {
        const k = normRu(o);
        if (!k) continue;
        if (!occ.has(k)) occ.set(k, new Bitset(n));
        const b = occ.get(k);
        for (const i of this.featuresOfEponym[e]) b.set(i);
      }
    }
    this.occupations = [...occ.entries()].map(([word, bits]) => ({ word, bits })).sort((a, b) => (a.word < b.word ? -1 : a.word > b.word ? 1 : 0));
  }

  // ================================================================== crossings
  _buildCrossings(core) {
    const n = this.n;
    const x = core.x || [], xp = core.xp || [], xd = core.xd || [];
    const T = Math.floor(x.length / 2);
    const a = new Int32Array(T), b = new Int32Array(T), lat = new Float64Array(T), lon = new Float64Array(T);
    const maskA = new Uint8Array(T), maskB = new Uint8Array(T);
    const deg = new Int32Array(n + 1);
    for (let t = 0; t < T; t++) {
      a[t] = x[2 * t]; b[t] = x[2 * t + 1];
      lat[t] = (xp[2 * t] || 0) / 1e5; lon[t] = (xp[2 * t + 1] || 0) / 1e5;
      maskA[t] = xd[2 * t] | 0; maskB[t] = xd[2 * t + 1] | 0;
      if (a[t] >= 0 && a[t] < n) deg[a[t] + 1]++;
      if (b[t] >= 0 && b[t] < n) deg[b[t] + 1]++;
    }
    for (let i = 0; i < n; i++) deg[i + 1] += deg[i];
    const offsets = deg;
    const m = offsets[n];
    const nbr = new Int32Array(m), xi = new Int32Array(m), mask = new Uint8Array(m);
    const fill = offsets.slice(0, n);
    for (let t = 0; t < T; t++) {
      const p = a[t], q = b[t];
      if (p < 0 || p >= n || q < 0 || q >= n) continue;
      let k = fill[p]++; nbr[k] = q; xi[k] = t; mask[k] = maskA[t];
      k = fill[q]++; nbr[k] = p; xi[k] = t; mask[k] = maskB[t];
    }
    this.cross = { count: T, a, b, lat, lon, maskA, maskB };
    this.adj = { offsets, nbr, xi, mask };

    const near = core.near || [];
    const nd = new Int32Array(n + 1);
    for (let k = 0; k + 1 < near.length; k += 2) { nd[near[k] + 1]++; nd[near[k + 1] + 1]++; }
    for (let i = 0; i < n; i++) nd[i + 1] += nd[i];
    const nn = new Int32Array(nd[n]);
    const nf = nd.slice(0, n);
    for (let k = 0; k + 1 < near.length; k += 2) { const p = near[k], q = near[k + 1]; nn[nf[p]++] = q; nn[nf[q]++] = p; }
    this.near = { offsets: nd, nbr: nn };
  }

  /** Features crossing any feature of `bits` (optionally filtered by crossing predicate / direction bit). */
  crossingBits(bits, { crossingOk = null, dirBit = -1 } = {}) {
    const out = new Bitset(this.n);
    const { offsets, nbr, xi, mask } = this.adj;
    for (let f = 0; f < this.n; f++) {
      for (let k = offsets[f]; k < offsets[f + 1]; k++) {
        if (!bits.has(nbr[k])) continue;
        if (dirBit >= 0 && !((mask[k] >>> dirBit) & 1)) continue;
        if (crossingOk && !crossingOk(xi[k])) continue;
        out.set(f);
        break;
      }
    }
    return out;
  }

  // ================================================================== lazy parts
  /** Attach geom/houses/areas (from the given object or from this.pack) once they are loaded. */
  attach(parts) {
    const src = parts || this.pack;
    let changed = false;
    if (src.geom && src.geom !== this.geomData) {
      this.geomData = src.geom; this._geomCache = new Array(this.n); this._shapeCache.fill(-2); this._grid = null; changed = true;
      if (parts && parts.geom) this.pack.geom = parts.geom;
    }
    if (src.houses && src.houses !== this.housesData) {
      this.housesData = src.houses; changed = true;
      if (parts && parts.houses) this.pack.houses = parts.houses;
    }
    if (src.areas && src.areas !== this.areasData) {
      this.areasData = src.areas; this._areaCache = new Map(); changed = true;
      if (parts && parts.areas) this.pack.areas = parts.areas;
    }
    if (changed) {
      this.log.info('index', `attached: geom=${!!this.geomData} houses=${!!this.housesData} areas=${!!this.areasData}`);
      for (const cb of this._listeners) { try { cb(this); } catch (e) { this.log.error('index', 'listener failed: ' + e.message); } }
    }
    return this;
  }

  get hasGeom() { return !!this.geomData; }
  get hasHouses() { return !!this.housesData; }
  get hasAreas() { return !!this.areasData; }

  /** Subscribe to attach() changes; returns an unsubscribe function. */
  onChange(cb) { this._listeners.add(cb); return () => this._listeners.delete(cb); }

  /** Decoded geometry of feature i: Array<Array<[lat, lon]>> ([] before geom.json is loaded). */
  geometry(i) {
    if (!this.geomData) return [];
    let g = this._geomCache[i];
    if (g) return g;
    const parts = (this.geomData.g && this.geomData.g[i]) || [];
    const prec = this.geomData.prec || 5;
    g = [];
    for (const s of parts) {
      try { g.push(decodePolyline(s, prec)); } catch (e) { this.log.warn('index', `bad polyline in feature ${i}: ${e.message}`); }
    }
    this._geomCache[i] = g;
    return g;
  }

  /** SHAPE.POINT | LINE | POLYGON (or UNKNOWN before geometry is loaded and len is 0). */
  shape(i) {
    const c = this._shapeCache[i];
    if (c !== -2) return c;
    let s;
    if (!this.geomData) return this.len[i] > 0 ? SHAPE.LINE : SHAPE.UNKNOWN;
    const g = this.geometry(i);
    const code = this.kinds[this.kindOf[i]] ? this.kinds[this.kindOf[i]].code : '';
    if (!g.length) s = SHAPE.UNKNOWN;
    else if (g.every((p) => p.length === 1)) s = SHAPE.POINT;
    else if (g.every((p) => isClosedRing(p)) && (this.len[i] === 0 || AREA_KINDS.has(code))) s = SHAPE.POLYGON;
    else s = SHAPE.LINE;
    this._shapeCache[i] = s;
    return s;
  }

  /** Raw house rows of feature i: [raw, n, lit, sub, lat_e5, lon_e5, n_to?, corp?] ([] if not loaded). */
  houses(i) {
    if (!this.housesData || !this.housesData.h) return [];
    return this.housesData.h[String(i)] || [];
  }

  /** House rows of place p (addresses with addr:place only). */
  placeHouses(p) {
    if (!this.housesData || !this.housesData.place) return [];
    return this.housesData.place[String(p)] || [];
  }

  _decodeAreaPolys(entry) {
    const rings = [];
    for (const poly of (entry && entry.poly) || []) for (const r of poly) { try { rings.push(decodePolyline(r)); } catch (e) { this.log.warn('index', 'bad area ring: ' + e.message); } }
    return rings;
  }

  /** Flat rings (even-odd: outer rings and holes) of district d, or null if not available. */
  districtRings(d) {
    if (!this.areasData) return null;
    const key = 'd' + d;
    if (this._areaCache.has(key)) return this._areaCache.get(key);
    const rings = this._decodeAreaPolys((this.areasData.districts || [])[d]);
    const res = rings.length ? rings : null;
    this._areaCache.set(key, res);
    return res;
  }

  /** Flat rings of place p; a place without a polygon gets a 300 m circle around core.places[p].c. */
  placeRings(p) {
    const key = 'p' + p;
    if (this._areaCache && this._areaCache.has(key)) return this._areaCache.get(key);
    let rings = this.areasData ? this._decodeAreaPolys((this.areasData.places || [])[p]) : [];
    if (!rings.length) {
      const pl = this.places[p];
      if (!pl || !pl.c) return null;
      rings = [circlePolygon([pl.c[0] / 1e5, pl.c[1] / 1e5], 300)];
    }
    if (this._areaCache) this._areaCache.set(key, rings);
    return rings;
  }

  // ================================================================== resolution helpers for the DSL
  /** Bitset of features whose name skeletons (ka, ru, en, alt, old) contain skeleton(word); null if word has no skeleton. */
  textBits(word) {
    const s = skeleton(word);
    if (!s) return null;
    const out = new Bitset(this.n);
    const sk = this.skel;
    for (let i = 0; i < this.n; i++) if (sk[i].indexOf(s) >= 0) out.set(i);
    return out;
  }

  /** Bitset of features with an old name whose skeleton contains skeleton(word). */
  oldTextBits(word) {
    const s = skeleton(word);
    if (!s) return null;
    const out = new Bitset(this.n);
    for (let i = 0; i < this.n; i++) if (this.oldSkel[i] && this.oldSkel[i].indexOf(s) >= 0) out.set(i);
    return out;
  }

  /**
   * Name match on current names (ka, ru, en, alt) and their base names (without kind word / ordinal).
   * mode: 'prefix' | 'suffix' | 'exact' | 'substring'.
   */
  nameBits(word, mode = 'substring') {
    const s = skeleton(word);
    if (!s) return null;
    const out = new Bitset(this.n);
    const test = mode === 'prefix' ? (x) => x.startsWith(s) : mode === 'suffix' ? (x) => x.endsWith(s)
      : mode === 'exact' ? (x) => x === s : (x) => x.indexOf(s) >= 0;
    for (let i = 0; i < this.n; i++) {
      if (this.baseSkel[i].some(test) || this.nameSkel[i].some(test)) out.set(i);
    }
    return out;
  }

  /** Relevance of feature i for skeleton s: 0 exact base name, 1 prefix, 2 substring, 3 old/other, 4 none. */
  textRank(i, s) {
    if (!s) return 4;
    const bases = this.baseSkel[i];
    if (bases.includes(s) || this.nameSkel[i].includes(s)) return 0;
    if (bases.some((b) => b.startsWith(s)) || this.nameSkel[i].some((b) => b.startsWith(s))) return 1;
    if (this.nameSkel[i].some((b) => b.indexOf(s) >= 0)) return 2;
    if (this.oldSkel[i].indexOf(s) >= 0) return 3;
    return 4;
  }

  /** Raw ka/ru names plus old ka/ru names of feature i (for regex queries). */
  regexNames(i) {
    const f = this.pack.core.f[i];
    const out = [];
    if (f.ka) out.push(f.ka);
    if (f.ru) out.push(f.ru);
    if (f.old) for (const o of f.old) { if (o.ka) out.push(o.ka); if (o.ru) out.push(o.ru); }
    return out;
  }

  /** Display name (ru if present, else ka). */
  displayName(i) {
    const f = this.pack.core.f[i];
    return (f && (f.ru || f.ka || f.en)) || '';
  }

  /**
   * Resolve a kind value of `тип:` ("переулок", "улица*", "street", "сад").
   * Returns {codes, bits, group} or null.
   */
  resolveKind(value) {
    let v = normRu(value);
    if (!v) return null;
    const star = v.endsWith('*');
    if (star) v = v.slice(0, -1).trim();
    if (this.groupBits.has(v + '*')) {
      const group = v + '*';
      return { codes: this.kinds.filter((k) => k.group === group).map((k) => k.code), bits: this.groupBits.get(group).clone(), group };
    }
    const code = this.kindLookup.get(v);
    if (!code) return null;
    const kind = this.kinds.find((k) => k.code === code);
    if (star && kind.group) {
      return { codes: this.kinds.filter((k) => k.group === kind.group).map((k) => k.code), bits: this.groupBits.get(kind.group).clone(), group: kind.group };
    }
    return { codes: [code], bits: this.kindBits.get(code).clone(), group: null };
  }

  /** Kind names for autocomplete / suggestions: [{name, code, group, count}]. */
  kindNames() {
    const out = [];
    for (const k of this.kinds) {
      const count = this.kindBits.get(k.code).count();
      for (const nm of [k.ru, ...(k.syn || [])]) if (nm) out.push({ name: nm, code: k.code, group: k.group || null, count });
    }
    const groups = new Map();
    for (const k of this.kinds) if (k.group && !groups.has(k.group)) groups.set(k.group, k);
    for (const [g, k] of groups) out.push({ name: k.ru + '*', code: null, group: g, count: this.groupBits.get(g).count() });
    return out;
  }

  suggestKind(value, limit = 5) { return closest(String(value).replace(/\*$/, ''), this.kindNames().map((k) => k.name), limit); }

  _areaMatches(list, value) {
    const s = skeleton(value);
    if (!s) return [];
    const out = [];
    list.forEach((a, i) => {
      const sks = [a.ka, a.ru, a.en].filter(Boolean).map((x) => skeleton(x));
      if (sks.some((x) => x.startsWith(s))) out.push({ i, rank: 0 });
      else if (sks.some((x) => x.indexOf(s) >= 0)) out.push({ i, rank: 1 });
    });
    const best = out.some((o) => o.rank === 0) ? out.filter((o) => o.rank === 0) : out;
    return best.map((o) => o.i);
  }

  /** District indices matching a `район:` value (prefix, else substring on ka/ru/en skeletons). */
  resolveDistrict(value) { return this._areaMatches(this.districts, value); }

  /** Place indices matching a value. */
  resolvePlace(value) { return this._areaMatches(this.places, value); }

  /** `в:` value -> {places:[...], districts:[...], bits} (bits from core f.p / f.d) or null. */
  resolveArea(value) {
    const places = this.resolvePlace(value);
    const districts = this.resolveDistrict(value);
    if (!places.length && !districts.length) return null;
    const bits = new Bitset(this.n);
    for (const p of places) bits.ior(this.placeBits[p]);
    for (const d of districts) bits.ior(this.districtBits[d]);
    return { places, districts, bits };
  }

  suggestArea(value, limit = 5) {
    const names = [];
    for (const a of [...this.places, ...this.districts]) for (const x of [a.ru, a.ka, a.en]) if (x) names.push(x);
    return closest(value, names, limit, 4);
  }

  /** All members of the families that contain any feature of `bits` (features without family keep themselves). */
  familyClosure(bits) {
    const out = bits.clone();
    const seen = new Set();
    bits.forEach((i) => {
      const fm = this.familyOf[i];
      if (fm < 0 || seen.has(fm)) return;
      seen.add(fm);
      for (const j of this.membersOfFamily[fm]) out.set(j);
    });
    return out;
  }

  /** Bitset of features whose eponym satisfies pred(eponym, eponymIndex). */
  eponymBits(pred) {
    const out = new Bitset(this.n);
    for (let e = 0; e < this.eponyms.length; e++) {
      const fs = this.featuresOfEponym[e];
      if (!fs.length) continue;
      if (pred(this.eponyms[e], e)) for (const i of fs) out.set(i);
    }
    return out;
  }

  /** Bitset of features with an old name whose eponym satisfies pred(eponym, eponymIndex). */
  oldEponymBits(pred) {
    const out = new Bitset(this.n);
    const cache = new Map();
    for (let i = 0; i < this.n; i++) {
      for (const e of this.oldEponymsOf[i]) {
        let ok = cache.get(e);
        if (ok === undefined) { ok = !!pred(this.eponyms[e], e); cache.set(e, ok); }
        if (ok) { out.set(i); break; }
      }
    }
    return out;
  }

  /** Union of occupation bitsets whose normalised word starts with the value; null when none. */
  occupationBits(prefix) {
    const p = normRu(prefix);
    if (!p) return null;
    let out = null;
    // binary search for the first word >= p
    const occ = this.occupations;
    let lo = 0, hi = occ.length;
    while (lo < hi) { const mid = (lo + hi) >> 1; if (occ[mid].word < p) lo = mid + 1; else hi = mid; }
    for (let k = lo; k < occ.length && occ[k].word.startsWith(p); k++) {
      if (!out) out = new Bitset(this.n);
      out.ior(occ[k].bits);
    }
    // also match inner words of multi-word occupations ("общественная деятельница" ~ "деятель")
    for (const o of occ) {
      if (o.word.indexOf(' ') < 0) continue;
      if (o.word.split(' ').some((w) => w.startsWith(p))) { if (!out) out = new Bitset(this.n); out.ior(o.bits); }
    }
    return out;
  }

  /**
   * Resolve a `кто:` value: taxonomy id, taxonomy alias (parents include children), eponym kind
   * alias, then raw occupation prefix. Returns {bits, via, ids} or null.
   */
  resolveWho(value) {
    const raw = String(value || '').trim().toLowerCase();
    if (!raw) return null;
    if (this.catBits.has(raw)) return { bits: this.catBits.get(raw).clone(), via: 'taxonomy', ids: [raw] };
    const v = normRu(raw);
    const ids = this.taxAliases.get(v);
    if (ids && ids.size) {
      const bits = new Bitset(this.n);
      for (const id of ids) if (this.catBits.has(id)) bits.ior(this.catBits.get(id));
      return { bits, via: 'alias', ids: [...ids] };
    }
    if (this.kindAliasBits.has(v)) return { bits: this.kindAliasBits.get(v).clone(), via: 'kind', ids: this.kindAliasCodes.get(v) };
    const occ = this.occupationBits(v);
    if (occ) return { bits: occ, via: 'occupation', ids: [] };
    return null;
  }

  /** Values for `кто:` autocomplete: [{name, count, via}]. */
  whoValues() {
    const out = [];
    for (const [alias, ids] of this.taxAliases) {
      const b = new Bitset(this.n);
      for (const id of ids) if (this.catBits.has(id)) b.ior(this.catBits.get(id));
      out.push({ name: alias, count: b.count(), via: 'alias' });
    }
    for (const [alias, bits] of this.kindAliasBits) out.push({ name: alias, count: bits.count(), via: 'kind' });
    for (const o of this.occupations) out.push({ name: o.word, count: o.bits.count(), via: 'occupation' });
    return out;
  }

  suggestWho(value, limit = 5) { return closest(value, this.whoValues().map((w) => w.name), limit); }

  /** Rank of every feature in name order (ru if present else ka), for fast sorting. */
  get nameRank() {
    if (this._nameRank) return this._nameRank;
    // Code-unit order of the lowercased key (ё folded to е) is the Russian alphabetical order, and
    // Mkhedruli code points follow the Georgian alphabet, so no (slow) Intl.Collator is needed.
    const idx = Array.from({ length: this.n }, (_, i) => i);
    const key = this.sortKey;
    idx.sort((a, b) => (key[a] < key[b] ? -1 : key[a] > key[b] ? 1 : a - b));
    const rank = new Int32Array(this.n);
    idx.forEach((f, r) => { rank[f] = r; });
    this._nameRank = rank;
    return rank;
  }

  // ================================================================== proximity
  /**
   * Uniform 100 m grid over densified geometry points of all features (built lazily; uses the
   * representative points while geom.json is not loaded).
   */
  pointGrid(stepM = 25) {
    if (this._grid && this._grid.stepM === stepM && this._grid.withGeom === this.hasGeom) return this._grid.grid;
    const t0 = now();
    const grid = new PointGrid(100);
    for (let i = 0; i < this.n; i++) {
      const g = this.hasGeom ? this.geometry(i) : [];
      if (g.length) for (const part of g) grid.addLine(part, i, stepM);
      else if (this.cLat[i] || this.cLon[i]) grid.add(this.cLat[i], this.cLon[i], i);
    }
    grid.build();
    this._grid = { grid, stepM, withGeom: this.hasGeom };
    this.log.info('index', `point grid: ${grid.size} points in ${(now() - t0).toFixed(1)} ms`);
    return grid;
  }

  /** Densified geometry points of feature i (or its representative point). */
  densePoints(i, stepM = 25) {
    const g = this.hasGeom ? this.geometry(i) : [];
    if (!g.length) return (this.cLat[i] || this.cLon[i]) ? [[this.cLat[i], this.cLon[i]]] : [];
    const out = [];
    for (const part of g) for (const p of densify(part, stepM)) out.push(p);
    return out;
  }

  /**
   * Features with any geometry point within r metres of any point of a feature in `bits`.
   * `inside(lat, lon)` optionally restricts the SOURCE points (e.g. to the zone).
   */
  nearBits(bits, r = 200, { inside = null, includeSelf = true } = {}) {
    const grid = this.pointGrid();
    const out = new Bitset(this.n);
    const seenCell = new Set();
    const snap = Math.max(5, r / 20); // source points closer than this are deduplicated
    bits.forEach((i) => {
      for (const p of this.densePoints(i)) {
        if (inside && !inside(p[0], p[1])) continue;
        const key = Math.round(p[0] * 111195 / snap) + ':' + Math.round(p[1] * 83000 / snap);
        if (seenCell.has(key)) continue;
        seenCell.add(key);
        grid.forEachWithin(p[0], p[1], r, (id) => { out.set(id); });
      }
    });
    if (!includeSelf) out.iandNot(bits);
    return out;
  }

  /** Features with any geometry point within r metres of (lat, lon). */
  nearPointBits(lat, lon, r = 200) {
    const out = new Bitset(this.n);
    this.pointGrid().forEachWithin(lat, lon, r, (id) => { out.set(id); });
    return out;
  }

  /** Minimal distance (m) from (lat, lon) to the densified geometry of feature i. */
  distanceTo(i, lat, lon) {
    let best = Infinity;
    const kx = 111195 * Math.cos(lat * Math.PI / 180), ky = 111195;
    for (const p of this.densePoints(i, 10)) {
      const dx = (p[1] - lon) * kx, dy = (p[0] - lat) * ky;
      const d = Math.sqrt(dx * dx + dy * dy);
      if (d < best) best = d;
    }
    return best;
  }

  // ================================================================== fuzzy suggestions
  /** Typo-tolerant suggestions (MiniSearch over name skeleton words): [{i, score}]. */
  suggest(text, limit = 5) {
    const q = String(text || '').trim();
    if (!q) return [];
    if (!this._mini) {
      const t0 = now();
      this._mini = new MiniSearch({
        fields: ['words'], idField: 'id',
        tokenize: (s) => s.split(' '),
        processTerm: (t) => skeleton(t) || null,
        searchOptions: { prefix: true, fuzzy: 0.25 },
      });
      const docs = [];
      for (let i = 0; i < this.n; i++) {
        const words = new Set();
        for (const nm of this.names[i]) for (const w of String(nm).split(/[\s\-]+/)) if (w) words.add(w);
        docs.push({ id: i, words: [...words].join(' ') });
      }
      this._mini.addAll(docs);
      this.log.info('index', `minisearch built in ${(now() - t0).toFixed(1)} ms`);
    }
    return this._mini.search(q.split(/[\s\-]+/).filter(Boolean).join(' ')).slice(0, limit).map((r) => ({ i: r.id, score: r.score }));
  }
}

/** Build the search index for a pack ({manifest, core, eponyms, geom?, houses?, areas?, ready?}). */
export function buildIndex(pack, opts) {
  return new SearchIndex(pack, opts);
}
