// City registry ("районы" of the app) and per-city storage names (docs/client-architecture.md
// "Cities"). Tbilisi is the legacy city: it keeps the storage keys and cache names of the
// single-city app (pack pointer 'pack:current', caches 'pack-v<version>', zone 'zone' +
// localStorage 'quchebi.zone', ...), so an installed app keeps its pack, tiles and zone.
// Every other city gets suffixed names: 'pack:current:<id>', 'pack-<id>-v<version>',
// 'zone:<id>', 'quchebi.zone.<id>'.
//
// The chosen city is a global setting ('city' in IndexedDB settings) mirrored to localStorage
// ('quchebi.city') so it is known synchronously before the first paint; `?city=<id>` in the URL
// selects a city directly (deep links, e2e) and is remembered.

export const CITIES = [
  { id: 'tbilisi', title: 'Тбилиси', sub: 'Грузия', dataDir: './data/', lang: 'ka' },
  { id: 'khamovniki', title: 'Москва. Хамовники', sub: 'Россия', dataDir: './data/khamovniki/', lang: 'ru', whole: 'Весь район' },
];

/** The city whose storage names have no suffix (the app was Tbilisi-only before cities). */
export const LEGACY_CITY = 'tbilisi';
export const CITY_SETTING = 'city';
export const CITY_LS_KEY = 'quchebi.city';
export const CITY_PARAM = 'city';

const BY_ID = new Map(CITIES.map((c) => [c.id, c]));

/** City record by id, or null. */
export function getCity(id) {
  return (id && BY_ID.get(String(id))) || null;
}

export function isLegacyCity(id) {
  return !id || id === LEGACY_CITY;
}

/** IndexedDB key of a per-city record: 'zone' (Tbilisi) / 'zone:khamovniki'. */
export function cityKey(base, id) {
  return isLegacyCity(id) ? base : `${base}:${id}`;
}

/** localStorage key of a per-city record: 'quchebi.zone' (Tbilisi) / 'quchebi.zone.khamovniki'. */
export function cityLsKey(base, id) {
  return isLegacyCity(id) ? base : `${base}.${id}`;
}

/** Pack pointer key in IndexedDB: 'pack:current' / 'pack:current:khamovniki'. */
export function pointerKey(id) {
  return cityKey('pack:current', id);
}

/** Cache Storage prefix of the pack caches: 'pack-v' / 'pack-khamovniki-v'. */
export function cachePrefix(id) {
  return isLegacyCity(id) ? 'pack-v' : `pack-${id}-v`;
}

/**
 * Which city owns a Cache Storage name ('pack-v20261024-3' -> 'tbilisi',
 * 'pack-khamovniki-v20261110-1' -> 'khamovniki'), or null for other caches. A name of the form
 * 'pack-<word>-v…' never belongs to Tbilisi, even for a city that is not registered (yet), so
 * cleaning up old Tbilisi caches can never delete another city's pack.
 */
export function cacheOwner(name) {
  const s = String(name || '');
  if (!s.startsWith('pack-')) return null;
  for (const c of CITIES) {
    if (isLegacyCity(c.id)) continue;
    if (s.startsWith(cachePrefix(c.id))) return c.id;
  }
  const legacy = cachePrefix(LEGACY_CITY);
  if (s.startsWith(legacy) && !/^[a-z][a-z0-9_]*-v/i.test(s.slice(legacy.length))) return LEGACY_CITY;
  return null;
}

/** Is the cache `name` a pack cache of city `id`? */
export function ownsCache(name, id) {
  return cacheOwner(name) === (id || LEGACY_CITY);
}

/** Is the cache `name` a pack cache of any city (for sha256-checked reuse of shared files)? */
export function isPackCache(name) {
  return String(name || '').startsWith('pack-');
}

// ------------------------------------------------------------------------------ current city

let currentId = null;

/** Id of the city this page was booted for (null before the choice). */
export function currentCityId() {
  return currentId;
}

/** City record this page was booted for, or null. */
export function currentCity() {
  return getCity(currentId);
}

/** Label of "no zone, search everywhere": «Весь город» for a city, «Весь район» for a district. */
export function wholeLabel() {
  return (currentCity() && currentCity().whole) || 'Весь город';
}

/** Set the city of this page load (storage helpers and pack.js default to it). */
export function setCurrentCity(id) {
  currentId = getCity(id) ? id : null;
  return currentCity();
}

function lsGet(storage, key) {
  try { return storage ? storage.getItem(key) : null; } catch { return null; }
}

function lsSet(storage, key, value) {
  try {
    if (!storage) return;
    if (value === null || value === undefined) storage.removeItem(key);
    else storage.setItem(key, value);
  } catch { /* storage blocked or full */ }
}

function defaultStorage() {
  try { return typeof localStorage !== 'undefined' ? localStorage : null; } catch { return null; }
}

/** `?city=<id>` of a URL search string, if it names a known city. */
export function cityFromUrl(search) {
  try {
    const id = new URLSearchParams(search || '').get(CITY_PARAM);
    return getCity(id) ? id : null;
  } catch {
    return null;
  }
}

/**
 * The city to boot synchronously: URL parameter first, then the localStorage mirror.
 * Returns {id, from: 'url'|'storage'} or null when the user has not chosen yet.
 */
export function initialCity({ search, storage } = {}) {
  const st = storage === undefined ? defaultStorage() : storage;
  const fromUrl = cityFromUrl(search === undefined ? (typeof location !== 'undefined' ? location.search : '') : search);
  if (fromUrl) return { id: fromUrl, from: 'url' };
  const saved = lsGet(st, CITY_LS_KEY);
  if (getCity(saved)) return { id: saved, from: 'storage' };
  return null;
}

/** Remember the choice in the localStorage mirror (sync) and in IndexedDB settings (async, if a db is given). */
export async function rememberCity(id, { storage, db } = {}) {
  if (!getCity(id)) throw new Error(`неизвестный район «${id}»`);
  lsSet(storage === undefined ? defaultStorage() : storage, CITY_LS_KEY, id);
  if (db && db.setSetting) {
    try { await db.setSetting(CITY_SETTING, id); } catch { /* the mirror is enough to boot */ }
  }
}

/** Forget the choice (the picker shows on the next start). */
export async function forgetCity({ storage, db } = {}) {
  lsSet(storage === undefined ? defaultStorage() : storage, CITY_LS_KEY, null);
  if (db && db.setSetting) {
    try { await db.setSetting(CITY_SETTING, undefined); } catch { /* ignore */ }
  }
}

/**
 * Language of a pack: manifest.city.lang, else the registry language of the city it was loaded
 * for (pack.lang, set by main.js), else 'ka' (the Tbilisi manifest has no `city`).
 */
export function packLang(pack) {
  return pack?.manifest?.city?.lang || pack?.lang || 'ka';
}

/**
 * Basemap settings of a pack, from its manifest.tiles:
 * {path: 'tiles/x.pmtiles' | null, file: 'x.pmtiles' | null, bbox: [w, s, e, n] | null,
 *  center: [lon, lat, zoom] | null, minzoom, maxzoom}.
 * Accepts both manifest.tiles shapes (with `basemap`/`files`, or {"x.pmtiles": {...}}).
 */
export function tilesInfo(manifest) {
  const t = (manifest && manifest.tiles) || {};
  let path = typeof t.basemap === 'string' ? t.basemap : null;
  if (!path && t.files && typeof t.files === 'object') path = Object.keys(t.files).find((k) => k.endsWith('.pmtiles')) || null;
  if (!path) {
    const k = Object.keys(t).find((x) => x.endsWith('.pmtiles'));
    if (k) path = k;
  }
  if (path) {
    path = path.replace(/^\.\//, '');
    if (!path.startsWith('tiles/')) path = `tiles/${path}`;
  }
  const nums = (a, n) => (Array.isArray(a) && a.length >= n && a.slice(0, n).every(Number.isFinite) ? a.slice(0, n) : null);
  const center = nums(t.center, 2);
  return {
    path,
    file: path ? path.slice(path.lastIndexOf('/') + 1) : null,
    bbox: nums(t.bbox, 4),
    center: center ? [center[0], center[1], Number.isFinite(t.center[2]) ? t.center[2] : null] : null,
    minzoom: Number.isFinite(t.minzoom) ? t.minzoom : null,
    maxzoom: Number.isFinite(t.maxzoom) ? t.maxzoom : null,
  };
}
