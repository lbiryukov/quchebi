/* Quchebi service worker (vite-plugin-pwa injectManifest).
 * - Precaches the app shell (Workbox); a new version waits until the user confirms
 *   (message SKIP_WAITING), so it never switches mid-game.
 * - Serves the data pack (data/*) and the basemap (tiles/*) from Cache Storage, where
 *   store/pack.js puts them (cache "pack-v<version>"); falls back to the network.
 * - Basemap fonts/sprites are cache-first with a runtime cache.
 * - PMTiles Range requests are answered from the cached full file (206). */
import { precacheAndRoute, cleanupOutdatedCaches, createHandlerBoundToURL } from 'workbox-precaching';
import { registerRoute, NavigationRoute } from 'workbox-routing';
import { createPartialResponse } from 'workbox-range-requests';

const ASSET_CACHE = 'basemap-assets-v1';

self.addEventListener('message', (event) => {
  if (event.data && event.data.type === 'SKIP_WAITING') self.skipWaiting();
});

precacheAndRoute(self.__WB_MANIFEST);
cleanupOutdatedCaches();

// SPA navigation -> precached index.html (offline start from the Home Screen icon).
try {
  registerRoute(new NavigationRoute(createHandlerBoundToURL('index.html')));
} catch (err) {
  // index.html missing from the precache manifest (dev); navigation goes to the network.
}

const scopeUrl = new URL(self.registration.scope);

function under(url, dir) {
  return url.origin === scopeUrl.origin && url.pathname.startsWith(scopeUrl.pathname + dir);
}

async function fromCaches(request) {
  const url = new URL(request.url);
  url.search = '';
  return caches.match(url.href, { ignoreSearch: true, ignoreVary: true });
}

async function rangeAware(request, response) {
  if (request.headers.has('range') && response.status === 200) {
    try {
      return await createPartialResponse(request, response);
    } catch (err) {
      return response;
    }
  }
  return response;
}

/** Data pack and PMTiles: cache-first unless the page explicitly bypasses caches. */
async function packHandler({ request }) {
  const bypass = request.cache === 'no-store' || request.cache === 'reload' || request.cache === 'no-cache';
  if (!bypass) {
    const hit = await fromCaches(request);
    if (hit) return rangeAware(request, hit);
  }
  try {
    return await fetch(request);
  } catch (err) {
    const hit = await fromCaches(request);
    if (hit) return rangeAware(request, hit);
    return new Response('offline', { status: 503, statusText: 'offline' });
  }
}

/** Glyphs and sprites: cache-first, store on first fetch. */
async function assetHandler({ request }) {
  const hit = await fromCaches(request);
  if (hit) return hit;
  try {
    const resp = await fetch(request);
    const type = resp.headers.get('content-type') || '';
    if (resp.ok && !type.includes('text/html')) {
      const cache = await caches.open(ASSET_CACHE);
      await cache.put(request.url.split('?')[0], resp.clone());
    }
    return resp;
  } catch (err) {
    return new Response('', { status: 504, statusText: 'offline' });
  }
}

registerRoute(({ url, request }) => request.method === 'GET'
  && (under(url, 'tiles/fonts/') || under(url, 'tiles/sprites/')), assetHandler);

registerRoute(({ url, request }) => request.method === 'GET'
  && (under(url, 'data/') || under(url, 'tiles/')), packHandler);

self.addEventListener('activate', (event) => {
  event.waitUntil(self.clients.claim());
});
