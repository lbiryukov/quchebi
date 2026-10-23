// Regex literals of the DSL: /…/ with the letter classes \С (consonant) and \Г (vowel).

export const CONSONANTS = 'бвгджзйклмнпрстфхцчшщბგდვზთკლმნპჟრსტფქღყშჩცძწჭხჯჰ';
export const VOWELS = 'аеёиоуыэюяაეიოუ';

// \С and \Г per docs/dsl.md; lowercase Cyrillic and Latin \C / \V are accepted as well.
const CLASS_ESCAPES = { 'С': CONSONANTS, 'с': CONSONANTS, 'C': CONSONANTS, 'Г': VOWELS, 'г': VOWELS, 'V': VOWELS };

/**
 * Expand the letter-class escapes (inside [...] they expand to the bare letters) and fold the
 * literal characters (lowercase, ё -> е). Escape sequences (\d, \S, А, ...) are kept as is.
 */
export function expandClasses(pattern) {
  let out = '';
  let inClass = false;
  for (let i = 0; i < pattern.length; i++) {
    const ch = pattern[i];
    if (ch === '\\' && i + 1 < pattern.length) {
      const nx = pattern[i + 1];
      const cls = CLASS_ESCAPES[nx];
      if (cls) out += inClass ? cls : `[${cls}]`;
      else out += ch + nx;
      i++;
      continue;
    }
    if (ch === '[' && !inClass) inClass = true;
    else if (ch === ']' && inClass) inClass = false;
    out += ch === 'ё' || ch === 'Ё' ? 'е' : ch.toLowerCase();
  }
  return out;
}

/**
 * Compile a DSL regex. The pattern is folded instead of using the 'i' flag (twice as fast on
 * large letter classes), so the regex must be tested against folded text: lexer.fold(s).
 * Returns {re} or {error: Russian message}.
 */
export function compileRegex(pattern) {
  if (!pattern) return { error: 'пустой шаблон //' };
  try {
    return { re: new RegExp(expandClasses(pattern)) };
  } catch (e) {
    return { error: `шаблон /${pattern}/ не разбирается — проверьте скобки и символы \\ ( ) [ ] { }`, detail: String(e?.message || e) };
  }
}
