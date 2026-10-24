// DSL-side caches over an Index (search/index.js) and its pack: skeletons per name variant,
// eponym attributes, old-name entries, adjacency, name order. Built lazily once per index and
// kept in a WeakMap, so the evaluator stays independent of optional Index fields: anything the
// Index already has (kindBits, districtBits, adj, geometry(), houses(), ...) is used as is,
// anything missing is derived from index.pack.
import { Bitset } from '../search/bitset.js';
import { skeleton, stripType, normRu } from '../search/normalize.js';
import { decodePolyline } from '../zone/geo.js';
import { fold } from './lexer.js';
import { log } from '../log/log.js';

const CACHE = new WeakMap();

/** DSL data for an index (built on first use). */
export function dslData(index) {
  let d = CACHE.get(index);
  if (!d) {
    d = buildData(index);
    CACHE.set(index, d);
  }
  return d;
}

function asArray(x) { return Array.isArray(x) ? x : x == null ? [] : [x]; }

function buildData(index) {
  const t0 = nowMs();
  const pack = index.pack || {};
  const core = pack.core || {};
  const F = core.f || [];
  const n = index.n ?? F.length;
  const kinds = core.kinds || [];
  const fam = core.fam || [];
  const ep = pack.eponyms || { e: [], taxonomy: [], kinds: {} };
  const E = ep.e || [];

  // core.kinds[].ka may list several words ("ეკლესია, ტაძარი, მონასტერი")
  const kindTypeWords = [];
  for (const k of kinds) {
    if (k.ka) kindTypeWords.push(...String(k.ka).split(/[,\s]+/).filter(Boolean));
    if (k.ru) kindTypeWords.push(k.ru);
    for (const s of k.syn || []) kindTypeWords.push(s);
  }

  // ---- kinds: ranking tiers (see TIER) and the street-like set of the fuzzy street fallback
  const kindOf = new Int16Array(n);
  for (let i = 0; i < n; i++) kindOf[i] = F[i] && F[i].k != null ? F[i].k : -1;
  const isPlace = new Uint8Array(n);
  const tier = new Int8Array(n);
  const streetish = new Bitset(n);
  for (let i = 0; i < n; i++) {
    const k = kinds[kindOf[i]];
    const g = k ? k.group : null;
    if (g === PLACE_GROUP) {
      isPlace[i] = 1;
      tier[i] = MINOR_PLACE_KINDS.has(k.code) ? TIER.minorPlace : TIER.place;
      continue;
    }
    const street = g === 'street*' || g === 'lane*' || (k && k.code === 'square');
    if (street) streetish.set(i);
    // numbered lanes and the lanes / dead ends of a street (par) come after the main objects
    tier[i] = F[i] && (F[i].o || F[i].par != null) ? TIER.ordinal : street ? TIER.street : TIER.other;
  }

  // ---- names (base names and folded raw names are built lazily: baseData(), regexData())
  const curSk = new Array(n); // skeletons of current names (ka, ru, en, alt, Russian kind name of a place)
  const allSk = new Array(n); // joined '|' of current + old (bare-word text search)
  const curJoined = new Array(n);
  const ruKindName = new Array(n); // places without a real Russian name: "музей Museum of Illusions"
  let nRuKind = 0;
  const oldF = [];
  const oldE = [];
  const oldSk = [];
  const oldRaw = [];
  const ixName = Array.isArray(index.nameSkel) && index.nameSkel.length === n ? index.nameSkel : null;
  const ixSkel = Array.isArray(index.skel) && index.skel.length === n ? index.skel : null;
  for (let i = 0; i < n; i++) {
    const f = F[i] || {};
    let cs;
    if (ixName) cs = ixName[i].filter(Boolean);
    else cs = uniq([f.ka, f.ru, f.en, ...asArray(f.alt)].filter(Boolean).map(skeleton).filter(Boolean));
    let extraSk = '';
    if (isPlace[i]) {
      const rk = placeRuKindName(f, kinds[kindOf[i]]);
      if (rk) {
        ruKindName[i] = rk;
        nRuKind++;
        extraSk = skeleton(rk);
        if (extraSk && !cs.includes(extraSk)) cs = [...cs, extraSk];
        else extraSk = '';
      }
    }
    curSk[i] = cs;
    curJoined[i] = cs.join('|');
    const olds = [];
    for (const o of asArray(f.old)) {
      const sks = uniq([o.ka, o.ru, o.lat].filter(Boolean).map(skeleton).filter(Boolean));
      oldF.push(i);
      oldE.push(o.e == null ? -1 : o.e);
      oldSk.push(sks.join('|'));
      oldRaw.push([o.ka, o.ru].filter(Boolean).map(fold));
      olds.push(...sks);
    }
    if (ixSkel) allSk[i] = extraSk ? ixSkel[i] + '|' + extraSk : ixSkel[i];
    else allSk[i] = olds.length ? curJoined[i] + '|' + olds.join('|') : curJoined[i];
  }

  // ---- kinds, districts, places
  const kindBits = kinds.map((k, ki) => {
    const fromIndex = index.kindBits && index.kindBits.get ? index.kindBits.get(k.code) : null;
    if (fromIndex instanceof Bitset) return fromIndex;
    const b = new Bitset(n);
    for (let i = 0; i < n; i++) if (kindOf[i] === ki) b.set(i);
    return b;
  });
  const districts = core.districts || [];
  const places = core.places || [];
  const districtBits = areaBits(index.districtBits, districts.length, n, F, 'd');
  const placeBits = areaBits(index.placeBits, places.length, n, F, 'p');

  // ---- families and eponyms
  let familyOf = index.familyOf;
  if (!(familyOf instanceof Int32Array) || familyOf.length !== n) {
    familyOf = new Int32Array(n).fill(-1);
    for (let i = 0; i < n; i++) if (F[i] && F[i].fam != null) familyOf[i] = F[i].fam;
  }
  let members = index.membersOfFamily;
  if (!Array.isArray(members) || members.length !== fam.length) {
    const lists = fam.map(() => []);
    for (let i = 0; i < n; i++) if (familyOf[i] >= 0 && lists[familyOf[i]]) lists[familyOf[i]].push(i);
    members = lists.map((l) => Int32Array.from(l));
  }
  let eponymOf = index.eponymOf;
  if (!(eponymOf instanceof Int32Array) || eponymOf.length !== n) {
    eponymOf = new Int32Array(n).fill(-1);
    for (let i = 0; i < n; i++) {
      const fm = familyOf[i] >= 0 ? fam[familyOf[i]] : null;
      if (fm && fm.e != null) eponymOf[i] = fm.e;
    }
  }

  // taxonomy: id -> node, parent; aliases -> ids
  const taxIds = new Set();
  const parentOf = new Map();
  const aliasToIds = new Map();
  const taxLabels = []; // {label, id}
  const addAlias = (a, id) => {
    const k = normRu(a);
    if (!k) return;
    if (!aliasToIds.has(k)) aliasToIds.set(k, new Set());
    aliasToIds.get(k).add(id);
  };
  const visit = (node, parent) => {
    taxIds.add(node.id);
    if (parent) parentOf.set(node.id, parent.id);
    addAlias(node.ru, node.id);
    for (const a of node.aliases || []) addAlias(a, node.id);
    taxLabels.push({ id: node.id, label: normRu(node.ru || node.id), group: !parent });
    for (const ch of node.children || []) visit(ch, node);
  };
  for (const root of ep.taxonomy || []) visit(root, null);
  const childrenOf = new Map();
  for (const [c, p] of parentOf) { if (!childrenOf.has(p)) childrenOf.set(p, []); childrenOf.get(p).push(c); }

  const nE = E.length;
  const eCats = new Array(nE); // expanded categories (with ancestors)
  const eOcc = new Array(nE);
  for (let j = 0; j < nE; j++) {
    const e = E[j] || {};
    const cats = new Set();
    for (const c of asArray(e.cat)) {
      let cur = c;
      while (cur) {
        if (cats.has(cur)) break;
        cats.add(cur);
        cur = parentOf.get(cur) || (cur.includes('.') ? cur.slice(0, cur.lastIndexOf('.')) : null);
      }
    }
    eCats[j] = cats;
    eOcc[j] = asArray(e.occ).map(normRu);
  }
  // kind aliases (eponyms.kinds: code -> aliases)
  const kindAlias = new Map();
  for (const [code, aliases] of Object.entries(ep.kinds || {})) {
    for (const a of [code, ...asArray(aliases)]) {
      const k = normRu(a);
      if (!kindAlias.has(k)) kindAlias.set(k, new Set());
      kindAlias.get(k).add(code);
      if (code === 'person') kindAlias.get(k).add('persons_multi');
    }
  }
  const occWords = uniq(eOcc.flat()).sort();

  // ---- crossings
  let adj = index.adj;
  const X = core.x || [];
  if (!adj || !adj.offsets || adj.offsets.length !== n + 1) adj = buildAdj(n, X);
  const xd = core.xd || [];

  // ---- name order: folded ru name (else ka, en), digit runs compared as numbers, then ordinal.
  // Code-point order of folded Cyrillic and Mkhedruli is alphabetical, and it is ~10x faster
  // than Intl.Collator on 5 600 names (matters on phones).
  const ixSort = Array.isArray(index.sortKey) && index.sortKey.length === n ? index.sortKey : null;
  const sortKey = new Array(n);
  for (let i = 0; i < n; i++) {
    const f = F[i] || {};
    const k = ixSort ? ixSort[i] : fold(f.ru || f.ka || f.en || '');
    sortKey[i] = /\d/.test(k) ? k.replace(/\d+/g, (m) => m.padStart(6, '0')) : k;
  }
  const ordIdx = Array.from({ length: n }, (_, i) => i);
  ordIdx.sort((a, b) => (sortKey[a] < sortKey[b] ? -1 : sortKey[a] > sortKey[b] ? 1 : ((F[a].o || 0) - (F[b].o || 0)) || a - b));
  const nameRank = new Int32Array(n);
  ordIdx.forEach((f, r) => { nameRank[f] = r; });

  const d = {
    index, pack, core, F, n, kinds, fam, E, nE, districts, places,
    curSk, curJoined, allSk, baseSk: null, rawFold: null, kindTypeWords, ruKindName, nameTok: null, loanTok: null,
    old: { f: Int32Array.from(oldF), e: Int32Array.from(oldE), sk: oldSk, raw: oldRaw, m: oldF.length },
    kindOf, kindBits, districtBits, placeBits, isPlace, tier, streetish,
    familyOf, members, eponymOf,
    taxIds, parentOf, childrenOf, aliasToIds, taxLabels, eCats, eOcc, eBio: null, eBioRaw: null, kindAlias, occWords,
    adj, X, xd, nameRank,
    textCache: new Map(),
    geomCache: new Map(),
    buildMs: 0,
  };
  d.buildMs = nowMs() - t0;
  log.info('dsl', `caches built: ${n} features, ${nE} eponyms, ${oldF.length} old names, ` +
    `${nRuKind} Russian kind names of places in ${d.buildMs.toFixed(1)} ms`);
  return d;
}

// ------------------------------------------------------------------------------ places

const PLACE_GROUP = 'place*';

/**
 * Ranking tiers at equal text relevance (eval.js orderResults): streets, lanes and squares first,
 * then other non-place objects (bridges, parks, rivers, areas), then the lanes and dead ends of a
 * street (numbered or with par), then the main places (museums, theatres, churches, monuments,
 * ...), last the minor ones.
 */
export const TIER = Object.freeze({ street: 0, other: 1, ordinal: 2, place: 3, minorPlace: 4 });

/** Place kinds ranked below the main places: memorial plaques, schools, university buildings, housing. */
export const MINOR_PLACE_KINDS = new Set(['plaque', 'school', 'university', 'residential', 'factory']);

/**
 * Russian words for the kind keywords of English place names. A place without a real Russian
 * name (no name:ru, or the pack's ru is only a transliteration of ka) gets a search-only name
 * "<Russian kind words> <en>", so "музей" finds "Museum of Illusions".
 */
const EN_KIND_RU = {
  museum: 'музей', gallery: 'галерея', theatre: 'театр', theater: 'театр', opera: 'опера', philharmonic: 'филармония',
  circus: 'цирк', cinema: 'кинотеатр', monument: 'памятник', memorial: 'мемориал', statue: 'статуя',
  sculpture: 'скульптура', bust: 'бюст', plaque: 'доска', church: 'церковь', cathedral: 'собор', temple: 'храм',
  monastery: 'монастырь', convent: 'монастырь', basilica: 'базилика', chapel: 'часовня', mosque: 'мечеть',
  synagogue: 'синагога', fortress: 'крепость', castle: 'замок', citadel: 'цитадель', palace: 'дворец',
  bath: 'баня', baths: 'бани', stadium: 'стадион', arena: 'арена', mall: 'торгцентр', market: 'рынок',
  bazaar: 'базар', station: 'вокзал', university: 'университет', academy: 'академия', institute: 'институт',
  conservatory: 'консерватория', conservatoire: 'консерватория', college: 'колледж', school: 'школа',
  lyceum: 'лицей', gymnasium: 'гимназия', library: 'библиотека', cemetery: 'кладбище', pantheon: 'пантеон',
  factory: 'завод', plant: 'завод', tower: 'башня',
};

/** Search-only Russian name of a place without a real Russian name, or null. */
export function placeRuKindName(f, kind) {
  if (!kind || kind.group !== PLACE_GROUP || !f || !f.en) return null;
  if (f.ru && skeleton(f.ru) !== skeleton(f.ka)) return null; // a real Russian name
  const words = [];
  for (const w of String(f.en).toLowerCase().split(/[^a-z]+/)) {
    const ru = EN_KIND_RU[w];
    if (ru && !words.includes(ru)) words.push(ru);
  }
  if (!words.length && kind.ru && kind.code !== 'landmark') words.push(kind.ru);
  return words.length ? `${words.join(' ')} ${f.en}` : null;
}

const TOKEN_SPLIT = /[\s\-–—,.«»"“”„()/№#'’]+/;

/**
 * Base-name skeletons of feature i for relevance: the family base (baseData) plus the feature's
 * own names without kind word and ordinal (a family base can come from another member, e.g. the
 * "... მარცხენა" embankment of the Baratashvili family). Built per feature on first use.
 */
export function relBase(d, i) {
  if (!d.relBaseSk) d.relBaseSk = new Array(d.n);
  let r = d.relBaseSk[i];
  if (!r) {
    const base = baseData(d)[i];
    const f = d.F[i] || {};
    const set = new Set(base);
    for (const nm of [f.ka, f.ru, f.en, ...asArray(f.alt)]) {
      if (!nm) continue;
      const s = skeleton(stripType(nm, d.kindTypeWords));
      if (s) set.add(s);
    }
    r = set.size === base.length ? base : [...set];
    d.relBaseSk[i] = r;
  }
  return r;
}

/** Skeletons of the words of all current names of feature i (relevance: a word equals a name word). */
export function nameTokens(d, i) {
  if (!d.nameTok) d.nameTok = new Array(d.n);
  let t = d.nameTok[i];
  if (!t) {
    const f = d.F[i] || {};
    const set = new Set();
    for (const nm of [f.ka, f.ru, f.en, ...asArray(f.alt), d.ruKindName[i]]) {
      if (!nm) continue;
      for (const w of String(nm).split(TOKEN_SPLIT)) {
        const s = skeleton(w);
        if (s.length >= 2) set.add(s);
      }
    }
    t = [...set];
    d.nameTok[i] = t;
  }
  return t;
}

/**
 * "Loanword" normal form of a skeleton, for Russian renderings of English words in place names:
 * x = ks, ю (iu) = u, з (z) = s, doubled letters collapsed ("иллюзий" ~ "Illusions").
 */
export function loanForm(sk) {
  return fuzzyForm(sk).replace(/iu/g, 'u').replace(/z/g, 's').replace(/(.)\1+/g, '$1');
}

/**
 * Latin words of the English / alternative names of places in loanword form:
 * {toks: string[], feats: Map<token, Int32Array>, sample: Map<token, raw word>}, built on first use.
 */
export function loanData(d) {
  if (!d.loanTok) {
    const t0 = nowMs();
    const feats = new Map();
    const sample = new Map();
    for (let i = 0; i < d.n; i++) {
      if (!d.isPlace[i]) continue;
      const f = d.F[i] || {};
      for (const nm of [f.en, ...asArray(f.alt)]) {
        if (!nm || /[Ѐ-ӿႠ-ჿᲐ-Ჿ]/.test(nm)) continue; // Latin-script names only
        for (const w of String(nm).split(TOKEN_SPLIT)) {
          const s = loanForm(skeleton(w));
          if (s.length < 4) continue;
          let list = feats.get(s);
          if (!list) { list = []; feats.set(s, list); sample.set(s, w); }
          if (list[list.length - 1] !== i) list.push(i);
        }
      }
    }
    for (const [s, list] of feats) feats.set(s, Int32Array.from(list));
    d.loanTok = { toks: [...feats.keys()], feats, sample };
    log.info('dsl', `loanword index: ${feats.size} tokens in ${(nowMs() - t0).toFixed(1)} ms`);
  }
  return d.loanTok;
}

function areaBits(fromIndex, count, n, F, field) {
  if (Array.isArray(fromIndex) && fromIndex.length === count && fromIndex.every((b) => b instanceof Bitset)) return fromIndex;
  const out = Array.from({ length: count }, () => new Bitset(n));
  for (let i = 0; i < n; i++) for (const a of asArray(F[i] && F[i][field])) if (out[a]) out[a].set(i);
  return out;
}

function buildAdj(n, X) {
  const deg = new Int32Array(n + 1);
  const m = X.length >> 1;
  for (let t = 0; t < m; t++) { deg[X[2 * t]]++; deg[X[2 * t + 1]]++; }
  const offsets = new Int32Array(n + 1);
  for (let i = 0; i < n; i++) offsets[i + 1] = offsets[i] + deg[i];
  const fill = offsets.slice(0, n);
  const nbr = new Int32Array(2 * m);
  const xi = new Int32Array(2 * m);
  for (let t = 0; t < m; t++) {
    const a = X[2 * t];
    const b = X[2 * t + 1];
    nbr[fill[a]] = b; xi[fill[a]++] = t;
    nbr[fill[b]] = a; xi[fill[b]++] = t;
  }
  return { offsets, nbr, xi };
}

function uniq(arr) { return [...new Set(arr)]; }

export function nowMs() {
  return typeof performance !== 'undefined' && performance.now ? performance.now() : Date.now();
}

/**
 * Base-name skeletons per feature (name without the kind word and the ordinal, plus the family
 * base name), for имя:^/$ and relevance. Built on first use: parseName over all names is the
 * most expensive part of the caches.
 */
export function baseData(d) {
  if (!d.baseSk) {
    // A family's base name (core.fam[].ka/ru/en) is the name without kind word and ordinal, so
    // features with a family need no per-name parsing; the rest are parsed with stripType().
    const { F, n, fam } = d;
    const famSk = new Array(fam.length);
    const baseSk = new Array(n);
    for (let i = 0; i < n; i++) {
      const f = F[i] || {};
      const fm = f.fam != null ? fam[f.fam] : null;
      if (fm) {
        if (!famSk[f.fam]) famSk[f.fam] = uniq([fm.ka, fm.ru, fm.en].filter(Boolean).map(skeleton).filter(Boolean));
        baseSk[i] = famSk[f.fam];
      } else {
        baseSk[i] = uniq([f.ka, f.ru, f.en].filter(Boolean).map((nm) => skeleton(stripType(nm, d.kindTypeWords))).filter(Boolean));
      }
    }
    d.baseSk = baseSk;
  }
  return d.baseSk;
}

/** Folded raw ka/ru names and old ka/ru names per feature (regex queries), built on first use. */
export function regexData(d) {
  if (!d.rawFold) {
    const rawFold = new Array(d.n);
    for (let i = 0; i < d.n; i++) {
      const f = d.F[i] || {};
      const raws = [f.ka, f.ru].filter(Boolean).map(fold);
      for (const o of asArray(f.old)) { if (o.ka) raws.push(fold(o.ka)); if (o.ru) raws.push(fold(o.ru)); }
      rawFold[i] = raws;
    }
    d.rawFold = rawFold;
  }
  return d.rawFold;
}

/** Bit set of the letters a-z and digits (folded into 32 bits) present in a skeleton. */
export function letterMask(s) {
  let m = 0;
  for (let k = 0; k < s.length; k++) {
    const c = s.charCodeAt(k);
    m |= 1 << ((c >= 97 ? c - 97 : c - 48 + 26) & 31);
  }
  return m;
}

/** Skeleton normal form for fuzzy token comparison: x is spelled ks in Georgian/Russian names. */
export function fuzzyForm(sk) {
  return sk.replace(/x/g, 'ks');
}

/**
 * Word tokens of all name variants (current and old) for fuzzy fallback of bare text:
 * {byLen: Map<length, string[]>, feats: Map<token, Int32Array>, sample: Map<token, raw word>}.
 * Built on the first bare word without exact hits; distinct raw words are skeletonized once.
 */
export function tokenData(d) {
  if (!d.tokens) {
    const t0 = nowMs();
    const wordTok = new Map(); // raw word -> token ('' if too short)
    const feats = new Map();
    const sample = new Map();
    const lastFeat = new Map(); // token -> last feature index added (dedupe within a feature)
    const SPLIT = /[\s\-–—,.«»"“”„()/]+/;
    const addName = (nm, i) => {
      for (const w of String(nm).split(SPLIT)) {
        if (!w) continue;
        let tok = wordTok.get(w);
        if (tok === undefined) {
          const sk = fuzzyForm(skeleton(w));
          tok = sk.length >= 3 && !/^\d+$/.test(sk) ? sk : '';
          wordTok.set(w, tok);
        }
        if (!tok || lastFeat.get(tok) === i) continue;
        lastFeat.set(tok, i);
        let list = feats.get(tok);
        if (!list) { list = []; feats.set(tok, list); sample.set(tok, w); }
        list.push(i);
      }
    };
    for (let i = 0; i < d.n; i++) {
      const f = d.F[i] || {};
      if (f.ka) addName(f.ka, i);
      if (f.ru) addName(f.ru, i);
      if (f.en) addName(f.en, i);
      if (f.alt) for (const a of f.alt) if (a) addName(a, i);
      if (f.old) for (const o of f.old) { if (o.ka) addName(o.ka, i); if (o.ru) addName(o.ru, i); if (o.lat) addName(o.lat, i); }
    }
    const groups = new Map();
    for (const [tok, list] of feats) {
      feats.set(tok, Int32Array.from(list));
      if (!groups.has(tok.length)) groups.set(tok.length, []);
      groups.get(tok.length).push(tok);
    }
    // per length: tokens + letter-presence masks (a cheap lower bound before the edit distance)
    const byLen = new Map();
    for (const [len, toks] of groups) byLen.set(len, { toks, masks: Int32Array.from(toks, letterMask) });
    d.tokens = { byLen, feats, sample };
    log.info('dsl', `fuzzy token index: ${feats.size} tokens from ${wordTok.size} words in ${(nowMs() - t0).toFixed(1)} ms`);
  }
  return d.tokens;
}

/** Biography skeletons (desc, occupations, names) per eponym, built on the first био: query. */
export function bioData(d) {
  if (!d.eBio) {
    const eBio = new Array(d.nE);
    const eBioRaw = new Array(d.nE);
    for (let j = 0; j < d.nE; j++) {
      const e = d.E[j] || {};
      const segs = [e.desc, ...asArray(e.occ), e.ka, e.ru, e.en].filter(Boolean);
      eBio[j] = segs.map(skeleton).filter(Boolean).join('|');
      eBioRaw[j] = segs.map(fold);
    }
    d.eBio = eBio;
    d.eBioRaw = eBioRaw;
  }
  return d;
}

/** House rows of feature i (empty array when houses are not loaded or there are none). */
export function houseRows(d, i) {
  if (typeof d.index.houses === 'function') {
    const r = d.index.houses(i);
    if (r) return r;
  }
  const h = d.pack.houses && d.pack.houses.h;
  return (h && h[String(i)]) || [];
}

/** True when house numbers are available (houses.json loaded). */
export function housesLoaded(d) {
  if (d.index.hasHouses != null) return !!d.index.hasHouses;
  return !!(d.pack.houses && d.pack.houses.h);
}

/** True when geometries are available (geom.json loaded). */
export function geometryLoaded(d) {
  if (d.index.hasGeom != null) return !!d.index.hasGeom;
  return !!(d.pack.geom && d.pack.geom.g);
}

/** Geometry of feature i as Array<Array<[lat, lon]>>; falls back to the representative point. */
export function geometry(d, i) {
  if (typeof d.index.geometry === 'function') {
    const g = d.index.geometry(i);
    if (g && g.length) return g;
  }
  let g = d.geomCache.get(i);
  if (g) return g;
  const enc = d.pack.geom && d.pack.geom.g && d.pack.geom.g[i];
  if (enc && enc.length) g = enc.map((s) => decodePolyline(s));
  else {
    const c = d.F[i] && d.F[i].c;
    g = c ? [[[c[0] / 1e5, c[1] / 1e5]]] : [];
  }
  d.geomCache.set(i, g);
  return g;
}
