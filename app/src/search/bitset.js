// Fixed-size bitset over feature indices, backed by a Uint32Array.
// Binary operations (and/or/andNot/xor/not) return a NEW Bitset; the *InPlace variants
// (iand/ior/iandNot/inot) mutate `this` and return it. Sizes must match.

function popcount32(v) {
  v = v - ((v >>> 1) & 0x55555555);
  v = (v & 0x33333333) + ((v >>> 2) & 0x33333333);
  return (((v + (v >>> 4)) & 0x0f0f0f0f) * 0x01010101) >>> 24;
}

export class Bitset {
  /** @param {number} size number of bits; @param {Uint32Array} [words] backing words (taken as is) */
  constructor(size, words) {
    this.size = size | 0;
    const nw = (this.size + 31) >>> 5;
    this.words = words || new Uint32Array(nw);
    if (this.words.length !== nw) throw new Error(`Bitset: expected ${nw} words, got ${this.words.length}`);
  }

  static full(size) { const b = new Bitset(size); b.words.fill(0xffffffff); b._trim(); return b; }

  static fromIndices(size, indices) {
    const b = new Bitset(size);
    for (const i of indices) b.set(i);
    return b;
  }

  _trim() {
    const r = this.size & 31;
    if (r && this.words.length) this.words[this.words.length - 1] &= 0xffffffff >>> (32 - r);
    return this;
  }

  _check(o) {
    if (!(o instanceof Bitset) || o.size !== this.size) throw new Error(`Bitset size mismatch: ${this.size} vs ${o && o.size}`);
  }

  has(i) { return i >= 0 && i < this.size && ((this.words[i >>> 5] >>> (i & 31)) & 1) === 1; }
  get(i) { return this.has(i); }

  set(i, value = true) {
    if (i < 0 || i >= this.size) throw new RangeError(`Bitset.set(${i}) out of range ${this.size}`);
    if (value) this.words[i >>> 5] |= 1 << (i & 31);
    else this.words[i >>> 5] &= ~(1 << (i & 31));
    return this;
  }

  clear(i) { return this.set(i, false); }

  clone() { return new Bitset(this.size, this.words.slice()); }

  // ----- new-object operations
  and(o) { return this.clone().iand(o); }
  or(o) { return this.clone().ior(o); }
  andNot(o) { return this.clone().iandNot(o); }
  xor(o) { return this.clone().ixor(o); }
  not() { return this.clone().inot(); }

  // ----- in-place operations
  iand(o) { this._check(o); const a = this.words, b = o.words; for (let k = 0; k < a.length; k++) a[k] &= b[k]; return this; }
  ior(o) { this._check(o); const a = this.words, b = o.words; for (let k = 0; k < a.length; k++) a[k] |= b[k]; return this; }
  iandNot(o) { this._check(o); const a = this.words, b = o.words; for (let k = 0; k < a.length; k++) a[k] &= ~b[k]; return this; }
  ixor(o) { this._check(o); const a = this.words, b = o.words; for (let k = 0; k < a.length; k++) a[k] ^= b[k]; return this; }
  inot() { const a = this.words; for (let k = 0; k < a.length; k++) a[k] = ~a[k]; return this._trim(); }
  fill(value = true) { this.words.fill(value ? 0xffffffff : 0); return value ? this._trim() : this; }

  count() {
    let c = 0;
    const a = this.words;
    for (let k = 0; k < a.length; k++) if (a[k]) c += popcount32(a[k]);
    return c;
  }

  isEmpty() { const a = this.words; for (let k = 0; k < a.length; k++) if (a[k]) return false; return true; }
  any() { return !this.isEmpty(); }

  equals(o) {
    if (!(o instanceof Bitset) || o.size !== this.size) return false;
    for (let k = 0; k < this.words.length; k++) if (this.words[k] !== o.words[k]) return false;
    return true;
  }

  /** True when this ∩ o is non-empty. */
  intersects(o) { this._check(o); const a = this.words, b = o.words; for (let k = 0; k < a.length; k++) if (a[k] & b[k]) return true; return false; }

  /** Calls fn(index) for every set bit in ascending order. Return false from fn to stop early. */
  forEach(fn) {
    const a = this.words;
    for (let k = 0; k < a.length; k++) {
      let w = a[k];
      while (w) {
        const t = w & -w;
        const i = (k << 5) + (31 - Math.clz32(t));
        if (fn(i) === false) return;
        w ^= t;
      }
    }
  }

  /** Set bit indices as Int32Array (ascending). */
  toArray() {
    const out = new Int32Array(this.count());
    let j = 0;
    this.forEach((i) => { out[j++] = i; });
    return out;
  }

  /** First set bit or -1. */
  first() {
    const a = this.words;
    for (let k = 0; k < a.length; k++) if (a[k]) { const w = a[k]; return (k << 5) + (31 - Math.clz32(w & -w)); }
    return -1;
  }

  *[Symbol.iterator]() {
    const a = this.words;
    for (let k = 0; k < a.length; k++) {
      let w = a[k];
      while (w) { const t = w & -w; yield (k << 5) + (31 - Math.clz32(t)); w ^= t; }
    }
  }

  toString() { return `Bitset(${this.count()}/${this.size})`; }
}

/** OR of many bitsets (all of the given size); empty list -> empty set. */
export function unionAll(size, sets) {
  const out = new Bitset(size);
  for (const s of sets) if (s) out.ior(s);
  return out;
}

/** AND of many bitsets; empty list -> full set. */
export function intersectAll(size, sets) {
  const out = Bitset.full(size);
  for (const s of sets) if (s) out.iand(s);
  return out;
}

export { popcount32 };
