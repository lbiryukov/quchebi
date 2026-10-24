// DSL evaluator: AST -> bitset over features (docs/dsl.md "Semantics" and "Output").
//
// evaluate(ast, index, ctx) -> {bits, count, order, explain, warnings, timeMs, error?}
//   ctx = {zone: ZoneMasks | null, me: {lat, lon} | null, mapCenter: {lat, lon} | null}
// run(text, index, ctx) = parse + evaluate.
//
// The zone filter (featDefault / featTouch / featWhole by mode, default = the zone's own rule)
// is applied once, to the final top-level result. Inside relations the zone acts by meaning:
// houses count only when their point is inside the zone polygon, crossings only when their
// point is inside, рядом: uses only the parts of the subquery's geometry inside the zone.
// Subqueries of x:, рядом:, семья: are evaluated without the zone.
import { Bitset, popcount32 } from '../search/bitset.js';
import { skeleton, normRu } from '../search/normalize.js';
import { PointGrid, PreparedPolygon, densify, isClosedRing, makeProjection } from '../zone/geo.js';
import { parse } from './parser.js';
import { explain as explainAst } from './explain.js';
import { closest, plural, damerau } from './keys.js';
import { fold } from './lexer.js';
import { matchHouses } from '../search/houses.js';
import { log } from '../log/log.js';
import {
  dslData, houseRows, housesLoaded, geometry, geometryLoaded, nowMs, bioData, baseData, regexData, tokenData, fuzzyForm,
  letterMask, nameTokens, loanForm, loanData, relBase,
} from './data.js';

class EvalError extends Error {
  constructor(pos, len, message, suggestions = []) {
    super(message);
    this.pos = pos;
    this.len = len;
    this.suggestions = suggestions;
  }
}

const DEFAULT_NEAR_M = 200;
const FUZZY_STREET_MIN = 6; // skeleton length from which exact non-street hits also try fuzzy streets
const SLOW_MS = 50;
const CURRENT_YEAR = new Date().getFullYear();
const EPONYM_SPAN = 70; // assumed lifetime when only one of birth/death is known

// ------------------------------------------------------------------------------ zone helpers

/**
 * Effective zone: {z, on, mode}. mode is 'default' | 'touch' | 'whole' when on, 'off' when the
 * query disabled an existing zone, null when no zone is set. Without зона: in the query (or with
 * зона:вкл) the zone's own rule (ZoneMasks.rule, chosen when it was drawn) applies.
 */
function zoneState(ast, ctx) {
  const z = ctx && ctx.zone ? ctx.zone : null;
  let mode = ast.zone;
  if (!mode || mode === 'on') mode = (z && z.rule) || 'default';
  const on = !!z && mode !== 'off';
  return { z, on, mode: on ? mode : (z ? 'off' : null) };
}

function zoneMask(zs) {
  const z = zs.z;
  if (!zs.on) return null;
  if (zs.mode === 'touch') return z.featTouch || z.featDefault;
  if (zs.mode === 'whole') return z.featWhole || z.featDefault;
  return z.featDefault;
}

function preparedZone(E) {
  if (E.zonePoly === undefined) {
    const z = E.zs.z;
    E.zonePoly = z && z.polygon && z.polygon.length ? (z.prepared || new PreparedPolygon(z.polygon)) : null;
  }
  return E.zonePoly;
}

/** Is house row k of feature f inside the zone polygon? */
function houseInZone(E, f, k, row) {
  const z = E.zs.z;
  if (typeof z.houseIn === 'function') return !!z.houseIn(f, k, row);
  const P = preparedZone(E);
  return P ? P.contains(row[4] / 1e5, row[5] / 1e5) : true;
}

function rowsFor(E, f) {
  const rows = houseRows(E.d, f);
  if (!E.zs.on || !rows.length) return rows;
  const out = [];
  for (let k = 0; k < rows.length; k++) if (houseInZone(E, f, k, rows[k])) out.push(rows[k]);
  return out;
}

// ------------------------------------------------------------------------------ evaluation core

/**
 * Evaluation context for a subquery of x:, рядом:, семья: — "evaluated without the zone": no
 * zone restriction of houses, crossings or lengths inside it (the poet's street may extend
 * outside the zone). The relation itself then applies the zone (crossing point, geometry).
 */
function withoutZone(E) {
  if (!E.noZone) {
    E.noZone = E.sub && !E.zs.on ? E
      : { ...E, zs: E.zs.on ? { z: E.zs.z, on: false, mode: 'off' } : E.zs, noZone: null, sub: true, neg: 0 };
  }
  return E.noZone;
}

/** Relative cost of evaluating a node: cheap set lookups first, geometry last. */
function cost(node) {
  switch (node.type) {
    case 'not': return cost(node.item);
    case 'and': case 'or': return Math.max(...node.items.map(cost));
    case 'text': return node.mode === 'regex' ? 2 : 0;
    case 'filter':
      switch (node.key) {
        case 'near': return 4;
        case 'house': case 'houses': return 2;
        case 'cross': case 'family': case 'old': return 1 + cost(node.sub || { type: 'text' });
        case 'name': return node.items.some((i) => i.mode === 'regex') ? 2 : 1;
        default: return 0;
      }
    default: return 0;
  }
}

/**
 * Evaluate a node. `cand` (Bitset | null) is the set of features the caller still cares about:
 * bits outside it may be wrong, which lets expensive filters (рядом:, дом:, regexes) skip
 * features already excluded by cheaper siblings of an AND, or by the zone at the top level.
 */
function evalNode(E, node, cand = null) {
  switch (node.type) {
    case 'and': {
      const items = node.items.map((it, k) => ({ it, k, c: cost(it) })).sort((a, b) => a.c - b.c || a.k - b.k);
      let acc = null;
      for (const { it } of items) {
        const b = evalNode(E, it, acc || cand);
        acc = acc ? acc.iand(b) : b.clone();
        if (cand && acc.intersects(cand) === false) break;
        if (acc.isEmpty()) break;
      }
      return acc || Bitset.full(E.d.n);
    }
    case 'or': {
      const acc = new Bitset(E.d.n);
      for (const it of node.items) acc.ior(evalNode(E, it, cand));
      return acc;
    }
    case 'not': {
      E.neg++;
      try {
        return evalNode(E, node.item, cand).not();
      } finally {
        E.neg--;
      }
    }
    case 'text':
      return evalText(E, node, cand);
    case 'filter':
      return evalFilter(E, node, cand);
    default:
      throw new EvalError(node.pos || 0, node.len || 0, `неизвестный узел запроса «${node.type}»`);
  }
}

function regexBits(E, re, cand) {
  const d = E.d;
  const n = d.n;
  const b = new Bitset(n);
  const rawFold = regexData(d);
  for (let i = 0; i < n; i++) {
    if (cand && !cand.has(i)) continue;
    const raws = rawFold[i];
    for (let k = 0; k < raws.length; k++) if (re.test(raws[k])) { b.set(i); break; }
  }
  return b;
}

function evalText(E, node, cand) {
  const d = E.d;
  const n = d.n;
  if (node.mode === 'regex') return regexBits(E, node.re, cand);
  const sk = skeleton(node.value);
  if (!sk) {
    E.warn(`слово «${node.value}» пустое после нормализации — пропущено`);
    return Bitset.full(n);
  }
  const key = (node.mode === 'quoted' ? 'q:' : 'w:') + sk;
  let entry = d.textCache.get(key);
  if (!entry) {
    let b = exactText(d, sk);
    let warn = null;
    let extra = null;
    const words = node.mode === 'quoted' ? String(node.value).split(/\s+/).filter(Boolean) : [node.value];
    if (b.isEmpty()) {
      // no exact hit: typo-tolerant match on name word tokens (Latin spellings from addresses)
      const fz = fuzzyWords(d, words);
      if (fz) { b = fz.bits; warn = `нечёткое совпадение: «${node.value}» ≈ ${fz.shown}`; }
    } else if (sk.length >= FUZZY_STREET_MIN && !b.intersects(d.streetish)) {
      // exact hits are only places, bridges, parks...: a misspelt street name must not be lost
      // behind a plaque or a university of the same person ("apollon" vs აპოლონ); short words
      // ("рике") would only bring noise
      const fz = fuzzyWords(d, words, d.streetish);
      if (fz) {
        extra = fz.bits.andNot(b);
        if (extra.isEmpty()) extra = null;
        else { b = b.or(extra); warn = `нечёткое совпадение среди улиц: «${node.value}» ≈ ${fz.shown}`; }
      }
    }
    entry = { bits: b, warn, extra };
    if (d.textCache.size > 300) d.textCache.clear();
    d.textCache.set(key, entry);
  }
  if (entry.extra) {
    // shown only when the fuzzy streets reach the result (or may matter: subquery, negation)
    E.fuzzyNotes.push({ bits: entry.extra, warn: entry.warn, always: !!E.sub || E.neg > 0 });
  } else if (entry.warn) E.warn(entry.warn);
  return entry.bits.clone();
}

function exactText(d, sk) {
  const b = new Bitset(d.n);
  const all = d.allSk;
  for (let i = 0, n = d.n; i < n; i++) if (all[i].includes(sk)) b.set(i);
  return b;
}

/** Allowed edit distance for a fuzzy token of this length: ≤ 1 for 4–5 chars, ≤ 2 from 6. */
function fuzzyBudget(len) {
  return len >= 6 ? 2 : len >= 4 ? 1 : 0;
}

/**
 * Fuzzy match of words (AND over words): each word matches name tokens within the edit budget
 * (words with exact hits keep them). With `restrict` (a Bitset) only those features count, for
 * exact and fuzzy hits alike. Returns {bits, shown} or null when some word has no match.
 */
function fuzzyWords(d, words, restrict = null) {
  let acc = null;
  const shown = [];
  for (const w of words) {
    const sk = skeleton(w);
    if (!sk) continue;
    // a single word is known to have no exact hit (in `restrict`)
    let b = words.length === 1 ? null : exactText(d, sk);
    if (b && restrict) b.iand(restrict);
    if (!b || b.isEmpty()) {
      const hit = fuzzyToken(d, sk, restrict) || (!restrict && /[а-яё]/i.test(w) ? loanToken(d, sk) : null);
      if (!hit) return null;
      b = hit.bits;
      shown.push(...hit.shown);
    }
    acc = acc ? acc.iand(b) : b;
    if (acc.isEmpty()) return null;
  }
  if (!acc || !shown.length) return null;
  return { bits: acc, shown: shown.map((s) => `«${s}»`).join(', ') };
}

/** Name tokens within the edit budget of skeleton sk: {bits, shown: raw words} or null. */
function fuzzyToken(d, sk, restrict) {
  const q = fuzzyForm(sk);
  const max = fuzzyBudget(q.length);
  if (!max) return null;
  const tk = tokenData(d);
  const hits = [];
  const qm = letterMask(q);
  for (let L = q.length - max; L <= q.length + max; L++) {
    const g = tk.byLen.get(L);
    if (!g) continue;
    for (let k = 0; k < g.toks.length; k++) {
      // each edit changes the presence of at most two letters
      if (popcount32(qm ^ g.masks[k]) > 2 * max) continue;
      const t = g.toks[k];
      const dist = damerau(q, t, max);
      if (dist > max) continue;
      const feats = tk.feats.get(t);
      let nf = feats.length;
      if (restrict) {
        nf = 0;
        for (const i of feats) if (restrict.has(i)) nf++;
        if (!nf) continue;
      }
      hits.push({ t, dist, nf });
    }
  }
  if (!hits.length) return null;
  hits.sort((a, c) => a.dist - c.dist || c.nf - a.nf);
  const b = new Bitset(d.n);
  for (const h of hits) for (const i of tk.feats.get(h.t)) if (!restrict || restrict.has(i)) b.set(i);
  return { bits: b, shown: hits.slice(0, 3).map((h) => tk.sample.get(h.t)) };
}

/**
 * A Russian word as the rendering of an English word of a place name ("иллюзий" ~ "Illusions"):
 * its loanword stem (inflection vowels dropped, at least 4 letters) starts a Latin word of an
 * English or alternative place name. {bits, shown} or null.
 */
function loanToken(d, sk) {
  const q = loanForm(sk).replace(/[aeiou]+$/, '');
  if (q.length < 4) return null;
  const lt = loanData(d);
  const hits = lt.toks.filter((t) => t.startsWith(q) && t.length - q.length <= 6);
  if (!hits.length) return null;
  const b = new Bitset(d.n);
  for (const t of hits) for (const i of lt.feats.get(t)) b.set(i);
  hits.sort((a, c) => a.length - c.length);
  return { bits: b, shown: hits.slice(0, 3).map((t) => lt.sample.get(t)) };
}

function evalFilter(E, f, cand) {
  switch (f.key) {
    case 'name': return evalName(E, f, cand);
    case 'type': return evalType(E, f);
    case 'district': return evalArea(E, f, false);
    case 'in': return evalArea(E, f, true);
    case 'who': case 'bio': case 'century': case 'gender': case 'own':
      return featuresOfEponyms(E, eponymPred(E, f));
    case 'house': return evalHouse(E, f, cand);
    case 'houses': return evalNumeric(E, f, (i) => (E.zs.on ? rowsFor(E, i).length : houseCount(E, i)), cand);
    case 'len': return evalNumeric(E, f, (i) => featLen(E, i), null);
    case 'ord': return evalNumeric(E, f, (i) => (E.d.F[i].o == null ? null : E.d.F[i].o), null);
    case 'cross': return evalCross(E, f, cand);
    case 'near': return evalNear(E, f, cand);
    case 'family': return evalFamily(E, f);
    case 'old': return evalOld(E, f);
    default:
      throw new EvalError(f.keyPos, f.keyLen, `ключ «${f.keyRaw}» здесь не поддерживается`);
  }
}

function houseCount(E, i) {
  const rows = houseRows(E.d, i);
  if (rows.length) return rows.length;
  if (!housesLoaded(E.d)) return E.d.F[i].hn || 0;
  return 0;
}

function featLen(E, i) {
  if (E.zs.on && E.zs.z.lenInside) return E.zs.z.lenInside[i];
  return E.d.F[i].len || 0;
}

function cmpNum(v, nm) {
  if (v == null) return false;
  switch (nm.op) {
    case '=': return v === nm.a;
    case '>': return v > nm.a;
    case '<': return v < nm.a;
    case '>=': return v >= nm.a;
    case '<=': return v <= nm.a;
    case 'range': return v >= nm.a && v <= nm.b;
    default: return false;
  }
}

function evalNumeric(E, f, valueOf, cand) {
  const n = E.d.n;
  const b = new Bitset(n);
  if (f.key === 'houses' && !housesLoaded(E.d)) E.warn('номера домов ещё загружаются — считаю по общему числу');
  for (let i = 0; i < n; i++) {
    if (cand && !cand.has(i)) continue;
    const v = valueOf(i);
    for (const nm of f.nums) if (cmpNum(v, nm)) { b.set(i); break; }
  }
  return b;
}

// ---- имя

function evalName(E, f, cand) {
  const d = E.d;
  const n = d.n;
  const out = new Bitset(n);
  for (const it of f.items) {
    if (it.mode === 'regex') { out.ior(regexBits(E, it.re, cand)); continue; }
    const sk = skeleton(it.value);
    if (!sk) { E.warn(`пустое имя «${it.raw}» — пропущено`); continue; }
    const baseSk = it.mode === 'sub' ? null : baseData(d);
    for (let i = 0; i < n; i++) {
      if (out.has(i) || (cand && !cand.has(i))) continue;
      if (nameMatch(it.mode, sk, d.curSk[i], baseSk ? baseSk[i] : [], d.curJoined[i])) out.set(i);
    }
  }
  return out;
}

function nameMatch(mode, sk, cur, base, curJoined) {
  switch (mode) {
    case 'sub': return curJoined.includes(sk);
    case 'prefix':
      for (const s of base) if (s.startsWith(sk)) return true;
      for (const s of cur) if (s.startsWith(sk)) return true;
      return false;
    case 'suffix':
      for (const s of base) if (s.endsWith(sk)) return true;
      for (const s of cur) if (s.endsWith(sk)) return true;
      return false;
    case 'exact':
      for (const s of base) if (s === sk) return true;
      for (const s of cur) if (s === sk) return true;
      return false;
    default: return false;
  }
}

// ---- тип

const KIND_PLURALS = {
  'улицы': 'улица', 'проспекты': 'проспект', 'дороги': 'дорога', 'набережные': 'набережная', 'подъемы': 'подъем',
  'спуски': 'спуск', 'аллеи': 'аллея', 'ряды': 'ряд', 'переулки': 'переулок', 'тупики': 'тупик', 'проезды': 'проезд',
  'площади': 'площадь', 'мосты': 'мост', 'тоннели': 'тоннель', 'балки': 'балка', 'скверы': 'сквер', 'сады': 'сад',
  'парки': 'парк', 'реки': 'река', 'ручьи': 'ручей', 'каналы': 'канал', 'озера': 'озеро', 'станции': 'станция',
  'районы': 'район', 'микрорайоны': 'микрорайон', 'кварталы': 'квартал', 'туннель': 'тоннель',
};

/** Resolve one тип: value to kind indices (or throw with suggestions). */
export function resolveKind(d, item) {
  const kinds = d.kinds;
  let v = fold(item.value).replace(/\*+$/, '');
  if (KIND_PLURALS[v]) v = KIND_PLURALS[v];
  // core.kinds[].ka may list several words ("სკვერი, ბაღი")
  const names = (k) => [k.ru, k.code, ...String(k.ka || '').split(/[,\s]+/), ...(k.syn || [])].filter(Boolean).map(fold);
  let hit = kinds.findIndex((k) => names(k).includes(v));
  let groupHit = null;
  if (hit < 0) {
    const g = kinds.filter((k) => k.group && (fold(k.group) === v || fold(k.group) === v + '*'));
    if (g.length) groupHit = g[0].group;
  }
  if (hit < 0 && !groupHit && v.length >= 3) {
    const pre = kinds.map((k, i) => (names(k).some((nm) => nm.startsWith(v)) ? i : -1)).filter((i) => i >= 0);
    if (pre.length === 1) hit = pre[0];
  }
  if (hit < 0 && !groupHit) {
    const cands = [];
    for (const k of kinds) { cands.push(k.ru); for (const s of k.syn || []) cands.push(s); }
    const sugg = closest(v, cands.filter(Boolean), { limit: 5 });
    throw new EvalError(item.pos, item.len, `неизвестный тип «${item.raw}»${sugg.length ? ` — может быть: ${sugg.join(', ')}?` : ''}`,
      sugg.map((s) => (item.star ? s + '*' : s)));
  }
  if (groupHit || item.star) {
    const group = groupHit || kinds[hit].group;
    if (group) return { kinds: kinds.map((k, i) => (k.group === group ? i : -1)).filter((i) => i >= 0), label: group };
  }
  return { kinds: [hit], label: kinds[hit].ru };
}

function evalType(E, f) {
  const out = new Bitset(E.d.n);
  for (const it of f.items) {
    const r = resolveKind(E.d, it);
    for (const ki of r.kinds) out.ior(E.d.kindBits[ki]);
  }
  return out;
}

// ---- район / в

/** Areas whose names match a value: exact skeleton first, then prefix, then substring. */
export function matchAreas(list, value) {
  const sk = skeleton(value);
  if (!sk) return [];
  const tiers = [[], [], []];
  list.forEach((a, i) => {
    let best = 3;
    for (const nm of [a.ka, a.ru, a.en, ...(a.alt || [])]) {
      if (!nm) continue;
      const s = skeleton(nm);
      const sb = skeleton(String(nm).replace(/\s+(რაიონი|район|district)$/i, ''));
      if (s === sk || sb === sk) best = Math.min(best, 0);
      else if (s.startsWith(sk) || sb.startsWith(sk)) best = Math.min(best, 1);
      else if (s.includes(sk)) best = Math.min(best, 2);
    }
    if (best < 3) tiers[best].push(i);
  });
  return tiers.find((t) => t.length) || [];
}

function evalArea(E, f, inArea) {
  const d = E.d;
  const out = new Bitset(d.n);
  for (const it of f.items) {
    const dist = matchAreas(d.districts, it.value);
    const pl = inArea ? matchAreas(d.places, it.value) : [];
    let used = false;
    if (inArea) {
      // places are more precise than districts; take the better tier of the two
      const plExact = pl.length && skeletonEq(d.places[pl[0]], it.value);
      const diExact = dist.length && skeletonEq(d.districts[dist[0]], it.value);
      const usePl = pl.length && (plExact || !diExact);
      const useDi = dist.length && (diExact || !pl.length);
      if (usePl) { for (const i of pl) out.ior(d.placeBits[i]); used = true; E.label(it, d.places[pl[0]]); }
      if (useDi) { for (const i of dist) out.ior(d.districtBits[i]); used = true; if (!usePl) E.label(it, d.districts[dist[0]]); }
    } else if (dist.length) {
      for (const i of dist) out.ior(d.districtBits[i]);
      used = true;
      E.label(it, d.districts[dist[0]]);
    }
    if (!used) {
      const list = inArea ? [...d.places, ...d.districts] : d.districts;
      const cands = list.map((a) => a.ru || a.en || a.ka).filter(Boolean);
      const sugg = closest(it.value, cands, { limit: 5 });
      const what = inArea ? 'микрорайон или район' : 'район';
      throw new EvalError(it.pos, it.len, `неизвестный ${what} «${it.raw}»${sugg.length ? ` — может быть: ${sugg.join(', ')}?` : ''}`,
        sugg.map((s) => fold(s)));
    }
  }
  return out;
}

function skeletonEq(a, value) {
  const sk = skeleton(value);
  return [a.ka, a.ru, a.en].some((nm) => nm && (skeleton(nm) === sk || skeleton(String(nm).replace(/\s+(რაიონი|район|district)$/i, '')) === sk));
}

// ---- eponyms

/** Resolve one кто: value to an eponym predicate (Uint8Array over eponyms.e). */
export function resolveWho(d, item) {
  const v = normRu(item.value);
  const nE = d.nE;
  const pred = new Uint8Array(nE);
  const byCats = (ids) => {
    for (let j = 0; j < nE; j++) for (const id of ids) if (d.eCats[j].has(id)) { pred[j] = 1; break; }
  };
  if (d.taxIds.has(item.value) || d.taxIds.has(v)) { byCats([d.taxIds.has(item.value) ? item.value : v]); return { pred, how: 'id' }; }
  const ali = d.aliasToIds.get(v);
  if (ali) { byCats([...ali]); return { pred, how: 'alias' }; }
  const ka = d.kindAlias.get(v);
  if (ka) {
    for (let j = 0; j < nE; j++) if (ka.has(d.E[j].k)) pred[j] = 1;
    return { pred, how: 'kind' };
  }
  let any = false;
  for (let j = 0; j < nE; j++) {
    for (const o of d.eOcc[j]) if (o.startsWith(v)) { pred[j] = 1; any = true; break; }
  }
  if (any) return { pred, how: 'occ' };
  const cands = [...d.aliasToIds.keys(), ...d.kindAlias.keys(), ...d.occWords];
  const sugg = closest(v, cands, { limit: 5 });
  throw new EvalError(item.pos, item.len, `неизвестная категория «${item.raw}» для «кто:»${sugg.length ? ` — может быть: ${sugg.join(', ')}?` : ''}`, sugg);
}

/** Years [lo, hi] covered by a век: value (век:19 -> 1801..1900, век:>18 -> 1801..). */
function centuryRange(nm) {
  const lo = nm.a;
  const hi = nm.op === 'range' ? nm.b : nm.a;
  switch (nm.op) {
    case '>': return [nm.a * 100 + 1, 9999];
    case '>=': return [(nm.a - 1) * 100 + 1, 9999];
    case '<': return [-9999, (nm.a - 1) * 100];
    case '<=': return [-9999, nm.a * 100];
    default: return [(lo - 1) * 100 + 1, hi * 100];
  }
}

/** Predicate over eponyms for an eponym filter (кто, био, век, пол, свой). */
function eponymPred(E, f) {
  const d = E.d;
  const nE = d.nE;
  const key = f.key + '|' + (f.items ? f.items.map((i) => i.value + (i.mode || '')).join(',') : f.nums.map((x) => x.op + x.a + '-' + x.b).join(','));
  const hit = E.epCache.get(key);
  if (hit) return hit;
  let pred = new Uint8Array(nE);
  switch (f.key) {
    case 'who':
      for (const it of f.items) {
        const r = resolveWho(d, it);
        for (let j = 0; j < nE; j++) if (r.pred[j]) pred[j] = 1;
      }
      break;
    case 'bio':
      bioData(d);
      for (const it of f.items) {
        if (it.mode === 'regex') {
          for (let j = 0; j < nE; j++) if (!pred[j] && d.eBioRaw[j].some((s) => it.re.test(s))) pred[j] = 1;
          continue;
        }
        const sk = skeleton(it.value);
        if (!sk) continue;
        for (let j = 0; j < nE; j++) if (!pred[j] && d.eBio[j].includes(sk)) pred[j] = 1;
      }
      break;
    case 'century':
      for (let j = 0; j < nE; j++) {
        const e = d.E[j];
        let b = e.b;
        let dd = e.d;
        if (b == null && dd == null) continue;
        if (b == null) b = dd - EPONYM_SPAN;
        if (dd == null) dd = Math.min(b + EPONYM_SPAN, CURRENT_YEAR);
        for (const nm of f.nums) {
          const [lo, hi] = centuryRange(nm);
          if (b <= hi && dd >= lo) { pred[j] = 1; break; }
        }
      }
      break;
    case 'gender': {
      const want = new Set(f.items.map((i) => i.value));
      for (let j = 0; j < nE; j++) if (want.has(d.E[j].g)) pred[j] = 1;
      break;
    }
    case 'own': {
      const want = new Set(f.items.map((i) => i.value));
      for (let j = 0; j < nE; j++) {
        const o = d.E[j].o;
        if (!o || o === 'unknown' || o === '?') continue;
        const isGe = o === 'ge' || o === 'georgian';
        if ((isGe && want.has('yes')) || (!isGe && want.has('no'))) pred[j] = 1;
      }
      break;
    }
    default:
      pred = new Uint8Array(nE);
  }
  E.epCache.set(key, pred);
  return pred;
}

function featuresOfEponyms(E, pred) {
  const d = E.d;
  const b = new Bitset(d.n);
  const eo = d.eponymOf;
  for (let i = 0; i < d.n; i++) { const e = eo[i]; if (e >= 0 && pred[e]) b.set(i); }
  return b;
}

// ---- дом

function evalHouse(E, f, cand) {
  const d = E.d;
  const n = d.n;
  const out = new Bitset(n);
  if (!housesLoaded(d)) E.warn('номера домов ещё загружаются — результат неполный');
  const loaded = housesLoaded(d);
  for (let i = 0; i < n; i++) {
    if (cand && !cand.has(i)) continue;
    const rows = rowsFor(E, i);
    for (const it of f.items) {
      const q = it.q;
      let ok;
      // before houses.json arrives, есть/нет fall back to the per-feature count core.f[].hn
      if (q.kind === 'has') ok = rows.length > 0 || (!loaded && !E.zs.on && (d.F[i].hn || 0) > 0);
      else if (q.kind === 'none') ok = rows.length === 0 && (loaded || E.zs.on || !(d.F[i].hn > 0));
      else ok = rows.length > 0 && matchHouses(rows, q);
      if (ok) { out.set(i); break; }
    }
  }
  return out;
}

// ---- x: (crossings)

function evalCross(E, f, cand) {
  const d = E.d;
  const n = d.n;
  const Q = evalNode(withoutZone(E), f.sub);
  const out = new Bitset(n);
  if (Q.isEmpty()) return out;
  const { offsets, nbr, xi, mask } = d.adj;
  const X = d.X;
  const xd = d.xd;
  const zoneOn = E.zs.on;
  const crossingIn = zoneOn ? E.zs.z.crossingIn : null;
  const bit = f.dir ? 1 << f.dir.bit : 0;
  const haveDir = !!mask || xd.length > 0;
  if (bit && !haveDir) E.warn('нет данных о направлениях улиц — «~» не учтено');
  for (let i = 0; i < n; i++) {
    if (cand && !cand.has(i)) continue;
    for (let k = offsets[i], ke = offsets[i + 1]; k < ke; k++) {
      if (!Q.has(nbr[k])) continue;
      const t = xi[k];
      if (crossingIn && !crossingIn[t]) continue;
      if (bit && haveDir) {
        // direction mask of feature i (not of its neighbour) at crossing t
        const m = mask ? mask[k] : (X[2 * t] === i ? xd[2 * t] : xd[2 * t + 1]);
        if (!(m & bit)) continue;
      }
      out.set(i);
      break;
    }
  }
  return out;
}

// ---- рядом:

function evalNear(E, f, cand) {
  const d = E.d;
  const n = d.n;
  const r = f.radius ? f.radius.m : DEFAULT_NEAR_M;
  if (!geometryLoaded(d)) E.warn('геометрия ещё загружается — расстояния по центрам объектов');
  const grid = new PointGrid(Math.max(50, r));
  let Q = null;
  let qBox = [Infinity, Infinity, -Infinity, -Infinity];
  const addPt = (lat, lon) => {
    grid.add(lat, lon, 0);
    if (lat < qBox[0]) qBox[0] = lat; if (lon < qBox[1]) qBox[1] = lon;
    if (lat > qBox[2]) qBox[2] = lat; if (lon > qBox[3]) qBox[3] = lon;
  };
  if (f.me) {
    const me = E.ctx && E.ctx.me;
    if (!me || me.lat == null || me.lon == null) {
      throw new EvalError(f.pos, f.len, 'нет геолокации — нажмите «Где я»');
    }
    addPt(me.lat, me.lon);
  } else {
    Q = evalNode(withoutZone(E), f.sub);
    const P = E.zs.on ? preparedZone(E) : null;
    const step = Math.max(10, Math.min(50, r / 4));
    Q.forEach((i) => {
      for (const line of geometry(d, i)) {
        const pts = line.length > 1 ? densify(line, step) : line;
        for (const p of pts) if (!P || P.contains(p[0], p[1])) addPt(p[0], p[1]);
      }
    });
  }
  const out = new Bitset(n);
  if (grid.size === 0) return out;
  grid.build();
  const proj = grid.proj;
  // bbox prefilter in e5 degrees
  const dLat = (r / 111195) * 1e5;
  const dLon = dLat / Math.cos((((qBox[0] + qBox[2]) / 2) * Math.PI) / 180);
  const qb = [qBox[0] * 1e5 - dLat, qBox[1] * 1e5 - dLon, qBox[2] * 1e5 + dLat, qBox[3] * 1e5 + dLon];
  const always = () => true;
  const P = Q && E.zs.on ? preparedZone(E) : null;
  for (let i = 0; i < n; i++) {
    if (cand && !cand.has(i)) continue;
    // a subquery feature is at distance 0 from itself (unless the zone cut its geometry away)
    if (Q && Q.has(i) && (!P || E.zs.z.featTouch.has(i))) { out.set(i); continue; }
    const bb = d.F[i].bb;
    if (bb) {
      if (bb[0] > qb[2] || bb[2] < qb[0] || bb[1] > qb[3] || bb[3] < qb[1]) continue;
      // coarse reject: no subquery point at all within r of the feature's bbox
      const [x0, y0] = proj([bb[0] / 1e5, bb[1] / 1e5]);
      const [x1, y1] = proj([bb[2] / 1e5, bb[3] / 1e5]);
      if (!gridAny(grid, x0 - r, y0 - r, x1 + r, y1 + r, always)) continue;
    }
    if (featureNear(grid, proj, geometry(d, i), r)) out.set(i);
  }
  return out;
}

/**
 * Does any grid point inside the box [x0, x1] × [y0, y1] satisfy test(px, py)? Stops at the
 * first hit (PointGrid.forEachWithinXY has no early exit). Uses the built grid's cell arrays.
 */
function gridAny(grid, x0, y0, x1, y1, test) {
  const c = grid.cellM;
  const cx0 = Math.max(0, Math.floor((x0 - grid.minx) / c));
  const cx1 = Math.min(grid.nx - 1, Math.floor((x1 - grid.minx) / c));
  const cy0 = Math.max(0, Math.floor((y0 - grid.miny) / c));
  const cy1 = Math.min(grid.ny - 1, Math.floor((y1 - grid.miny) / c));
  const xs = grid.xs;
  const ys = grid.ys;
  for (let cy = cy0; cy <= cy1; cy++) {
    for (let cx = cx0; cx <= cx1; cx++) {
      const cell = cy * grid.nx + cx;
      for (let k = grid.start[cell], ke = grid.start[cell + 1]; k < ke; k++) {
        if (test(xs[k], ys[k])) return true;
      }
    }
  }
  return false;
}

function featureNear(grid, proj, lines, r) {
  const r2 = r * r;
  for (const line of lines) {
    if (!line.length) continue;
    const pts = line.map(proj);
    if (pts.length === 1) {
      const [x, y] = pts[0];
      if (gridAny(grid, x - r, y - r, x + r, y + r, (px, py) => (px - x) ** 2 + (py - y) ** 2 <= r2)) return true;
      continue;
    }
    for (let s = 0; s + 1 < pts.length; s++) {
      const [ax, ay] = pts[s];
      const [bx, by] = pts[s + 1];
      if (gridAny(grid, Math.min(ax, bx) - r, Math.min(ay, by) - r, Math.max(ax, bx) + r, Math.max(ay, by) + r,
        (px, py) => segDist(px, py, ax, ay, bx, by) <= r)) return true;
    }
    if (isClosedRing(line)) {
      // a subquery point inside an area feature (park, square) is at distance 0
      let minx = Infinity; let maxx = -Infinity; let miny = Infinity; let maxy = -Infinity;
      for (const [x, y] of pts) { if (x < minx) minx = x; if (x > maxx) maxx = x; if (y < miny) miny = y; if (y > maxy) maxy = y; }
      if (gridAny(grid, minx, miny, maxx, maxy, (px, py) => pointInRing(px, py, pts))) return true;
    }
  }
  return false;
}

function segDist(px, py, ax, ay, bx, by) {
  const dx = bx - ax;
  const dy = by - ay;
  const L2 = dx * dx + dy * dy;
  let t = L2 > 0 ? ((px - ax) * dx + (py - ay) * dy) / L2 : 0;
  t = Math.max(0, Math.min(1, t));
  return Math.hypot(ax + dx * t - px, ay + dy * t - py);
}

function pointInRing(x, y, pts) {
  let inside = false;
  for (let i = 0, j = pts.length - 1; i < pts.length; j = i++) {
    const [xi, yi] = pts[i];
    const [xj, yj] = pts[j];
    if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}

// ---- семья:

function evalFamily(E, f) {
  const d = E.d;
  const Q = evalNode(withoutZone(E), f.sub);
  const out = new Bitset(d.n);
  Q.forEach((i) => {
    const fm = d.familyOf[i];
    if (fm >= 0 && d.members[fm]) for (const j of d.members[fm]) out.set(j);
    else out.set(i);
  });
  return out;
}

// ---- было:

function evalOld(E, f) {
  const d = E.d;
  const m = d.old.m;
  const hits = evalOldNode(E, f.sub);
  const out = new Bitset(d.n);
  for (let k = 0; k < m; k++) if (hits[k]) out.set(d.old.f[k]);
  return out;
}

/** Evaluate a было:(…) subtree over old-name entries -> Uint8Array(m). */
function evalOldNode(E, node) {
  const d = E.d;
  const m = d.old.m;
  const res = new Uint8Array(m);
  switch (node.type) {
    case 'and': {
      res.fill(1);
      for (const it of node.items) { const r = evalOldNode(E, it); for (let k = 0; k < m; k++) res[k] &= r[k]; }
      return res;
    }
    case 'or': {
      for (const it of node.items) { const r = evalOldNode(E, it); for (let k = 0; k < m; k++) res[k] |= r[k]; }
      return res;
    }
    case 'not': {
      const r = evalOldNode(E, node.item);
      for (let k = 0; k < m; k++) res[k] = r[k] ? 0 : 1;
      return res;
    }
    case 'text': {
      if (node.mode === 'regex') {
        for (let k = 0; k < m; k++) if (d.old.raw[k].some((s) => node.re.test(s))) res[k] = 1;
        return res;
      }
      const sk = skeleton(node.value);
      if (!sk) { res.fill(1); return res; }
      for (let k = 0; k < m; k++) if (d.old.sk[k].includes(sk)) res[k] = 1;
      return res;
    }
    case 'filter': {
      if (node.key === 'name') {
        for (const it of node.items) {
          if (it.mode === 'regex') { for (let k = 0; k < m; k++) if (d.old.raw[k].some((s) => it.re.test(s))) res[k] = 1; continue; }
          const sk = skeleton(it.value);
          for (let k = 0; k < m; k++) {
            const list = d.old.sk[k] ? d.old.sk[k].split('|') : [];
            if (nameMatch(it.mode, sk, list, [], d.old.sk[k])) res[k] = 1;
          }
        }
        return res;
      }
      const pred = eponymPred(E, node);
      for (let k = 0; k < m; k++) { const e = d.old.e[k]; if (e >= 0 && pred[e]) res[k] = 1; }
      return res;
    }
    default:
      return res;
  }
}

// ------------------------------------------------------------------------------ ordering

/**
 * Text terms that drive relevance: positive bare words / имя: values outside subqueries.
 * `words` collects the bare words in query order (their concatenation is the phrase).
 */
function relevanceTerms(node, out = [], words = null) {
  if (!node) return out;
  if (node.type === 'and' || node.type === 'or') for (const it of node.items) relevanceTerms(it, out, words);
  else if (node.type === 'text' && node.mode !== 'regex') {
    const s = skeleton(node.value);
    if (s) { out.push(s); if (words) words.push(s); }
  } else if (node.type === 'filter' && node.key === 'name') {
    for (const it of node.items) if (it.mode !== 'regex') { const s = skeleton(it.value); if (s) out.push(s); }
  }
  return out;
}

/**
 * Name skeleton s equals query skeleton t, also in the Georgian genitive of a name
 * (ბარათაშვილის = baratashvili + s, პუშკინის = pushkin + is, ტაბიძის = tabidze -> tabidzis) and
 * with the nominative -i of a stem typed without it (вазисубан = vazisubani).
 */
function nameEq(s, t) {
  if (s === t) return true;
  const dl = s.length - t.length;
  if (dl === 1) return s.startsWith(t) ? s.endsWith('s') || s.endsWith('i') : (/[aeou]$/.test(t) && s === t.slice(0, -1) + 'is');
  if (dl === 2) return s === t + 'is';
  return false;
}

/**
 * Score of one term for feature i: 4 a whole (base) name, 3 a word of a name, 2 the start of a
 * name or of a word, 1 inside a current name, 0 only in old names.
 */
function termScore(d, base, i, t) {
  let s = 0;
  for (const b of base) { if (nameEq(b, t)) return 4; if (b.startsWith(t)) s = 2; }
  for (const c of d.curSk[i]) { if (nameEq(c, t)) return 4; if (c.startsWith(t)) s = 2; }
  for (const w of nameTokens(d, i)) { if (nameEq(w, t)) return 3; if (!s && w.startsWith(t)) s = 2; }
  if (s) return s;
  return d.curJoined[i].includes(t) ? 1 : 0;
}

/** Score of the phrase (bare words joined): 3 a whole (base) name, 2 its start, 1 inside, 0 none. */
function phraseScore(d, base, i, p) {
  let s = 0;
  for (const b of base) { if (nameEq(b, p)) return 3; if (b.startsWith(p)) s = 2; }
  for (const c of d.curSk[i]) { if (nameEq(c, p)) return 3; if (c.startsWith(p)) s = 2; }
  if (s) return s;
  return d.curJoined[i].includes(p) ? 1 : 0;
}

/** Relevance of feature i: the phrase match dominates, then the sum of the term scores. */
function relevance(d, i, terms, phrase) {
  const base = relBase(d, i);
  let r = phrase ? 100 * phraseScore(d, base, i, phrase) : 0;
  for (const t of terms) r += termScore(d, base, i, t);
  return r;
}

function distanceTo(d, i, p, proj) {
  let best = Infinity;
  const [px, py] = proj([p.lat, p.lon]);
  for (const line of geometry(d, i)) {
    const pts = line.map(proj);
    if (pts.length === 1) best = Math.min(best, Math.hypot(pts[0][0] - px, pts[0][1] - py));
    for (let s = 0; s + 1 < pts.length; s++) best = Math.min(best, segDist(px, py, pts[s][0], pts[s][1], pts[s + 1][0], pts[s + 1][1]));
    if (isClosedRing(line) && pointInRing(px, py, pts)) best = 0;
  }
  return best;
}

function orderResults(E, ast, bits) {
  const d = E.d;
  const idx = bits.toArray();
  const rank = d.nameRank;
  let mode = ast.sort;
  const words = [];
  const terms = relevanceTerms(ast.expr, [], words);
  const implicit = !mode;
  if (!mode || mode === 'rel') mode = terms.length ? 'rel' : 'name';
  const byName = (a, b) => rank[a] - rank[b];
  if (mode === 'name') {
    // without сорт:имя places go after streets and other objects (then by name)
    if (implicit) { const pl = d.isPlace; idx.sort((a, b) => pl[a] - pl[b] || rank[a] - rank[b]); } else idx.sort(byName);
    return idx;
  }
  const key = new Float64Array(d.n);
  if (mode === 'rel') {
    // equal relevance: streets, lanes, squares; other objects; numbered lanes; places; minor places
    const phrase = words.length > 1 ? words.join('') : null;
    const tier = d.tier;
    for (const i of idx) key[i] = -relevance(d, i, terms, phrase) * 8 + tier[i];
  } else if (mode === 'len') {
    for (const i of idx) key[i] = -featLen(E, i);
  } else if (mode === 'houses') {
    for (const i of idx) key[i] = -(E.zs.on ? rowsFor(E, i).length : houseCount(E, i));
  } else if (mode === 'dist') {
    const p = (E.ctx && E.ctx.me) || (E.ctx && E.ctx.mapCenter);
    if (!p) {
      E.warn('нет точки для расстояния — нажмите «Где я» или откройте карту; порядок по имени');
      idx.sort(byName);
      return idx;
    }
    const proj = makeProjection(p.lat, p.lon);
    for (const i of idx) key[i] = distanceTo(d, i, p, proj);
  }
  idx.sort((a, b) => key[a] - key[b] || rank[a] - rank[b]);
  return idx;
}

// ------------------------------------------------------------------------------ public API

function emptyResult(n) {
  return new Bitset(n);
}

/**
 * Evaluate a parsed query (root from parse().ast, or any node) over an index.
 * Returns {bits, count, order, explain, warnings, timeMs}; on value-resolution errors also
 * `error: {pos, len, message, suggestions}` with an empty result.
 */
export function evaluate(ast, index, ctx = {}) {
  const t0 = nowMs();
  const root = !ast ? { type: 'query', expr: null, zone: null, sort: null, warnings: [] }
    : ast.type === 'query' ? ast : { type: 'query', expr: ast, zone: null, sort: null, warnings: [] };
  const d = dslData(index);
  const warnings = [...(root.warnings || [])];
  const labels = new Map();
  const zs = zoneState(root, ctx || {});
  const E = {
    d, ctx: ctx || {}, zs, epCache: new Map(), zonePoly: undefined, sub: false, neg: 0, fuzzyNotes: [],
    warn(msg) { if (!warnings.includes(msg)) warnings.push(msg); },
    label(item, area) { if (area) labels.set(item, area.ru || area.en || area.ka); },
  };
  if (root.zone === 'off' && ctx && ctx.zone) E.warn('зона выключена');
  if (root.zone && root.zone !== 'off' && !(ctx && ctx.zone)) E.warn('зона не задана — ищу по всему городу');
  if (zs.on && zs.z.withGeom === false && geometryLoaded(d)) E.warn('зона посчитана до загрузки геометрии — пересчитайте её');
  let bits;
  try {
    // the zone mask is the candidate set of the whole query: it is applied at the end anyway
    bits = root.expr ? evalNode(E, root.expr, zoneMask(zs)) : Bitset.full(d.n);
  } catch (e) {
    if (!(e instanceof EvalError)) {
      log.error('dsl', `evaluate failed: ${e && e.message}`, { query: root.text, stack: e && e.stack });
      throw e;
    }
    const bitsE = emptyResult(d.n);
    return {
      bits: bitsE, count: 0, order: new Int32Array(0),
      explain: safeExplain(root, zs, labels), warnings,
      timeMs: nowMs() - t0,
      error: { pos: e.pos, len: e.len, message: e.message, suggestions: e.suggestions || [] },
    };
  }
  const zm = zoneMask(zs);
  if (zm) bits = bits.iand(zm);
  for (const note of E.fuzzyNotes) if (note.always || bits.intersects(note.bits)) E.warn(note.warn);
  const order = orderResults(E, root, bits);
  const timeMs = nowMs() - t0;
  if (timeMs > SLOW_MS) log.warn('dsl', `slow query ${timeMs.toFixed(1)} ms`, { query: root.text, count: order.length });
  return {
    bits,
    count: order.length,
    order,
    explain: safeExplain(root, zs, labels),
    warnings,
    timeMs,
  };
}

/**
 * Build the DSL caches for an index ahead of the first query (e.g. on idle after loading).
 * With {all: true} also the lazy parts: base names (relevance, имя:^…), folded names (regexes),
 * biographies (био:), the fuzzy token index and the per-feature relevance names (own base
 * names, name words). Returns the time spent, ms.
 */
export function prepare(index, { all = false } = {}) {
  const t0 = nowMs();
  const d = dslData(index);
  if (all) {
    baseData(d); regexData(d); bioData(d); tokenData(d);
    // relevance caches: own base names and name words of every feature
    for (let i = 0; i < d.n; i++) { relBase(d, i); nameTokens(d, i); }
  }
  return nowMs() - t0;
}

function safeExplain(root, zs, labels) {
  try {
    return explainAst(root, { zone: zs.mode, labels });
  } catch {
    return '';
  }
}

/** Parse and evaluate a query line. Parse errors come back as {error, count: 0, ...}. */
export function run(text, index, ctx = {}) {
  const t0 = nowMs();
  const p = parse(text);
  if (p.error) {
    return { bits: new Bitset(index.n ?? dslData(index).n), count: 0, order: new Int32Array(0), explain: '', warnings: [], timeMs: nowMs() - t0, error: p.error };
  }
  return evaluate(p.ast, index, ctx);
}

/** "41 объект" — count with an agreed noun, for the result line. */
export function countText(n) {
  return `${n} ${plural(n, ['объект', 'объекта', 'объектов'])}`;
}

export { EvalError };
