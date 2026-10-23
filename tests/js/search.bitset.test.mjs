import { test } from 'node:test';
import assert from 'node:assert/strict';
import { Bitset, unionAll, intersectAll, popcount32 } from '../../app/src/search/bitset.js';

test('popcount32', () => {
  assert.equal(popcount32(0), 0);
  assert.equal(popcount32(0xffffffff), 32);
  assert.equal(popcount32(0x80000001), 2);
  assert.equal(popcount32(0b1011), 3);
});

test('set/has/count and boundaries', () => {
  for (const size of [0, 1, 31, 32, 33, 64, 100, 5620]) {
    const b = new Bitset(size);
    assert.equal(b.count(), 0);
    if (size) {
      b.set(0); b.set(size - 1);
      assert.ok(b.has(0) && b.has(size - 1));
      assert.equal(b.count(), size === 1 ? 1 : 2);
    }
    assert.equal(b.has(-1), false);
    assert.equal(b.has(size), false);
    assert.throws(() => b.set(size), RangeError);
    const full = Bitset.full(size);
    assert.equal(full.count(), size, 'full set must not count padding bits');
    assert.equal(full.not().count(), 0);
  }
});

test('and/or/andNot/xor/not return new sets; in-place variants mutate', () => {
  const a = Bitset.fromIndices(100, [1, 2, 3, 50, 99]);
  const b = Bitset.fromIndices(100, [2, 3, 4, 99]);
  assert.deepEqual([...a.and(b)], [2, 3, 99]);
  assert.deepEqual([...a.or(b)], [1, 2, 3, 4, 50, 99]);
  assert.deepEqual([...a.andNot(b)], [1, 50]);
  assert.deepEqual([...a.xor(b)], [1, 4, 50]);
  assert.equal(a.not().count(), 95);
  assert.deepEqual([...a], [1, 2, 3, 50, 99], 'a unchanged');
  const c = a.clone();
  c.iand(b);
  assert.deepEqual([...c], [2, 3, 99]);
  c.ior(Bitset.fromIndices(100, [7]));
  assert.deepEqual(Array.from(c.toArray()), [2, 3, 7, 99]);
  c.iandNot(Bitset.fromIndices(100, [3]));
  assert.deepEqual([...c], [2, 7, 99]);
  c.inot();
  assert.equal(c.count(), 97);
  assert.throws(() => a.and(new Bitset(101)), /size mismatch/);
});

test('forEach order, early stop, first, isEmpty, intersects, equals', () => {
  const a = Bitset.fromIndices(200, [199, 0, 64, 31, 32]);
  const seen = [];
  a.forEach((i) => { seen.push(i); });
  assert.deepEqual(seen, [0, 31, 32, 64, 199]);
  const two = [];
  a.forEach((i) => { two.push(i); return two.length < 2; });
  assert.deepEqual(two, [0, 31]);
  assert.equal(a.first(), 0);
  assert.equal(new Bitset(10).first(), -1);
  assert.ok(new Bitset(10).isEmpty());
  assert.ok(a.intersects(Bitset.fromIndices(200, [64])));
  assert.ok(!a.intersects(Bitset.fromIndices(200, [65])));
  assert.ok(a.equals(a.clone()));
  assert.ok(!a.equals(Bitset.fromIndices(200, [0])));
  a.clear(0);
  assert.equal(a.has(0), false);
});

test('unionAll / intersectAll', () => {
  const s = [Bitset.fromIndices(10, [1, 2]), Bitset.fromIndices(10, [2, 3])];
  assert.deepEqual([...unionAll(10, s)], [1, 2, 3]);
  assert.deepEqual([...intersectAll(10, s)], [2]);
  assert.equal(intersectAll(10, []).count(), 10);
});

test('performance: 10k and+count on 5600-bit sets', () => {
  const n = 5600;
  const a = Bitset.full(n), b = new Bitset(n);
  for (let i = 0; i < n; i += 3) b.set(i);
  const t0 = performance.now();
  let c = 0;
  for (let k = 0; k < 10000; k++) c += a.and(b).count();
  const ms = performance.now() - t0;
  assert.equal(c, 10000 * Math.ceil(n / 3));
  assert.ok(ms < 500, `10k and+count took ${ms.toFixed(1)} ms`);
});
