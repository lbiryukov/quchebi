// Name normalisation shared with the pipeline (pipeline/names/translit.py).
// Parity is checked by tests/vectors/translit.json in both languages.
//
// skeleton(s): lowercase (Mtavruli -> Mkhedruli), ё -> е, дж -> j, per-character Georgian /
//   Cyrillic -> Latin, keep only [a-z0-9], then gh -> g, q -> k, w -> v, y -> i, f -> p (in order).
// kaToRu(s):   per-character Georgian -> Russian, word-initial ე -> э; other characters unchanged.
// kaToLat(s):  Georgian national romanisation (2002) without apostrophes; others unchanged.
// normRu(s):   lowercase, ё -> е, dashes unified, quotes dropped, spaces collapsed (for aliases).
// stripType(name): name without the kind word and the lane ordinal ("base name").

const KA_LETTERS = 'აბგდევზთიკლმნოპჟრსტუფქღყშჩცძწჭხჯჰ';
const KA_LAT_LIST = ['a', 'b', 'g', 'd', 'e', 'v', 'z', 't', 'i', 'k', 'l', 'm', 'n', 'o', 'p', 'zh', 'r', 's', 't',
  'u', 'p', 'k', 'gh', 'q', 'sh', 'ch', 'ts', 'dz', 'ts', 'ch', 'kh', 'j', 'h'];
const KA_RU_LIST = ['а', 'б', 'г', 'д', 'е', 'в', 'з', 'т', 'и', 'к', 'л', 'м', 'н', 'о', 'п', 'ж', 'р', 'с', 'т',
  'у', 'п', 'к', 'г', 'к', 'ш', 'ч', 'ц', 'дз', 'ц', 'ч', 'х', 'дж', 'х'];
const KA_ARCHAIC_LAT = { 'ჱ': 'e', 'ჲ': 'i', 'ჳ': 'v', 'ჴ': 'q', 'ჵ': 'o', 'ჶ': 'p', 'ჷ': '', 'ჸ': '', 'ჹ': '', 'ჺ': '' };
const KA_ARCHAIC_RU = { 'ჱ': 'е', 'ჲ': 'й', 'ჳ': 'в', 'ჴ': 'х', 'ჵ': 'о', 'ჶ': 'ф', 'ჷ': '', 'ჸ': '', 'ჹ': '', 'ჺ': '' };

export const KA_TO_LAT = Object.freeze({
  ...Object.fromEntries([...KA_LETTERS].map((c, i) => [c, KA_LAT_LIST[i]])), ...KA_ARCHAIC_LAT,
});
export const KA_TO_RU = Object.freeze({
  ...Object.fromEntries([...KA_LETTERS].map((c, i) => [c, KA_RU_LIST[i]])), ...KA_ARCHAIC_RU,
});
export const RU_TO_LAT = Object.freeze({
  'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'e', 'ж': 'zh', 'з': 'z',
  'и': 'i', 'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm', 'н': 'n', 'о': 'o', 'п': 'p', 'р': 'r',
  'с': 's', 'т': 't', 'у': 'u', 'ф': 'f', 'х': 'kh', 'ц': 'ts', 'ч': 'ch', 'ш': 'sh',
  'щ': 'shch', 'ъ': '', 'ы': 'y', 'ь': '', 'э': 'e', 'ю': 'yu', 'я': 'ya',
});

// Per-character table for the skeleton (applied after lowercasing). Exactly the Python
// SKELETON_TABLE: the 33 modern Georgian letters + the Russian alphabet; anything else
// (archaic letters, Nuskhuri, other Cyrillic) is dropped by the [a-z0-9] filter.
const SKEL = new Map();
for (let i = 0; i < KA_LETTERS.length; i++) SKEL.set(KA_LETTERS[i], KA_LAT_LIST[i]);
for (const [k, v] of Object.entries(RU_TO_LAT)) SKEL.set(k, v);
// Mtavruli (U+1C90..U+1CBA) -> as the Mkhedruli letter, in case the engine's toLowerCase is
// older than Unicode 11 (Python str.lower maps them to Mkhedruli).
for (let i = 0; i < KA_LETTERS.length; i++) {
  const m = KA_LETTERS.charCodeAt(i);
  SKEL.set(String.fromCharCode(m - 0x10d0 + 0x1c90), KA_LAT_LIST[i]);
}

// Dense lookup table by UTF-16 code unit (all mapped characters are below U+1CC0).
// The final folds q->k, w->v, y->i, f->p are applied per character here: they never touch
// 'g' or 'h', so folding before the gh->g step gives exactly the Python result.
const fold = (v) => v.replace(/q/g, 'k').replace(/w/g, 'v').replace(/y/g, 'i').replace(/f/g, 'p');
const TBL_SIZE = 0x1cc0;
const TBL = new Array(TBL_SIZE).fill('');
for (let c = 48; c <= 57; c++) TBL[c] = String.fromCharCode(c);
for (let c = 97; c <= 122; c++) TBL[c] = fold(String.fromCharCode(c));
for (const [k, v] of SKEL) TBL[k.charCodeAt(0)] = fold(v);

/** Search skeleton: Latin [a-z0-9] only, spelling-tolerant. */
export function skeleton(s) {
  if (!s) return '';
  let t = String(s).toLowerCase();
  if (t.indexOf('ё') >= 0) t = t.replace(/ё/g, 'е');
  if (t.indexOf('дж') >= 0) t = t.replace(/дж/g, 'j');
  let out = '';
  for (let i = 0; i < t.length; i++) {
    const code = t.charCodeAt(i);
    if (code < TBL_SIZE) out += TBL[code];
  }
  return out.indexOf('gh') >= 0 ? out.replace(/gh/g, 'g') : out;
}

function nuskhuriToMkhedruli(ch) {
  const o = ch.charCodeAt(0);
  if (o >= 0x2d00 && o <= 0x2d25) return String.fromCharCode(o - 0x2d00 + 0x10d0);
  return ch;
}

/** Lowercase only Georgian capitals (Mtavruli, Asomtavruli); other scripts keep their case. */
export function kaLower(s) {
  let out = '';
  for (const ch of String(s)) {
    const o = ch.codePointAt(0);
    if ((o >= 0x1c90 && o <= 0x1cbf) || (o >= 0x10a0 && o <= 0x10cf)) {
      if (o >= 0x1c90 && o <= 0x1cbf) out += String.fromCharCode(o - 0x1c90 + 0x10d0);
      else out += nuskhuriToMkhedruli(ch.toLowerCase());
    } else out += ch;
  }
  return out;
}

const LETTER = /[\p{L}\p{Nl}\p{No}]/u;

/** Per-character Georgian -> Russian. Word-initial ე becomes э. */
export function kaToRu(s) {
  if (!s) return '';
  const t = kaLower(s);
  let out = '';
  let prevLetter = false;
  for (const ch of t) {
    if (ch === 'ე' && !prevLetter) out += 'э';
    else {
      const m = KA_TO_RU[ch];
      out += m === undefined ? ch : m;
    }
    prevLetter = LETTER.test(ch);
  }
  return out;
}

/**
 * Georgian national romanisation without apostrophes: ჭავჭავაძე -> chavchavadze.
 * capitalize=true gives every word that starts with a Georgian letter an initial capital.
 */
export function kaToLat(s, capitalize = false) {
  if (!s) return '';
  const t = kaLower(s);
  let out = '';
  let prevLetter = false;
  for (const ch of t) {
    const lat = KA_TO_LAT[ch];
    if (lat === undefined) out += ch;
    else if (capitalize && !prevLetter && lat) out += lat[0].toUpperCase() + lat.slice(1);
    else out += lat;
    prevLetter = LETTER.test(ch);
  }
  return out;
}

/** Normalised Russian (or any) text for alias / value matching. */
export function normRu(s) {
  if (!s) return '';
  return String(s)
    .toLowerCase()
    .replace(/ё/g, 'е')
    .replace(/[‐-―−]/g, '-')
    .replace(/["'«»„“”‘’`]/g, '')
    .replace(/\s+/g, ' ')
    .trim();
}

// ------------------------------------------------------------------ type words and ordinals

// Kind words by language. ka words are the last word of the name (except "მდინარე" which can
// lead); ru words may lead or trail (adjective names like "Метехский мост"); en words trail.
const KA_TYPE_WORDS = ['ქუჩა', 'გამზირი', 'გზატკეცილი', 'გზა', 'სანაპირო', 'აღმართი', 'დაღმართი', 'ხეივანი',
  'რიგი', 'შესახვევი', 'ჩიხი', 'გასასვლელი', 'მოედანი', 'ხიდი', 'გვირაბი', 'ხევი', 'სკვერი', 'ბაღი', 'პარკი',
  'მდინარე', 'ღელე', 'არხი', 'ტბა', 'რაიონი', 'დასახლება', 'კვარტალი', 'მიკრორაიონი', 'სადგური', 'მეტრო'];
const RU_TYPE_WORDS = ['улица', 'ул.', 'ул', 'проспект', 'просп.', 'пр-т', 'пр.', 'шоссе', 'дорога', 'набережная',
  'наб.', 'подъём', 'подъем', 'спуск', 'аллея', 'ряд', 'переулок', 'пер.', 'пер', 'тупик', 'проезд', 'съезд',
  'площадь', 'пл.', 'мост', 'тоннель', 'туннель', 'балка', 'ущелье', 'сквер', 'сад', 'парк', 'река', 'ручей',
  'канал', 'озеро', 'станция', 'метро', 'ст.', 'район', 'микрорайон', 'мкр.', 'мкр', 'квартал', 'местность'];
const EN_TYPE_WORDS = ['street', 'st', 'st.', 'avenue', 'ave', 'ave.', 'highway', 'hwy', 'road', 'rd', 'rd.',
  'embankment', 'emb.', 'ascent', 'descent', 'alley', 'row', 'lane', 'ln', 'ln.', 'dead end', 'dead-end', 'cul-de-sac',
  'passage', 'drive', 'square', 'sq', 'sq.', 'bridge', 'tunnel', 'ravine', 'gorge', 'garden', 'gardens', 'park',
  'river', 'stream', 'canal', 'lake', 'metro', 'station', 'district', 'neighbourhood', 'neighborhood', 'quarter'];

const TYPE_SET = new Set([...KA_TYPE_WORDS, ...RU_TYPE_WORDS, ...EN_TYPE_WORDS]);

const ROMAN = /^(?:x{0,3})(?:ix|iv|v?i{0,3})$/i;
const RU_ORD = /^(\d+)-?(?:й|я|е|ый|ой|ий|ая|ое|ья)$/i;
const EN_ORD = /^(\d+)(?:st|nd|rd|th)$/i;
const KA_ORD_PRE = /^მე-?(\d+)(?:-ე)?$/; // მე-2
const KA_ORD_POST = /^(\d+)-?ლი$/; // 1-ლი

function romanToInt(s) {
  const v = { i: 1, v: 5, x: 10 };
  let total = 0, prev = 0;
  for (const ch of s.toLowerCase().split('').reverse()) {
    const n = v[ch];
    if (n < prev) total -= n; else { total += n; prev = n; }
  }
  return total;
}

/** Ordinal value of a single word (I, II, 2-й, 2nd, მე-2, 1-ლი) or 0. */
export function ordinalOf(word) {
  const w = String(word || '').trim();
  if (!w) return 0;
  if (!mayBeOrdinal(w)) return 0;
  if (ROMAN.test(w) && /^[ivx]+$/i.test(w)) return romanToInt(w);
  const m = RU_ORD.exec(w) || EN_ORD.exec(w) || KA_ORD_PRE.exec(w) || KA_ORD_POST.exec(w);
  if (m) return parseInt(m[1], 10);
  return 0;
}

// cheap pre-check: ordinals contain a digit or consist of i/v/x only
function mayBeOrdinal(w) {
  let roman = true;
  for (let i = 0; i < w.length; i++) {
    const c = w.charCodeAt(i);
    if (c >= 48 && c <= 57) return true;
    if (!(c === 105 || c === 118 || c === 120 || c === 73 || c === 86 || c === 88)) roman = false;
  }
  return roman;
}

const typeSetCache = new WeakMap();
function typeSetFor(extra) {
  if (!extra) return TYPE_SET;
  let set = typeSetCache.get(extra);
  if (!set) {
    set = new Set(TYPE_SET);
    for (const w of extra) set.add(String(w).toLowerCase().replace(/ё/g, 'е'));
    typeSetCache.set(extra, set);
  }
  return set;
}

const lowerE = (w) => { const l = w.toLowerCase(); return l.indexOf('ё') >= 0 ? l.replace(/ё/g, 'е') : l; };
const QUOTES = /[«»"„“”]/g;
const SPACES = /\s+/;

/**
 * Split a name into {base, kind, ord}: kind is the type word found (as written, lowercased),
 * ord the lane ordinal (0 if none), base the remaining words joined by single spaces.
 * `extraTypeWords` (e.g. from core.kinds[].ka/ru/syn; pass the same array each time, it is
 * cached) are recognised too.
 */
export function parseName(name, extraTypeWords) {
  let str = String(name || '');
  if (QUOTES.test(str)) { QUOTES.lastIndex = 0; str = str.replace(QUOTES, ' '); }
  QUOTES.lastIndex = 0;
  const words = str.trim().split(SPACES).filter(Boolean);
  const types = typeSetFor(extraTypeWords);
  let kind = '', ord = 0;
  const keep = [];
  const lws = words.map(lowerE);
  for (let k = 0; k < words.length; k++) {
    const w = words[k];
    const lw = lws[k];
    // two-word English "dead end"
    if (k + 1 < words.length && types.has(lw + ' ' + lws[k + 1])) {
      if (!kind) kind = lw + ' ' + lws[k + 1];
      k++;
      continue;
    }
    if (types.has(lw)) { if (!kind) kind = lw; continue; }
    if (!ord && words.length > 1) {
      const o = ordinalOf(w);
      if (o) {
        // Roman numerals are ordinals only right before the type word ("II შესახვევი", "II Lane");
        // elsewhere they are regnal numbers ("улица Ираклия II").
        const roman = /^[ivx]+$/i.test(w);
        const nextIsType = k + 1 < words.length && types.has(lws[k + 1]);
        if (!roman || nextIsType) { ord = o; continue; }
      }
    }
    keep.push(w);
  }
  // never strip everything: "Парк" alone stays "Парк"
  if (!keep.length) return { base: words.join(' '), kind: '', ord: 0 };
  return { base: keep.join(' '), kind, ord };
}

/** Name without the kind word and the ordinal: "ლადო ასათიანის II შესახვევი" -> "ლადო ასათიანის". */
export function stripType(name, extraTypeWords) {
  return parseName(name, extraTypeWords).base;
}

/** Damerau-Levenshtein (optimal string alignment) distance, for "did you mean" suggestions. */
export function editDistance(a, b, max = Infinity) {
  a = String(a); b = String(b);
  const n = a.length, m = b.length;
  if (Math.abs(n - m) > max) return max + 1;
  let prev2 = null, prev = Array.from({ length: m + 1 }, (_, j) => j), cur;
  for (let i = 1; i <= n; i++) {
    cur = [i];
    let rowMin = i;
    for (let j = 1; j <= m; j++) {
      const cost = a[i - 1] === b[j - 1] ? 0 : 1;
      let v = Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost);
      if (prev2 && i > 1 && j > 1 && a[i - 1] === b[j - 2] && a[i - 2] === b[j - 1]) v = Math.min(v, prev2[j - 2] + 1);
      cur[j] = v;
      if (v < rowMin) rowMin = v;
    }
    if (rowMin > max) return max + 1;
    prev2 = prev; prev = cur;
  }
  return prev[m];
}
