// store/cities.js: registry, per-city storage names (Tbilisi keeps the legacy ones), the chosen
// city (URL parameter / localStorage mirror), per-city db records and pack caches that never
// touch another city's caches.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import {
  CITIES, getCity, cityKey, cityLsKey, pointerKey, cachePrefix, cacheOwner, ownsCache, initialCity, rememberCity,
  forgetCity, setCurrentCity, currentCityId, cityFromUrl, packLang, tilesInfo, CITY_LS_KEY,
} from '../../app/src/store/cities.js';
import { createDb, memoryKV, ZONE_LS_KEY, zoneLsKey } from '../../app/src/store/db.js';
import { loadPack, downloadPack, removePack, packStatus, cityPackInfo, POINTER_KEY, CACHE_PREFIX } from '../../app/src/store/pack.js';

function fakeStorage() {
  const m = new Map();
  return { map: m, getItem: (k) => (m.has(k) ? m.get(k) : null), setItem: (k, v) => { m.set(k, String(v)); }, removeItem: (k) => { m.delete(k); } };
}
const POLY = [[55.73, 37.58], [55.74, 37.58], [55.74, 37.59]];

test('registry: two cities with data dirs and languages', () => {
  assert.deepEqual(CITIES.map((c) => c.id), ['tbilisi', 'khamovniki']);
  assert.equal(getCity('tbilisi').dataDir, './data/');
  assert.equal(getCity('tbilisi').lang, 'ka');
  assert.equal(getCity('khamovniki').title, 'Москва. Хамовники');
  assert.equal(getCity('khamovniki').dataDir, './data/khamovniki/');
  assert.equal(getCity('khamovniki').lang, 'ru');
  assert.equal(getCity('moscow'), null);
  assert.equal(getCity(null), null);
});

test('Tbilisi keeps the legacy storage names, other cities get suffixed ones', () => {
  assert.equal(pointerKey('tbilisi'), 'pack:current');
  assert.equal(pointerKey('tbilisi'), POINTER_KEY);
  assert.equal(cachePrefix('tbilisi'), 'pack-v');
  assert.equal(cachePrefix('tbilisi'), CACHE_PREFIX);
  assert.equal(cityKey('zone', 'tbilisi'), 'zone');
  assert.equal(cityLsKey('quchebi.zone', 'tbilisi'), 'quchebi.zone');
  assert.equal(zoneLsKey('tbilisi'), ZONE_LS_KEY);
  assert.equal(pointerKey('khamovniki'), 'pack:current:khamovniki');
  assert.equal(cachePrefix('khamovniki'), 'pack-khamovniki-v');
  assert.equal(cityKey('history', 'khamovniki'), 'history:khamovniki');
  assert.equal(zoneLsKey('khamovniki'), 'quchebi.zone.khamovniki');
});

test('cache ownership: Tbilisi cleanup never matches another city', () => {
  assert.equal(cacheOwner('pack-v20261024-3'), 'tbilisi');
  assert.equal(cacheOwner('pack-khamovniki-v20261110-1'), 'khamovniki');
  assert.equal(cacheOwner('pack-vake-v1'), null, 'an unknown city that starts with "v" is not Tbilisi');
  assert.equal(cacheOwner('workbox-precache-v2'), null);
  assert.equal(cacheOwner('basemap-assets-v1'), null);
  assert.ok(ownsCache('pack-v20261024-3', 'tbilisi'));
  assert.ok(!ownsCache('pack-khamovniki-v20261110-1', 'tbilisi'));
  assert.ok(!ownsCache('pack-v20261024-3', 'khamovniki'));
});

test('chosen city: URL parameter first, then the localStorage mirror', async () => {
  const storage = fakeStorage();
  assert.equal(initialCity({ search: '', storage }), null);
  assert.equal(cityFromUrl('?city=khamovniki'), 'khamovniki');
  assert.equal(cityFromUrl('?city=nowhere'), null);
  assert.deepEqual(initialCity({ search: '?city=khamovniki', storage }), { id: 'khamovniki', from: 'url' });
  const db = createDb({ kv: memoryKV(), storage: null });
  await rememberCity('tbilisi', { storage, db });
  assert.equal(storage.getItem(CITY_LS_KEY), 'tbilisi');
  assert.equal(await db.getSetting('city'), 'tbilisi');
  assert.deepEqual(initialCity({ search: '', storage }), { id: 'tbilisi', from: 'storage' });
  assert.deepEqual(initialCity({ search: '?city=khamovniki', storage }), { id: 'khamovniki', from: 'url' });
  storage.setItem(CITY_LS_KEY, 'atlantis');
  assert.equal(initialCity({ search: '', storage }), null, 'unknown remembered id is ignored');
  await assert.rejects(() => rememberCity('atlantis', { storage }), /неизвестный район/);
  await forgetCity({ storage, db });
  assert.equal(storage.getItem(CITY_LS_KEY), null);
  assert.equal(await db.getSetting('city', null), null);
});

test('current city defaults the db to its keys; theme and game mode stay global', async () => {
  const storage = fakeStorage();
  const kv = memoryKV();
  const db = createDb({ kv, storage });
  const prev = currentCityId();
  try {
    setCurrentCity('tbilisi');
    await db.setZone({ polygon: POLY, mode: 'draw' });
    await db.addHistory('асатиани');
    await db.setCitySetting('zoneSkipped', true);
    await db.setSetting('theme', 'dark');
    setCurrentCity('khamovniki');
    assert.equal(await db.getZone(), null, 'Moscow has no zone yet');
    assert.deepEqual(await db.getHistory(), []);
    assert.equal(await db.getCitySetting('zoneSkipped', false), false);
    assert.equal(await db.getSetting('theme'), 'dark', 'theme is global');
    await db.setZone({ polygon: POLY, mode: 'points' });
    await db.addHistory('толстого');
    await db.addSaved({ q: 'тип:остановка' });
    await db.addNote({ text: 'нет номера', featureKey: 'x|' });
    assert.ok(kv.map.has('zone:khamovniki'));
    assert.ok(kv.map.has('history:khamovniki'));
    assert.ok(kv.map.has('saved:khamovniki'));
    assert.ok(kv.map.has('notes:khamovniki'));
    assert.ok(storage.map.has('quchebi.zone.khamovniki'));
    setCurrentCity('tbilisi');
    assert.equal((await db.getZone()).mode, 'draw', 'Tbilisi zone untouched (legacy key "zone")');
    assert.deepEqual(await db.getHistory(), ['асатиани']);
    assert.deepEqual(await db.getSaved(), []);
    assert.equal(await db.getCitySetting('zoneSkipped', false), true);
    assert.ok(kv.map.has('zone') && kv.map.has('history'));
    assert.ok(storage.map.has('quchebi.zone'));
    assert.deepEqual((await db.getSettings()), { theme: 'dark', zoneSkipped: true });
  } finally {
    setCurrentCity(prev);
  }
});

test('explicit db city wins over the current city', async () => {
  const kv = memoryKV();
  const a = createDb({ kv, storage: null, city: 'khamovniki' });
  await a.addHistory('q');
  assert.deepEqual(await kv.get('history:khamovniki'), ['q']);
  assert.equal(await kv.get('history'), undefined);
});

test('pack language and basemap info from the manifest', () => {
  assert.equal(packLang({ manifest: {} }), 'ka');
  assert.equal(packLang({ manifest: {}, lang: 'ru' }), 'ru');
  assert.equal(packLang({ manifest: { city: { id: 'khamovniki', lang: 'ru' } } }), 'ru');
  const t = tilesInfo({ tiles: { basemap: 'tiles/khamovniki.pmtiles', bbox: [37.55, 55.72, 37.61, 55.75], center: [37.58, 55.735, 14], minzoom: 0, maxzoom: 15 } });
  assert.deepEqual(t, { path: 'tiles/khamovniki.pmtiles', file: 'khamovniki.pmtiles', bbox: [37.55, 55.72, 37.61, 55.75], center: [37.58, 55.735, 14], minzoom: 0, maxzoom: 15 });
  assert.equal(tilesInfo({ tiles: { 'tbilisi.pmtiles': { bytes: 1, sha256: 'x' } } }).path, 'tiles/tbilisi.pmtiles', 'old shape');
  assert.equal(tilesInfo({ tiles: { files: { 'tiles/a.pmtiles': {}, 'tiles/fonts/x.pbf': {} } } }).file, 'a.pmtiles');
  assert.deepEqual(tilesInfo({}), { path: null, file: null, bbox: null, center: null, minzoom: null, maxzoom: null });
});

// ---- per-city packs over fake Cache Storage

const BASE = 'https://app.test/quchebi/';
const sha = (buf) => createHash('sha256').update(buf).digest('hex');

class FakeCache {
  constructor() { this.m = new Map(); }
  async match(url) { const e = this.m.get(String(url)); return e ? new Response(e.body.slice(0), { headers: e.headers }) : undefined; }
  async put(url, resp) { this.m.set(String(url), { body: await resp.arrayBuffer(), headers: new Headers(resp.headers) }); }
}
class FakeCaches {
  constructor() { this.m = new Map(); }
  async open(name) { if (!this.m.has(name)) this.m.set(name, new FakeCache()); return this.m.get(name); }
  async has(name) { return this.m.has(name); }
  async keys() { return [...this.m.keys()]; }
  async delete(name) { return this.m.delete(name); }
}

/** Server with a tiny pack per city (data dir) and a shared font file under tiles/. */
function makeServer() {
  const files = {};
  const font = Buffer.from('glyphs');
  const addPack = (dir, version, extra = {}) => {
    const core = Buffer.from(JSON.stringify({ v: 1, kinds: [], f: [{ ka: `${dir}-street`, k: 0, len: 0, c: [0, 0], hn: 0 }] }));
    const ep = Buffer.from(JSON.stringify({ v: 1, taxonomy: [], kinds: {}, e: [] }));
    const manifest = {
      v: 1, version, built: '10/11/2026',
      files: { 'core.json': { bytes: core.length, sha256: sha(core) }, 'eponyms.json': { bytes: ep.length, sha256: sha(ep) } },
      tiles: { basemap: 'tiles/x.pmtiles', files: { 'tiles/fonts/a.pbf': { bytes: font.length, sha256: sha(font) } } },
      ...extra,
    };
    files[`${BASE}${dir}core.json`] = core;
    files[`${BASE}${dir}eponyms.json`] = ep;
    files[`${BASE}${dir}manifest.json`] = Buffer.from(JSON.stringify(manifest));
  };
  files[`${BASE}tiles/fonts/a.pbf`] = font;
  const s = { files, calls: [], addPack };
  s.fetch = async (url) => {
    s.calls.push(String(url));
    const b = s.files[String(url)];
    return b ? new Response(Buffer.from(b), { status: 200 }) : new Response('nf', { status: 404 });
  };
  return s;
}

test('two cities install side by side; updates and removal touch only their own caches', async () => {
  const server = makeServer();
  server.addPack('data/', '20261024-3');
  server.addPack('data/khamovniki/', '20261110-1', { city: { id: 'khamovniki', title: 'Москва. Хамовники', lang: 'ru' } });
  const e = { fetch: server.fetch, caches: new FakeCaches(), subtle: globalThis.crypto.subtle, kv: memoryKV(), baseURI: BASE };

  const tb = await loadPack({ city: 'tbilisi', env: e });
  assert.equal(tb.core.f[0].ka, 'data/-street');
  const kh = await loadPack({ city: 'khamovniki', env: e });
  assert.equal(kh.core.f[0].ka, 'data/khamovniki/-street');
  assert.equal(kh.manifest.city.lang, 'ru');
  assert.deepEqual((await e.caches.keys()).sort(), ['pack-khamovniki-v20261110-1', 'pack-v20261024-3']);
  assert.equal((await e.kv.get('pack:current')).version, '20261024-3');
  assert.equal((await e.kv.get('pack:current:khamovniki')).version, '20261110-1');

  // tiles: the shared font is downloaded once and reused (same URL and sha256) by the other city
  await downloadPack({ city: 'tbilisi', includeTiles: true, env: e });
  const fontCalls = () => server.calls.filter((u) => u.endsWith('fonts/a.pbf')).length;
  assert.equal(fontCalls(), 1);
  await downloadPack({ city: 'khamovniki', includeTiles: true, env: e });
  assert.equal(fontCalls(), 1, 'reused from the Tbilisi cache');
  assert.equal((await packStatus({ city: 'khamovniki', env: e })).tilesComplete, true);

  // a new Tbilisi version deletes the old Tbilisi cache only
  server.addPack('data/', '20261201-1');
  await downloadPack({ city: 'tbilisi', includeTiles: false, env: e });
  assert.deepEqual((await e.caches.keys()).sort(), ['pack-khamovniki-v20261110-1', 'pack-v20261201-1']);
  // a new Moscow version deletes the old Moscow cache only
  server.addPack('data/khamovniki/', '20261202-1');
  await downloadPack({ city: 'khamovniki', includeTiles: false, env: e });
  assert.deepEqual((await e.caches.keys()).sort(), ['pack-khamovniki-v20261202-1', 'pack-v20261201-1']);

  const info = await cityPackInfo('khamovniki', { env: e });
  assert.equal(info.version, '20261202-1');
  assert.equal(info.cached, true);
  assert.ok(info.bytes > 0);
  const none = await cityPackInfo('khamovniki', { env: { ...e, kv: memoryKV() } });
  assert.equal(none.cached, false);
  assert.equal(none.version, null);

  await removePack({ city: 'khamovniki', env: e });
  assert.deepEqual(await e.caches.keys(), ['pack-v20261201-1'], 'Tbilisi pack kept');
  assert.equal(await e.kv.get('pack:current:khamovniki'), null);
  assert.equal((await e.kv.get('pack:current')).version, '20261201-1');
});
