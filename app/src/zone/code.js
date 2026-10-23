// Short share code for a zone: "Z1." + Google encoded polyline (precision 5) of the outer ring
// after simplification to <= 60 vertices. Decoding tolerates whatever messengers do to pasted
// text: spaces, line breaks, zero-width characters, quotes and surrounding words.
import simplify from 'simplify-js';
import { encodePolyline, decodePolyline, isClosedRing, toRings } from './geo.js';

export const ZONE_CODE_PREFIX = 'Z1.';
export const ZONE_CODE_MAX_VERTICES = 60;

/** Open ring (no repeated closing vertex), consecutive duplicates removed. */
function openRing(ring) {
  const out = [];
  for (const p of ring) {
    const q = [Number(p[0]), Number(p[1])];
    const last = out[out.length - 1];
    if (!last || last[0] !== q[0] || last[1] !== q[1]) out.push(q);
  }
  if (out.length > 1 && out[0][0] === out[out.length - 1][0] && out[0][1] === out[out.length - 1][1]) out.pop();
  return out;
}

/** Simplify an open ring to at most maxVertices vertices (Douglas-Peucker with growing tolerance). */
export function simplifyRing(ring, maxVertices = ZONE_CODE_MAX_VERTICES) {
  let pts = openRing(ring);
  if (pts.length <= maxVertices) return pts;
  // work in approximately metric units (degrees of lon scaled by cos(lat))
  const lat0 = pts.reduce((s, p) => s + p[0], 0) / pts.length;
  const k = Math.cos(lat0 * Math.PI / 180);
  const xy = pts.map((p) => ({ x: p[1] * k, y: p[0] }));
  xy.push({ x: xy[0].x, y: xy[0].y }); // closed for simplification so the seam is kept stable
  let tol = 1e-6; // ~0.1 m
  let res = xy;
  for (let it = 0; it < 60; it++) {
    res = simplify(xy, tol, true);
    if (res.length - 1 <= maxVertices) break;
    tol *= 1.5;
  }
  const out = res.slice(0, -1).map((p) => [p.y, p.x / k]);
  // Douglas-Peucker on a closed ring always keeps the seam vertex; fall back to uniform sampling
  // in the pathological case it still has too many vertices.
  if (out.length > maxVertices) {
    const step = out.length / maxVertices;
    return Array.from({ length: maxVertices }, (_, i) => out[Math.floor(i * step)]);
  }
  return out;
}

/** Encode a zone polygon (ring or rings; only the outer ring is kept) into a share code. */
export function encodeZone(polygon, { maxVertices = ZONE_CODE_MAX_VERTICES } = {}) {
  const rings = toRings(polygon);
  if (!rings.length || rings[0].length < 3) throw new Error('Зона должна иметь хотя бы три вершины');
  const pts = simplifyRing(rings[0], maxVertices);
  if (pts.length < 3) throw new Error('Зона должна иметь хотя бы три вершины');
  return ZONE_CODE_PREFIX + encodePolyline(pts, 5);
}

/** Strip messenger noise; returns the polyline part after the prefix or null. */
export function extractZoneCode(text) {
  let s = String(text == null ? '' : text);
  // zero-width and BOM characters, all whitespace (incl. NBSP and line breaks)
  s = s.replace(/[​-‍⁠﻿­]/g, '').replace(/\s+/g, '');
  const m = /z1\./i.exec(s);
  if (!m) return null;
  let body = s.slice(m.index + m[0].length);
  // the polyline alphabet is ASCII 63..126; stop at the first character outside it
  const end = body.search(/[^\x3F-\x7E]/);
  if (end >= 0) body = body.slice(0, end);
  // typographic quotes are outside the alphabet; a trailing straight quote/backtick is not
  body = body.replace(/[`'"]+$/, '');
  return body;
}

/** Decode a share code into an open ring [[lat, lon], ...]. Throws Error with a Russian message. */
export function decodeZone(code) {
  const body = extractZoneCode(code);
  if (body === null) throw new Error('Это не код зоны: он должен начинаться с «Z1.»');
  if (!body.length) throw new Error('Код зоны пустой');
  let pts;
  try { pts = decodePolyline(body, 5); } catch (e) { throw new Error('Код зоны повреждён: ' + e.message); }
  if (isClosedRing(pts)) pts = pts.slice(0, -1);
  if (pts.length < 3) throw new Error('В коде зоны меньше трёх вершин');
  for (const [lat, lon] of pts) {
    if (!Number.isFinite(lat) || !Number.isFinite(lon) || Math.abs(lat) > 90 || Math.abs(lon) > 180) {
      throw new Error('Код зоны повреждён: координаты вне диапазона');
    }
  }
  // all vertices must stay near each other (a corrupted code jumps across the globe)
  const lat0 = pts[0][0], lon0 = pts[0][1];
  if (pts.some(([lat, lon]) => Math.abs(lat - lat0) > 1 || Math.abs(lon - lon0) > 1)) {
    throw new Error('Код зоны повреждён: вершины слишком далеко друг от друга');
  }
  return pts;
}

/** Like decodeZone but returns null instead of throwing. */
export function tryDecodeZone(code) {
  try { return decodeZone(code); } catch { return null; }
}
