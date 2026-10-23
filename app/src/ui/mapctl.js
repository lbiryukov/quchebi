// Shared map controller: lazily loads MapLibre (code-split), owns the single map view and keeps
// its layers in sync with the UI state. Screens mount it into their host element.
import { log } from '../log/log.js';
import { h } from './dom.js';
import { state, on } from './state.js';
import { effectiveTheme } from './theme.js';
import { houseRows } from './feature.js';
import { houseQuery } from './dsledit.js';

const container = h('div.map-el', { id: 'map' });
let viewPromise = null;
let view = null;
let mod = null;
let failed = null;

/** Handlers set by the active screen. */
export const hooks = {
  onFeatureTap: null,
  onAreaTap: null,
};

export function currentView() {
  return view;
}

export function mapFailed() {
  return failed;
}

function webglAvailable() {
  try {
    const c = document.createElement('canvas');
    return Boolean(c.getContext('webgl2') || c.getContext('webgl'));
  } catch {
    return false;
  }
}

export function getMapView() {
  if (viewPromise) return viewPromise;
  viewPromise = (async () => {
    if (!webglAvailable()) throw new Error('WebGL недоступен');
    const t0 = performance.now();
    mod = await import('../map/map.js');
    view = await mod.createMapView(container, {
      theme: effectiveTheme(),
      onFeatureTap: (i, lngLat) => hooks.onFeatureTap?.(i, lngLat),
      onAreaTap: (kind, idx) => hooks.onAreaTap?.(kind, idx),
      onMoveEnd: (c) => { state.mapCenter = c; },
    });
    state.basemapMode = view.basemap.mode;
    if (state.packReady && state.index) syncData();
    syncAll();
    log.info('map', 'map view created', { ms: Math.round(performance.now() - t0), basemap: view.basemap.mode });
    return view;
  })().catch((err) => {
    failed = err;
    log.error('map', 'map creation failed', { err: String(err), stack: err?.stack });
    throw err;
  });
  return viewPromise;
}

/** Mount the map into `host` (creating it on first use). Resolves with the view or null. */
export async function mountMap(host) {
  if (container.parentElement !== host) host.prepend(container);
  try {
    const v = await getMapView();
    v.mount(host);
    return v;
  } catch (err) {
    if (!host.querySelector('.map-unavailable')) {
      host.append(h('div.map-unavailable', h('p', 'Карта недоступна на этом устройстве.'), h('p.small', String(err?.message || err))));
    }
    return null;
  }
}

function syncData() {
  if (!view || !state.index) return;
  try {
    view.setIndex(state.index);
    view.setAreas(state.pack);
  } catch (err) {
    log.error('map', 'sync data failed', { err: String(err), stack: err?.stack });
  }
}

function syncAll() {
  if (!view) return;
  view.setResults(state.result && !state.result.error ? state.result.order : []);
  syncSelected();
  view.setZone(state.zone?.polygon || null, state.zoneEnabled);
  view.setMe(state.me);
}

function syncSelected() {
  if (!view || !mod) return;
  const i = state.selected;
  if (i < 0) {
    view.setSelected(-1, null);
    return;
  }
  const hq = houseQuery(state.query || '');
  const rows = houseRows(i);
  const hit = hq && Number.isFinite(hq.n) ? (r) => r[1] === hq.n || (r[6] && hq.n >= r[1] && hq.n <= r[6]) : null;
  view.setSelected(i, mod.housesGeoJSON(rows, hit));
}

on('packReady', syncData);
on('result', (r) => view?.setResults(r && !r.error ? r.order : []));
on('select', syncSelected);
on('zone', () => view?.setZone(state.zone?.polygon || null, state.zoneEnabled));
on('me', (me) => view?.setMe(me));
on('theme', (t) => view?.setTheme(t));
