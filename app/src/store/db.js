// Persistent app state in IndexedDB (idb-keyval custom store "quchebi"/"kv"):
// zone (+ localStorage mirror), settings, query history, saved queries, notes.
// Every call is wrapped in try/catch: storage failures are logged and never break the UI.
//
//   import * as db from './store/db.js';
//   await db.setZone({polygon, createdAt: Date.now(), mode: 'draw', name: 'Сололаки'});
import { createStore, get as idbGet, set as idbSet, del as idbDel } from 'idb-keyval';
import { log } from '../log/log.js';

export const ZONE_LS_KEY = 'quchebi.zone';
export const HISTORY_MAX = 100;
export const SAVED_MAX = 200;
export const NOTES_MAX = 1000;

/** In-memory kv with the idb-keyval-like interface (fallback and tests). */
export function memoryKV() {
  const m = new Map();
  const clone = (v) => (v === undefined ? undefined : structuredCloneSafe(v));
  return {
    map: m,
    get: async (k) => clone(m.get(k)),
    set: async (k, v) => { m.set(k, clone(v)); },
    del: async (k) => { m.delete(k); },
  };
}

function structuredCloneSafe(v) {
  if (typeof structuredClone === 'function') return structuredClone(v);
  return JSON.parse(JSON.stringify(v));
}

/** idb-keyval backed kv, or null when IndexedDB is unavailable. */
export function idbKV(dbName = 'quchebi', storeName = 'kv') {
  if (typeof indexedDB === 'undefined') return null;
  try {
    const store = createStore(dbName, storeName);
    return {
      get: (k) => idbGet(k, store),
      set: (k, v) => idbSet(k, v, store),
      del: (k) => idbDel(k, store),
    };
  } catch (e) {
    log.warn('db', 'IndexedDB unavailable: ' + (e && e.message));
    return null;
  }
}

function defaultStorage() {
  try {
    if (typeof localStorage !== 'undefined' && localStorage) return localStorage;
  } catch { /* SecurityError in some private modes */ }
  return null;
}

/**
 * Create a db facade over a kv store and a localStorage-like storage.
 * @param {{kv?: {get,set,del}, storage?: Storage|null}} o
 */
export function createDb(o = {}) {
  let kv = o.kv === undefined ? idbKV() : o.kv;
  if (!kv) kv = memoryKV();
  const storage = o.storage === undefined ? defaultStorage() : o.storage;
  let kvBroken = false;

  async function kget(key, def) {
    if (kvBroken) return def;
    try {
      const v = await kv.get(key);
      return v === undefined ? def : v;
    } catch (e) {
      log.error('db', `get ${key} failed: ${e && e.message}`);
      return def;
    }
  }
  async function kset(key, value) {
    if (kvBroken) return false;
    try { await kv.set(key, value); return true; } catch (e) {
      log.error('db', `set ${key} failed: ${e && e.message}`);
      if (e && /InvalidStateError|UnknownError/.test(String(e.name))) kvBroken = true;
      return false;
    }
  }
  async function kdel(key) {
    try { await kv.del(key); return true; } catch (e) { log.error('db', `del ${key} failed: ${e && e.message}`); return false; }
  }
  function lsGet(key) {
    if (!storage) return null;
    try { const s = storage.getItem(key); return s ? JSON.parse(s) : null; } catch (e) { log.warn('db', `localStorage read ${key}: ${e && e.message}`); return null; }
  }
  function lsSet(key, value) {
    if (!storage) return false;
    try { storage.setItem(key, JSON.stringify(value)); return true; } catch (e) { log.warn('db', `localStorage write ${key}: ${e && e.message}`); return false; }
  }
  function lsDel(key) {
    if (!storage) return;
    try { storage.removeItem(key); } catch { /* ignore */ }
  }

  function validZone(z) {
    return z && Array.isArray(z.polygon) && z.polygon.length >= 3;
  }

  const api = {
    kvGet: kget, kvSet: kset, kvDel: kdel,

    // ------------------------------------------------------------ zone
    /** Saved zone {polygon, createdAt, mode, name?, enabled?, buffer?} or null. IDB first, then the mirror. */
    async getZone() {
      const z = await kget('zone', null);
      if (validZone(z)) return z;
      const m = lsGet(ZONE_LS_KEY);
      if (validZone(m)) {
        log.warn('db', 'zone restored from localStorage mirror');
        kset('zone', m);
        return m;
      }
      return null;
    },
    /** Save the zone to IndexedDB and the localStorage mirror; true if at least one succeeded. */
    async setZone(zone) {
      if (!validZone(zone)) throw new Error('setZone: polygon with >= 3 vertices required');
      const z = { createdAt: Date.now(), ...zone };
      const a = await kset('zone', z);
      const b = lsSet(ZONE_LS_KEY, z);
      if (!a && !b) log.error('db', 'zone could not be saved anywhere');
      else log.info('db', `zone saved (${z.polygon.length} vertices, idb=${a}, mirror=${b})`);
      return a || b;
    },
    async clearZone() {
      await kdel('zone');
      lsDel(ZONE_LS_KEY);
      log.info('db', 'zone cleared');
    },

    // ------------------------------------------------------------ settings
    async getSettings() { return { ...(await kget('settings', {})) }; },
    async getSetting(key, def) {
      const s = await kget('settings', {});
      return s && Object.prototype.hasOwnProperty.call(s, key) ? s[key] : def;
    },
    async setSetting(key, value) {
      const s = { ...(await kget('settings', {})) };
      if (value === undefined) delete s[key]; else s[key] = value;
      return kset('settings', s);
    },

    // ------------------------------------------------------------ history
    /** Query history, newest first. */
    async getHistory() { const h = await kget('history', []); return Array.isArray(h) ? h : []; },
    async addHistory(query) {
      const q = String(query || '').trim();
      if (!q) return false;
      const h = (await api.getHistory()).filter((x) => x !== q);
      h.unshift(q);
      return kset('history', h.slice(0, HISTORY_MAX));
    },
    async clearHistory() { return kset('history', []); },

    // ------------------------------------------------------------ saved queries
    /** Saved queries [{q, name, t}], newest first. */
    async getSaved() { const s = await kget('saved', []); return Array.isArray(s) ? s : []; },
    async addSaved(item) {
      const q = String((item && item.q) || '').trim();
      if (!q) return false;
      const list = (await api.getSaved()).filter((x) => x.q !== q);
      list.unshift({ q, name: (item.name && String(item.name)) || q, t: item.t || Date.now() });
      return kset('saved', list.slice(0, SAVED_MAX));
    },
    async removeSaved(q) {
      const list = (await api.getSaved()).filter((x) => x.q !== q);
      return kset('saved', list);
    },

    // ------------------------------------------------------------ notes
    /** Notes [{t, featureKey, text}], newest first. featureKey should be stable across rebuilds (names/QID). */
    async getNotes() { const s = await kget('notes', []); return Array.isArray(s) ? s : []; },
    async addNote(note) {
      if (!note || !String(note.text || '').trim()) return false;
      const list = await api.getNotes();
      const t = note.t || Date.now();
      const rest = list.filter((x) => x.t !== t);
      rest.unshift({ ...note, t, text: String(note.text) });
      return kset('notes', rest.slice(0, NOTES_MAX));
    },
    async removeNote(t) {
      const list = (await api.getNotes()).filter((x) => x.t !== t);
      return kset('notes', list);
    },
  };
  return api;
}

let defaultDb = null;
/** The default db instance (IndexedDB + localStorage). Replace with configureDb() in tests. */
export function getDb() {
  if (!defaultDb) defaultDb = createDb();
  return defaultDb;
}
/** Replace the default instance (tests, or a custom store). */
export function configureDb(o) { defaultDb = createDb(o); return defaultDb; }

export const kvGet = (k, d) => getDb().kvGet(k, d);
export const kvSet = (k, v) => getDb().kvSet(k, v);
export const kvDel = (k) => getDb().kvDel(k);
export const getZone = () => getDb().getZone();
export const setZone = (z) => getDb().setZone(z);
export const clearZone = () => getDb().clearZone();
export const getSettings = () => getDb().getSettings();
export const getSetting = (k, d) => getDb().getSetting(k, d);
export const setSetting = (k, v) => getDb().setSetting(k, v);
export const getHistory = () => getDb().getHistory();
export const addHistory = (q) => getDb().addHistory(q);
export const clearHistory = () => getDb().clearHistory();
export const getSaved = () => getDb().getSaved();
export const addSaved = (i) => getDb().addSaved(i);
export const removeSaved = (q) => getDb().removeSaved(q);
export const getNotes = () => getDb().getNotes();
export const addNote = (n) => getDb().addNote(n);
export const removeNote = (t) => getDb().removeNote(t);
