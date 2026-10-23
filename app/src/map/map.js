// MapLibre map view: offline basemap + our layers (all features, results, selection, houses,
// zone, draft zone, areas for "Из районов", my position). One instance, re-parented between the
// "Карта" and "Зона" screens.
import maplibregl from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import { decodePolyline, circlePolygon } from '../zone/geo.js';
import { log } from '../log/log.js';
import { resolveBasemap, buildStyle, BG } from './basemap.js';

const TBILISI = { center: [44.8015, 41.7151], zoom: 12 };
const MAX_BOUNDS = [[44.45, 41.5], [45.2, 41.95]];

export const COLORS = {
  light: { res: '#8a2242', sel: '#1d3b6e', selCase: '#ffffff', base: '#9aa6b8', zone: '#23683f', draft: '#8a2242', area: '#1d3b6e', house: '#1d3b6e', text: '#0d1626', halo: '#ffffff', me: '#1a73e8' },
  dark: { res: '#e8839f', sel: '#9dbbf0', selCase: '#0e131b', base: '#4b5870', zone: '#6cc795', draft: '#e8839f', area: '#9dbbf0', house: '#9dbbf0', text: '#e8edf5', halo: '#0e131b', me: '#4ea1ff' },
};

const EMPTY = { type: 'FeatureCollection', features: [] };
const toLngLat = (pts) => pts.map(([lat, lon]) => [lon, lat]);

function ringClosed(r) {
  if (!r.length) return r;
  const a = r[0];
  const b = r[r.length - 1];
  return a[0] === b[0] && a[1] === b[1] ? r : [...r, a];
}

export function polygonFeature(poly, props = {}) {
  if (!poly || poly.length < 3) return null;
  return { type: 'Feature', properties: props, geometry: { type: 'Polygon', coordinates: [ringClosed(toLngLat(poly))] } };
}

/**
 * createMapView(container, {theme, onFeatureTap(i, lngLat), onAreaTap(kind, idx), onMoveEnd(center)})
 * Resolves after the first style load.
 */
export async function createMapView(container, opts = {}) {
  const bm = await resolveBasemap();
  let theme = opts.theme === 'dark' ? 'dark' : 'light';
  const view = {
    map: null,
    basemap: bm,
    theme,
    mode: 'browse', // 'browse' | 'areas' | 'draw' | 'edit'
    data: { all: null, areas: null },
    st: { results: [], selected: -1, houses: EMPTY, zone: null, zoneOn: true, draft: null, me: null, areasKind: null, areasSel: [], vertex: null },
    listeners: new Set(),
  };

  const map = new maplibregl.Map({
    container,
    style: buildStyle(theme, bm),
    center: TBILISI.center,
    zoom: TBILISI.zoom,
    minZoom: 10,
    maxZoom: 19,
    maxBounds: MAX_BOUNDS,
    dragRotate: false,
    touchPitch: false,
    pitchWithRotate: false,
    maxPitch: 0,
    attributionControl: false,
    fadeDuration: 0,
    cooperativeGestures: false,
  });
  view.map = map;
  map.touchZoomRotate.disableRotation();
  map.keyboard.disableRotation?.();
  map.addControl(new maplibregl.AttributionControl({
    compact: true,
    customAttribution: '© OpenStreetMap contributors (ODbL)',
  }), 'bottom-left');
  map.addControl(new maplibregl.ScaleControl({ maxWidth: 90, unit: 'metric' }), 'bottom-left');

  map.on('error', (e) => {
    const msg = String(e?.error?.message || e?.error || e);
    // Missing tiles/glyphs are normal at the edges of the extract; log once per message.
    if (!view._errs) view._errs = new Set();
    if (!view._errs.has(msg)) {
      view._errs.add(msg);
      log.warn('map', 'maplibre error', { msg });
    }
  });

  map.on('style.load', () => {
    try {
      addOurLayers(view);
      applyAll(view);
      for (const fn of view.listeners) fn('style');
    } catch (err) {
      log.error('map', 'adding layers failed', { err: String(err), stack: err?.stack });
    }
  });

  map.on('click', (e) => onClick(view, e, opts));
  map.on('moveend', () => {
    const c = map.getCenter();
    opts.onMoveEnd?.({ lat: c.lat, lon: c.lng });
  });

  await new Promise((resolve) => {
    if (map.isStyleLoaded()) resolve();
    else map.once('load', resolve);
    setTimeout(resolve, 8000);
  });
  log.info('map', 'map ready', { mode: bm.mode, theme });

  /* ---------- public API ---------- */

  Object.assign(view, {
    on(fn) { view.listeners.add(fn); return () => view.listeners.delete(fn); },
    mount(host) {
      if (container.parentElement !== host) host.prepend(container);
      requestAnimationFrame(() => map.resize());
    },
    resize() { map.resize(); },
    setTheme(t) {
      const next = t === 'dark' ? 'dark' : 'light';
      if (next === view.theme) return;
      view.theme = next;
      for (const fn of view.listeners) fn('beforeStyle');
      map.setStyle(buildStyle(next, bm), { diff: false });
      log.info('map', 'theme changed', { theme: next });
    },
    /** Provide the index once geometry is available (builds the all-features source). */
    setIndex(index) {
      view.index = index;
      view.data.all = buildAllFeatures(index);
      setData(view, 'q-all', view.data.all);
    },
    setAreas(pack) {
      view.data.areas = buildAreas(pack);
      setData(view, 'q-areas', view.data.areas);
    },
    setResults(order) {
      view.st.results = order ? Array.from(order) : [];
      applyResults(view);
    },
    setSelected(i, houses) {
      view.st.selected = i;
      view.st.houses = houses || EMPTY;
      applySelected(view);
    },
    setZone(poly, on = true) {
      view.st.zone = poly || null;
      view.st.zoneOn = on;
      applyZone(view);
    },
    setDraft(poly) {
      view.st.draft = poly || null;
      applyDraft(view);
    },
    setVertex(latlon) {
      view.st.vertex = latlon;
      setData(view, 'q-vertex', latlon ? { type: 'FeatureCollection', features: [{ type: 'Feature', properties: {}, geometry: { type: 'Point', coordinates: [latlon[1], latlon[0]] } }] } : EMPTY);
    },
    setMe(me) {
      view.st.me = me;
      setData(view, 'q-me', me ? { type: 'FeatureCollection', features: [{ type: 'Feature', properties: {}, geometry: { type: 'Point', coordinates: [me.lon, me.lat] } }] } : EMPTY);
    },
    setMode(mode) {
      view.mode = mode;
      map.getCanvas().style.cursor = mode === 'areas' ? 'pointer' : '';
    },
    showAreas(kind, selected = []) {
      view.st.areasKind = kind;
      view.st.areasSel = selected;
      applyAreas(view);
    },
    fitFeature(i, { maxZoom = 17 } = {}) {
      const f = view.index?.pack?.core?.f?.[i];
      if (!f) return;
      if (f.bb) {
        const [a, b, c, d] = f.bb.map((x) => x / 1e5);
        map.fitBounds([[b, a], [d, c]], { padding: 60, maxZoom, duration: 400 });
      } else if (f.c) {
        map.easeTo({ center: [f.c[1] / 1e5, f.c[0] / 1e5], zoom: 16, duration: 400 });
      }
    },
    fitPolygon(poly, padding = 40) {
      if (!poly || !poly.length) return;
      let b = [Infinity, Infinity, -Infinity, -Infinity];
      for (const [lat, lon] of poly) b = [Math.min(b[0], lon), Math.min(b[1], lat), Math.max(b[2], lon), Math.max(b[3], lat)];
      map.fitBounds([[b[0], b[1]], [b[2], b[3]]], { padding, duration: 400, maxZoom: 16 });
    },
    fitResults() {
      const core = view.index?.pack?.core;
      if (!core || !view.st.results.length) return false;
      let b = [Infinity, Infinity, -Infinity, -Infinity];
      for (const i of view.st.results.slice(0, 3000)) {
        const f = core.f[i];
        const bb = f?.bb || (f?.c ? [f.c[0], f.c[1], f.c[0], f.c[1]] : null);
        if (!bb) continue;
        b = [Math.min(b[0], bb[1]), Math.min(b[1], bb[0]), Math.max(b[2], bb[3]), Math.max(b[3], bb[2])];
      }
      if (!Number.isFinite(b[0])) return false;
      map.fitBounds([[b[0] / 1e5, b[1] / 1e5], [b[2] / 1e5, b[3] / 1e5]], { padding: 50, maxZoom: 17, duration: 400 });
      return true;
    },
    flyTo(lat, lon, zoom = 16) {
      map.easeTo({ center: [lon, lat], zoom, duration: 500 });
    },
    project(latlon) {
      return map.project([latlon[1], latlon[0]]);
    },
    unproject(x, y) {
      const p = map.unproject([x, y]);
      return [p.lat, p.lng];
    },
    getCanvasContainer() { return map.getCanvasContainer(); },
  });
  return view;
}

/* ---------- data builders ---------- */

function buildAllFeatures(index) {
  const t0 = performance.now();
  const core = index.pack.core;
  const feats = [];
  for (let i = 0; i < index.n; i++) {
    let parts = [];
    try {
      parts = index.geometry(i) || [];
    } catch {
      parts = [];
    }
    const f = core.f[i];
    let geometry = null;
    const lines = parts.filter((p) => p.length > 1).map(toLngLat);
    if (lines.length === 1) geometry = { type: 'LineString', coordinates: lines[0] };
    else if (lines.length > 1) geometry = { type: 'MultiLineString', coordinates: lines };
    else if (parts.length && parts[0].length === 1) geometry = { type: 'Point', coordinates: [parts[0][0][1], parts[0][0][0]] };
    else if (f?.c) geometry = { type: 'Point', coordinates: [f.c[1] / 1e5, f.c[0] / 1e5] };
    if (!geometry) continue;
    const kind = core.kinds?.[f.k];
    feats.push({ type: 'Feature', properties: { i, g: kind?.group || '', k: kind?.code || '', name: f?.ka || f?.ru || '' }, geometry });
  }
  log.info('map', 'all-features source built', { n: feats.length, ms: Math.round(performance.now() - t0) });
  return { type: 'FeatureCollection', features: feats };
}

function decodeRings(poly) {
  // poly: [[outer, hole...], [outer2...]] of encoded strings
  const polys = [];
  for (const rings of poly || []) {
    const rr = rings.map((s) => ringClosed(toLngLat(decodePolyline(s)))).filter((r) => r.length >= 4);
    if (rr.length) polys.push(rr);
  }
  return polys;
}

function buildAreas(pack) {
  const feats = [];
  const core = pack.core;
  const areas = pack.areas;
  if (!areas) return EMPTY;
  const push = (kind, idx, rec, name, center) => {
    let polys = [];
    try {
      polys = decodeRings(rec?.poly);
    } catch (err) {
      log.warn('map', 'area decode failed', { kind, idx, err: String(err) });
    }
    if (!polys.length && center) {
      try {
        polys = [[ringClosed(toLngLat(circlePolygon(center, 300)))]];
      } catch {
        polys = [];
      }
    }
    if (!polys.length) return;
    feats.push({
      type: 'Feature',
      properties: { kind, idx, name },
      geometry: polys.length === 1 ? { type: 'Polygon', coordinates: polys[0] } : { type: 'MultiPolygon', coordinates: polys },
    });
  };
  (areas.districts || []).forEach((rec, idx) => push('d', idx, rec, core.districts?.[idx]?.ru || '', null));
  (areas.places || []).forEach((rec, idx) => {
    const p = core.places?.[idx];
    push('p', idx, rec, p?.ru || p?.ka || '', p?.c ? [p.c[0] / 1e5, p.c[1] / 1e5] : null);
  });
  return { type: 'FeatureCollection', features: feats };
}

/* ---------- layers ---------- */

function setData(view, id, data) {
  const src = view.map.getSource(id);
  if (src) src.setData(data);
}

function addOurLayers(view) {
  const map = view.map;
  const c = COLORS[view.theme];
  const fallback = !view.basemap.url;
  const glyphs = view.basemap.glyphsOk;
  const add = (id, data) => { if (!map.getSource(id)) map.addSource(id, { type: 'geojson', data: data || EMPTY, tolerance: 0.2 }); };
  add('q-all', view.data.all);
  add('q-areas', view.data.areas);
  add('q-zone');
  add('q-draft');
  add('q-houses');
  add('q-me');
  add('q-vertex');

  if (fallback) {
    // No basemap: our own streets are the map.
    map.addLayer({ id: 'q-base-line', type: 'line', source: 'q-all', filter: ['!=', ['geometry-type'], 'Point'],
      paint: { 'line-color': c.base, 'line-width': ['interpolate', ['linear'], ['zoom'], 11, 0.6, 15, 2, 18, 5] } });
    if (glyphs) {
      map.addLayer({ id: 'q-base-label', type: 'symbol', source: 'q-all', minzoom: 15,
        filter: ['!=', ['geometry-type'], 'Point'],
        layout: { 'symbol-placement': 'line', 'text-field': ['get', 'name'], 'text-font': ['Noto Sans Regular'], 'text-size': 11 },
        paint: { 'text-color': c.text, 'text-halo-color': c.halo, 'text-halo-width': 1.2 } });
    }
  } else {
    // Invisible hit layer so taps on any street open its card.
    map.addLayer({ id: 'q-base-line', type: 'line', source: 'q-all', filter: ['!=', ['geometry-type'], 'Point'],
      paint: { 'line-color': c.base, 'line-opacity': 0, 'line-width': 12 } });
  }

  map.addLayer({ id: 'q-areas-fill', type: 'fill', source: 'q-areas', filter: ['==', ['get', 'kind'], '-'],
    paint: { 'fill-color': c.area, 'fill-opacity': 0.06 } });
  map.addLayer({ id: 'q-areas-line', type: 'line', source: 'q-areas', filter: ['==', ['get', 'kind'], '-'],
    paint: { 'line-color': c.area, 'line-width': 1.2, 'line-opacity': 0.7 } });
  map.addLayer({ id: 'q-areas-sel', type: 'fill', source: 'q-areas', filter: ['==', ['get', 'kind'], '-'],
    paint: { 'fill-color': c.draft, 'fill-opacity': 0.28 } });
  if (glyphs) {
    map.addLayer({ id: 'q-areas-label', type: 'symbol', source: 'q-areas', filter: ['==', ['get', 'kind'], '-'],
      layout: { 'text-field': ['get', 'name'], 'text-font': ['Noto Sans Regular'], 'text-size': 12 },
      paint: { 'text-color': c.text, 'text-halo-color': c.halo, 'text-halo-width': 1.5 } });
  }

  map.addLayer({ id: 'q-zone-fill', type: 'fill', source: 'q-zone', paint: { 'fill-color': c.zone, 'fill-opacity': 0.07 } });
  map.addLayer({ id: 'q-zone-line', type: 'line', source: 'q-zone',
    paint: { 'line-color': c.zone, 'line-width': 2.5, 'line-dasharray': [3, 2] } });

  map.addLayer({ id: 'q-res-line', type: 'line', source: 'q-all', filter: ['in', ['get', 'i'], ['literal', []]],
    layout: { 'line-cap': 'round', 'line-join': 'round' },
    paint: { 'line-color': c.res, 'line-width': ['interpolate', ['linear'], ['zoom'], 11, 2, 15, 4, 18, 8], 'line-opacity': 0.85 } });
  map.addLayer({ id: 'q-res-pt', type: 'circle', source: 'q-all',
    filter: ['all', ['==', ['geometry-type'], 'Point'], ['in', ['get', 'i'], ['literal', []]]],
    paint: { 'circle-color': c.res, 'circle-radius': 6, 'circle-stroke-color': c.halo, 'circle-stroke-width': 1.5 } });

  map.addLayer({ id: 'q-sel-case', type: 'line', source: 'q-all', filter: ['==', ['get', 'i'], -1],
    layout: { 'line-cap': 'round', 'line-join': 'round' },
    paint: { 'line-color': c.selCase, 'line-width': ['interpolate', ['linear'], ['zoom'], 11, 6, 15, 10, 18, 16] } });
  map.addLayer({ id: 'q-sel-line', type: 'line', source: 'q-all', filter: ['==', ['get', 'i'], -1],
    layout: { 'line-cap': 'round', 'line-join': 'round' },
    paint: { 'line-color': c.sel, 'line-width': ['interpolate', ['linear'], ['zoom'], 11, 3, 15, 6, 18, 10] } });
  map.addLayer({ id: 'q-sel-pt', type: 'circle', source: 'q-all',
    filter: ['all', ['==', ['geometry-type'], 'Point'], ['==', ['get', 'i'], -1]],
    paint: { 'circle-color': c.sel, 'circle-radius': 9, 'circle-stroke-color': c.selCase, 'circle-stroke-width': 2 } });

  map.addLayer({ id: 'q-houses', type: 'circle', source: 'q-houses', minzoom: 14,
    paint: {
      'circle-color': ['case', ['==', ['get', 'hit'], 1], c.res, c.house],
      'circle-radius': ['case', ['==', ['get', 'hit'], 1], 7, 4],
      'circle-stroke-color': c.halo, 'circle-stroke-width': 1.2,
    } });
  if (glyphs) {
    map.addLayer({ id: 'q-houses-label', type: 'symbol', source: 'q-houses', minzoom: 15,
      layout: { 'text-field': ['get', 'raw'], 'text-font': ['Noto Sans Regular'], 'text-size': 12, 'text-offset': [0, 1], 'text-anchor': 'top', 'text-allow-overlap': false },
      paint: { 'text-color': c.text, 'text-halo-color': c.halo, 'text-halo-width': 1.5 } });
  }

  map.addLayer({ id: 'q-draft-fill', type: 'fill', source: 'q-draft', paint: { 'fill-color': c.draft, 'fill-opacity': 0.12 } });
  map.addLayer({ id: 'q-draft-line', type: 'line', source: 'q-draft', paint: { 'line-color': c.draft, 'line-width': 3 } });
  map.addLayer({ id: 'q-vertex', type: 'circle', source: 'q-vertex',
    paint: { 'circle-color': c.draft, 'circle-radius': 12, 'circle-opacity': 0.35, 'circle-stroke-color': c.draft, 'circle-stroke-width': 2 } });
  map.addLayer({ id: 'q-me', type: 'circle', source: 'q-me',
    paint: { 'circle-color': c.me, 'circle-radius': 8, 'circle-stroke-color': '#ffffff', 'circle-stroke-width': 3 } });

}

function applyAll(view) {
  if (view.data.all) setData(view, 'q-all', view.data.all);
  if (view.data.areas) setData(view, 'q-areas', view.data.areas);
  applyResults(view);
  applySelected(view);
  applyZone(view);
  applyDraft(view);
  applyAreas(view);
  view.setMe?.(view.st.me);
  view.setVertex?.(view.st.vertex);
}

function applyResults(view) {
  const map = view.map;
  if (!map.getLayer('q-res-line')) return;
  const ids = view.st.results.length > 20000 ? view.st.results.slice(0, 20000) : view.st.results;
  map.setFilter('q-res-line', ['in', ['get', 'i'], ['literal', ids]]);
  map.setFilter('q-res-pt', ['all', ['==', ['geometry-type'], 'Point'], ['in', ['get', 'i'], ['literal', ids]]]);
}

function applySelected(view) {
  const map = view.map;
  if (!map.getLayer('q-sel-line')) return;
  const i = view.st.selected;
  map.setFilter('q-sel-case', ['all', ['!=', ['geometry-type'], 'Point'], ['==', ['get', 'i'], i]]);
  map.setFilter('q-sel-line', ['all', ['!=', ['geometry-type'], 'Point'], ['==', ['get', 'i'], i]]);
  map.setFilter('q-sel-pt', ['all', ['==', ['geometry-type'], 'Point'], ['==', ['get', 'i'], i]]);
  setData(view, 'q-houses', view.st.houses || EMPTY);
}

function applyZone(view) {
  const f = view.st.zone && view.st.zoneOn ? polygonFeature(view.st.zone) : null;
  setData(view, 'q-zone', f ? { type: 'FeatureCollection', features: [f] } : EMPTY);
}

function applyDraft(view) {
  const f = view.st.draft ? polygonFeature(view.st.draft) : null;
  setData(view, 'q-draft', f ? { type: 'FeatureCollection', features: [f] } : EMPTY);
}

function applyAreas(view) {
  const map = view.map;
  if (!map.getLayer('q-areas-fill')) return;
  const kind = view.st.areasKind || '-';
  const base = ['==', ['get', 'kind'], kind];
  map.setFilter('q-areas-fill', base);
  map.setFilter('q-areas-line', base);
  if (map.getLayer('q-areas-label')) map.setFilter('q-areas-label', base);
  map.setFilter('q-areas-sel', ['all', base, ['in', ['get', 'idx'], ['literal', view.st.areasSel || []]]]);
}

function onClick(view, e, opts) {
  const map = view.map;
  if (view.mode === 'draw' || view.mode === 'edit') return;
  if (view.mode === 'areas') {
    const hits = map.queryRenderedFeatures(e.point, { layers: ['q-areas-fill'] });
    if (hits.length) {
      // Smallest area under the finger wins (microdistricts inside districts).
      const p = hits[hits.length - 1].properties;
      opts.onAreaTap?.(p.kind, Number(p.idx));
    }
    return;
  }
  const r = 10;
  const box = [[e.point.x - r, e.point.y - r], [e.point.x + r, e.point.y + r]];
  const layersOrder = ['q-sel-line', 'q-sel-pt', 'q-res-pt', 'q-res-line', 'q-base-line'].filter((id) => map.getLayer(id));
  const hits = map.queryRenderedFeatures(box, { layers: layersOrder });
  if (!hits.length) {
    opts.onFeatureTap?.(-1);
    return;
  }
  const pri = (h) => layersOrder.indexOf(h.layer.id);
  hits.sort((a, b) => pri(a) - pri(b));
  opts.onFeatureTap?.(Number(hits[0].properties.i), e.lngLat);
}

export function housesGeoJSON(rows, hitFn) {
  return {
    type: 'FeatureCollection',
    features: rows.map((r, k) => ({
      type: 'Feature',
      properties: { raw: String(r[0]), hit: hitFn && hitFn(r, k) ? 1 : 0 },
      geometry: { type: 'Point', coordinates: [r[5] / 1e5, r[4] / 1e5] },
    })),
  };
}

export { BG };
