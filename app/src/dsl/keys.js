// DSL key table, fixed enumerations and fuzzy matching helpers (docs/dsl.md "Keys").
import { fold } from './lexer.js';

/**
 * Canonical keys. `ru` is the display name, `names` all accepted spellings (folded),
 * `value` the value class used by the parser:
 *   text   — list of words / quoted / regex
 *   enum   — list of words resolved against the index (тип, район, в, кто)
 *   fixed  — list of words from a fixed set (пол, свой, зона, сорт)
 *   num    — number, range or comparison (домов, длина, №, век)
 *   house  — house number forms
 *   sub    — subquery "(…)" (or a single text item treated as a subquery)
 */
export const KEYS = [
  { id: 'name', ru: 'имя', names: ['имя', 'n', 'name'], value: 'text', desc: 'точнее по названию: ^начало, конец$, "фраза", /шаблон/' },
  { id: 'type', ru: 'тип', names: ['тип', 't', 'type'], value: 'enum', desc: 'тип объекта; * — с родственными' },
  { id: 'district', ru: 'район', names: ['район', 'р', 'd'], value: 'enum', desc: 'официальный район' },
  { id: 'in', ru: 'в', names: ['в', 'in'], value: 'enum', desc: 'внутри микрорайона или района' },
  { id: 'who', ru: 'кто', names: ['кто', 'e', 'who'], value: 'enum', desc: 'категория эпонима' },
  { id: 'bio', ru: 'био', names: ['био', 'bio'], value: 'text', desc: 'подстрока в биографии эпонима' },
  { id: 'century', ru: 'век', names: ['век', 'c'], value: 'num', desc: 'век жизни эпонима: 19 или 11-12' },
  { id: 'gender', ru: 'пол', names: ['пол', 'g'], value: 'fixed', desc: 'пол эпонима: м или ж' },
  { id: 'own', ru: 'свой', names: ['свой', 'ge'], value: 'fixed', desc: 'эпоним грузин: да или нет' },
  { id: 'house', ru: 'дом', names: ['дом', 'h'], value: 'house', desc: 'номер дома: 3, =3, 3а, 3/16, 1-9, 3?, есть, нет' },
  { id: 'houses', ru: 'домов', names: ['домов', 'hn'], value: 'num', desc: 'сколько номеров домов: домов>30' },
  { id: 'cross', ru: 'x', names: ['x', 'х', 'пересекает', 'cross'], value: 'sub', desc: 'пересекает объект подзапроса: x:(…)~в' },
  { id: 'near', ru: 'рядом', names: ['рядом', 'near'], value: 'sub', desc: 'в радиусе: рядом:(…)~300, рядом:я~500' },
  { id: 'family', ru: 'семья', names: ['семья', 'f', 'fam'], value: 'sub', desc: 'улица и все её именные переулки' },
  { id: 'ord', ru: '№', names: ['№', 'ord', 'no'], value: 'num', desc: 'порядковый номер переулка' },
  { id: 'old', ru: 'было', names: ['было', 'old'], value: 'sub', desc: 'прежние названия: текст или (кто:…)' },
  { id: 'len', ru: 'длина', names: ['длина', 'len'], value: 'num', desc: 'длина в метрах (в зоне — внутри зоны)' },
  { id: 'zone', ru: 'зона', names: ['зона', 'zone'], value: 'fixed', desc: 'режим зоны: выкл, задевает, целиком' },
  { id: 'sort', ru: 'сорт', names: ['сорт', 'sort'], value: 'fixed', desc: 'порядок: имя, длина, домов, расстояние' },
];

export const KEY_BY_ID = new Map(KEYS.map((k) => [k.id, k]));
const KEY_BY_NAME = new Map();
for (const k of KEYS) for (const nm of k.names) KEY_BY_NAME.set(fold(nm), k);

/** Look up a key by any accepted spelling (case-insensitive, ё=е). */
export function findKey(word) {
  return KEY_BY_NAME.get(fold(word)) || null;
}

/** All accepted key spellings with their key, for autocomplete. */
export function allKeyNames() {
  const out = [];
  for (const k of KEYS) for (const nm of k.names) out.push({ name: nm, key: k });
  return out;
}

/** Keys allowed inside было:(…) — eponym filters and text. */
export const EPONYM_KEYS = new Set(['who', 'bio', 'century', 'gender', 'own', 'name']);
/** Keys that accept a comparison operator right after the key. */
export const NUM_KEYS = new Set(['houses', 'len', 'ord', 'century']);
/** Directives: not filters, act on the whole query. */
export const DIRECTIVE_KEYS = new Set(['zone', 'sort']);

// ---- fixed enumerations -------------------------------------------------------------------

/** value spelling -> canonical value, plus display label for suggestions/explain. */
export const FIXED = {
  gender: {
    values: { 'м': 'm', 'm': 'm', 'муж': 'm', 'мужской': 'm', 'мужчина': 'm', 'male': 'm',
      'ж': 'f', 'f': 'f', 'жен': 'f', 'женский': 'f', 'женщина': 'f', 'female': 'f', 'w': 'f' },
    show: ['м', 'ж'],
  },
  own: {
    values: { 'да': 'yes', 'yes': 'yes', 'y': 'yes', '1': 'yes', '+': 'yes', 'свой': 'yes', 'грузин': 'yes',
      'нет': 'no', 'no': 'no', 'n': 'no', '0': 'no', 'чужой': 'no', 'иностранец': 'no' },
    show: ['да', 'нет'],
  },
  zone: {
    values: { 'выкл': 'off', 'off': 'off', 'нет': 'off', 'город': 'off',
      'задевает': 'touch', 'touch': 'touch', 'любая': 'touch',
      'целиком': 'whole', 'whole': 'whole', 'вся': 'whole',
      'вкл': 'on', 'on': 'on', 'default': 'on', 'обычно': 'on' },
    show: ['выкл', 'задевает', 'целиком', 'вкл'],
  },
  sort: {
    values: { 'имя': 'name', 'name': 'name', 'название': 'name', 'алфавит': 'name',
      'длина': 'len', 'len': 'len', 'length': 'len',
      'домов': 'houses', 'дома': 'houses', 'hn': 'houses', 'houses': 'houses',
      'расстояние': 'dist', 'dist': 'dist', 'distance': 'dist', 'близость': 'dist',
      'релевантность': 'rel', 'rel': 'rel' },
    show: ['имя', 'длина', 'домов', 'расстояние'],
  },
};

/** Compass sectors: bit index in the crossing direction mask (0=N … 7=NW). */
export const COMPASS = {
  'с': 0, 'св': 1, 'в': 2, 'юв': 3, 'ю': 4, 'юз': 5, 'з': 6, 'сз': 7,
  'n': 0, 'ne': 1, 'e': 2, 'se': 3, 's': 4, 'sw': 5, 'w': 6, 'nw': 7,
  // full words are accepted too
  'север': 0, 'северо-восток': 1, 'восток': 2, 'юго-восток': 3, 'юг': 4, 'юго-запад': 5, 'запад': 6, 'северо-запад': 7,
  'north': 0, 'northeast': 1, 'east': 2, 'southeast': 3, 'south': 4, 'southwest': 5, 'west': 6, 'northwest': 7,
};
export const COMPASS_RU = ['север', 'северо-восток', 'восток', 'юго-восток', 'юг', 'юго-запад', 'запад', 'северо-запад'];
export const COMPASS_SHOW = ['с', 'св', 'в', 'юв', 'ю', 'юз', 'з', 'сз'];

// ---- fuzzy matching --------------------------------------------------------------------------

/** Optimal-string-alignment Damerau–Levenshtein distance, with early exit above `max`. */
let BUF = new Int32Array(3 * 64);
export function damerau(a, b, max = Infinity) {
  const la = a.length;
  const lb = b.length;
  if (Math.abs(la - lb) > max) return max + 1;
  if (la === 0) return lb;
  if (lb === 0) return la;
  const w = lb + 1;
  if (BUF.length < 3 * w) BUF = new Int32Array(3 * w * 2);
  // three rows in one reusable buffer: r2 = row i-2, r1 = row i-1, r0 = row i
  let r2 = 0;
  let r1 = w;
  let r0 = 2 * w;
  for (let j = 0; j <= lb; j++) BUF[r1 + j] = j;
  for (let i = 1; i <= la; i++) {
    const ca = a.charCodeAt(i - 1);
    const pa = i > 1 ? a.charCodeAt(i - 2) : -1;
    BUF[r0] = i;
    let rowMin = i;
    for (let j = 1; j <= lb; j++) {
      const cb = b.charCodeAt(j - 1);
      let v = BUF[r1 + j - 1] + (ca === cb ? 0 : 1);
      const del = BUF[r1 + j] + 1;
      const ins = BUF[r0 + j - 1] + 1;
      if (del < v) v = del;
      if (ins < v) v = ins;
      if (i > 1 && j > 1 && ca === b.charCodeAt(j - 2) && pa === cb) {
        const tr = BUF[r2 + j - 2] + 1;
        if (tr < v) v = tr;
      }
      BUF[r0 + j] = v;
      if (v < rowMin) rowMin = v;
    }
    if (rowMin > max) return max + 1;
    const t = r2; r2 = r1; r1 = r0; r0 = t;
  }
  return BUF[r1 + lb];
}

/**
 * Closest candidates to `word` (folded comparison). Candidates within distance `maxDist`
 * come first (by distance), then candidates that start with the word, then that contain it.
 * Returns up to `limit` distinct display strings.
 */
export function closest(word, candidates, { limit = 5, maxDist = 2 } = {}) {
  const w = fold(word);
  const scored = [];
  const seen = new Set();
  for (const c of candidates) {
    const disp = typeof c === 'string' ? c : c.label;
    const f = fold(typeof c === 'string' ? c : (c.match ?? c.label));
    if (seen.has(disp)) continue;
    let score = null;
    const d = damerau(w, f, maxDist);
    if (d <= maxDist) score = d;
    else if (w.length >= 2 && f.startsWith(w)) score = maxDist + 1;
    else if (w.length >= 3 && f.includes(w)) score = maxDist + 2;
    else if (w.length >= 4 && w.startsWith(f) && f.length >= 3) score = maxDist + 2;
    if (score !== null) { scored.push({ disp, score, len: f.length }); seen.add(disp); }
  }
  scored.sort((x, y) => x.score - y.score || x.len - y.len || (x.disp < y.disp ? -1 : x.disp > y.disp ? 1 : 0));
  return scored.slice(0, limit).map((x) => x.disp);
}

/** Closest key display names for an unknown key word (distance ≤ 2). */
export function closestKeys(word) {
  const cands = [];
  for (const k of KEYS) for (const nm of k.names) cands.push({ label: nm === k.names[0] ? k.ru : nm, match: nm });
  const w = fold(word);
  const scored = [];
  for (const c of cands) {
    const d = damerau(w, fold(c.match), 2);
    if (d <= 2 && !(w.length <= 2 && d >= w.length)) scored.push({ label: c.label, d });
  }
  scored.sort((a, b) => a.d - b.d);
  const out = [];
  for (const s of scored) if (!out.includes(s.label)) out.push(s.label);
  return out.slice(0, 3);
}

/** Russian plural form: plural(5, ['объект', 'объекта', 'объектов']). */
export function plural(n, forms) {
  const a = Math.abs(n) % 100;
  const b = a % 10;
  if (a > 10 && a < 20) return forms[2];
  if (b > 1 && b < 5) return forms[1];
  if (b === 1) return forms[0];
  return forms[2];
}
