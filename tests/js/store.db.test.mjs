import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createDb, memoryKV, ZONE_LS_KEY, HISTORY_MAX, configureDb, getZone, setZone } from '../../app/src/store/db.js';

function fakeStorage() {
  const m = new Map();
  return { map: m, getItem: (k) => (m.has(k) ? m.get(k) : null), setItem: (k, v) => { m.set(k, String(v)); }, removeItem: (k) => { m.delete(k); } };
}
const brokenKV = () => ({
  get: async () => { throw new Error('boom'); },
  set: async () => { throw new Error('boom'); },
  del: async () => { throw new Error('boom'); },
});
const POLY = [[41.69, 44.79], [41.70, 44.79], [41.70, 44.80]];

test('zone: IndexedDB + localStorage mirror', async () => {
  const storage = fakeStorage();
  const db = createDb({ kv: memoryKV(), storage });
  assert.equal(await db.getZone(), null);
  assert.equal(await db.setZone({ polygon: POLY, mode: 'draw', name: 'Сололаки' }), true);
  const z = await db.getZone();
  assert.deepEqual(z.polygon, POLY);
  assert.equal(z.mode, 'draw');
  assert.ok(z.createdAt > 0);
  assert.ok(storage.map.has(ZONE_LS_KEY));
  await db.clearZone();
  assert.equal(await db.getZone(), null);
  assert.ok(!storage.map.has(ZONE_LS_KEY));
  await assert.rejects(() => db.setZone({ polygon: [[1, 1]] }), /polygon/);
});

test('zone survives a broken IndexedDB via the mirror, and is restored back', async () => {
  const storage = fakeStorage();
  const broken = createDb({ kv: brokenKV(), storage });
  assert.equal(await broken.setZone({ polygon: POLY, mode: 'points' }), true, 'mirror write succeeded');
  assert.equal((await broken.getZone()).mode, 'points');
  // fresh IndexedDB (e.g. cleared) but the mirror is still there
  const kv = memoryKV();
  const db = createDb({ kv, storage });
  assert.equal((await db.getZone()).mode, 'points');
  await new Promise((r) => setTimeout(r, 5));
  assert.equal((await kv.get('zone')).mode, 'points', 'restored into IndexedDB');
  // corrupted mirror is ignored
  storage.setItem(ZONE_LS_KEY, '{oops');
  const db2 = createDb({ kv: memoryKV(), storage });
  assert.equal(await db2.getZone(), null);
  // no storage at all
  const db3 = createDb({ kv: brokenKV(), storage: null });
  assert.equal(await db3.setZone({ polygon: POLY }), false);
  assert.equal(await db3.getZone(), null);
});

test('settings', async () => {
  const db = createDb({ kv: memoryKV(), storage: null });
  assert.equal(await db.getSetting('theme', 'light'), 'light');
  await db.setSetting('theme', 'dark');
  await db.setSetting('gameMode', true);
  assert.equal(await db.getSetting('theme', 'light'), 'dark');
  assert.deepEqual(await db.getSettings(), { theme: 'dark', gameMode: true });
  await db.setSetting('theme', undefined);
  assert.equal(await db.getSetting('theme', 'x'), 'x');
  const broken = createDb({ kv: brokenKV(), storage: null });
  assert.equal(await broken.getSetting('a', 1), 1, 'failures return the default');
  assert.equal(await broken.setSetting('a', 2), false);
});

test('history: newest first, dedupe, cap', async () => {
  const db = createDb({ kv: memoryKV(), storage: null });
  await db.addHistory('дом:3');
  await db.addHistory('тип:улица* дом:3');
  await db.addHistory('дом:3');
  assert.deepEqual(await db.getHistory(), ['дом:3', 'тип:улица* дом:3']);
  assert.equal(await db.addHistory('   '), false);
  for (let i = 0; i < HISTORY_MAX + 20; i++) await db.addHistory('q' + i);
  const h = await db.getHistory();
  assert.equal(h.length, HISTORY_MAX);
  assert.equal(h[0], 'q' + (HISTORY_MAX + 19));
  await db.clearHistory();
  assert.deepEqual(await db.getHistory(), []);
});

test('saved queries and notes', async () => {
  const db = createDb({ kv: memoryKV(), storage: null });
  await db.addSaved({ q: 'кто:поэт', name: 'Поэты' });
  await db.addSaved({ q: 'дом:3' });
  await db.addSaved({ q: 'кто:поэт', name: 'Поэты 2' });
  const s = await db.getSaved();
  assert.deepEqual(s.map((x) => [x.q, x.name]), [['кто:поэт', 'Поэты 2'], ['дом:3', 'дом:3']]);
  await db.removeSaved('дом:3');
  assert.equal((await db.getSaved()).length, 1);
  assert.equal(await db.addNote({ featureKey: 'ka:ლადო ასათიანის ქუჩა', text: '' }), false);
  await db.addNote({ featureKey: 'ka:ლადო ასათიანის ქუჩა', text: 'КП 5 здесь', t: 1 });
  await db.addNote({ featureKey: 'q:Q144086', text: 'проверить', t: 2 });
  const n = await db.getNotes();
  assert.deepEqual(n.map((x) => x.t), [2, 1]);
  await db.removeNote(1);
  assert.deepEqual((await db.getNotes()).map((x) => x.text), ['проверить']);
});

test('module-level helpers use the configured default instance', async () => {
  configureDb({ kv: memoryKV(), storage: fakeStorage() });
  assert.equal(await getZone(), null);
  await setZone({ polygon: POLY, mode: 'code' });
  assert.equal((await getZone()).mode, 'code');
});
