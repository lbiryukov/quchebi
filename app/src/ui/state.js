// Shared UI state and a tiny event bus. Logic modules (search/dsl/zone/store) stay pure;
// the UI keeps the current pack, index, zone and query here.

export const state = {
  city: null,          // city record of this page load (store/cities.js)
  pack: null,          // Pack from store/pack.js (pack.lang: 'ka' | 'ru')
  index: null,         // Index from search/index.js
  packReady: false,    // geom/houses/areas attached (pack.ready resolved)
  zone: null,          // stored zone record {polygon, createdAt, mode, name, base, buffer}
  masks: null,         // ZoneMasks for zone.polygon
  zoneEnabled: true,   // temporary switch ("временно выключить")
  me: null,            // {lat, lon} after "Где я"
  mapCenter: null,     // {lat, lon}
  query: '',
  result: null,        // last evaluate() result
  selected: -1,        // feature index shown in card / on map
  screen: 'search',
  settings: { theme: 'auto', gameMode: false },
  updateWaiting: false,
  swRegistered: false,
  basemapMode: 'none', // 'pmtiles-cache' | 'pmtiles-net' | 'fallback' | 'none'
};

const listeners = new Map();

export function on(event, fn) {
  if (!listeners.has(event)) listeners.set(event, new Set());
  listeners.get(event).add(fn);
  return () => listeners.get(event)?.delete(fn);
}

export function emit(event, data) {
  const set = listeners.get(event);
  if (!set) return;
  for (const fn of [...set]) {
    try {
      fn(data);
    } catch (err) {
      // A broken listener must never break the others; main.js mirrors this to the log.
      console.error('[state] listener failed for', event, err);
    }
  }
}

/** Evaluation context for dsl run()/evaluate(). */
export function evalCtx() {
  return {
    zone: state.zone && state.zoneEnabled && state.masks ? state.masks : null,
    me: state.me,
    mapCenter: state.mapCenter,
  };
}

/** Language of the loaded pack: 'ka' (Tbilisi) or 'ru' (Moscow). */
export function packLanguage() {
  return state.pack?.lang || state.pack?.manifest?.city?.lang || state.city?.lang || 'ka';
}

export function zoneActive() {
  return Boolean(state.zone && state.zoneEnabled && state.masks);
}
