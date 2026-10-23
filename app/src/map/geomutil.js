// Planar helpers for zone editing on city scale: local metric projection, area, orientation,
// outward buffer ("+50 м запаса"), cleanup of self-intersections and union of area polygons.
// Polygons are open rings [[lat, lon], ...].
import simplify from 'simplify-js';
import kinks from '@turf/kinks';
import unkinkPolygon from '@turf/unkink-polygon';

const R = 6371008.8;
const DEG = Math.PI / 180;

export function projector(lat0, lon0) {
  const ky = R * DEG;
  const kx = R * DEG * Math.cos(lat0 * DEG);
  return {
    fwd: ([lat, lon]) => ({ x: (lon - lon0) * kx, y: (lat - lat0) * ky }),
    inv: ({ x, y }) => [lat0 + y / ky, lon0 + x / kx],
  };
}

function centroidish(ring) {
  let lat = 0;
  let lon = 0;
  for (const p of ring) { lat += p[0]; lon += p[1]; }
  return [lat / ring.length, lon / ring.length];
}

export function openRing(ring) {
  if (ring.length > 1) {
    const a = ring[0];
    const b = ring[ring.length - 1];
    if (a[0] === b[0] && a[1] === b[1]) return ring.slice(0, -1);
  }
  return ring;
}

function signedAreaXY(pts) {
  let s = 0;
  for (let i = 0, j = pts.length - 1; i < pts.length; j = i++) s += (pts[j].x * pts[i].y - pts[i].x * pts[j].y);
  return s / 2;
}

/** Area of an open or closed ring in km². */
export function ringAreaKm2(ring) {
  const r = openRing(ring);
  if (r.length < 3) return 0;
  const [lat0, lon0] = centroidish(r);
  const pr = projector(lat0, lon0);
  return Math.abs(signedAreaXY(r.map(pr.fwd))) / 1e6;
}

export function pointInRing([lat, lon], ring) {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [yi, xi] = ring[i];
    const [yj, xj] = ring[j];
    if ((yi > lat) !== (yj > lat) && lon < ((xj - xi) * (lat - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}

/* ---------- self-intersections ---------- */

function toGeo(ring) {
  const r = openRing(ring).map(([lat, lon]) => [lon, lat]);
  return { type: 'Feature', properties: {}, geometry: { type: 'Polygon', coordinates: [[...r, r[0]]] } };
}

/** Number of self-intersection points (turf kinks). */
export function kinkCount(ring) {
  try {
    return kinks(toGeo(ring)).features.length;
  } catch {
    return 0;
  }
}

/**
 * Remove self-intersections: unkink and keep the largest piece.
 * Returns {ring, fixed, pieces} or throws if the geometry is unusable.
 */
export function cleanRing(ring) {
  const r = openRing(ring);
  if (r.length < 3) throw new Error('меньше трёх вершин');
  if (kinkCount(r) === 0) return { ring: r, fixed: false, pieces: 1 };
  const fc = unkinkPolygon(toGeo(r));
  let best = null;
  let bestA = -1;
  for (const f of fc.features) {
    const outer = f.geometry.coordinates[0].map(([lon, lat]) => [lat, lon]);
    const a = ringAreaKm2(outer);
    if (a > bestA) { bestA = a; best = openRing(outer); }
  }
  if (!best || best.length < 3) throw new Error('не удалось исправить самопересечения');
  return { ring: best, fixed: true, pieces: fc.features.length };
}

/* ---------- buffer ---------- */

/** Offset a ring outward by `metres` (miter with bevel on sharp corners), then clean it. */
export function bufferRing(ring, metres) {
  const r = openRing(ring);
  if (r.length < 3 || !metres) return r;
  const [lat0, lon0] = centroidish(r);
  const pr = projector(lat0, lon0);
  // Drop zigzags smaller than half the buffer first: they would turn into spikes.
  let pts = simplify([...r.map(pr.fwd), pr.fwd(r[0])], metres / 2, true).slice(0, -1);
  if (pts.length < 3) pts = r.map(pr.fwd);
  if (signedAreaXY(pts) < 0) pts = pts.reverse(); // make CCW
  const n = pts.length;
  const out = [];
  const normal = (a, b) => {
    const dx = b.x - a.x;
    const dy = b.y - a.y;
    const len = Math.hypot(dx, dy) || 1;
    return { x: dy / len, y: -dx / len }; // outward for CCW
  };
  for (let i = 0; i < n; i++) {
    const prev = pts[(i - 1 + n) % n];
    const cur = pts[i];
    const next = pts[(i + 1) % n];
    const n1 = normal(prev, cur);
    const n2 = normal(cur, next);
    let bx = n1.x + n2.x;
    let by = n1.y + n2.y;
    const bl = Math.hypot(bx, by);
    const cross = (cur.x - prev.x) * (next.y - cur.y) - (cur.y - prev.y) * (next.x - cur.x);
    if (bl < 1e-9) {
      out.push({ x: cur.x + n1.x * metres, y: cur.y + n1.y * metres });
      continue;
    }
    bx /= bl;
    by /= bl;
    const dot = bx * n1.x + by * n1.y; // cos(half-angle)
    if (cross > 0 && dot < 0.5) {
      // sharp convex corner: bevel
      out.push({ x: cur.x + n1.x * metres, y: cur.y + n1.y * metres });
      out.push({ x: cur.x + n2.x * metres, y: cur.y + n2.y * metres });
    } else {
      const k = metres / Math.max(dot, 0.35);
      out.push({ x: cur.x + bx * k, y: cur.y + by * k });
    }
  }
  const ringOut = out.map(pr.inv);
  try {
    return cleanRing(ringOut).ring;
  } catch {
    return ringOut;
  }
}

/* ---------- union of area polygons ---------- */

const keyOf = (p) => `${Math.round(p[0] * 1e5)},${Math.round(p[1] * 1e5)}`;

/**
 * Exact union by cancelling shared edges (works when neighbouring boundaries share vertices,
 * as OSM boundaries usually do). `polys` = Array<Array<ring>> (each polygon: outer + holes).
 * Returns the outer ring or null if the result is not a single connected outline.
 */
function unionByEdges(polys) {
  const edges = new Map(); // undirected key -> {a, b, n}
  const pts = new Map();
  for (const poly of polys) {
    for (const ring0 of poly) {
      const ring = openRing(ring0);
      for (let i = 0; i < ring.length; i++) {
        const a = ring[i];
        const b = ring[(i + 1) % ring.length];
        const ka = keyOf(a);
        const kb = keyOf(b);
        if (ka === kb) continue;
        pts.set(ka, a);
        pts.set(kb, b);
        const k = ka < kb ? `${ka}|${kb}` : `${kb}|${ka}`;
        const e = edges.get(k);
        if (e) e.n++;
        else edges.set(k, { a: ka, b: kb, n: 1 });
      }
    }
  }
  const adj = new Map();
  for (const e of edges.values()) {
    if (e.n % 2 === 0) continue;
    if (!adj.has(e.a)) adj.set(e.a, []);
    if (!adj.has(e.b)) adj.set(e.b, []);
    adj.get(e.a).push(e.b);
    adj.get(e.b).push(e.a);
  }
  for (const list of adj.values()) if (list.length !== 2) return null; // pinch points: give up
  const seen = new Set();
  const rings = [];
  for (const start of adj.keys()) {
    if (seen.has(start)) continue;
    const ring = [];
    let prev = null;
    let cur = start;
    while (cur && !seen.has(cur)) {
      seen.add(cur);
      ring.push(pts.get(cur));
      const nb = adj.get(cur);
      const nxt = nb[0] !== prev ? nb[0] : nb[1];
      prev = cur;
      cur = nxt;
    }
    if (ring.length >= 3) rings.push(ring);
  }
  if (!rings.length) return null;
  rings.sort((a, b) => ringAreaKm2(b) - ringAreaKm2(a));
  const outer = rings[0];
  for (let i = 1; i < rings.length; i++) {
    if (!pointInRing(rings[i][0], outer)) return null; // disjoint parts
  }
  return outer;
}

/**
 * Raster union (fallback): rasterize, close small gaps, keep the largest component,
 * fill holes, trace the outline and simplify.
 */
function unionByRaster(polys, { gapMetres = 40 } = {}) {
  let minLat = Infinity; let minLon = Infinity; let maxLat = -Infinity; let maxLon = -Infinity;
  for (const poly of polys) for (const p of poly[0] || []) {
    minLat = Math.min(minLat, p[0]); maxLat = Math.max(maxLat, p[0]);
    minLon = Math.min(minLon, p[1]); maxLon = Math.max(maxLon, p[1]);
  }
  if (!Number.isFinite(minLat)) return null;
  const pr = projector((minLat + maxLat) / 2, (minLon + maxLon) / 2);
  const lo = pr.fwd([minLat, minLon]);
  const hi = pr.fwd([maxLat, maxLon]);
  const margin = gapMetres * 3;
  const w0 = hi.x - lo.x + 2 * margin;
  const h0 = hi.y - lo.y + 2 * margin;
  const cell = Math.max(10, Math.sqrt((w0 * h0) / 250000));
  const W = Math.ceil(w0 / cell);
  const H = Math.ceil(h0 / cell);
  const ox = lo.x - margin;
  const oy = lo.y - margin;
  const grid = new Uint8Array(W * H);
  // even-odd scanline per polygon (outer + holes)
  for (const poly of polys) {
    const rings = poly.map((r) => openRing(r).map(pr.fwd));
    const layer = new Uint8Array(W * H);
    for (let row = 0; row < H; row++) {
      const y = oy + (row + 0.5) * cell;
      const xs = [];
      for (const ring of rings) {
        for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
          const a = ring[i];
          const b = ring[j];
          if ((a.y > y) !== (b.y > y)) xs.push(a.x + ((y - a.y) * (b.x - a.x)) / (b.y - a.y));
        }
      }
      xs.sort((p, q) => p - q);
      for (let k = 0; k + 1 < xs.length; k += 2) {
        const c0 = Math.max(0, Math.ceil((xs[k] - ox) / cell - 0.5));
        const c1 = Math.min(W - 1, Math.floor((xs[k + 1] - ox) / cell - 0.5));
        for (let c = c0; c <= c1; c++) layer[row * W + c] = 1;
      }
    }
    for (let i = 0; i < grid.length; i++) grid[i] |= layer[i];
  }
  const rad = Math.max(1, Math.ceil(gapMetres / cell));
  const morph = (src, dilate) => {
    // separable square structuring element
    const tmp = new Uint8Array(W * H);
    const dst = new Uint8Array(W * H);
    for (let y = 0; y < H; y++) {
      for (let x = 0; x < W; x++) {
        let v = dilate ? 0 : 1;
        for (let d = -rad; d <= rad; d++) {
          const xx = x + d;
          const s = xx < 0 || xx >= W ? 0 : src[y * W + xx];
          if (dilate ? s : !s) { v = dilate ? 1 : 0; break; }
        }
        tmp[y * W + x] = v;
      }
    }
    for (let y = 0; y < H; y++) {
      for (let x = 0; x < W; x++) {
        let v = dilate ? 0 : 1;
        for (let d = -rad; d <= rad; d++) {
          const yy = y + d;
          const s = yy < 0 || yy >= H ? 0 : tmp[yy * W + x];
          if (dilate ? s : !s) { v = dilate ? 1 : 0; break; }
        }
        dst[y * W + x] = v;
      }
    }
    return dst;
  };
  let g = morph(morph(grid, true), false);
  // largest 4-connected component
  const label = new Int32Array(W * H).fill(-1);
  let best = -1;
  let bestSize = 0;
  let comps = 0;
  const stack = [];
  for (let i = 0; i < g.length; i++) {
    if (!g[i] || label[i] >= 0) continue;
    let size = 0;
    stack.push(i);
    label[i] = comps;
    while (stack.length) {
      const c = stack.pop();
      size++;
      const x = c % W;
      const y = (c - x) / W;
      const nb = [x > 0 ? c - 1 : -1, x < W - 1 ? c + 1 : -1, y > 0 ? c - W : -1, y < H - 1 ? c + W : -1];
      for (const nn of nb) if (nn >= 0 && g[nn] && label[nn] < 0) { label[nn] = comps; stack.push(nn); }
    }
    if (size > bestSize) { bestSize = size; best = comps; }
    comps++;
  }
  if (best < 0) return null;
  g = new Uint8Array(W * H);
  for (let i = 0; i < g.length; i++) g[i] = label[i] === best ? 1 : 0;
  // fill holes: flood background from the border
  const outside = new Uint8Array(W * H);
  for (let x = 0; x < W; x++) { stack.push(x, (H - 1) * W + x); }
  for (let y = 0; y < H; y++) { stack.push(y * W, y * W + W - 1); }
  while (stack.length) {
    const c = stack.pop();
    if (outside[c] || g[c]) continue;
    outside[c] = 1;
    const x = c % W;
    const y = (c - x) / W;
    if (x > 0) stack.push(c - 1);
    if (x < W - 1) stack.push(c + 1);
    if (y > 0) stack.push(c - W);
    if (y < H - 1) stack.push(c + W);
  }
  for (let i = 0; i < g.length; i++) if (!outside[i]) g[i] = 1;
  // boundary edges (filled cell on the left of the directed edge), corners as (x, y) ints
  const filled = (x, y) => x >= 0 && y >= 0 && x < W && y < H && g[y * W + x] === 1;
  const next = new Map();
  const ek = (x, y) => y * (W + 1) + x;
  const addEdge = (x0, y0, x1, y1) => {
    const k = ek(x0, y0);
    if (!next.has(k)) next.set(k, []);
    next.get(k).push([x1, y1]);
  };
  for (let y = 0; y < H; y++) {
    for (let x = 0; x < W; x++) {
      if (!filled(x, y)) continue;
      if (!filled(x, y - 1)) addEdge(x, y, x + 1, y);
      if (!filled(x + 1, y)) addEdge(x + 1, y, x + 1, y + 1);
      if (!filled(x, y + 1)) addEdge(x + 1, y + 1, x, y + 1);
      if (!filled(x - 1, y)) addEdge(x, y + 1, x, y);
    }
  }
  const startKey = next.keys().next().value;
  if (startKey === undefined) return null;
  const pts = [];
  let cx = startKey % (W + 1);
  let cy = (startKey - cx) / (W + 1);
  const sx = cx;
  const sy = cy;
  let guard = next.size + 5;
  let dir = null;
  do {
    pts.push({ x: ox + cx * cell, y: oy + cy * cell });
    const outs = next.get(ek(cx, cy));
    if (!outs || !outs.length) break;
    let pick = 0;
    if (outs.length > 1 && dir) {
      // at pinch corners prefer turning left relative to the incoming direction
      let bestScore = -Infinity;
      outs.forEach(([nx, ny], idx) => {
        const d = [nx - cx, ny - cy];
        const score = dir[0] * d[1] - dir[1] * d[0];
        if (score > bestScore) { bestScore = score; pick = idx; }
      });
    }
    const [nx, ny] = outs.splice(pick, 1)[0];
    dir = [nx - cx, ny - cy];
    cx = nx;
    cy = ny;
  } while ((cx !== sx || cy !== sy) && guard-- > 0);
  if (pts.length < 4) return null;
  const simp = simplify(pts, cell * 0.8, true);
  const ring = openRing(simp.map(pr.inv));
  return { ring, components: comps };
}

/**
 * Union of selected areas. `polys` = Array<Array<ring>>. Returns {ring, method, partial}.
 */
export function unionAreas(polys) {
  const valid = polys.filter((p) => p && p.length && p[0] && p[0].length >= 3);
  if (!valid.length) return null;
  if (valid.length === 1 && valid[0].length === 1) return { ring: openRing(valid[0][0]), method: 'single', partial: false };
  const exact = unionByEdges(valid);
  if (exact) {
    try {
      return { ring: cleanRing(exact).ring, method: 'edges', partial: false };
    } catch {
      /* fall through to raster */
    }
  }
  const r = unionByRaster(valid);
  if (!r) return null;
  return { ring: r.ring, method: 'raster', partial: r.components > 1 };
}
