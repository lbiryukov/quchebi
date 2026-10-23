// Zone masks: which features / crossings / houses are inside the competition zone.
// Computed once when the zone is saved; queries only read the masks (docs/dsl.md "Zone mode").
//
// Rules (docs/dsl.md, plan "Зона"):
//   default: lines with >= 30 m or >= 50% of their length inside; points inside; polygons intersecting
//   touch:   any part inside
//   whole:   >= 99% of the length inside (points inside; polygons: whole ring inside)
import { Bitset } from '../search/bitset.js';
import { PreparedPolygon, polygonAreaKm2, toRings, isClosedRing } from './geo.js';
import { SHAPE } from '../search/index.js';
import { log } from '../log/log.js';

export const ZONE_RULES = Object.freeze(['default', 'touch', 'whole']);
const EPS_M = 0.05; // inside lengths below 5 cm are numerical noise (shared borders)

const now = () => (typeof performance !== 'undefined' ? performance.now() : Date.now());

function bboxOverlap(b, i, zb) {
  // b: Float64Array 4n [minLat, minLon, maxLat, maxLon]
  return !(b[4 * i + 2] < zb[0] || b[4 * i] > zb[2] || b[4 * i + 3] < zb[1] || b[4 * i + 1] > zb[3]);
}

function polygonIntersects(prepared, rings, clipInside, zoneRings) {
  if (clipInside > EPS_M) return true;
  if (prepared.anyVertexInside(rings)) return true;
  // zone completely inside the feature polygon
  const fp = new PreparedPolygon(rings);
  for (const zr of zoneRings) for (const p of zr) if (fp.contains(p[0], p[1])) return true;
  return false;
}

/**
 * @param {import('../search/index.js').SearchIndex} index
 * @param {Array} polygon  zone ring [[lat, lon], ...] (open or closed) or rings [outer, hole, ...]
 * @param {{rule?: 'default'|'touch'|'whole', minInsideM?: number, minShare?: number, wholeShare?: number}} opts
 * @returns ZoneMasks
 */
export function computeZoneMasks(index, polygon, opts = {}) {
  const t0 = now();
  const rule = ZONE_RULES.includes(opts.rule) ? opts.rule : 'default';
  const minInsideM = opts.minInsideM ?? 30;
  const minShare = opts.minShare ?? 0.5;
  const wholeShare = opts.wholeShare ?? 0.99;
  const zoneRings = toRings(polygon).filter((r) => r && r.length >= 3);
  if (!zoneRings.length) throw new Error('computeZoneMasks: polygon needs at least 3 vertices');
  const prepared = new PreparedPolygon(zoneRings);
  const zb = prepared.bbox;
  const n = index.n;
  const featDefault = new Bitset(n), featTouch = new Bitset(n), featWhole = new Bitset(n);
  const lenInside = new Float32Array(n);
  const bb = index.bbox;
  const hasGeom = index.hasGeom;
  let candidates = 0;

  for (let i = 0; i < n; i++) {
    if (!bboxOverlap(bb, i, zb)) continue;
    candidates++;
    const g = hasGeom ? index.geometry(i) : [];
    if (!g.length) {
      // no geometry yet: representative point decides
      if (prepared.contains(index.cLat[i], index.cLon[i])) {
        featDefault.set(i); featTouch.set(i); featWhole.set(i); lenInside[i] = index.len[i];
      }
      continue;
    }
    const shape = index.shape(i);
    if (shape === SHAPE.POINT) {
      if (prepared.anyVertexInside(g)) { featDefault.set(i); featTouch.set(i); featWhole.set(i); }
      continue;
    }
    if (shape === SHAPE.POLYGON) {
      const r = prepared.clipLine(g);
      if (polygonIntersects(prepared, g, r.inside, zoneRings)) {
        featDefault.set(i); featTouch.set(i);
        if (r.total > 0 && r.inside >= wholeShare * r.total) featWhole.set(i);
      }
      continue;
    }
    // lines (and mixed geometry): clip parts with more than one vertex, test lone points
    const lines = g.filter((p) => p.length > 1);
    const r = lines.length ? prepared.clipLine(lines) : { inside: 0, total: 0 };
    if (r.total <= 0) {
      if (prepared.anyVertexInside(g)) { featDefault.set(i); featTouch.set(i); featWhole.set(i); }
      continue;
    }
    const inside = r.inside > EPS_M ? Math.min(r.inside, r.total) : 0;
    lenInside[i] = inside;
    if (inside > 0) {
      featTouch.set(i);
      if (inside >= minInsideM || inside >= minShare * r.total) featDefault.set(i);
      if (inside >= wholeShare * r.total) featWhole.set(i);
    }
  }

  // crossings: by point
  const C = index.cross;
  const crossingIn = new Uint8Array(C.count);
  for (let t = 0; t < C.count; t++) if (prepared.contains(C.lat[t], C.lon[t])) crossingIn[t] = 1;

  const masks = {
    polygon,
    rings: zoneRings,
    bbox: zb,
    areaKm2: polygonAreaKm2(zoneRings),
    rule,
    prepared,
    featDefault, featTouch, featWhole,
    featRule: rule === 'touch' ? featTouch : rule === 'whole' ? featWhole : featDefault,
    lenInside,
    crossingIn,
    houseMask: null, // Map<featureIndex, Uint8Array> when houses are loaded
    counts: { features: 0, default: 0, touch: 0, whole: 0, houses: null, candidates, crossings: 0 },
    timeMs: 0,
    /** Point-in-zone for (lat, lon) or [lat, lon]. */
    contains(lat, lon) { return prepared.contains(lat, lon); },
    /** Mask for a given rule name ('default' | 'touch' | 'whole'). */
    forRule(r) { return r === 'touch' ? featTouch : r === 'whole' ? featWhole : featDefault; },
    /** Is house row (pack row array, or its index in index.houses(i)) of feature i inside the zone? */
    houseIn(i, row) {
      if (typeof row === 'number') {
        const m = masks.houseMask && masks.houseMask.get(i);
        if (m) return m[row] === 1;
        row = index.houses(i)[row];
        if (!row) return false;
      }
      return prepared.contains(row[4] / 1e5, row[5] / 1e5);
    },
    /** Is house row of place p inside the zone? */
    placeHouseIn(p, row) {
      if (typeof row === 'number') row = index.placeHouses(p)[row];
      return !!row && prepared.contains(row[4] / 1e5, row[5] / 1e5);
    },
    /** Rows of feature i inside the zone (pack rows). */
    housesInside(i) {
      const rows = index.houses(i);
      const m = masks.houseMask && masks.houseMask.get(i);
      if (m) return rows.filter((_, k) => m[k] === 1);
      return rows.filter((r) => prepared.contains(r[4] / 1e5, r[5] / 1e5));
    },
    /** (Re)compute house masks, e.g. after houses.json arrived (pack.ready). */
    refreshHouses() {
      if (!index.hasHouses) { masks.counts.houses = null; return masks; }
      const t1 = now();
      const hm = new Map();
      let total = 0;
      for (let i = 0; i < n; i++) {
        const rows = index.houses(i);
        if (!rows.length) continue;
        const m = new Uint8Array(rows.length);
        if (bboxOverlap(bb, i, zb)) {
          for (let k = 0; k < rows.length; k++) if (prepared.contains(rows[k][4] / 1e5, rows[k][5] / 1e5)) { m[k] = 1; total++; }
        }
        hm.set(i, m);
      }
      masks.houseMask = hm;
      masks.counts.houses = total;
      log.debug('zone', `house masks: ${total} houses inside in ${(now() - t1).toFixed(1)} ms`);
      return masks;
    },
    /** Recompute everything (e.g. after geom.json arrived). Returns a new masks object. */
    recompute(newOpts) { return computeZoneMasks(index, polygon, { ...opts, ...(newOpts || {}) }); },
  };
  masks.refreshHouses();
  masks.counts.default = featDefault.count();
  masks.counts.touch = featTouch.count();
  masks.counts.whole = featWhole.count();
  masks.counts.features = masks.featRule.count();
  let xin = 0;
  for (let t = 0; t < C.count; t++) xin += crossingIn[t];
  masks.counts.crossings = xin;
  masks.withGeom = hasGeom;
  masks.timeMs = now() - t0;
  log.info('zone', `masks: ${masks.counts.features} features (${rule}), ${masks.counts.houses ?? '?'} houses, ` +
    `${xin} crossings, ${masks.areaKm2.toFixed(2)} km2 in ${masks.timeMs.toFixed(1)} ms`, { candidates, vertices: zoneRings.reduce((s, r) => s + r.length, 0) });
  return masks;
}

/** Validate a zone polygon for the UI: {ok, warnings[], areaKm2, vertices}. */
export function checkZonePolygon(polygon, { minKm2 = 0.05 } = {}) {
  const rings = toRings(polygon);
  const warnings = [];
  const outer = rings[0] || [];
  const vertices = isClosedRing(outer) ? outer.length - 1 : outer.length;
  if (vertices < 3) return { ok: false, warnings: ['в зоне меньше трёх вершин'], areaKm2: 0, vertices };
  const areaKm2 = polygonAreaKm2(rings);
  if (areaKm2 < minKm2) warnings.push(`зона очень маленькая: ${areaKm2.toFixed(2)} км²`);
  return { ok: true, warnings, areaKm2, vertices };
}
