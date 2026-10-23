import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createLogger, memoryLogStore, sanitize, formatTime, log, getLogs, exportLogs } from '../../app/src/log/log.js';

const quietConsole = () => {
  const lines = [];
  const rec = (level) => (...a) => lines.push([level, a.join(' ')]);
  return { lines, debug: rec('debug'), info: rec('info'), warn: rec('warn'), error: rec('error'), log: rec('log') };
};

test('ring buffer keeps the last N entries in order, buffered writes', async () => {
  const store = memoryLogStore();
  const lg = createLogger({ capacity: 5, store, console: null, flushMs: 10000 });
  for (let i = 0; i < 12; i++) lg.info('t', 'msg ' + i);
  assert.equal(store.map.size, 0, 'nothing written before flush (buffered)');
  const rows = await lg.getLogs();
  assert.deepEqual(rows.map((r) => r.msg), ['msg 7', 'msg 8', 'msg 9', 'msg 10', 'msg 11']);
  assert.deepEqual(rows.map((r) => r.seq), [7, 8, 9, 10, 11]);
  assert.equal(store.map.size, 6, '5 slots + seq');
});

test('batch size triggers an immediate flush; seq continues across sessions', async () => {
  const store = memoryLogStore();
  const a = createLogger({ capacity: 100, store, console: null, flushMs: 10000, batch: 3 });
  a.info('x', '1'); a.info('x', '2'); a.info('x', '3');
  await a.flush();
  assert.equal(await store.get('seq'), 3);
  const b = createLogger({ capacity: 100, store, console: null, flushMs: 10000 });
  b.warn('y', '4', { k: 1 });
  const rows = await b.getLogs();
  assert.deepEqual(rows.map((r) => [r.seq, r.level, r.tag, r.msg]), [[0, 'info', 'x', '1'], [1, 'info', 'x', '2'], [2, 'info', 'x', '3'], [3, 'warn', 'y', '4']]);
  assert.deepEqual(rows[3].data, { k: 1 });
  await b.clear();
  assert.deepEqual(await b.getLogs(), []);
});

test('levels, console mirror, minLevel, (tag, msg) shorthand', async () => {
  const con = quietConsole();
  const lg = createLogger({ store: null, console: con, mirrorLevel: 'info', minLevel: 'info' });
  lg.debug('d', 'hidden');
  lg.info('pack', 'loaded', { v: 1 });
  lg.error('pack', 'failed');
  lg.write('info', 'only message');
  const rows = await lg.getLogs();
  assert.deepEqual(rows.map((r) => r.level), ['info', 'error', 'info']);
  assert.equal(rows[2].tag, 'app');
  assert.equal(con.lines.length, 3);
  assert.equal(con.lines[0][0], 'info');
  assert.match(con.lines[0][1], /\[pack\] loaded/);
});

test('export text uses DD/MM/YYYY and includes data', async () => {
  const lg = createLogger({ store: memoryLogStore(), console: null, flushMs: 0 });
  lg.info('zone', 'saved', { vertices: 12 });
  const text = await lg.exportText();
  assert.match(text, /^\d{2}\/\d{2}\/\d{4} \d{2}:\d{2}:\d{2}\.\d{3} INFO  \[zone\] saved \{"vertices":12\}$/);
  assert.equal(formatTime(new Date(2026, 9, 24, 7, 5, 3, 9).getTime()), '24/10/2026 07:05:03.009');
});

test('sanitize: errors, cycles, functions, size limit', () => {
  const e = sanitize(new Error('boom'));
  assert.equal(e.message, 'boom');
  const cyc = { a: 1 }; cyc.self = cyc;
  assert.deepEqual(sanitize(cyc), { a: 1, self: '[circular]' });
  assert.deepEqual(sanitize({ f: () => 1, n: 2 }), { n: 2 });
  assert.equal(sanitize(undefined), undefined);
  assert.equal(sanitize({ big: 'x'.repeat(10000) }).truncated, true);
  assert.deepEqual(sanitize({ err: new TypeError('t') }).err.name, 'TypeError');
});

test('storage failure falls back to memory', async () => {
  const con = quietConsole();
  const bad = { get: async () => 0, setMany: async () => { throw new Error('quota'); }, entries: async () => [], clear: async () => {} };
  const lg = createLogger({ store: bad, console: con, mirrorLevel: 'error', flushMs: 10000 });
  lg.info('a', 'one');
  await lg.flush();
  lg.info('a', 'two');
  const rows = await lg.getLogs();
  assert.deepEqual(rows.map((r) => r.msg), ['one', 'two']);
  assert.ok(con.lines.some(([, l]) => /memory only/.test(l)));
});

test('default logger works in Node without IndexedDB', async () => {
  log.info('test', 'hello from node');
  const rows = await getLogs();
  assert.ok(rows.some((r) => r.msg === 'hello from node'));
  assert.match(await exportLogs(), /hello from node/);
});
