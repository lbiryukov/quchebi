// Basemap assets (glyph PBFs and sprites) listed in tiles/tiles-manifest.json: prefetch them into
// Cache Storage so labels render offline. The PMTiles archive itself is handled by store/pack.js.
import { log } from '../log/log.js';

export const ASSET_CACHE = 'basemap-assets-v1';

function abs(path) {
  return new URL(`tiles/${path}`, document.baseURI).href;
}

async function sha256Hex(buf) {
  const d = await crypto.subtle.digest('SHA-256', buf);
  return [...new Uint8Array(d)].map((b) => b.toString(16).padStart(2, '0')).join('');
}

export async function tilesManifest() {
  const url = abs('tiles-manifest.json');
  try {
    const r = await fetch(url, { cache: 'no-store' });
    const type = r.headers.get('content-type') || '';
    if (r.ok && !type.includes('text/html')) {
      const j = await r.json();
      try {
        const c = await caches.open(ASSET_CACHE);
        await c.put(url, new Response(JSON.stringify(j), { headers: { 'content-type': 'application/json' } }));
      } catch { /* ignore */ }
      return j;
    }
  } catch {
    /* offline */
  }
  try {
    const hit = await caches.match(url);
    if (hit) return await hit.json();
  } catch {
    /* ignore */
  }
  return null;
}

function assetEntries(man, { withTiles = false } = {}) {
  return Object.entries(man?.files || {})
    .filter(([p]) => p.startsWith('fonts/') || p.startsWith('sprites/') || (withTiles && p.endsWith('.pmtiles')));
}

function basemapKey(man) {
  return man?.basemap || Object.keys(man?.files || {}).find((p) => p.endsWith('.pmtiles')) || null;
}

async function readWithProgress(resp, onBytes) {
  if (!resp.body || typeof resp.body.getReader !== 'function') return resp.arrayBuffer();
  const reader = resp.body.getReader();
  const chunks = [];
  let loaded = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    chunks.push(value);
    loaded += value.byteLength;
    onBytes?.(loaded);
  }
  const out = new Uint8Array(loaded);
  let off = 0;
  for (const c of chunks) { out.set(c, off); off += c.byteLength; }
  return out.buffer;
}

/** {total, cached, bytes, cachedBytes} for fonts/sprites. */
export async function assetStatus() {
  const man = await tilesManifest();
  if (!man) return null;
  const list = assetEntries(man);
  let cached = 0;
  let bytes = 0;
  let cachedBytes = 0;
  for (const [p, meta] of list) {
    bytes += meta.bytes || 0;
    try {
      if (await caches.match(abs(p))) { cached++; cachedBytes += meta.bytes || 0; }
    } catch { /* ignore */ }
  }
  const key = basemapKey(man);
  let pmtiles = null;
  if (key) {
    let hit = false;
    try { hit = Boolean(await caches.match(abs(key))); } catch { /* ignore */ }
    pmtiles = { key, bytes: man.files[key]?.bytes || 0, cached: hit };
  }
  return { total: list.length, cached, bytes, cachedBytes, built: man.built, pmtiles };
}

/**
 * Download missing fonts/sprites (and, with withTiles, the PMTiles archive when the data pack
 * does not ship it) with sha256 check. onProgress({done, total, file, loaded, bytes}).
 */
export async function prefetchAssets({ onProgress, withTiles = false } = {}) {
  const man = await tilesManifest();
  if (!man) throw new Error('нет tiles-manifest.json');
  const list = assetEntries(man, { withTiles });
  const cache = await caches.open(ASSET_CACHE);
  let done = 0;
  let fetched = 0;
  let failed = 0;
  const queue = [...list];
  const worker = async () => {
    while (queue.length) {
      const [p, meta] = queue.shift();
      const url = abs(p);
      try {
        if (!(await caches.match(url))) {
          const r = await fetch(url, { cache: 'no-store' });
          if (!r.ok) throw new Error(`HTTP ${r.status}`);
          const buf = await readWithProgress(r, (loaded) => onProgress?.({ done, total: list.length, file: p, loaded, bytes: meta.bytes }));
          if (meta.sha256 && crypto?.subtle) {
            const got = await sha256Hex(buf);
            if (got !== meta.sha256) throw new Error('sha256 mismatch');
          }
          const type = p.endsWith('.json') ? 'application/json' : p.endsWith('.png') ? 'image/png'
            : p.endsWith('.pmtiles') ? 'application/octet-stream' : 'application/x-protobuf';
          await cache.put(url, new Response(buf, { headers: { 'content-type': type } }));
          fetched++;
        }
      } catch (err) {
        failed++;
        log.warn('assets', 'basemap asset failed', { p, err: String(err) });
      }
      done++;
      onProgress?.({ done, total: list.length });
    }
  };
  await Promise.all([worker(), worker(), worker(), worker()]);
  log.info('assets', 'basemap assets prefetched', { total: list.length, fetched, failed });
  return { total: list.length, fetched, failed };
}
