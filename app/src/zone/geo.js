// Geometry helpers shared by the index, the zone masks and the DSL evaluator.
// Coordinates are [lat, lon] in degrees everywhere in the public API.
// Internally lengths are computed in a local equirectangular projection (metres), which is
// accurate to <0.1% inside a city the size of Tbilisi.

export const R_EARTH = 6371008.8; // mean Earth radius, metres
const DEG = Math.PI / 180;
export const M_PER_DEG_LAT = R_EARTH * DEG; // ~111195 m

// ---------------------------------------------------------------- polyline codec

/** Decode a Google encoded polyline into [[lat, lon], ...]. Throws on malformed input. */
export function decodePolyline(str, precision = 5) {
  const factor = Math.pow(10, precision);
  const out = [];
  let i = 0, lat = 0, lon = 0;
  const len = str.length;
  while (i < len) {
    let shift = 0, result = 0, b;
    do {
      if (i >= len) throw new Error('polyline: truncated');
      b = str.charCodeAt(i++) - 63;
      if (b < 0 || b > 63) throw new Error('polyline: bad char at ' + (i - 1));
      result |= (b & 0x1f) << shift;
      shift += 5;
    } while (b >= 0x20);
    lat += (result & 1) ? ~(result >> 1) : (result >> 1);
    shift = 0; result = 0;
    do {
      if (i >= len) throw new Error('polyline: truncated');
      b = str.charCodeAt(i++) - 63;
      if (b < 0 || b > 63) throw new Error('polyline: bad char at ' + (i - 1));
      result |= (b & 0x1f) << shift;
      shift += 5;
    } while (b >= 0x20);
    lon += (result & 1) ? ~(result >> 1) : (result >> 1);
    out.push([lat / factor, lon / factor]);
  }
  return out;
}

function encodeSigned(v, parts) {
  let s = v < 0 ? ~(v << 1) : (v << 1);
  while (s >= 0x20) {
    parts.push(String.fromCharCode((0x20 | (s & 0x1f)) + 63));
    s >>>= 5;
  }
  parts.push(String.fromCharCode(s + 63));
}

/** Encode [[lat, lon], ...] as a Google encoded polyline. */
export function encodePolyline(points, precision = 5) {
  const factor = Math.pow(10, precision);
  const parts = [];
  let plat = 0, plon = 0;
  for (const p of points) {
    const lat = Math.round(p[0] * factor);
    const lon = Math.round(p[1] * factor);
    encodeSigned(lat - plat, parts);
    encodeSigned(lon - plon, parts);
    plat = lat; plon = lon;
  }
  return parts.join('');
}

// ---------------------------------------------------------------- distances and bearings

/**
 * Great-circle distance in metres. Accepts (a, b) as [lat, lon] arrays or {lat, lon} objects,
 * or four numbers (lat1, lon1, lat2, lon2).
 */
export function haversine(a, b, c, d) {
  let lat1, lon1, lat2, lon2;
  if (typeof a === 'number') { lat1 = a; lon1 = b; lat2 = c; lon2 = d; } else {
    if (Array.isArray(a)) { lat1 = a[0]; lon1 = a[1]; } else { lat1 = a.lat; lon1 = a.lon; }
    if (Array.isArray(b)) { lat2 = b[0]; lon2 = b[1]; } else { lat2 = b.lat; lon2 = b.lon; }
  }
  const p1 = lat1 * DEG, p2 = lat2 * DEG;
  const dp = p2 - p1, dl = (lon2 - lon1) * DEG;
  const h = Math.sin(dp / 2) ** 2 + Math.cos(p1) * Math.cos(p2) * Math.sin(dl / 2) ** 2;
  return 2 * R_EARTH * Math.asin(Math.min(1, Math.sqrt(h)));
}

/** Initial bearing from a to b, degrees in [0, 360), 0 = north, 90 = east. */
export function bearing(a, b) {
  const p1 = a[0] * DEG, p2 = b[0] * DEG, dl = (b[1] - a[1]) * DEG;
  const y = Math.sin(dl) * Math.cos(p2);
  const x = Math.cos(p1) * Math.sin(p2) - Math.sin(p1) * Math.cos(p2) * Math.cos(dl);
  const deg = Math.atan2(y, x) / DEG;
  return (deg + 360) % 360;
}

/** Sector index 0..7 (N, NE, E, SE, S, SW, W, NW) of a bearing; sectors are 45 deg centred on the direction. */
export function bearingSector(deg) {
  return Math.round((((deg % 360) + 360) % 360) / 45) % 8;
}

/** Compass words (ru and en) -> direction-mask bit index (docs/pack-format.md). */
export const COMPASS = Object.freeze({
  'с': 0, 'св': 1, 'в': 2, 'юв': 3, 'ю': 4, 'юз': 5, 'з': 6, 'сз': 7,
  n: 0, ne: 1, e: 2, se: 3, s: 4, sw: 5, w: 6, nw: 7,
});
export const COMPASS_RU = Object.freeze(['с', 'св', 'в', 'юв', 'ю', 'юз', 'з', 'сз']);

/** Bit index for a compass word or -1. */
export function compassBit(word) {
  const k = String(word || '').trim().toLowerCase().replace(/ё/g, 'е');
  return Object.prototype.hasOwnProperty.call(COMPASS, k) ? COMPASS[k] : -1;
}

/**
 * Direction mask of a line at point p: sectors in which the geometry leaves p, measured over
 * the first `reach` metres along every branch (same rule as the pipeline, pack-format.md).
 * `lines` is Array<Array<[lat, lon]>>.
 */
export function directionMask(lines, p, reach = 30, tolM = 3, minStubM = 5) {
  const proj = makeProjection(p[0]);
  const [px, py] = proj(p);
  let mask = 0;
  for (const line of lines) {
    if (line.length < 2) continue;
    const xy = line.map(proj);
    // find the closest position on the line
    let best = Infinity, bi = 0, bt = 0;
    for (let i = 0; i + 1 < xy.length; i++) {
      const [ax, ay] = xy[i], [bx, by] = xy[i + 1];
      const dx = bx - ax, dy = by - ay, L2 = dx * dx + dy * dy;
      let t = L2 > 0 ? ((px - ax) * dx + (py - ay) * dy) / L2 : 0;
      t = Math.max(0, Math.min(1, t));
      const d = Math.hypot(ax + dx * t - px, ay + dy * t - py);
      if (d < best) { best = d; bi = i; bt = t; }
    }
    if (best > tolM) continue;
    const sx = xy[bi][0] + (xy[bi + 1][0] - xy[bi][0]) * bt;
    const sy = xy[bi][1] + (xy[bi + 1][1] - xy[bi][1]) * bt;
    const closed = isClosedRing(line);
    for (const dir of [1, -1]) {
      const end = walk(xy, bi, bt, dir, reach, closed);
      if (!end || end[2] < minStubM) continue; // ignore tiny stubs past the crossing
      const dx = end[0] - sx, dy = end[1] - sy;
      if (Math.hypot(dx, dy) < 0.5) continue;
      const deg = (Math.atan2(dx, dy) / DEG + 360) % 360;
      mask |= 1 << bearingSector(deg);
    }
  }
  return mask;
}

// Walk `reach` metres from position (segment i, param t) in direction dir;
// returns [x, y, walkedMetres] or null.
function walk(xy, i, t, dir, reach, closed) {
  const n = xy.length;
  let cx = xy[i][0] + (xy[i + 1][0] - xy[i][0]) * t;
  let cy = xy[i][1] + (xy[i + 1][1] - xy[i][1]) * t;
  let left = reach;
  let next = dir > 0 ? i + 1 : (t > 0 ? i : i - 1);
  if (dir < 0 && t === 0 && i === 0 && !closed) return null;
  let moved = false;
  let guard = 0;
  while (left > 0 && guard++ < 4 * n) {
    if (next < 0 || next >= n) {
      if (!closed) break;
      next = next < 0 ? n - 2 : 1; // ring: skip duplicated closing vertex
    }
    const [nx, ny] = xy[next];
    const d = Math.hypot(nx - cx, ny - cy);
    if (d >= left) {
      const f = left / d;
      return [cx + (nx - cx) * f, cy + (ny - cy) * f, reach];
    }
    if (d > 0) moved = true;
    left -= d; cx = nx; cy = ny;
    next += dir;
  }
  return moved ? [cx, cy, reach - left] : null;
}

// ---------------------------------------------------------------- projection

/** Local equirectangular projection around lat0: returns fn([lat, lon]) -> [x_east_m, y_north_m]. */
export function makeProjection(lat0, lon0 = 0) {
  const ky = M_PER_DEG_LAT, kx = M_PER_DEG_LAT * Math.cos(lat0 * DEG);
  const f = (p) => [(p[1] - lon0) * kx, (p[0] - lat0) * ky];
  f.kx = kx; f.ky = ky; f.lat0 = lat0; f.lon0 = lon0;
  f.inverse = (x, y) => [y / ky + lat0, x / kx + lon0];
  return f;
}

/** Length of a polyline [[lat, lon], ...] in metres. */
export function lineLength(line) {
  let L = 0;
  for (let i = 0; i + 1 < line.length; i++) L += haversine(line[i], line[i + 1]);
  return L;
}

export function isClosedRing(ring) {
  if (!ring || ring.length < 4) return false;
  const a = ring[0], b = ring[ring.length - 1];
  return a[0] === b[0] && a[1] === b[1];
}

/** Normalise a polygon argument: a ring [[lat, lon], ...] or rings [[[lat, lon], ...], ...] -> rings. */
export function toRings(polygon) {
  if (!polygon || !polygon.length) return [];
  if (typeof polygon[0][0] === 'number') return [polygon];
  return polygon;
}

// ---------------------------------------------------------------- prepared polygon

const BANDS_MAX = 256;
const RASTER_CELLS = 64;

/**
 * Pre-processed polygon for fast point-in-polygon and segment clipping.
 * Rings use even-odd semantics, so holes (and multipolygon parts) just work.
 * Edges are bucketed into horizontal bands so every query only touches nearby edges.
 */
export class PreparedPolygon {
  constructor(polygon, lat0) {
    const rings = toRings(polygon).filter((r) => r && r.length >= 3);
    if (lat0 === undefined) {
      let s = 0, k = 0;
      for (const r of rings) for (const p of r) { s += p[0]; k++; }
      lat0 = k ? s / k : 0;
    }
    this.proj = makeProjection(lat0);
    this.rings = rings;
    let ne = 0;
    for (const r of rings) ne += r.length; // closing edge included
    const ex0 = new Float64Array(ne), ey0 = new Float64Array(ne), ex1 = new Float64Array(ne), ey1 = new Float64Array(ne);
    let e = 0;
    let minx = Infinity, miny = Infinity, maxx = -Infinity, maxy = -Infinity;
    let minLat = Infinity, minLon = Infinity, maxLat = -Infinity, maxLon = -Infinity;
    for (const r of rings) {
      let m = r.length;
      if (isClosedRing(r)) m -= 1;
      for (let i = 0; i < m; i++) {
        const a = r[i], b = r[(i + 1) % m];
        const pa = this.proj(a), pb = this.proj(b);
        if (pa[0] === pb[0] && pa[1] === pb[1]) continue;
        ex0[e] = pa[0]; ey0[e] = pa[1]; ex1[e] = pb[0]; ey1[e] = pb[1]; e++;
        if (pa[0] < minx) minx = pa[0]; if (pa[0] > maxx) maxx = pa[0];
        if (pa[1] < miny) miny = pa[1]; if (pa[1] > maxy) maxy = pa[1];
        if (a[0] < minLat) minLat = a[0]; if (a[0] > maxLat) maxLat = a[0];
        if (a[1] < minLon) minLon = a[1]; if (a[1] > maxLon) maxLon = a[1];
      }
    }
    this.ne = e;
    this.ex0 = ex0.subarray(0, e); this.ey0 = ey0.subarray(0, e);
    this.ex1 = ex1.subarray(0, e); this.ey1 = ey1.subarray(0, e);
    this.minx = minx; this.miny = miny; this.maxx = maxx; this.maxy = maxy;
    this.bbox = [minLat, minLon, maxLat, maxLon];
    this.empty = e < 3;
    // bands
    const nb = this.empty ? 1 : Math.max(1, Math.min(BANDS_MAX, Math.ceil(Math.sqrt(e) * 2)));
    this.nb = nb;
    this.bandH = this.empty ? 1 : Math.max((maxy - miny) / nb, 1e-6);
    const counts = new Int32Array(nb + 1);
    const bandRange = (k) => {
      const ya = Math.min(this.ey0[k], this.ey1[k]), yb = Math.max(this.ey0[k], this.ey1[k]);
      return [this._band(ya), this._band(yb)];
    };
    for (let k = 0; k < e; k++) { const [b0, b1] = bandRange(k); for (let b = b0; b <= b1; b++) counts[b + 1]++; }
    for (let b = 0; b < nb; b++) counts[b + 1] += counts[b];
    const fill = counts.slice(0, nb);
    const items = new Int32Array(counts[nb]);
    for (let k = 0; k < e; k++) { const [b0, b1] = bandRange(k); for (let b = b0; b <= b1; b++) items[fill[b]++] = k; }
    this.bandStart = counts; this.bandItems = items;
    this._stamp = new Int32Array(e); this._stampVal = 0;
    this._buildRaster();
  }

  // Coarse raster over the bbox: every cell is 1 = fully inside, 2 = fully outside or
  // 3 = crossed by an edge. Lets most points and short segments skip the edge tests.
  _buildRaster() {
    this.raster = null;
    if (this.empty || this.ne < 8) return;
    const G = RASTER_CELLS;
    const w = this.maxx - this.minx, h = this.maxy - this.miny;
    if (!(w > 0) || !(h > 0)) return;
    const cw = w / G, ch = h / G;
    const r = new Uint8Array(G * G);
    const mark = (cx, cy) => {
      for (let yy = Math.max(0, cy - 1); yy <= Math.min(G - 1, cy + 1); yy++) {
        for (let xx = Math.max(0, cx - 1); xx <= Math.min(G - 1, cx + 1); xx++) r[yy * G + xx] = 3;
      }
    };
    const step = Math.min(cw, ch) / 2;
    for (let k = 0; k < this.ne; k++) {
      const x0 = this.ex0[k], y0 = this.ey0[k], x1 = this.ex1[k], y1 = this.ey1[k];
      const L = Math.hypot(x1 - x0, y1 - y0);
      const n = Math.max(1, Math.ceil(L / step));
      for (let j = 0; j <= n; j++) {
        const x = x0 + (x1 - x0) * j / n, y = y0 + (y1 - y0) * j / n;
        mark(Math.min(G - 1, Math.floor((x - this.minx) / cw)), Math.min(G - 1, Math.floor((y - this.miny) / ch)));
      }
    }
    this.raster = null; // containsXY must use the exact test while classifying
    for (let cy = 0; cy < G; cy++) {
      for (let cx = 0; cx < G; cx++) {
        const q = cy * G + cx;
        if (r[q] === 3) continue;
        r[q] = this.containsXY(this.minx + (cx + 0.5) * cw, this.miny + (cy + 0.5) * ch) ? 1 : 2;
      }
    }
    this.raster = r; this.rG = G; this.rcw = cw; this.rch = ch;
  }

  /** Raster status of the cell containing (x, y): 1 inside, 2 outside, 3 boundary, 0 no raster. */
  cellStatus(x, y) {
    if (!this.raster) return 0;
    const G = this.rG;
    let cx = Math.floor((x - this.minx) / this.rcw), cy = Math.floor((y - this.miny) / this.rch);
    if (cx < 0 || cy < 0 || cx > G || cy > G) return 2;
    if (cx === G) cx = G - 1;
    if (cy === G) cy = G - 1;
    return this.raster[cy * G + cx];
  }

  _band(y) {
    const b = Math.floor((y - this.miny) / this.bandH);
    return b < 0 ? 0 : (b >= this.nb ? this.nb - 1 : b);
  }

  /** Point-in-polygon in projected metres (even-odd). */
  containsXY(x, y) {
    if (this.empty || x < this.minx || x > this.maxx || y < this.miny || y > this.maxy) return false;
    if (this.raster) {
      const st = this.cellStatus(x, y);
      if (st === 1) return true;
      if (st === 2) return false;
    }
    const b = this._band(y);
    let inside = false;
    const { ex0, ey0, ex1, ey1, bandItems } = this;
    for (let q = this.bandStart[b], qe = this.bandStart[b + 1]; q < qe; q++) {
      const k = bandItems[q];
      const yi = ey0[k], yj = ey1[k];
      if ((yi > y) !== (yj > y)) {
        const xi = ex0[k], xj = ex1[k];
        if (x < (xj - xi) * (y - yi) / (yj - yi) + xi) inside = !inside;
      }
    }
    return inside;
  }

  /** Point-in-polygon for [lat, lon] or (lat, lon). */
  contains(lat, lon) {
    if (typeof lat !== 'number') { lon = lat[1]; lat = lat[0]; }
    const p = this.proj([lat, lon]);
    return this.containsXY(p[0], p[1]);
  }

  /** Inside length (metres) of the projected segment a->b. */
  clipSegmentXY(ax, ay, bx, by) {
    const sdx = bx - ax, sdy = by - ay;
    const sl = Math.sqrt(sdx * sdx + sdy * sdy);
    if (sl === 0 || this.empty) return 0;
    if (Math.max(ax, bx) < this.minx || Math.min(ax, bx) > this.maxx ||
        Math.max(ay, by) < this.miny || Math.min(ay, by) > this.maxy) return 0;
    if (this.raster) {
      // all raster cells under the segment bbox share one non-boundary status -> no edge can cut it
      const G = this.rG;
      const c0 = Math.floor((Math.min(ax, bx) - this.minx) / this.rcw), c1 = Math.floor((Math.max(ax, bx) - this.minx) / this.rcw);
      const r0 = Math.floor((Math.min(ay, by) - this.miny) / this.rch), r1 = Math.floor((Math.max(ay, by) - this.miny) / this.rch);
      if ((c1 - c0 + 1) * (r1 - r0 + 1) <= 9) {
        let st = -1;
        for (let cy = r0; cy <= r1 && st !== 3; cy++) {
          for (let cx = c0; cx <= c1; cx++) {
            const v = (cx < 0 || cy < 0 || cx >= G || cy >= G) ? 2 : this.raster[cy * G + cx];
            if (st === -1) st = v; else if (v !== st) { st = 3; break; }
            if (v === 3) { st = 3; break; }
          }
        }
        if (st === 1) return sl;
        if (st === 2) return 0;
      }
    }
    const ts = this._ts || (this._ts = []);
    ts.length = 0;
    const b0 = this._band(Math.min(ay, by)), b1 = this._band(Math.max(ay, by));
    const stampVal = ++this._stampVal;
    if (stampVal > 2e9) { this._stamp.fill(0); this._stampVal = 1; }
    const sv = this._stampVal;
    const { ex0, ey0, ex1, ey1, bandItems, _stamp } = this;
    const d1x = bx - ax, d1y = by - ay;
    const sminx = Math.min(ax, bx), smaxx = Math.max(ax, bx);
    for (let b = b0; b <= b1; b++) {
      for (let q = this.bandStart[b], qe = this.bandStart[b + 1]; q < qe; q++) {
        const k = bandItems[q];
        if (_stamp[k] === sv) continue;
        _stamp[k] = sv;
        const cx = ex0[k], cy = ey0[k], dx = ex1[k], dy = ey1[k];
        if (Math.max(cx, dx) < sminx || Math.min(cx, dx) > smaxx) continue;
        const d2x = dx - cx, d2y = dy - cy;
        const den = d1x * d2y - d1y * d2x;
        if (den === 0) continue;
        const t = ((cx - ax) * d2y - (cy - ay) * d2x) / den;
        const u = ((cx - ax) * d1y - (cy - ay) * d1x) / den;
        if (t > 0 && t < 1 && u >= 0 && u <= 1) ts.push(t);
      }
    }
    if (ts.length === 0) {
      return this.containsXY((ax + bx) / 2, (ay + by) / 2) ? sl : 0;
    }
    ts.push(0, 1);
    ts.sort((p, q) => p - q);
    let inside = 0;
    for (let q = 0; q + 1 < ts.length; q++) {
      const ta = ts[q], tb = ts[q + 1];
      if (tb - ta < 1e-12) continue;
      const tm = (ta + tb) / 2;
      if (this.containsXY(ax + d1x * tm, ay + d1y * tm)) inside += (tb - ta) * sl;
    }
    return inside;
  }

  /** {inside, total} metres of a line [[lat, lon], ...] (or several lines). */
  clipLine(line) {
    const lines = line.length && typeof line[0][0] === 'number' ? [line] : line;
    let inside = 0, total = 0;
    const kx = this.proj.kx, ky = this.proj.ky, lat0 = this.proj.lat0, lon0 = this.proj.lon0;
    const { minx, maxx, miny, maxy } = this;
    for (const l of lines) {
      if (l.length < 2) continue;
      let px = (l[0][1] - lon0) * kx, py = (l[0][0] - lat0) * ky;
      for (let i = 1; i < l.length; i++) {
        const qx = (l[i][1] - lon0) * kx, qy = (l[i][0] - lat0) * ky;
        const dx = qx - px, dy = qy - py;
        total += Math.sqrt(dx * dx + dy * dy);
        // cheap reject: segment bbox outside polygon bbox
        if (!((px < minx && qx < minx) || (px > maxx && qx > maxx) || (py < miny && qy < miny) || (py > maxy && qy > maxy))) {
          inside += this.clipSegmentXY(px, py, qx, qy);
        }
        px = qx; py = qy;
      }
    }
    return { inside, total };
  }

  /** True when any vertex of the lines is inside. */
  anyVertexInside(lines) {
    for (const l of lines) for (const p of l) if (this.contains(p[0], p[1])) return true;
    return false;
  }

  /** Area in km^2 (outer rings minus holes, via even-odd nesting depth of each ring). */
  areaKm2() {
    return polygonAreaKm2(this.rings);
  }
}

/** Point-in-polygon; polygon is a ring or rings (holes, even-odd). Point is [lat, lon]. */
export function pointInPolygon(pt, polygon) {
  const prepared = polygon instanceof PreparedPolygon ? polygon : new PreparedPolygon(polygon, pt[0]);
  return prepared.contains(pt[0], pt[1]);
}

/**
 * Length of `line` (one line or Array of lines, [lat, lon]) inside `polygon` (ring, rings or
 * PreparedPolygon). Segments are cut by polygon edges and the midpoint of every piece is tested.
 * Returns {inside, total, share} in metres / fraction.
 */
export function clipLengthInside(line, polygon) {
  const prepared = polygon instanceof PreparedPolygon ? polygon : new PreparedPolygon(polygon);
  const r = prepared.clipLine(line);
  r.share = r.total > 0 ? r.inside / r.total : 0;
  return r;
}

export function preparePolygon(polygon, lat0) {
  return polygon instanceof PreparedPolygon ? polygon : new PreparedPolygon(polygon, lat0);
}

/** Signed shoelace area (m^2) of a projected ring. */
function ringAreaM2(ring, proj) {
  let s = 0;
  let m = ring.length;
  if (isClosedRing(ring)) m -= 1;
  for (let i = 0; i < m; i++) {
    const [x1, y1] = proj(ring[i]);
    const [x2, y2] = proj(ring[(i + 1) % m]);
    s += x1 * y2 - x2 * y1;
  }
  return s / 2;
}

/**
 * Area of a polygon in km^2. Accepts a ring, rings [outer, hole, ...] or a multipolygon
 * [[outer, hole], [outer2]]. Rings nested inside an odd number of other rings count as holes.
 */
export function polygonAreaKm2(polygon) {
  let rings;
  if (!polygon || !polygon.length) return 0;
  if (typeof polygon[0][0] === 'number') rings = [polygon];
  else if (typeof polygon[0][0][0] === 'number') rings = polygon;
  else rings = polygon.flat(1);
  rings = rings.filter((r) => r && r.length >= 3);
  if (!rings.length) return 0;
  let s = 0, k = 0;
  for (const r of rings) for (const p of r) { s += p[0]; k++; }
  const proj = makeProjection(s / k);
  let total = 0;
  for (let i = 0; i < rings.length; i++) {
    const a = Math.abs(ringAreaM2(rings[i], proj));
    let depth = 0;
    const probe = rings[i][0];
    for (let j = 0; j < rings.length; j++) {
      if (j === i) continue;
      if (new PreparedPolygon(rings[j], probe[0]).contains(probe[0], probe[1])) depth++;
    }
    total += depth % 2 === 0 ? a : -a;
  }
  return Math.max(0, total) / 1e6;
}

/** Approximate circle polygon (closed ring) of radius r metres around [lat, lon]. */
export function circlePolygon(center, r, n = 32) {
  const proj = makeProjection(center[0], center[1]);
  const ring = [];
  for (let i = 0; i < n; i++) {
    const a = (2 * Math.PI * i) / n;
    ring.push(proj.inverse(r * Math.sin(a), r * Math.cos(a)));
  }
  ring.push(ring[0].slice());
  return ring;
}

/** Bounding box [minLat, minLon, maxLat, maxLon] of points or lines. */
export function bboxOf(lines) {
  let a = Infinity, b = Infinity, c = -Infinity, d = -Infinity;
  const visit = (p) => { if (p[0] < a) a = p[0]; if (p[0] > c) c = p[0]; if (p[1] < b) b = p[1]; if (p[1] > d) d = p[1]; };
  if (lines.length && typeof lines[0][0] === 'number') lines.forEach(visit);
  else for (const l of lines) l.forEach(visit);
  return [a, b, c, d];
}

/** Distance in metres from point p to segment a-b (all [lat, lon]), local projection. */
export function pointSegmentDistance(p, a, b) {
  const proj = makeProjection(p[0], p[1]);
  const [ax, ay] = proj(a), [bx, by] = proj(b);
  const dx = bx - ax, dy = by - ay, L2 = dx * dx + dy * dy;
  let t = L2 > 0 ? (-ax * dx - ay * dy) / L2 : 0;
  t = Math.max(0, Math.min(1, t));
  return Math.hypot(ax + dx * t, ay + dy * t);
}

/** Distance in metres from point p to a polyline (or several lines). */
export function pointLineDistance(p, line) {
  const lines = line.length && typeof line[0][0] === 'number' ? [line] : line;
  let best = Infinity;
  for (const l of lines) {
    if (l.length === 1) best = Math.min(best, haversine(p, l[0]));
    for (let i = 0; i + 1 < l.length; i++) best = Math.min(best, pointSegmentDistance(p, l[i], l[i + 1]));
  }
  return best;
}

/**
 * Densify a line so that consecutive points are at most `stepM` apart (used by the near grid,
 * so long straight segments are not missed by a point-based radius query).
 */
export function densify(line, stepM = 25) {
  if (line.length < 2) return line.slice();
  const out = [line[0]];
  for (let i = 0; i + 1 < line.length; i++) {
    const a = line[i], b = line[i + 1];
    const d = haversine(a, b);
    const k = Math.ceil(d / stepM);
    for (let j = 1; j < k; j++) out.push([a[0] + (b[0] - a[0]) * j / k, a[1] + (b[1] - a[1]) * j / k]);
    out.push(b);
  }
  return out;
}

// ---------------------------------------------------------------- uniform grid of points

/**
 * Uniform grid (default 100 m cells) over points tagged with an integer id (feature index).
 * Usage: const g = new PointGrid(); g.add(lat, lon, id) ...; g.build();
 *        g.forEachWithin(lat, lon, r, (id, distM, k) => {...});
 */
export class PointGrid {
  constructor(cellM = 100, lat0 = 41.72, lon0 = 44.79) {
    this.cellM = cellM;
    this.proj = makeProjection(lat0, lon0);
    this._x = []; this._y = []; this._id = [];
    this.built = false;
  }

  add(lat, lon, id) {
    this._x.push((lon - this.proj.lon0) * this.proj.kx); this._y.push((lat - this.proj.lat0) * this.proj.ky); this._id.push(id);
    this.built = false;
  }

  /**
   * Add the points of a polyline densified to <= stepM spacing (no intermediate arrays).
   * Line: [[lat, lon], ...].
   */
  addLine(line, id, stepM = 25) {
    const { kx, ky, lat0, lon0 } = this.proj;
    if (!line.length) return;
    let px = (line[0][1] - lon0) * kx, py = (line[0][0] - lat0) * ky;
    this._x.push(px); this._y.push(py); this._id.push(id);
    for (let i = 1; i < line.length; i++) {
      const qx = (line[i][1] - lon0) * kx, qy = (line[i][0] - lat0) * ky;
      const dx = qx - px, dy = qy - py;
      const k = Math.ceil(Math.sqrt(dx * dx + dy * dy) / stepM);
      for (let j = 1; j < k; j++) { this._x.push(px + dx * j / k); this._y.push(py + dy * j / k); this._id.push(id); }
      this._x.push(qx); this._y.push(qy); this._id.push(id);
      px = qx; py = qy;
    }
    this.built = false;
  }

  get size() { return this.built ? this.ids.length : this._id.length; }

  build() {
    const n = this._id.length;
    const xs = Float64Array.from(this._x), ys = Float64Array.from(this._y), ids = Int32Array.from(this._id);
    let minx = Infinity, miny = Infinity, maxx = -Infinity, maxy = -Infinity;
    for (let i = 0; i < n; i++) {
      if (xs[i] < minx) minx = xs[i]; if (xs[i] > maxx) maxx = xs[i];
      if (ys[i] < miny) miny = ys[i]; if (ys[i] > maxy) maxy = ys[i];
    }
    if (!n) { minx = miny = 0; maxx = maxy = 1; }
    const c = this.cellM;
    const nx = Math.max(1, Math.floor((maxx - minx) / c) + 1), ny = Math.max(1, Math.floor((maxy - miny) / c) + 1);
    const start = new Int32Array(nx * ny + 1);
    const cellOf = new Int32Array(n);
    for (let i = 0; i < n; i++) {
      const cx = Math.floor((xs[i] - minx) / c), cy = Math.floor((ys[i] - miny) / c);
      cellOf[i] = cy * nx + cx; start[cellOf[i] + 1]++;
    }
    for (let k = 0; k < nx * ny; k++) start[k + 1] += start[k];
    const fill = start.slice(0, nx * ny);
    const order = new Int32Array(n);
    for (let i = 0; i < n; i++) order[fill[cellOf[i]]++] = i;
    this.xs = new Float64Array(n); this.ys = new Float64Array(n); this.ids = new Int32Array(n);
    for (let k = 0; k < n; k++) { const i = order[k]; this.xs[k] = xs[i]; this.ys[k] = ys[i]; this.ids[k] = ids[i]; }
    this.start = start; this.nx = nx; this.ny = ny; this.minx = minx; this.miny = miny;
    this._x = []; this._y = []; this._id = [];
    this.built = true;
    return this;
  }

  /** Calls cb(id, distM, pointIndex) for every point within r metres of (lat, lon). */
  forEachWithin(lat, lon, r, cb) {
    if (!this.built) this.build();
    const [x, y] = this.proj([lat, lon]);
    this.forEachWithinXY(x, y, r, cb);
  }

  forEachWithinXY(x, y, r, cb) {
    const c = this.cellM;
    const cx0 = Math.max(0, Math.floor((x - r - this.minx) / c)), cx1 = Math.min(this.nx - 1, Math.floor((x + r - this.minx) / c));
    const cy0 = Math.max(0, Math.floor((y - r - this.miny) / c)), cy1 = Math.min(this.ny - 1, Math.floor((y + r - this.miny) / c));
    const r2 = r * r;
    for (let cy = cy0; cy <= cy1; cy++) {
      for (let cx = cx0; cx <= cx1; cx++) {
        const cell = cy * this.nx + cx;
        for (let k = this.start[cell], ke = this.start[cell + 1]; k < ke; k++) {
          const dx = this.xs[k] - x, dy = this.ys[k] - y;
          const d2 = dx * dx + dy * dy;
          if (d2 <= r2) cb(this.ids[k], Math.sqrt(d2), k);
        }
      }
    }
  }

  /** Set of ids within r metres of any of the given [lat, lon] points. */
  idsWithin(points, r) {
    const out = new Set();
    for (const p of points) this.forEachWithin(p[0], p[1], r, (id) => out.add(id));
    return out;
  }
}
