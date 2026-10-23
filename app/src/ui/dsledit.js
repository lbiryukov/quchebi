// String-level editing of DSL queries for chips: split into top-level atoms, find/replace
// "key:value" atoms, toggle values inside comma lists. The DSL string stays the source of truth.

/** Split text into top-level atoms separated by whitespace outside (), "" and /regex/. */
export function atoms(text) {
  const out = [];
  let depth = 0;
  let quote = false;
  let regex = false;
  let start = -1;
  for (let i = 0; i <= text.length; i++) {
    const ch = i < text.length ? text[i] : ' ';
    if (start < 0) {
      if (/\s/.test(ch)) continue;
      start = i;
    }
    if (quote) {
      if (ch === '"') quote = false;
      continue;
    }
    if (regex) {
      if (ch === '\\') { i++; continue; }
      if (ch === '/') regex = false;
      continue;
    }
    if (ch === '"') { quote = true; continue; }
    if (ch === '/' && (i === start || /[:=(\s,]/.test(text[i - 1] || ' '))) { regex = true; continue; }
    if (ch === '(') depth++;
    else if (ch === ')') depth = Math.max(0, depth - 1);
    else if (/\s/.test(ch) && depth === 0) {
      out.push({ start, end: i, text: text.slice(start, i) });
      start = -1;
    }
  }
  if (start >= 0 && start < text.length) out.push({ start, end: text.length, text: text.slice(start) });
  return out;
}

const KEY_RE = /^([-!]?)([^\s:=()"]+)([:=])([\s\S]*)$/;

/** Parse an atom "key:value" -> {neg, key, sep, value} or null. */
export function parseAtom(atomText) {
  const m = KEY_RE.exec(atomText);
  if (!m) return null;
  return { neg: m[1], key: m[2].toLowerCase(), sep: m[3], value: m[4] };
}

/** First top-level positive atom whose key is in `keys`. */
export function findAtom(text, keys) {
  const set = new Set(keys.map((k) => k.toLowerCase()));
  for (const a of atoms(text)) {
    const p = parseAtom(a.text);
    if (p && !p.neg && set.has(p.key)) return { ...a, ...p };
  }
  return null;
}

export function findAllAtoms(text, keys) {
  const set = new Set(keys.map((k) => k.toLowerCase()));
  const out = [];
  for (const a of atoms(text)) {
    const p = parseAtom(a.text);
    if (p && set.has(p.key)) out.push({ ...a, ...p });
  }
  return out;
}

function tidy(text) {
  return text.replace(/\s{2,}/g, ' ').replace(/^\s+/, '');
}

/** Replace the first atom with key in `keys` by `newAtom` (or append). newAtom '' removes it. */
export function setAtom(text, keys, newAtom) {
  const a = findAtom(text, keys);
  if (a) return tidy(text.slice(0, a.start) + newAtom + text.slice(a.end));
  if (!newAtom) return text;
  const base = text.replace(/\s+$/, '');
  return base ? `${base} ${newAtom}` : newAtom;
}

export function appendAtom(text, atomText) {
  const base = text.replace(/\s+$/, '');
  return base ? `${base} ${atomText}` : atomText;
}

export function removeAtomAt(text, a) {
  return tidy(text.slice(0, a.start) + text.slice(a.end));
}

function splitList(value) {
  return value.split(',').map((s) => s.trim()).filter(Boolean);
}

/** Values of the first list atom with key in keys (lowercased). */
export function listValues(text, keys) {
  const a = findAtom(text, keys);
  return a ? splitList(a.value.toLowerCase()) : [];
}

/** Toggle `value` in the list of the first atom with key in keys; writes `canonKey:`. */
export function toggleListValue(text, keys, canonKey, value) {
  const a = findAtom(text, keys);
  const v = value.toLowerCase();
  if (!a) return appendAtom(text, `${canonKey}:${value}`);
  const vals = splitList(a.value);
  const i = vals.findIndex((x) => x.toLowerCase() === v);
  if (i >= 0) vals.splice(i, 1);
  else vals.push(value);
  const next = vals.length ? `${a.key}${a.sep}${vals.join(',')}` : '';
  return tidy(text.slice(0, a.start) + next + text.slice(a.end));
}

/** Quote a value if it contains spaces or DSL punctuation. */
export function quoteValue(v) {
  return /[\s,()|:="]/.test(v) ? `"${v.replace(/"/g, '')}"` : v;
}

/**
 * Extract the inner text of every top-level "x:(...)" atom (any key synonym),
 * used to show which crossing made a result match.
 */
export function crossSubqueries(text) {
  const out = [];
  for (const a of findAllAtoms(text, ['x', 'пересекает', 'cross', 'х'])) {
    if (a.neg) continue;
    const v = a.value;
    if (!v.startsWith('(')) continue;
    let depth = 0;
    let end = -1;
    for (let i = 0; i < v.length; i++) {
      if (v[i] === '(') depth++;
      else if (v[i] === ')') {
        depth--;
        if (depth === 0) { end = i; break; }
      }
    }
    out.push(v.slice(1, end < 0 ? v.length : end));
  }
  return out;
}

/** House number token value from the query, e.g. "3", "=3", "3а", "3?" -> {n, raw, probable, exact}. */
export function houseQuery(text) {
  const a = findAtom(text, ['дом', 'h']);
  if (!a) return null;
  const raw = a.value.trim();
  const m = /^(=)?(\d+)([^\s?\-]*)(\?)?$/.exec(raw);
  if (!m) return { raw, n: null };
  return { raw, exact: Boolean(m[1]), n: Number(m[2]), lit: (m[3] || '').toLowerCase(), probable: Boolean(m[4]) };
}
