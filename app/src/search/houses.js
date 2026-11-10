// House numbers: parsing raw numbers (parity with the pipeline, tests/vectors/house_numbers.json),
// parsing `дом:` query values and matching them against pack rows
// [raw, n, lit, sub, lat_e5, lon_e5, n_to?, corp?] (docs/pack-format.md, docs/dsl.md "Houses").
//
// Packs of language 'ru' (Moscow) store Moscow-style addresses: lit is a lowercase Cyrillic letter,
// corp the space-separated building tokens "к2", "с1", "к2 с1" (корпус, строение). Their `дом:`
// values are parsed by parseHouseQuery(value, {lang: 'ru'}) (vectors: tests/vectors/house_query_ru.json).

// Single letters normalised to Georgian by sound (Latin and Cyrillic).
const LETTER_TO_KA = {
  a: 'ა', 'а': 'ა', b: 'ბ', 'б': 'ბ', g: 'გ', 'г': 'გ', d: 'დ', 'д': 'დ', e: 'ე', 'е': 'ე', 'ё': 'ე',
  v: 'ვ', 'в': 'ვ', z: 'ზ', 'з': 'ზ', i: 'ი', 'и': 'ი', k: 'კ', 'к': 'კ', l: 'ლ', 'л': 'ლ', m: 'მ', 'м': 'მ',
  n: 'ნ', 'н': 'ნ', o: 'ო', 'о': 'ო', p: 'პ', 'п': 'პ', r: 'რ', 'р': 'რ', s: 'ს', 'с': 'ს', t: 'ტ', 'т': 'ტ',
  u: 'უ', 'у': 'უ',
};

/** Normalise one house letter to Georgian ("а", "a", "A", "ა", Mtavruli -> "ა"); others lowercased. */
export function normLetter(ch) {
  if (!ch) return '';
  let c = String(ch).toLowerCase();
  const o = c.codePointAt(0);
  if (o >= 0x1c90 && o <= 0x1cbf) c = String.fromCharCode(o - 0x1c90 + 0x10d0);
  return LETTER_TO_KA[c] || c;
}

/** Single letters normalised to Cyrillic for 'ru' packs (Latin look-alikes and sound pairs). */
const LETTER_TO_RU = { a: 'а', b: 'б', v: 'в', g: 'г', d: 'д', e: 'е', 'ё': 'е' };

/** Normalise one house letter of a 'ru' pack to lowercase Cyrillic ("A", "a", "А" -> "а"). */
export function normLetterRu(ch) {
  if (!ch) return '';
  const c = String(ch).toLowerCase();
  return LETTER_TO_RU[c] || c;
}

const L = '[\\p{L}]';
const RE_INT = /^(\d+)$/u;
const RE_LETTER = new RegExp(`^(\\d+)\\s*(${L})$`, 'u');
const RE_FRACTION = new RegExp(`^(\\d+)\\s*(${L})?\\s*\\/\\s*(\\d+${L}?)$`, 'u');
const RE_DASH = /^(\d+)\s*-\s*(\d+)$/u;
const RE_CORP = /^(\d+)\s*(?:კორპ(?:უსი)?\.?|корп(?:ус)?\.?|ბლოკი|блок|k\.?|корп)\s*(\d+\S*)$/iu;

function part(n, lit = '', sub = '', nTo = null, corp = '', kind = 'int') {
  return { n, lit, sub, n_to: nTo, corp, kind };
}

function parseOne(s) {
  s = s.trim();
  if (!s) return null;
  let m;
  if ((m = RE_INT.exec(s))) return part(+m[1]);
  if ((m = RE_LETTER.exec(s))) return part(+m[1], normLetter(m[2]), '', null, '', 'letter');
  if ((m = RE_FRACTION.exec(s))) return part(+m[1], m[2] ? normLetter(m[2]) : '', m[3], null, '', 'fraction');
  if ((m = RE_DASH.exec(s))) {
    const a = +m[1], b = +m[2];
    if (b > a) return part(a, '', '', b, '', 'range');
    return part(a, '', m[2], null, '', 'fraction'); // "221-4" = 221/4
  }
  if ((m = RE_CORP.exec(s))) return part(+m[1], '', '', null, m[2], 'corp');
  return null;
}

/**
 * Parse a raw house number into parts {n, lit, sub, n_to, corp, kind}; [] when unparseable.
 * Same rules as the pipeline (tests/vectors/house_numbers.json).
 */
export function parseHouse(raw) {
  const s = String(raw == null ? '' : raw).trim();
  if (!s) return [];
  const items = s.split(/[;,]/).map((x) => x.trim()).filter(Boolean);
  if (items.length > 1) {
    const out = [];
    for (const it of items) {
      const p = parseOne(it);
      if (!p) return [];
      p.kind = 'list';
      out.push(p);
    }
    return out;
  }
  const p = parseOne(s);
  return p ? [p] : [];
}

/**
 * Parse a `дом:` value. Returns
 *   {kind: 'any'|'exact'|'lit'|'sub'|'corp'|'range'|'has'|'none', n, lit, sub, corp, from, to, probable}
 * or {error: message}.
 *   3 -> any n == 3 (3, 3ა, 3/16, 3 корп. 2, ranges containing 3); =3 -> plain "3" only;
 *   3а/3ა/3a -> lit; 3/16 -> sub; 1-9 -> range of n; 3? -> any + "probably there"; есть / нет.
 */
export function parseHouseQuery(value, { lang = 'ka' } = {}) {
  if (lang === 'ru') return parseHouseQueryRu(value);
  let v = String(value == null ? '' : value).trim().toLowerCase().replace(/ё/g, 'е');
  if (!v) return { error: 'пустой номер дома' };
  if (v === 'есть' || v === 'да' || v === 'yes' || v === 'any') return { kind: 'has', probable: false };
  if (v === 'нет' || v === 'no' || v === 'none') return { kind: 'none', probable: false };
  let probable = false;
  if (v.endsWith('?')) { probable = true; v = v.slice(0, -1).trim(); }
  let exact = false;
  if (v.startsWith('=')) { exact = true; v = v.slice(1).trim(); }
  const dash = RE_DASH.exec(v);
  if (dash && +dash[2] > +dash[1]) {
    if (exact) return { error: '«=» нельзя сочетать с диапазоном' };
    return { kind: 'range', from: +dash[1], to: +dash[2], probable };
  }
  const parts = parseHouse(v);
  if (parts.length !== 1) return { error: `не понимаю номер дома «${value}»` };
  const p = parts[0];
  const base = { n: p.n, lit: p.lit, sub: p.sub, corp: p.corp, probable };
  if (exact) {
    if (p.kind === 'int') return { ...base, kind: 'exact' };
    if (p.kind === 'letter') return { ...base, kind: 'lit', exact: true };
    if (p.kind === 'fraction') return { ...base, kind: 'sub', exact: true };
    if (p.kind === 'corp') return { ...base, kind: 'corp' };
  }
  switch (p.kind) {
    case 'int': return { ...base, kind: 'any' };
    case 'letter': return { ...base, kind: 'lit' };
    case 'fraction': return { ...base, kind: 'sub' };
    case 'corp': return { ...base, kind: 'corp' };
    default: return { error: `не понимаю номер дома «${value}»` };
  }
}

// ---- 'ru' packs: Moscow addresses "12", "12а", "12 к2", "12 с1", "12 к2 с1", "5/7", "5/7 с2"

/** корпус / строение spellings (folded, without dots) -> token letter. */
const CORP_WORD = { 'к': 'к', 'k': 'к', 'корп': 'к', 'корпус': 'к', 'с': 'с', 'c': 'с', 's': 'с', 'стр': 'с', 'строение': 'с' };
const RE_CORP_TOKEN = /([a-zа-я]+)\.?\s*(\d+)/gu;

/**
 * Building tokens of a Moscow address part: "к2с1" / "корп.2 стр.1" -> ["к2", "с1"];
 * null when a word is not корпус/строение.
 */
export function corpTokens(text) {
  const out = [];
  const s = String(text || '').trim();
  if (!s) return out;
  let rest = s;
  for (const m of s.matchAll(RE_CORP_TOKEN)) {
    const w = CORP_WORD[m[1]];
    if (!w) return null;
    out.push(w + String(+m[2]));
    rest = rest.replace(m[0], '');
  }
  return rest.trim() ? null : out;
}

/**
 * Tail after a number: building tokens, optionally after one letter ("ак2" = а + к2, "к2" = к2,
 * "к" = letter к). {letter, tokens} or null.
 */
function letterAndTokens(text) {
  const rest = String(text || '').trim();
  const t = corpTokens(rest);
  if (t) return { letter: '', tokens: t };
  if (/^[a-zа-я]/u.test(rest)) {
    const t2 = corpTokens(rest.slice(1));
    if (t2) return { letter: rest[0], tokens: t2 };
  }
  return null;
}

/**
 * `дом:` value of a 'ru' pack. Same kinds as parseHouseQuery plus building tokens: a letter
 * right before a digit is корпус/строение ("12к2", "12с1", "12к2с1", also "стр"/"корп"), a
 * single trailing letter is the литера ("12а", Latin "12a"). Kind 'corp' carries `corp`
 * ("к2 с1") and `corpTokens` (["к2", "с1"]); rows match when their corp has all the tokens.
 */
export function parseHouseQueryRu(value) {
  let v = String(value == null ? '' : value).trim().toLowerCase().replace(/ё/g, 'е');
  if (!v) return { error: 'пустой номер дома' };
  if (v === 'есть' || v === 'да' || v === 'yes' || v === 'any') return { kind: 'has', probable: false, lang: 'ru' };
  if (v === 'нет' || v === 'no' || v === 'none') return { kind: 'none', probable: false, lang: 'ru' };
  let probable = false;
  if (v.endsWith('?')) { probable = true; v = v.slice(0, -1).trim(); }
  let exact = false;
  if (v.startsWith('=')) { exact = true; v = v.slice(1).trim(); }
  const dash = RE_DASH.exec(v);
  if (dash && +dash[2] > +dash[1]) {
    if (exact) return { error: '«=» нельзя сочетать с диапазоном' };
    return { kind: 'range', from: +dash[1], to: +dash[2], probable, lang: 'ru' };
  }
  const bad = { error: `не понимаю номер дома «${value}»` };
  const m = /^(\d+)\s*(.*)$/u.exec(v);
  if (!m) return bad;
  let lit = '';
  let sub = '';
  let tokens;
  const slash = m[2].indexOf('/');
  if (slash >= 0) {
    const before = m[2].slice(0, slash).trim();
    if (before && !/^[a-zа-я]$/u.test(before)) return bad;
    lit = normLetterRu(before);
    const a = /^\s*(\d+)(.*)$/u.exec(m[2].slice(slash + 1));
    const r = a && letterAndTokens(a[2]);
    if (!r) return bad;
    sub = String(+a[1]) + normLetterRu(r.letter);
    tokens = r.tokens;
  } else {
    const r = letterAndTokens(m[2]);
    if (!r) return bad;
    lit = normLetterRu(r.letter);
    tokens = r.tokens;
  }
  const base = { n: +m[1], lit, sub, corp: tokens.join(' '), probable, lang: 'ru' };
  if (tokens.length) return { ...base, kind: 'corp', corpTokens: tokens, exact };
  if (sub) return exact ? { ...base, kind: 'sub', exact: true } : { ...base, kind: 'sub' };
  if (lit) return exact ? { ...base, kind: 'lit', exact: true } : { ...base, kind: 'lit' };
  return { ...base, kind: exact ? 'exact' : 'any' };
}

const rowN = (r) => r[1];
const rowLit = (r) => r[2] || '';
const rowSub = (r) => r[3] || '';
const rowTo = (r) => (r.length > 6 && r[6] !== null && r[6] !== undefined ? r[6] : null);
const rowCorp = (r) => (r.length > 7 && r[7] ? String(r[7]) : '');

/**
 * Does a range row "a-b" contain number q? Ranges whose ends have the same parity cover one
 * side of the street only ("3-5" = 3 and 5, not 4); mixed-parity ranges cover every number.
 */
export function rangeContains(a, b, q) {
  if (q < a || q > b) return false;
  if ((a & 1) === (b & 1)) return (q & 1) === (a & 1);
  return true;
}

/** Does one pack row match a parsed query (kinds any/exact/lit/sub/corp/range)? */
export function houseRowMatches(row, q) {
  const n = rowN(row);
  const to = rowTo(row);
  switch (q.kind) {
    case 'any':
      return n === q.n || (to !== null && rangeContains(n, to, q.n));
    case 'exact':
      return n === q.n && !rowLit(row) && !rowSub(row) && to === null && !rowCorp(row);
    case 'lit':
      return n === q.n && rowLit(row) === q.lit && (!q.exact || (!rowSub(row) && (q.lang !== 'ru' || !rowCorp(row))));
    case 'sub':
      return n === q.n && rowSub(row) === q.sub && (!q.lit || rowLit(row) === q.lit)
        && (!q.exact || q.lang !== 'ru' || !rowCorp(row));
    case 'corp': {
      if (n !== q.n) return false;
      if (!q.corpTokens) return rowCorp(row) === q.corp;
      // 'ru': the row has every building token of the query (and the letter / fraction if given)
      const have = rowCorp(row).toLowerCase().split(/\s+/).filter(Boolean);
      if (q.exact && (have.length !== q.corpTokens.length || rowLit(row) !== q.lit || rowSub(row) !== q.sub)) return false;
      if (q.lit && rowLit(row) !== q.lit) return false;
      if (q.sub && rowSub(row) !== q.sub) return false;
      return q.corpTokens.every((t) => have.includes(t));
    }
    case 'range': {
      if (n >= q.from && n <= q.to) return true;
      return to !== null && to >= q.from && n <= q.to; // row range overlaps the query range
    }
    default:
      return false;
  }
}

/**
 * "Probably there": the same-parity side has numbers below and above n, but no row with n.
 * Rows are pack rows (already restricted to the zone by the caller when needed).
 */
export function probable(rows, n) {
  let below = false, above = false;
  const par = n & 1;
  for (const r of rows) {
    const a = rowN(r), to = rowTo(r);
    if (a === n || (to !== null && rangeContains(a, to, n))) return false;
    for (const x of to !== null ? [a, to] : [a]) {
      if ((x & 1) !== par) continue;
      if (x < n) below = true; else if (x > n) above = true;
    }
  }
  return below && above;
}

/**
 * Does a feature with these rows match the query? `inside(row) -> boolean` restricts rows
 * (e.g. to the zone); rows failing it are ignored entirely (also for "probable" and has/none).
 * Returns true/false; for kind 'any'/'exact'/... with q.probable it also accepts probable features.
 */
export function matchHouses(rows, q, inside = null) {
  const use = inside ? rows.filter(inside) : rows;
  if (q.kind === 'has') return use.length > 0;
  if (q.kind === 'none') return use.length === 0;
  for (const r of use) if (houseRowMatches(r, q)) return true;
  if (q.probable && q.n !== undefined && (q.kind === 'any' || q.kind === 'exact' || q.kind === 'lit' || q.kind === 'sub')) {
    return probable(use, q.n);
  }
  return false;
}

/**
 * Classify a feature for the UI: {matched: rowIndices[], probable: boolean}.
 * `probable` is true only when nothing matched and the number is probably there.
 */
export function houseMatchDetail(rows, q, inside = null) {
  const matched = [];
  rows.forEach((r, k) => { if ((!inside || inside(r)) && houseRowMatches(r, q)) matched.push(k); });
  let prob = false;
  if (!matched.length && q.probable && q.n !== undefined) prob = probable(inside ? rows.filter(inside) : rows, q.n);
  return { matched, probable: prob };
}

/** Sort rows for display: by n, then letter, then sub. */
export function sortHouseRows(rows) {
  return rows.slice().sort((a, b) => rowN(a) - rowN(b) || (rowLit(a) < rowLit(b) ? -1 : rowLit(a) > rowLit(b) ? 1 : 0)
    || String(rowSub(a)).localeCompare(String(rowSub(b)), 'en', { numeric: true }));
}
