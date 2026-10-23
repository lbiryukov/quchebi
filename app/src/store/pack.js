// Data pack loader (docs/pack-format.md, docs/client-architecture.md "Storage").
//
// * Files live in Cache Storage `pack-v<version>` under their absolute URLs
//   (data: new URL(name, baseUrl); tiles: new URL(key, appBaseUrl) for manifest.tiles.files).
// * Every downloaded file is checked against manifest sha256 (crypto.subtle) before it is used.
// * The installed version is a pointer in IndexedDB ('pack:current'); it is switched only after
//   all required files of the new version are cached and verified, then old pack-v* caches go.
// * Offline: loadPack() reads the pointed cache without touching the network.
// * loadPack() resolves after core.json + eponyms.json; pack.ready resolves when geom/houses/areas
//   are attached to the same object.
//
// Every function takes an optional `env` {fetch, caches, subtle, kv, baseURI} for tests.
import { kvGet, kvSet } from './db.js';
import { log } from '../log/log.js';

export const POINTER_KEY = 'pack:current';
export const CACHE_PREFIX = 'pack-v';
export const CORE_FILES = ['core.json', 'eponyms.json'];
export const LATE_FILES = ['geom.json', 'houses.json', 'areas.json'];
const MANIFEST = 'manifest.json';
const CONCURRENCY = 4;

function defaultEnv(env = {}) {
  const g = globalThis;
  return {
    fetch: env.fetch || (g.fetch ? g.fetch.bind(g) : null),
    caches: env.caches !== undefined ? env.caches : (g.caches || null),
    subtle: env.subtle || (g.crypto && g.crypto.subtle) || null,
    kv: env.kv || { get: (k) => kvGet(k, undefined), set: (k, v) => kvSet(k, v) },
    baseURI: env.baseURI || (typeof document !== 'undefined' && document.baseURI) || (g.location && g.location.href) || 'http://localhost/',
    timeoutMs: env.timeoutMs || 3000,
  };
}

function urls(env, baseUrl, appBaseUrl) {
  const data = new URL(baseUrl || './data/', env.baseURI);
  const app = new URL(appBaseUrl || './', env.baseURI);
  return { data, app, manifest: new URL(MANIFEST, data).href };
}

/** Tile entries of a manifest: [{key, url, bytes, sha256}]. Supports both manifest.tiles shapes. */
export function tileEntries(manifest, env, baseUrl, appBaseUrl) {
  const u = urls(defaultEnv(env), baseUrl, appBaseUrl);
  const t = (manifest && manifest.tiles) || {};
  const out = [];
  if (t.files && typeof t.files === 'object') {
    for (const [key, meta] of Object.entries(t.files)) out.push({ key, url: new URL(key, u.app).href, ...meta });
  } else {
    const tilesDir = new URL('tiles/', u.app);
    for (const [key, meta] of Object.entries(t)) {
      if (meta && typeof meta === 'object' && meta.sha256) out.push({ key, url: new URL(key, tilesDir).href, ...meta });
    }
  }
  return out;
}

function dataEntries(manifest, u) {
  return Object.entries((manifest && manifest.files) || {}).map(([key, meta]) => ({ key, url: new URL(key, u.data).href, ...meta }));
}

export async function sha256Hex(subtle, buf) {
  if (!subtle) throw new Error('crypto.subtle недоступен (нужен HTTPS)');
  const d = await subtle.digest('SHA-256', buf);
  return Array.from(new Uint8Array(d), (b) => b.toString(16).padStart(2, '0')).join('');
}

async function fetchWithTimeout(env, url, init = {}, timeoutMs) {
  if (!env.fetch) throw new Error('fetch недоступен');
  if (!timeoutMs || typeof AbortController === 'undefined') return env.fetch(url, init);
  const ac = new AbortController();
  const timer = setTimeout(() => ac.abort(), timeoutMs);
  try {
    return await env.fetch(url, { ...init, signal: ac.signal });
  } catch (e) {
    if (e && e.name === 'AbortError') throw new Error(`таймаут ${timeoutMs} мс: ${url}`);
    throw e;
  } finally {
    clearTimeout(timer);
  }
}

/** Read a response body as ArrayBuffer, reporting progress(loadedBytes). */
async function readBody(resp, progress) {
  if (resp.body && typeof resp.body.getReader === 'function') {
    const reader = resp.body.getReader();
    const chunks = [];
    let loaded = 0;
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      chunks.push(value);
      loaded += value.byteLength;
      if (progress) progress(loaded);
    }
    const out = new Uint8Array(loaded);
    let off = 0;
    for (const c of chunks) { out.set(c, off); off += c.byteLength; }
    return out.buffer;
  }
  const buf = await resp.arrayBuffer();
  if (progress) progress(buf.byteLength);
  return buf;
}

const decoder = typeof TextDecoder !== 'undefined' ? new TextDecoder('utf-8') : null;
function parseJson(buf, name) {
  try {
    const text = decoder ? decoder.decode(buf) : String(buf);
    const obj = JSON.parse(text);
    if (obj && obj.v !== undefined && obj.v !== 1) log.warn('pack', `${name}: unexpected format version ${obj.v}`);
    return obj;
  } catch (e) {
    throw new Error(`${name}: не JSON (${e.message})`);
  }
}

async function getPointer(env) {
  try { return (await env.kv.get(POINTER_KEY)) || null; } catch (e) { log.warn('pack', 'pointer read failed: ' + e.message); return null; }
}

/** Fetch the network manifest (cache: 'no-store', timeout). */
export async function fetchManifest({ baseUrl, env: e } = {}) {
  const env = defaultEnv(e);
  const u = urls(env, baseUrl);
  const resp = await fetchWithTimeout(env, u.manifest, { cache: 'no-store' }, env.timeoutMs);
  if (!resp.ok) throw new Error(`manifest.json: HTTP ${resp.status}`);
  const m = parseJson(await resp.arrayBuffer(), MANIFEST);
  if (!m || !m.version || !m.files) throw new Error('manifest.json: нет version/files');
  return m;
}

/** {available, version, current, manifest?, error?} — never throws. */
export async function checkForUpdate({ baseUrl, env: e } = {}) {
  const env = defaultEnv(e);
  const ptr = await getPointer(env);
  const current = ptr ? ptr.version : null;
  try {
    const m = await fetchManifest({ baseUrl, env });
    const available = m.version !== current;
    log.info('pack', `update check: network ${m.version}, installed ${current || '-'}`);
    return { available, version: m.version, current, manifest: m };
  } catch (err) {
    log.info('pack', 'update check failed (offline?): ' + err.message);
    return { available: false, version: null, current, error: err.message, offline: true };
  }
}

async function findInOtherCaches(env, entry, exceptName) {
  // reuse an identical file (same sha256) from a previous pack version instead of downloading
  if (!env.caches) return null;
  try {
    const names = await env.caches.keys();
    for (const name of names) {
      if (!name.startsWith(CACHE_PREFIX) || name === exceptName) continue;
      const c = await env.caches.open(name);
      const r = await c.match(entry.url);
      if (r && r.headers && r.headers.get('X-Sha256') === entry.sha256) return r;
    }
  } catch { /* ignore */ }
  return null;
}

/**
 * Download every file of `manifest` (data + optionally tiles) into cache pack-v<version>,
 * verify sha256 and switch the pointer. Files already cached and verified are skipped.
 * Returns the manifest.
 */
export async function downloadPack({ onProgress, baseUrl, appBaseUrl, includeTiles = true, manifest, env: e } = {}) {
  const env = defaultEnv(e);
  const u = urls(env, baseUrl, appBaseUrl);
  const report = (p) => { if (onProgress) try { onProgress(p); } catch (err) { log.warn('pack', 'onProgress threw: ' + err.message); } };
  report({ phase: 'manifest', file: MANIFEST, loaded: 0, total: 0, fraction: 0 });
  const m = manifest || await fetchManifest({ baseUrl, env });
  const cacheName = CACHE_PREFIX + m.version;
  if (!env.caches) throw new Error('Cache Storage недоступен (нужен HTTPS или localhost)');
  const cache = await env.caches.open(cacheName);
  const entries = dataEntries(m, u);
  if (includeTiles) entries.push(...tileEntries(m, env, baseUrl, appBaseUrl));
  const total = entries.reduce((s, x) => s + (x.bytes || 0), 0);
  let doneBytes = 0;
  const inflight = new Map();
  const emit = (file) => {
    let loaded = doneBytes;
    for (const v of inflight.values()) loaded += v;
    report({ phase: 'download', file, loaded, total, fraction: total ? Math.min(1, loaded / total) : 0 });
  };
  const t0 = Date.now();
  let fetched = 0, reused = 0, skipped = 0;

  async function one(entry) {
    const existing = await cache.match(entry.url);
    if (existing && existing.headers && existing.headers.get('X-Sha256') === entry.sha256) {
      skipped++; doneBytes += entry.bytes || 0; emit(entry.key); return;
    }
    const old = await findInOtherCaches(env, entry, cacheName);
    if (old) {
      await cache.put(entry.url, old.clone ? old.clone() : old);
      reused++; doneBytes += entry.bytes || 0; emit(entry.key); return;
    }
    let lastErr = null;
    for (let attempt = 1; attempt <= 2; attempt++) {
      try {
        const resp = await fetchWithTimeout(env, entry.url, { cache: 'no-store' }, 0);
        if (!resp.ok) throw new Error(`${entry.key}: HTTP ${resp.status}`);
        const buf = await readBody(resp, (n) => { inflight.set(entry.key, n); emit(entry.key); });
        inflight.delete(entry.key);
        report({ phase: 'verify', file: entry.key, loaded: doneBytes, total, fraction: total ? doneBytes / total : 0 });
        const h = await sha256Hex(env.subtle, buf);
        if (h !== entry.sha256) throw new Error(`${entry.key}: контрольная сумма не совпала`);
        const type = entry.key.endsWith('.json') ? 'application/json' : entry.key.endsWith('.pbf') ? 'application/x-protobuf'
          : entry.key.endsWith('.png') ? 'image/png' : 'application/octet-stream';
        await cache.put(entry.url, new Response(buf, { headers: { 'Content-Type': type, 'X-Sha256': h, 'Content-Length': String(buf.byteLength) } }));
        fetched++; doneBytes += entry.bytes || buf.byteLength; emit(entry.key);
        return;
      } catch (err) {
        inflight.delete(entry.key);
        lastErr = err;
        log.warn('pack', `download ${entry.key} attempt ${attempt} failed: ${err.message}`);
      }
    }
    throw lastErr;
  }

  // limited concurrency
  const queue = entries.slice();
  const workers = Array.from({ length: Math.min(CONCURRENCY, queue.length || 1) }, async () => {
    while (queue.length) await one(queue.shift());
  });
  await Promise.all(workers);
  // manifest last: its presence in the cache marks the version complete
  await cache.put(u.manifest, new Response(JSON.stringify(m), { headers: { 'Content-Type': 'application/json' } }));
  const prev = await getPointer(env);
  const tilesComplete = includeTiles || !!(prev && prev.version === m.version && prev.tiles);
  await env.kv.set(POINTER_KEY, { version: m.version, manifest: m, installedAt: Date.now(), tiles: tilesComplete });
  log.info('pack', `installed ${m.version}: ${fetched} downloaded, ${reused} reused, ${skipped} already cached in ${Date.now() - t0} ms`);
  // drop old versions only after the pointer switched
  try {
    for (const name of await env.caches.keys()) {
      if (name.startsWith(CACHE_PREFIX) && name !== cacheName) { await env.caches.delete(name); log.info('pack', 'deleted old cache ' + name); }
    }
  } catch (err) { log.warn('pack', 'old cache cleanup failed: ' + err.message); }
  report({ phase: 'done', file: null, loaded: total, total, fraction: 1 });
  return m;
}

async function readCached(env, cacheName, url) {
  const c = await env.caches.open(cacheName);
  const r = await c.match(url);
  if (!r) return null;
  return r.arrayBuffer();
}

/**
 * Load the installed pack (offline) or install it first (online, data files only).
 * Resolves with {manifest, core, eponyms, geom, houses, areas, ready, version, source}.
 */
export async function loadPack({ onProgress, baseUrl, appBaseUrl, env: e } = {}) {
  const env = defaultEnv(e);
  const u = urls(env, baseUrl, appBaseUrl);
  const t0 = Date.now();
  const report = (p) => { if (onProgress) try { onProgress(p); } catch { /* ignore */ } };
  let ptr = await getPointer(env);
  let source = 'cache';
  let haveCache = false;
  if (ptr && env.caches) {
    try { haveCache = await env.caches.has(CACHE_PREFIX + ptr.version); } catch { haveCache = false; }
  }
  if (!haveCache) {
    if (!env.caches) {
      // no Cache Storage (http dev server, old browser): plain network load
      log.warn('pack', 'Cache Storage unavailable: loading the pack from network without caching');
      return networkOnlyLoad(env, u, report, t0);
    }
    log.info('pack', ptr ? `cache for ${ptr.version} missing, reinstalling` : 'no pack installed, downloading');
    const m = await downloadPack({ onProgress, baseUrl, appBaseUrl, includeTiles: false, env: e });
    ptr = await getPointer(env);
    if (!ptr || ptr.version !== m.version) ptr = { version: m.version, manifest: m };
    source = 'network';
  }
  const manifest = ptr.manifest || parseJson(await readCached(env, CACHE_PREFIX + ptr.version, u.manifest), MANIFEST);
  const cacheName = CACHE_PREFIX + manifest.version;
  const pack = { manifest, version: manifest.version, source, core: null, eponyms: null, geom: null, houses: null, areas: null, ready: null };

  async function loadFile(name, required) {
    const url = new URL(name, u.data).href;
    let buf = await readCached(env, cacheName, url);
    if (!buf) {
      if (!manifest.files[name]) { if (required) throw new Error(`${name} нет в манифесте`); return null; }
      log.warn('pack', `${name} missing in ${cacheName}, refetching`);
      await downloadPack({ baseUrl, appBaseUrl, includeTiles: false, manifest, env: e });
      buf = await readCached(env, cacheName, url);
      if (!buf) throw new Error(`${name}: не удалось загрузить`);
    }
    report({ phase: 'parse', file: name, loaded: 0, total: 0, fraction: 1 });
    return parseJson(buf, name);
  }

  [pack.core, pack.eponyms] = await Promise.all(CORE_FILES.map((f) => loadFile(f, f === 'core.json')));
  if (!pack.core || !Array.isArray(pack.core.f)) throw new Error('core.json: нет списка объектов');
  if (!pack.eponyms) pack.eponyms = { v: 1, taxonomy: [], kinds: {}, e: [] };
  log.info('pack', `core loaded: ${manifest.version}, ${pack.core.f.length} features (${source}) in ${Date.now() - t0} ms`);
  pack.ready = (async () => {
    const t1 = Date.now();
    const names = { 'geom.json': 'geom', 'houses.json': 'houses', 'areas.json': 'areas' };
    await Promise.all(LATE_FILES.map(async (f) => {
      try { pack[names[f]] = await loadFile(f, false); } catch (err) { log.error('pack', `${f}: ${err.message}`); }
    }));
    log.info('pack', `late files loaded in ${Date.now() - t1} ms`);
    report({ phase: 'done', file: null, loaded: 0, total: 0, fraction: 1 });
    return pack;
  })();
  return pack;
}

async function networkOnlyLoad(env, u, report, t0) {
  const manifest = await fetchManifest({ baseUrl: u.data.href, env });
  const get = async (name) => {
    const meta = manifest.files[name];
    if (!meta) return null;
    const resp = await fetchWithTimeout(env, new URL(name, u.data).href, {}, 0);
    if (!resp.ok) throw new Error(`${name}: HTTP ${resp.status}`);
    const buf = await readBody(resp, (n) => report({ phase: 'download', file: name, loaded: n, total: meta.bytes, fraction: meta.bytes ? n / meta.bytes : 0 }));
    if (env.subtle) {
      const h = await sha256Hex(env.subtle, buf);
      if (h !== meta.sha256) throw new Error(`${name}: контрольная сумма не совпала`);
    }
    return parseJson(buf, name);
  };
  const pack = { manifest, version: manifest.version, source: 'network', core: null, eponyms: null, geom: null, houses: null, areas: null, ready: null };
  [pack.core, pack.eponyms] = await Promise.all(CORE_FILES.map(get));
  if (!pack.eponyms) pack.eponyms = { v: 1, taxonomy: [], kinds: {}, e: [] };
  log.info('pack', `core loaded from network (no cache) in ${Date.now() - t0} ms`);
  pack.ready = (async () => {
    const names = { 'geom.json': 'geom', 'houses.json': 'houses', 'areas.json': 'areas' };
    await Promise.all(LATE_FILES.map(async (f) => { try { pack[names[f]] = await get(f); } catch (err) { log.error('pack', `${f}: ${err.message}`); } }));
    return pack;
  })();
  return pack;
}

/** Readiness info: {version, installedAt, files:{name:{cached, bytes}}, tiles:{key:{cached, bytes}}, ...}. */
export async function packStatus({ baseUrl, appBaseUrl, env: e } = {}) {
  const env = defaultEnv(e);
  const u = urls(env, baseUrl, appBaseUrl);
  const ptr = await getPointer(env);
  const res = { version: ptr ? ptr.version : null, installedAt: ptr ? ptr.installedAt : null, files: {}, tiles: {},
    dataComplete: false, tilesComplete: false, cacheAvailable: !!env.caches };
  if (!ptr || !env.caches) return res;
  let cache = null;
  try { if (await env.caches.has(CACHE_PREFIX + ptr.version)) cache = await env.caches.open(CACHE_PREFIX + ptr.version); } catch { cache = null; }
  const check = async (entry) => !!(cache && (await cache.match(entry.url)));
  let allData = true, allTiles = true;
  for (const entry of dataEntries(ptr.manifest, u)) {
    const cached = await check(entry);
    res.files[entry.key] = { cached, bytes: entry.bytes };
    allData = allData && cached;
  }
  const tiles = tileEntries(ptr.manifest, env, baseUrl, appBaseUrl);
  for (const entry of tiles) {
    const cached = await check(entry);
    res.tiles[entry.key] = { cached, bytes: entry.bytes };
    allTiles = allTiles && cached;
  }
  res.dataComplete = allData;
  res.tilesComplete = tiles.length > 0 && allTiles;
  return res;
}

/** Remove the installed pack (pointer + caches). */
export async function removePack({ env: e } = {}) {
  const env = defaultEnv(e);
  await env.kv.set(POINTER_KEY, null);
  if (env.caches) for (const name of await env.caches.keys()) if (name.startsWith(CACHE_PREFIX)) await env.caches.delete(name);
  log.info('pack', 'pack removed');
}
