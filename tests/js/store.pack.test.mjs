// store/pack.js with mocked fetch / Cache Storage / kv (crypto.subtle is Node's real one).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { loadPack, downloadPack, checkForUpdate, packStatus, removePack, tileEntries, POINTER_KEY } from '../../app/src/store/pack.js';
import { readMiniBytes, readMini } from '../fixtures/pack_mini/_src/load.mjs';
import { memoryKV } from '../../app/src/store/db.js';

const BASE = 'https://app.test/quchebi/';
const DATA = BASE + 'data/';
const sha = (buf) => createHash('sha256').update(buf).digest('hex');

class FakeCache {
  constructor() { this.m = new Map(); }
  async match(url) {
    const e = this.m.get(String(url));
    return e ? new Response(e.body.slice(0), { headers: e.headers }) : undefined;
  }
  async put(url, resp) {
    const body = await resp.arrayBuffer();
    this.m.set(String(url), { body, headers: new Headers(resp.headers) });
  }
  async delete(url) { return this.m.delete(String(url)); }
}
class FakeCaches {
  constructor() { this.m = new Map(); }
  async open(name) { if (!this.m.has(name)) this.m.set(name, new FakeCache()); return this.m.get(name); }
  async has(name) { return this.m.has(name); }
  async keys() { return [...this.m.keys()]; }
  async delete(name) { return this.m.delete(name); }
  async match(url) { for (const c of this.m.values()) { const r = await c.match(url); if (r) return r; } return undefined; }
}

/** A tiny HTTP server mock: files {absUrl: Buffer}. */
function makeServer(files) {
  const s = { files, calls: [], offline: false, corrupt: new Set(), hang: false };
  s.fetch = async (url, init = {}) => {
    url = String(url);
    s.calls.push({ url, cache: init.cache });
    if (s.hang) {
      return new Promise((_, rej) => {
        if (init.signal) init.signal.addEventListener('abort', () => { const e = new Error('aborted'); e.name = 'AbortError'; rej(e); });
      });
    }
    if (s.offline) throw new TypeError('Failed to fetch');
    const body = s.files[url];
    if (!body) return new Response('not found', { status: 404 });
    let buf = Buffer.from(body);
    if (s.corrupt.has(url)) { buf = Buffer.from(buf); buf[10] ^= 0xff; }
    return new Response(buf, { status: 200 });
  };
  s.count = (suffix) => s.calls.filter((c) => c.url.endsWith(suffix)).length;
  return s;
}

const DATA_FILES = ['core.json', 'eponyms.json', 'geom.json', 'houses.json', 'areas.json'];
function miniFiles(manifestPatch = {}) {
  const files = {};
  for (const f of DATA_FILES) files[DATA + f] = readMiniBytes(f);
  const manifest = { ...readMini('manifest.json'), ...manifestPatch };
  files[DATA + 'manifest.json'] = Buffer.from(JSON.stringify(manifest));
  return { files, manifest };
}
function env(server, extra = {}) {
  return { fetch: server.fetch, caches: new FakeCaches(), subtle: globalThis.crypto.subtle, kv: memoryKV(), baseURI: BASE, ...extra };
}

test('first run downloads and verifies the data, later runs work offline', async () => {
  const { files } = miniFiles();
  const server = makeServer(files);
  const e = env(server);
  const progress = [];
  const pack = await loadPack({ onProgress: (p) => progress.push(p), env: e });
  assert.equal(pack.source, 'network');
  assert.equal(pack.version, '20261024-mini');
  assert.equal(pack.core.f.length, 26);
  assert.equal(pack.eponyms.e.length, 16);
  await pack.ready;
  assert.equal(pack.geom.g.length, 26);
  assert.ok(pack.houses.h['0'].length === 11);
  assert.equal(pack.areas.districts.length, 3);
  assert.equal(server.calls.find((c) => c.url.endsWith('manifest.json')).cache, 'no-store');
  for (const f of DATA_FILES) assert.equal(server.count(f), 1, f);
  assert.ok(progress.some((p) => p.phase === 'download'));
  const done = progress.filter((p) => p.phase === 'done');
  assert.ok(done.length >= 1 && done[0].fraction === 1);
  const ptr = await e.kv.get(POINTER_KEY);
  assert.equal(ptr.version, '20261024-mini');
  assert.deepEqual(await e.caches.keys(), ['pack-v20261024-mini']);
  // offline second start: everything from Cache Storage
  server.offline = true;
  const before = server.calls.length;
  const p2 = await loadPack({ env: e });
  assert.equal(p2.source, 'cache');
  await p2.ready;
  assert.equal(p2.geom.g.length, 26);
  assert.equal(server.calls.length, before, 'no network calls offline');
  const st = await packStatus({ env: e });
  assert.equal(st.version, '20261024-mini');
  assert.equal(st.dataComplete, true);
  assert.equal(st.files['core.json'].cached, true);
});

test('sha256 mismatch aborts the install and keeps the old pointer', async () => {
  const { files } = miniFiles();
  const server = makeServer(files);
  const e = env(server);
  await downloadPack({ env: e, includeTiles: false });
  const v2 = miniFiles({ version: '20261025-1' });
  server.files = v2.files;
  // a different houses.json in the new version, corrupted in transit
  const newHouses = Buffer.from(JSON.stringify({ v: 1, h: {}, place: {} }));
  server.files[DATA + 'houses.json'] = newHouses;
  const m2 = { ...v2.manifest, files: { ...v2.manifest.files, 'houses.json': { bytes: newHouses.length, sha256: sha(newHouses) } } };
  server.files[DATA + 'manifest.json'] = Buffer.from(JSON.stringify(m2));
  server.corrupt.add(DATA + 'houses.json');
  await assert.rejects(() => downloadPack({ env: e, includeTiles: false }), /контрольная сумма/);
  assert.equal(server.count('houses.json'), 1 + 2, 'one retry');
  assert.equal((await e.kv.get(POINTER_KEY)).version, '20261024-mini', 'pointer not switched');
  server.offline = true;
  const p = await loadPack({ env: e });
  assert.equal(p.version, '20261024-mini');
});

test('update: checkForUpdate, files reused by sha256, atomic switch, old cache removed', async () => {
  const { files } = miniFiles();
  const server = makeServer(files);
  const e = env(server);
  await loadPack({ env: e });
  assert.equal((await checkForUpdate({ env: e })).available, false);
  const v2 = miniFiles({ version: '20261025-1' });
  server.files = v2.files;
  const upd = await checkForUpdate({ env: e });
  assert.deepEqual([upd.available, upd.version, upd.current], [true, '20261025-1', '20261024-mini']);
  const before = server.count('core.json');
  await downloadPack({ env: e, includeTiles: false });
  assert.equal(server.count('core.json'), before, 'identical file copied from the old cache');
  assert.deepEqual(await e.caches.keys(), ['pack-v20261025-1']);
  assert.equal((await e.kv.get(POINTER_KEY)).version, '20261025-1');
  server.offline = true;
  const p = await loadPack({ env: e });
  assert.equal(p.version, '20261025-1');
  const off = await checkForUpdate({ env: e });
  assert.equal(off.available, false);
  assert.equal(off.offline, true);
});

test('checkForUpdate gives up after the timeout', async () => {
  const server = makeServer({});
  server.hang = true;
  const t0 = Date.now();
  const r = await checkForUpdate({ env: env(server, { timeoutMs: 60 }) });
  assert.equal(r.available, false);
  assert.match(r.error, /таймаут/);
  assert.ok(Date.now() - t0 < 1500);
});

test('tiles: manifest.tiles.files keys relative to the app root; legacy flat map', async () => {
  const tileBuf = Buffer.from('PMTiles-fake-'.repeat(1000));
  const glyph = Buffer.from('glyph');
  const tiles = { basemap: 'tiles/tbilisi.pmtiles', glyphs: 'tiles/fonts/{fontstack}/{range}.pbf', bytes: tileBuf.length + glyph.length,
    files: { 'tiles/tbilisi.pmtiles': { bytes: tileBuf.length, sha256: sha(tileBuf) },
      'tiles/fonts/Noto Sans Regular/0-255.pbf': { bytes: glyph.length, sha256: sha(glyph) } } };
  const { files } = miniFiles({ tiles });
  files[BASE + 'tiles/tbilisi.pmtiles'] = tileBuf;
  files[new URL('tiles/fonts/Noto Sans Regular/0-255.pbf', BASE).href] = glyph;
  const server = makeServer(files);
  const e = env(server);
  const pack = await loadPack({ env: e });
  assert.equal(server.count('tbilisi.pmtiles'), 0, 'loadPack does not download tiles');
  let st = await packStatus({ env: e });
  assert.equal(st.tilesComplete, false);
  const progress = [];
  await downloadPack({ env: e, onProgress: (p) => progress.push(p) });
  assert.equal(server.count('tbilisi.pmtiles'), 1);
  assert.equal(server.count('core.json'), 1, 'data already cached is not downloaded again');
  st = await packStatus({ env: e });
  assert.equal(st.tilesComplete, true);
  assert.equal(st.tiles['tiles/tbilisi.pmtiles'].cached, true);
  const r = await e.caches.match(BASE + 'tiles/tbilisi.pmtiles');
  assert.equal(Buffer.from(await r.arrayBuffer()).length, tileBuf.length);
  const last = progress[progress.length - 1];
  assert.equal(last.phase, 'done');
  assert.equal(pack.version, '20261024-mini');
  // legacy flat shape {"tbilisi.pmtiles": {...}} resolves under tiles/
  const legacy = tileEntries({ tiles: { 'tbilisi.pmtiles': { bytes: 1, sha256: 'x' } } }, { baseURI: BASE });
  assert.equal(legacy[0].url, BASE + 'tiles/tbilisi.pmtiles');
});

test('evicted file is refetched; no Cache Storage -> network only; removePack', async () => {
  const { files } = miniFiles();
  const server = makeServer(files);
  const e = env(server);
  await loadPack({ env: e });
  const cache = await e.caches.open('pack-v20261024-mini');
  await cache.delete(DATA + 'geom.json');
  const p = await loadPack({ env: e });
  await p.ready;
  assert.equal(p.geom.g.length, 26);
  assert.equal(server.count('geom.json'), 2);
  // no Cache Storage at all (http dev server)
  const server2 = makeServer(miniFiles().files);
  const p2 = await loadPack({ env: env(server2, { caches: null }) });
  assert.equal(p2.source, 'network');
  await p2.ready;
  assert.equal(p2.areas.places.length, 3);
  // remove
  await removePack({ env: e });
  assert.deepEqual(await e.caches.keys(), []);
  assert.equal(await e.kv.get(POINTER_KEY), null);
});

test('first run offline fails with a clear error', async () => {
  const server = makeServer(miniFiles().files);
  server.offline = true;
  await assert.rejects(() => loadPack({ env: env(server) }), /Failed to fetch/);
});
