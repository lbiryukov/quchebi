// Autocomplete for the query line (docs/dsl.md "Autocomplete").
//
// complete(text, cursor, index, ctx) -> [{label, insert, detail, from, to}]
//   Replace text[from, to) with `insert`. `detail` is a description for keys and the number of
//   objects in the current zone for values of enumerated keys ("41").
// Context is found from the tokens before the cursor, so unfinished input (open parentheses,
// open quotes) works; inside x:( … ), рядом:( … ), семья:( … ) the same rules apply recursively,
// inside было:( … ) only eponym keys are offered.
import { tokenize, TOKEN as T, fold } from './lexer.js';
import { KEYS, findKey, EPONYM_KEYS, FIXED, COMPASS_SHOW, COMPASS_RU } from './keys.js';
import { dslData } from './data.js';
import { resolveWho } from './eval.js';
import { log } from '../log/log.js';
import { Bitset } from '../search/bitset.js';
import { normRu } from '../search/normalize.js';

const MAX_VALUES = 15;

function zoneMaskOf(ctx) {
  const z = ctx && ctx.zone;
  return z ? z.featRule || z.featDefault || null : null; // the zone's own rule
}

function countIn(bits, zm) {
  if (!bits) return 0;
  if (!zm) return bits.count();
  let c = 0;
  const a = bits.words;
  const b = zm.words;
  for (let k = 0; k < a.length; k++) {
    let v = a[k] & b[k];
    while (v) { v &= v - 1; c++; }
  }
  return c;
}

function quoteIfNeeded(v) {
  return /[\s:,()|~"]/.test(v) ? `"${v}"` : v;
}

/**
 * Find the completion context at the cursor.
 * Returns {mode: 'key'|'value'|'tilde'|'none', key?, prefix, from, to, sub?}
 */
export function contextAt(text, cursor) {
  const src = String(text ?? '');
  const cur = Math.max(0, Math.min(cursor ?? src.length, src.length));
  const toks = tokenize(src.slice(0, cur)).filter((t) => t.type !== T.EOF);
  // the word under the cursor may continue after it
  let tail = cur;
  while (tail < src.length && !/[\s()|,~:=<>"]/.test(src[tail])) tail++;

  // enclosing subquery keys
  const stack = [];
  for (let k = 0; k < toks.length; k++) {
    const t = toks[k];
    if (t.type === T.LPAREN) {
      const sep = toks[k - 1];
      const key = toks[k - 2];
      const kk = sep && (sep.type === T.COLON || sep.type === T.EQ) && key && key.type === T.WORD ? findKey(key.value) : null;
      stack.push(kk ? kk.id : null);
    } else if (t.type === T.RPAREN) stack.pop();
  }
  const sub = stack.length ? stack.filter(Boolean).pop() || null : null;
  const inOld = stack.includes('old');

  const last = toks[toks.length - 1];
  // a gap after ':' or ',' still belongs to the value ("тип: ", "тип:переулок, ")
  const atGap = !last || (/\s/.test(src[cur - 1] || ' ') && ![T.COLON, T.EQ, T.COMMA].includes(last.type));
  if (atGap) {
    // after whitespace, '(' handled below
    return { mode: 'key', prefix: '', from: cur, to: cur, sub, inOld };
  }
  if (last.type === T.LPAREN || last.type === T.OR || last.type === T.NOT) {
    return { mode: 'key', prefix: '', from: cur, to: cur, sub, inOld };
  }
  if (last.type === T.QUOTED || last.type === T.REGEX) return { mode: 'none', prefix: '', from: cur, to: cur, sub, inOld };

  // walk back over a value list "key:v1,v2,pre"
  const keyBefore = (k) => {
    // toks[k] is the separator; returns key or null
    const kt = toks[k - 1];
    if (!kt || kt.type !== T.WORD) return null;
    if (toks[k].ws) return null;
    return findKey(kt.value);
  };
  const listKey = (k) => {
    // k: index of the token right before the value being typed
    while (k >= 0) {
      const t = toks[k];
      if (t.type === T.COLON || t.type === T.EQ) return keyBefore(k) ? { key: keyBefore(k), sepIdx: k } : null;
      if (t.type === T.COMMA) { k -= 2; continue; } // skip ", value"
      return null;
    }
    return null;
  };

  if (last.type === T.WORD) {
    const prev = toks[toks.length - 2];
    if (prev && (!last.ws || prev.type === T.COMMA || prev.type === T.COLON) && (prev.type === T.COLON || prev.type === T.EQ || prev.type === T.COMMA)) {
      const lk = listKey(toks.length - 2);
      if (lk) return { mode: 'value', key: lk.key, prefix: last.value, from: last.pos, to: tail, sub, inOld };
    }
    if (prev && !last.ws && prev.type === T.TILDE) {
      const owner = tildeOwner(toks, toks.length - 2);
      if (owner) return { mode: 'tilde', key: owner, prefix: last.value, from: last.pos, to: tail, sub, inOld };
    }
    if (prev && prev.type === T.EQ && toks[toks.length - 3] && toks[toks.length - 3].type === T.COLON) {
      const k = keyBefore(toks.length - 3);
      if (k && k.id === 'house') return { mode: 'value', key: k, prefix: '=' + last.value, from: prev.pos, to: tail, sub, inOld };
    }
    return { mode: 'key', prefix: last.value, from: last.pos, to: tail, sub, inOld };
  }
  if (last.type === T.COLON || last.type === T.EQ) {
    const k = keyBefore(toks.length - 1);
    if (k) return { mode: 'value', key: k, prefix: '', from: cur, to: cur, sub, inOld };
    if (last.type === T.EQ && toks[toks.length - 2] && toks[toks.length - 2].type === T.COLON) {
      const k2 = keyBefore(toks.length - 2);
      if (k2 && k2.id === 'house') return { mode: 'value', key: k2, prefix: '=', from: last.pos, to: cur, sub, inOld };
    }
    return { mode: 'none', prefix: '', from: cur, to: cur, sub, inOld };
  }
  if (last.type === T.COMMA) {
    const lk = listKey(toks.length - 1);
    if (lk) return { mode: 'value', key: lk.key, prefix: '', from: cur, to: cur, sub, inOld };
  }
  if (last.type === T.TILDE) {
    const owner = tildeOwner(toks, toks.length - 1);
    if (owner) return { mode: 'tilde', key: owner, prefix: '', from: cur, to: cur, sub, inOld };
  }
  return { mode: 'none', prefix: '', from: cur, to: cur, sub, inOld };
}

/** Key owning a '~' at token index k: x:(…)~ or рядом:(…)~ / рядом:я~. */
function tildeOwner(toks, k) {
  let j = k - 1;
  if (j < 0) return null;
  if (toks[j].type === T.RPAREN) {
    let depth = 0;
    for (; j >= 0; j--) {
      if (toks[j].type === T.RPAREN) depth++;
      else if (toks[j].type === T.LPAREN) { depth--; if (depth === 0) break; }
    }
    j -= 1;
  } else if (toks[j].type === T.WORD || toks[j].type === T.QUOTED) j -= 1;
  else return null;
  if (j < 1) return null;
  if (toks[j].type !== T.COLON && toks[j].type !== T.EQ) return null;
  const key = toks[j - 1].type === T.WORD ? findKey(toks[j - 1].value) : null;
  return key && (key.id === 'cross' || key.id === 'near') ? key : null;
}

// ---------------------------------------------------------------------------------- values

function keySuggestions(c) {
  const p = fold(c.prefix);
  const out = [];
  const seen = new Set();
  for (const k of KEYS) {
    if (c.inOld && !EPONYM_KEYS.has(k.id)) continue;
    if (c.sub && (k.id === 'zone' || k.id === 'sort')) continue;
    const name = k.names.find((nm) => fold(nm).startsWith(p));
    if (name === undefined) continue;
    if (seen.has(k.id)) continue;
    seen.add(k.id);
    const label = name === k.names[0] ? `${k.ru}:` : `${name}: (${k.ru})`;
    out.push({ label, insert: `${name}:`, detail: k.desc, from: c.from, to: c.to });
  }
  // exact key typed without colon goes first
  out.sort((a, b) => (fold(a.insert) === p + ':' ? -1 : 0) - (fold(b.insert) === p + ':' ? -1 : 0));
  return out;
}

function valueList(c, items) {
  const p = fold(c.prefix);
  const filtered = items.filter((it) => fold(it.value).startsWith(p) || (p.length >= 3 && fold(it.value).includes(p)));
  filtered.sort((a, b) => (fold(a.value).startsWith(p) ? 0 : 1) - (fold(b.value).startsWith(p) ? 0 : 1)
    || (b.count ?? -1) - (a.count ?? -1) || (a.value < b.value ? -1 : a.value > b.value ? 1 : 0));
  return filtered.slice(0, MAX_VALUES).map((it) => ({
    label: it.label || it.value,
    insert: quoteIfNeeded(it.value),
    detail: it.detail != null ? it.detail : it.count != null ? String(it.count) : '',
    from: c.from,
    to: c.to,
  }));
}

function eponymCountFn(d, zm) {
  return (pred) => {
    const b = new Bitset(d.n);
    for (let i = 0; i < d.n; i++) { const e = d.eponymOf[i]; if (e >= 0 && pred[e]) b.set(i); }
    return countIn(b, zm);
  };
}

function valueSuggestions(c, index, ctx) {
  const d = dslData(index);
  const zm = zoneMaskOf(ctx);
  const key = c.key;
  switch (key.id) {
    case 'type': {
      const items = [];
      d.kinds.forEach((k, ki) => {
        const cnt = countIn(d.kindBits[ki], zm);
        for (const nm of [k.ru, ...(k.syn || [])]) if (nm) items.push({ value: nm, count: cnt });
      });
      const groups = new Map();
      d.kinds.forEach((k, ki) => { if (k.group) { if (!groups.has(k.group)) groups.set(k.group, { name: k.ru, kis: [] }); groups.get(k.group).kis.push(ki); } });
      for (const g of groups.values()) {
        const b = new Bitset(d.n);
        for (const ki of g.kis) b.ior(d.kindBits[ki]);
        items.push({ value: g.name + '*', count: countIn(b, zm), label: `${g.name}* (группа)` });
      }
      return valueList(c, items);
    }
    case 'district':
      return valueList(c, d.districts.map((a, i) => ({ value: fold(a.ru || a.en || a.ka), label: a.ru || a.ka, count: countIn(d.districtBits[i], zm) })));
    case 'in': {
      const items = d.places.map((a, i) => ({ value: fold(a.ru || a.en || a.ka), label: a.ru || a.ka, count: countIn(d.placeBits[i], zm) }));
      d.districts.forEach((a, i) => items.push({ value: fold(a.ru || a.en || a.ka), label: `${a.ru || a.ka} (район)`, count: countIn(d.districtBits[i], zm) }));
      const seen = new Set();
      return valueList(c, items.filter((it) => (seen.has(it.value) ? false : seen.add(it.value))));
    }
    case 'who': {
      const p = normRu(c.prefix);
      // value -> {label, sig}; aliases with the same meaning (поэт / поэтесса / поэты) collapse
      // to the first one listed (the taxonomy label)
      const cands = new Map();
      const add = (v, label, sig) => { if (v && !cands.has(v)) cands.set(v, { label, sig }); };
      for (const t of d.taxLabels) add(t.label, t.group ? `${t.label} (вся группа)` : t.label, 'tax:' + t.id);
      for (const [a, ids] of d.aliasToIds) add(a, a, 'tax:' + [...ids].sort().join(','));
      for (const [a, codes] of d.kindAlias) if (!/^[a-z_]+$/.test(a)) add(a, a, 'kind:' + [...codes].sort().join(','));
      if (p) for (const o of d.occWords) add(o, o, 'occ:' + o);
      const seenSig = new Set();
      const pre = [];
      for (const [v, info] of cands) {
        if (!(v.startsWith(p) || (p.length >= 3 && v.includes(p)))) continue;
        if (seenSig.has(info.sig) && v !== p) continue;
        seenSig.add(info.sig);
        pre.push(v);
      }
      const counter = eponymCountFn(d, zm);
      const items = [];
      for (const v of pre.slice(0, 60)) {
        let pred;
        try { pred = resolveWho(d, { value: v, raw: v, pos: 0, len: 0 }).pred; } catch { continue; }
        items.push({ value: v, label: cands.get(v).label, count: counter(pred) });
      }
      return valueList(c, items);
    }
    case 'gender':
    case 'own': {
      const counter = eponymCountFn(d, zm);
      return valueList(c, FIXED[key.id].show.map((v) => {
        const canon = FIXED[key.id].values[v];
        const pred = new Uint8Array(d.nE);
        for (let j = 0; j < d.nE; j++) {
          const e = d.E[j];
          if (key.id === 'gender') pred[j] = e.g === canon ? 1 : 0;
          else {
            const o = e.o;
            const known = o && o !== 'unknown' && o !== '?';
            const ge = o === 'ge' || o === 'georgian';
            pred[j] = known && (canon === 'yes' ? ge : !ge) ? 1 : 0;
          }
        }
        return { value: v, count: counter(pred) };
      }));
    }
    case 'zone': {
      const z = ctx && ctx.zone;
      const cnt = (b) => (b ? b.count() : null);
      const items = [
        { value: 'выкл', count: d.n, label: 'выкл — весь город' },
        { value: 'задевает', count: z ? cnt(z.featTouch) : null, label: 'задевает — любая часть внутри' },
        { value: 'целиком', count: z ? cnt(z.featWhole) : null, label: 'целиком — только целиком внутри' },
        { value: 'вкл', count: z ? cnt(z.featDefault) : null, label: 'вкл — обычное правило' },
      ];
      return valueList(c, items.map((it) => ({ ...it, detail: it.count == null ? '' : String(it.count) })));
    }
    case 'sort':
      return valueList(c, [
        { value: 'имя', detail: 'по алфавиту' }, { value: 'длина', detail: 'сначала длинные' },
        { value: 'домов', detail: 'сначала с большим числом домов' }, { value: 'расстояние', detail: 'от меня или центра карты' },
      ]);
    case 'house':
      return houseForms(c);
    case 'century': {
      const items = [];
      for (let cent = 1; cent <= 21; cent++) {
        const pred = new Uint8Array(d.nE);
        const lo = (cent - 1) * 100 + 1;
        const hi = cent * 100;
        for (let j = 0; j < d.nE; j++) {
          const e = d.E[j];
          let b = e.b; let dd = e.d;
          if (b == null && dd == null) continue;
          if (b == null) b = dd - 70;
          if (dd == null) dd = b + 70;
          if (b <= hi && dd >= lo) pred[j] = 1;
        }
        const cnt = eponymCountFn(d, zm)(pred);
        if (cnt) items.push({ value: String(cent), count: cnt, label: `${cent} век` });
      }
      return valueList(c, items);
    }
    case 'ord':
      return valueList(c, [1, 2, 3, 4, 5].map((v) => ({ value: String(v), detail: `${v}-й переулок` })));
    case 'cross':
    case 'near':
    case 'family':
    case 'old':
      if (c.prefix) return [];
      return [{ label: '( … )', insert: '(', detail: key.id === 'near' ? 'подзапрос; или я — от меня' : 'подзапрос', from: c.from, to: c.to }]
        .concat(key.id === 'near' ? [{ label: 'я', insert: 'я', detail: 'от моей геопозиции', from: c.from, to: c.to }] : []);
    case 'houses':
      return valueList(c, [{ value: '>0', detail: 'есть номера' }, { value: '>30', detail: 'больше 30' }, { value: '=0', detail: 'нет номеров' }]);
    case 'len':
      return valueList(c, [{ value: '>1000', detail: 'длиннее 1 км' }, { value: '<100', detail: 'короче 100 м' }]);
    default:
      return [];
  }
}

function houseForms(c) {
  const p = c.prefix;
  const m = /^=?(\d+)/.exec(p);
  const mk = (value, detail) => ({ label: value, insert: value, detail, from: c.from, to: c.to });
  if (!m) {
    const out = [
      mk('есть', 'есть номера домов'), mk('нет', 'нет номеров домов'),
      mk('3', 'номер: 3, 3а, 3/16, 3 корп. 2'),
      mk('=3', 'строго 3'),
      mk('3а', 'с литерой'),
      mk('3/16', 'дробь'),
      mk('1-9', 'диапазон'),
      mk('3?', 'плюс «вероятно есть»'),
    ];
    const pf = fold(p);
    return out.filter((s) => !pf || fold(s.label).startsWith(pf));
  }
  const n = m[1];
  return [
    mk(n, `дом ${n}, ${n}а, ${n}/1, ${n} корп. 2`),
    mk(`=${n}`, `строго ${n}`),
    mk(`${n}а`, `${n} с литерой а`),
    mk(`${n}?`, `${n} или где он вероятен`),
    mk(`${n}-${+n + 10}`, 'диапазон номеров'),
  ].filter((s) => s.insert.startsWith(p) || p.startsWith('=') === s.insert.startsWith('='));
}

function tildeSuggestions(c) {
  const p = fold(c.prefix);
  if (c.key.id === 'cross') {
    return COMPASS_SHOW.filter((v) => v.startsWith(p)).map((v, i) => ({
      label: v, insert: v, detail: `уходит на ${COMPASS_RU[COMPASS_SHOW.indexOf(v)]}`, from: c.from, to: c.to,
    }));
  }
  return ['100', '200', '300', '500', '1000'].filter((v) => v.startsWith(p)).map((v) => ({
    label: `${v} м`, insert: v, detail: v === '200' ? 'по умолчанию' : '', from: c.from, to: c.to,
  }));
}

/** Suggestions at the cursor. Never throws: returns [] when nothing fits. */
export function complete(text, cursor, index, ctx = {}) {
  try {
    const c = contextAt(text, cursor);
    if (c.mode === 'key') return keySuggestions(c);
    if (c.mode === 'value') {
      if (c.inOld && !EPONYM_KEYS.has(c.key.id)) return [];
      return valueSuggestions(c, index, ctx);
    }
    if (c.mode === 'tilde') return tildeSuggestions(c);
    return [];
  } catch (e) {
    log.warn('dsl', `complete failed: ${e && e.message}`, { text, cursor, stack: e && e.stack });
    return [];
  }
}

