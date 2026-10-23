// DSL lexer: one input line -> tokens with character positions (docs/dsl.md "Lexical rules").
//
// Token: {type, value, pos, len, ws, unclosed?, kw?}
//   type   one of TOKEN values below
//   value  word text / quoted content / regex pattern / operator text
//   pos    start offset in the input (UTF-16 units; Georgian and Cyrillic are BMP, so = characters)
//   len    length of the token in the input, including quotes / slashes
//   ws     true when whitespace (or the start of input) precedes the token
//   unclosed  quoted string or regex without the closing delimiter (mobile typing)
//   kw     for 'or' / 'not' produced from the words "или" / "не": the original word

export const TOKEN = Object.freeze({
  WORD: 'word',
  QUOTED: 'quoted',
  REGEX: 'regex',
  LPAREN: 'lparen',
  RPAREN: 'rparen',
  OR: 'or',
  COMMA: 'comma',
  TILDE: 'tilde',
  COLON: 'colon',
  EQ: 'eq',
  CMP: 'cmp',
  NOT: 'not',
  AND: 'and', // the word "и": redundant, ignored by the parser with a warning
  EOF: 'eof',
});

const OPEN_QUOTES = new Set(['"', '“', '«', '„']); // " “ « „
const CLOSE_QUOTES = new Set(['"', '”', '»', '“']); // " ” » “
// Characters that always end a word.
const DELIMS = new Set(['(', ')', '|', ',', '~', ':', '=', '<', '>', '≤', '≥']);

function isSpace(ch) {
  return ch === ' ' || ch === '\t' || ch === '\n' || ch === '\r' || ch === ' ' ||
    ch === ' ' || ch === ' ' || ch === '　';
}

function isWordChar(ch) {
  return !isSpace(ch) && !DELIMS.has(ch) && !OPEN_QUOTES.has(ch) && !CLOSE_QUOTES.has(ch);
}

/** Lowercase + ё→е: the normal form used to compare keys and keyword values. */
export function fold(s) {
  return String(s).toLowerCase().replace(/ё/g, 'е');
}

/**
 * Split a query line into tokens. Never throws: unknown characters become part of words,
 * unclosed quotes / regexes run to the end of input and are flagged `unclosed`.
 * The returned array always ends with an EOF token.
 */
export function tokenize(text) {
  const s = String(text ?? '');
  const out = [];
  let i = 0;
  let ws = true;
  const n = s.length;
  const push = (type, value, pos, len, extra) => {
    const t = { type, value, pos, len, ws };
    if (extra) Object.assign(t, extra);
    out.push(t);
    ws = false;
  };
  while (i < n) {
    const ch = s[i];
    if (isSpace(ch)) { ws = true; i++; continue; }
    const start = i;
    if (ch === '(') { push(TOKEN.LPAREN, ch, i, 1); i++; continue; }
    if (ch === ')') { push(TOKEN.RPAREN, ch, i, 1); i++; continue; }
    if (ch === '|') { push(TOKEN.OR, ch, i, 1); i++; continue; }
    if (ch === ',') { push(TOKEN.COMMA, ch, i, 1); i++; continue; }
    if (ch === '~') { push(TOKEN.TILDE, ch, i, 1); i++; continue; }
    if (ch === ':') { push(TOKEN.COLON, ch, i, 1); i++; continue; }
    if (ch === '≥') { push(TOKEN.CMP, '>=', i, 1); i++; continue; }
    if (ch === '≤') { push(TOKEN.CMP, '<=', i, 1); i++; continue; }
    if (ch === '>' || ch === '<') {
      if (s[i + 1] === '=') { push(TOKEN.CMP, ch + '=', i, 2); i += 2; } else { push(TOKEN.CMP, ch, i, 1); i++; }
      continue;
    }
    if (ch === '=') { push(TOKEN.EQ, ch, i, 1); i++; continue; }
    if (OPEN_QUOTES.has(ch) || ch === '”' || ch === '»') {
      // A stray closing quote also opens a string: users type “ and ” inconsistently.
      let j = i + 1;
      while (j < n && !CLOSE_QUOTES.has(s[j])) j++;
      if (j < n) {
        push(TOKEN.QUOTED, s.slice(i + 1, j), start, j + 1 - start);
        i = j + 1;
      } else {
        push(TOKEN.QUOTED, s.slice(i + 1), start, n - start, { unclosed: true });
        i = n;
      }
      continue;
    }
    if (ch === '/') {
      let j = i + 1;
      let pat = '';
      while (j < n && s[j] !== '/') {
        if (s[j] === '\\' && j + 1 < n) { pat += s[j] + s[j + 1]; j += 2; continue; }
        pat += s[j];
        j++;
      }
      if (j < n) {
        // Optional flags right after the closing slash are swallowed (always case-insensitive).
        let k = j + 1;
        while (k < n && /[a-z]/i.test(s[k])) k++;
        push(TOKEN.REGEX, pat, start, k - start);
        i = k;
      } else {
        push(TOKEN.REGEX, pat, start, n - start, { unclosed: true });
        i = n;
      }
      continue;
    }
    if (ch === '-' || ch === '!') { push(TOKEN.NOT, ch, i, 1); i++; continue; }
    // Word: everything up to whitespace or a delimiter.
    let j = i;
    while (j < n && isWordChar(s[j])) j++;
    const word = s.slice(i, j);
    const next = s[j];
    const keyFollows = next === ':' || next === '=';
    const f = fold(word);
    if (!keyFollows && f === 'или') push(TOKEN.OR, '|', start, j - start, { kw: word });
    else if (!keyFollows && f === 'не') push(TOKEN.NOT, '-', start, j - start, { kw: word });
    else if (!keyFollows && f === 'и') push(TOKEN.AND, word, start, j - start, { kw: word });
    else push(TOKEN.WORD, word, start, j - start);
    i = j;
  }
  out.push({ type: TOKEN.EOF, value: '', pos: n, len: 0, ws });
  return out;
}
