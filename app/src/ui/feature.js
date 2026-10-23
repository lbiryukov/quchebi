// Read-only helpers over the pack/index for the UI (names, kinds, places, eponyms, crossings).
import { state } from './state.js';

export function core() {
  return state.pack?.core;
}

export function feat(i) {
  return state.pack?.core?.f?.[i];
}

export function kindOf(i) {
  const f = feat(i);
  return f ? core().kinds?.[f.k] : null;
}

export function kindRu(i) {
  return kindOf(i)?.ru || '';
}

export function isLineKind(i) {
  const g = kindOf(i)?.group;
  return g === 'street*' || g === 'lane*';
}

export function mainName(i) {
  const f = feat(i);
  if (!f) return '';
  return f.ka || f.ru || f.en || '';
}

export function ruName(i) {
  const f = feat(i);
  return f?.ru || f?.en || '';
}

/** Short label for chips/crossings, Russian first. */
export function shortName(i) {
  const f = feat(i);
  if (!f) return '';
  return f.ru || f.ka || f.en || '';
}

export function placeLabel(i) {
  const f = feat(i);
  const c = core();
  if (!f || !c) return '';
  if (f.p && f.p.length && c.places?.[f.p[0]]) return c.places[f.p[0]].ru || c.places[f.p[0]].ka;
  if (f.d && f.d.length && c.districts?.[f.d[0]]) return c.districts[f.d[0]].ru || c.districts[f.d[0]].ka;
  return '';
}

export function districtLabels(i) {
  const f = feat(i);
  const c = core();
  if (!f || !c) return [];
  return (f.d || []).map((d) => c.districts?.[d]).filter(Boolean).map((d) => d.ru || d.ka);
}

export function placeLabels(i) {
  const f = feat(i);
  const c = core();
  if (!f || !c) return [];
  return (f.p || []).map((p) => c.places?.[p]).filter(Boolean).map((p) => p.ru || p.ka);
}

export function familyOf(i) {
  const f = feat(i);
  if (!f || f.fam === undefined || f.fam === null) return null;
  return core()?.fam?.[f.fam] || null;
}

/** Eponym record for feature i (via family), or null. */
export function eponymOf(i) {
  const idx = state.index?.eponymOf ? state.index.eponymOf[i] : -1;
  if (idx === undefined || idx < 0) return null;
  return state.pack?.eponyms?.e?.[idx] || null;
}

export function eponymByIndex(e) {
  if (e === undefined || e === null || e < 0) return null;
  return state.pack?.eponyms?.e?.[e] || null;
}

/** Short eponym tag for result rows: first occupation or category label. */
export function eponymTag(i) {
  const e = eponymOf(i);
  if (!e) return '';
  if (e.occ && e.occ.length) return e.occ[0];
  if (e.cat && e.cat.length) return taxLabel(e.cat[0]);
  return '';
}

let taxMap = null;
export function taxLabel(id) {
  if (!taxMap) {
    taxMap = new Map();
    for (const g of state.pack?.eponyms?.taxonomy || []) {
      taxMap.set(g.id, g.ru);
      for (const c of g.children || []) taxMap.set(c.id, c.ru);
    }
  }
  return taxMap.get(id) || id;
}

export function resetCaches() {
  taxMap = null;
}

/** Crossings of feature i: [{j, t, lat, lon, mask}] using the CSR adjacency of the index. */
export function crossingsOf(i) {
  const idx = state.index;
  const adj = idx?.adj;
  if (!adj || !adj.offsets) return [];
  const out = [];
  const xp = core()?.xp || [];
  for (let k = adj.offsets[i]; k < adj.offsets[i + 1]; k++) {
    const j = adj.nbr[k];
    const t = adj.xi ? adj.xi[k] : -1;
    let lat = NaN;
    let lon = NaN;
    if (idx.cross && t >= 0) {
      lat = idx.cross.lat[t];
      lon = idx.cross.lon[t];
    } else if (t >= 0) {
      lat = xp[2 * t] / 1e5;
      lon = xp[2 * t + 1] / 1e5;
    }
    // Direction masks: `mask` = how feature i leaves the crossing, `nmask` = how neighbour j leaves it.
    let nmask = 0;
    if (idx.cross && t >= 0) nmask = idx.cross.a[t] === j ? idx.cross.maskA[t] : idx.cross.maskB[t];
    else if (t >= 0 && core()?.xd) nmask = core().x[2 * t] === j ? core().xd[2 * t] : core().xd[2 * t + 1];
    out.push({ j, t, lat, lon, mask: adj.mask ? adj.mask[k] : 0, nmask });
  }
  return out;
}

export function houseRows(i) {
  try {
    return state.index?.houses ? state.index.houses(i) || [] : [];
  } catch {
    return [];
  }
}

export function geometryOf(i) {
  try {
    return state.index?.geometry ? state.index.geometry(i) || [] : [];
  } catch {
    return [];
  }
}

/** Stable key for local notes: feature ids change between rebuilds, names do not. */
export function featureKey(i) {
  const f = feat(i);
  if (!f) return String(i);
  return `${f.ka || ''}|${f.ru || ''}`;
}

/** Find a feature by its key (used when re-opening a note). */
export function findByKey(key) {
  const fs = core()?.f || [];
  for (let i = 0; i < fs.length; i++) if (`${fs[i].ka || ''}|${fs[i].ru || ''}` === key) return i;
  return -1;
}

/** Source label for an eponym record. */
export function eponymSource(e) {
  if (!e) return null;
  if (e.ai) return { cls: 'ai', text: 'оценка ИИ' };
  const s = String(e.src || '').toLowerCase();
  if (s.startsWith('osm')) return { cls: 'osm', text: 'OSM' };
  if (s.startsWith('wd') || s.startsWith('wiki')) return { cls: 'wd', text: 'Wikidata' };
  if (s) return { cls: '', text: e.src };
  return null;
}
