// Offline basemap: PMTiles from Cache Storage (caches.match -> blob -> File -> FileSource),
// network PMTiles as a second choice, and a plain background (our own streets on top) as fallback.
import maplibregl from 'maplibre-gl';
import { Protocol, PMTiles, FileSource } from 'pmtiles';
import { layers, namedFlavor } from '@protomaps/basemaps';
import { log } from '../log/log.js';

export const TILES_FILE = 'tbilisi.pmtiles';
const FONT_PROBE = 'Noto Sans Regular';

let protocol = null;
let cachedResolve = null;

export function ensureProtocol() {
  if (!protocol) {
    protocol = new Protocol();
    maplibregl.addProtocol('pmtiles', protocol.tile);
    log.info('map', 'pmtiles protocol registered');
  }
  return protocol;
}

export function baseHref() {
  return new URL('./', document.baseURI).href;
}

export function tilesUrl() {
  return new URL(`tiles/${TILES_FILE}`, document.baseURI).href;
}

function glyphsTemplate() {
  return `${baseHref()}tiles/fonts/{fontstack}/{range}.pbf`;
}

function spriteUrl(flavor) {
  return `${baseHref()}tiles/sprites/${flavor}`;
}

async function urlAvailable(url, timeoutMs = 3000) {
  try {
    if ('caches' in window) {
      const hit = await caches.match(url, { ignoreSearch: true });
      if (hit && hit.ok && !(hit.headers.get('content-type') || '').includes('text/html')) return true;
    }
  } catch {
    /* Cache Storage may be unavailable (private mode) */
  }
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), timeoutMs);
  try {
    const r = await fetch(url, { signal: ctl.signal });
    // SPA fallbacks answer 200 with index.html for missing files.
    const type = r.headers.get('content-type') || '';
    return r.ok && !type.includes('text/html');
  } catch {
    return false;
  } finally {
    clearTimeout(timer);
  }
}

/**
 * Find the basemap. Returns {mode, url, header, glyphsOk, spriteOk, bytes}.
 * mode: 'pmtiles-cache' | 'pmtiles-net' | 'fallback'.
 */
export async function resolveBasemap({ force = false } = {}) {
  if (cachedResolve && !force) return cachedResolve;
  ensureProtocol();
  const t0 = performance.now();
  let result = { mode: 'fallback', url: null, header: null, bytes: 0 };

  // 1. Cache Storage (put there by store/pack.js downloadPack()).
  try {
    if ('caches' in window) {
      const resp = await caches.match(tilesUrl(), { ignoreSearch: true });
      if (resp && resp.ok) {
        const blob = await resp.blob();
        const file = new File([blob], TILES_FILE);
        const p = new PMTiles(new FileSource(file));
        const header = await p.getHeader();
        protocol.add(p);
        result = { mode: 'pmtiles-cache', url: `pmtiles://${TILES_FILE}`, header, bytes: blob.size };
      }
    }
  } catch (err) {
    log.warn('map', 'cached pmtiles unusable', { err: String(err) });
  }

  // 2. Network (dev server, or online before the pack was downloaded).
  if (result.mode === 'fallback') {
    try {
      const url = tilesUrl();
      const p = new PMTiles(url);
      const header = await Promise.race([
        p.getHeader(),
        new Promise((_, rej) => setTimeout(() => rej(new Error('timeout')), 4000)),
      ]);
      protocol.add(p);
      result = { mode: 'pmtiles-net', url: `pmtiles://${url}`, header, bytes: 0 };
    } catch (err) {
      log.info('map', 'no network pmtiles, using fallback basemap', { err: String(err) });
    }
  }

  const [glyphsOk, spriteOk] = await Promise.all([
    urlAvailable(`${baseHref()}tiles/fonts/${encodeURIComponent(FONT_PROBE)}/0-255.pbf`),
    urlAvailable(`${spriteUrl('light')}.json`),
  ]);
  result.glyphsOk = glyphsOk;
  result.spriteOk = spriteOk;
  log.info('map', 'basemap resolved', {
    mode: result.mode, glyphsOk, spriteOk, bytes: result.bytes, ms: Math.round(performance.now() - t0),
  });
  cachedResolve = result;
  return result;
}

export function resetBasemapCache() {
  cachedResolve = null;
}

export const BG = { light: '#eef1f5', dark: '#0e131b' };

/** Build the MapLibre style for a theme ('light'|'dark') and a resolved basemap. */
export function buildStyle(theme, bm) {
  const flavor = theme === 'dark' ? 'dark' : 'light';
  const style = {
    version: 8,
    name: `quchebi-${flavor}`,
    sources: {},
    layers: [],
  };
  if (bm.glyphsOk) style.glyphs = glyphsTemplate();
  if (bm.spriteOk) style.sprite = spriteUrl(flavor);
  if (bm.url) {
    style.sources.protomaps = {
      type: 'vector',
      url: bm.url,
      attribution: '<a href="https://protomaps.com">Protomaps</a> © <a href="https://openstreetmap.org/copyright">OpenStreetMap</a>',
    };
    let ls = [];
    try {
      ls = layers('protomaps', namedFlavor(flavor), { lang: 'ru' });
    } catch (err) {
      log.error('map', 'protomaps layers() failed', { err: String(err) });
    }
    // Never uppercase labels: Georgian mkhedruli would turn into mtavruli.
    for (const l of ls) if (l.layout && 'text-transform' in l.layout) delete l.layout['text-transform'];
    if (!bm.glyphsOk) ls = ls.filter((l) => l.type !== 'symbol');
    else if (!bm.spriteOk) ls = ls.filter((l) => !(l.layout && 'icon-image' in l.layout));
    style.layers = ls;
  } else {
    style.layers = [{ id: 'background', type: 'background', paint: { 'background-color': BG[flavor] } }];
  }
  return style;
}
