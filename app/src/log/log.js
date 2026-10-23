// Ring-buffer logger: the last `capacity` (2000) entries live in IndexedDB (idb-keyval custom
// store "quchebi-log"), writes are buffered and flushed in batches, every entry is mirrored to
// the console. Works without IndexedDB (Node, private mode): entries then stay in memory.
//
//   import { log, getLogs, clearLogs, exportLogs } from './log/log.js';
//   log.info('pack', 'loaded', {version});
import { createStore, setMany, entries as idbEntries, clear as idbClear, get as idbGet } from 'idb-keyval';

export const LEVELS = Object.freeze({ debug: 10, info: 20, warn: 30, error: 40 });
const SEQ_KEY = 'seq';
const MAX_DATA_CHARS = 4000;

function pad2(n) { return String(n).padStart(2, '0'); }

/** DD/MM/YYYY HH:MM:SS.mmm in local time. */
export function formatTime(t) {
  const d = new Date(t);
  return `${pad2(d.getDate())}/${pad2(d.getMonth() + 1)}/${d.getFullYear()} ${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}.${String(d.getMilliseconds()).padStart(3, '0')}`;
}

/** Make `data` structured-cloneable and bounded in size. */
export function sanitize(data) {
  if (data === undefined) return undefined;
  if (data instanceof Error) return { name: data.name, message: data.message, stack: data.stack };
  try {
    const seen = new WeakSet();
    const json = JSON.stringify(data, (k, v) => {
      if (v instanceof Error) return { name: v.name, message: v.message, stack: v.stack };
      if (typeof v === 'function') return undefined;
      if (typeof v === 'bigint') return String(v);
      if (v && typeof v === 'object') { if (seen.has(v)) return '[circular]'; seen.add(v); }
      return v;
    });
    if (json === undefined) return undefined;
    if (json.length > MAX_DATA_CHARS) return { truncated: true, text: json.slice(0, MAX_DATA_CHARS) };
    return JSON.parse(json);
  } catch (e) {
    return { unserializable: String(e && e.message) };
  }
}

/** idb-keyval backed storage adapter, or null when IndexedDB is unavailable. */
export function idbLogStore(dbName = 'quchebi-log', storeName = 'log') {
  if (typeof indexedDB === 'undefined') return null;
  try {
    const store = createStore(dbName, storeName);
    return {
      get: (k) => idbGet(k, store),
      setMany: (list) => setMany(list, store),
      entries: () => idbEntries(store),
      clear: () => idbClear(store),
    };
  } catch {
    return null;
  }
}

/** In-memory storage adapter with the same interface (tests, fallbacks). */
export function memoryLogStore() {
  const m = new Map();
  return {
    map: m,
    get: async (k) => m.get(k),
    setMany: async (list) => { for (const [k, v] of list) m.set(k, v); },
    entries: async () => [...m.entries()],
    clear: async () => { m.clear(); },
  };
}

export class Logger {
  /**
   * @param {object} o
   * @param {number} [o.capacity=2000]  ring size
   * @param {object|null} [o.store]      storage adapter (idbLogStore() by default; null = memory only)
   * @param {object} [o.console]         console to mirror to (null disables mirroring)
   * @param {string} [o.mirrorLevel]     minimal level mirrored to the console
   * @param {number} [o.flushMs=1000]    buffered write delay
   * @param {number} [o.batch=50]        flush immediately when this many entries are pending
   */
  constructor(o = {}) {
    this.capacity = o.capacity || 2000;
    this.store = o.store === undefined ? idbLogStore() : o.store;
    this.console = o.console === undefined ? globalThis.console : o.console;
    const isBrowser = typeof window !== 'undefined';
    this.mirrorLevel = LEVELS[o.mirrorLevel || (isBrowser ? 'debug' : 'warn')];
    this.minLevel = LEVELS[o.minLevel || 'debug'];
    this.flushMs = o.flushMs === undefined ? 1000 : o.flushMs;
    this.batch = o.batch || 50;
    this.pending = [];
    this.mem = []; // ring of recent entries (always kept; source of truth without a store)
    this.memSeq = 0;
    this._nextSeq = null;
    this._timer = null;
    this._flushing = null;
    this._failed = false;
    this.debug = (tag, msg, data) => this.write('debug', tag, msg, data);
    this.info = (tag, msg, data) => this.write('info', tag, msg, data);
    this.warn = (tag, msg, data) => this.write('warn', tag, msg, data);
    this.error = (tag, msg, data) => this.write('error', tag, msg, data);
    if (isBrowser && typeof window.addEventListener === 'function') {
      const flush = () => { this.flush().catch(() => {}); };
      window.addEventListener('pagehide', flush);
      if (typeof document !== 'undefined') document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'hidden') flush(); });
    }
  }

  write(level, tag, msg, data) {
    if (!(level in LEVELS)) level = 'info';
    if (LEVELS[level] < this.minLevel) return null;
    if (msg === undefined && typeof tag === 'string') { msg = tag; tag = 'app'; }
    const entry = { seq: -1, t: Date.now(), level, tag: String(tag || 'app'), msg: typeof msg === 'string' ? msg : String(msg) };
    const d = sanitize(data);
    if (d !== undefined) entry.data = d;
    entry.seq = this.memSeq++;
    this.mem.push(entry);
    if (this.mem.length > this.capacity) this.mem.splice(0, this.mem.length - this.capacity);
    if (this.console && LEVELS[level] >= this.mirrorLevel) {
      try {
        const fn = this.console[level] || this.console.log;
        if (d !== undefined) fn.call(this.console, `[${entry.tag}] ${entry.msg}`, d); else fn.call(this.console, `[${entry.tag}] ${entry.msg}`);
      } catch { /* console unavailable */ }
    }
    if (this.store && !this._failed) {
      this.pending.push(entry);
      if (this.pending.length >= this.batch) this.flush().catch(() => {});
      else this._schedule();
    }
    return entry;
  }

  _schedule() {
    if (this._timer || this.flushMs <= 0) { if (this.flushMs <= 0) this.flush().catch(() => {}); return; }
    this._timer = setTimeout(() => { this._timer = null; this.flush().catch(() => {}); }, this.flushMs);
    if (this._timer && typeof this._timer.unref === 'function') this._timer.unref();
  }

  async _initSeq() {
    if (this._nextSeq !== null) return;
    let s = 0;
    try { s = (await this.store.get(SEQ_KEY)) || 0; } catch { s = 0; }
    this._nextSeq = Number.isFinite(s) ? s : 0;
  }

  /** Write pending entries to the store. Resolves when they are persisted (or the store failed). */
  async flush() {
    if (this._timer) { clearTimeout(this._timer); this._timer = null; }
    if (!this.store || this._failed) { this.pending = []; return; }
    if (this._flushing) { await this._flushing; if (!this.pending.length) return; }
    if (!this.pending.length) return;
    const batch = this.pending;
    this.pending = [];
    this._flushing = (async () => {
      try {
        await this._initSeq();
        const list = [];
        for (const e of batch) {
          const seq = this._nextSeq++;
          list.push([seq % this.capacity, { ...e, seq }]);
        }
        list.push([SEQ_KEY, this._nextSeq]);
        await this.store.setMany(list);
      } catch (err) {
        this._failed = true; // keep working in memory
        if (this.console) try { this.console.warn('[log] storage failed, logging in memory only', err && err.message); } catch { /* */ }
      }
    })();
    try { await this._flushing; } finally { this._flushing = null; }
  }

  /** All stored entries (oldest first): [{seq, t, level, tag, msg, data?}]. */
  async getLogs() {
    await this.flush();
    if (!this.store || this._failed) return this.mem.slice();
    try {
      const all = await this.store.entries();
      const rows = all.filter(([k]) => typeof k === 'number').map(([, v]) => v).filter(Boolean);
      rows.sort((a, b) => a.seq - b.seq);
      return rows;
    } catch {
      return this.mem.slice();
    }
  }

  async clear() {
    this.pending = [];
    this.mem = [];
    if (this.store && !this._failed) {
      try { await this.store.clear(); this._nextSeq = 0; } catch { /* ignore */ }
    }
  }

  /** Plain-text export for the "Журнал" screen: one line per entry. */
  async exportText() {
    const rows = await this.getLogs();
    return rows.map((r) => {
      let line = `${formatTime(r.t)} ${r.level.toUpperCase().padEnd(5)} [${r.tag}] ${r.msg}`;
      if (r.data !== undefined) { try { line += ' ' + JSON.stringify(r.data); } catch { /* */ } }
      return line;
    }).join('\n');
  }
}

export function createLogger(opts) { return new Logger(opts); }

/** Default application logger. */
export const log = createLogger();

export const getLogs = () => log.getLogs();
export const clearLogs = () => log.clear();
export const exportLogs = () => log.exportText();
export const flushLogs = () => log.flush();

// Global window 'error' / 'unhandledrejection' handlers live in main.js (one place only).

export default log;
