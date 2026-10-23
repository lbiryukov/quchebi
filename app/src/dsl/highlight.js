// Syntax highlighting spans for the query input (UI: two-way highlight of chips and text).
//
// highlight(text) -> [{pos, len, cls}] with cls one of
//   'key' (known key), 'error' (unknown key), 'sep' (':' '=' ','), 'value', 'text', 'quoted',
//   'regex', 'op' ('|', 'или', '-', '!', 'не', comparisons), 'paren', 'tilde'.
import { tokenize, TOKEN as T } from './lexer.js';
import { findKey } from './keys.js';

export function highlight(text) {
  const toks = tokenize(text).filter((t) => t.type !== T.EOF);
  const out = [];
  let afterSep = false; // the previous token opened a value (':' '=' ',' comparison '~')
  for (let k = 0; k < toks.length; k++) {
    const t = toks[k];
    const nx = toks[k + 1];
    let cls;
    switch (t.type) {
      case T.WORD:
        if (nx && !nx.ws && (nx.type === T.COLON || nx.type === T.EQ || nx.type === T.CMP) && !afterSep) {
          cls = findKey(t.value) ? 'key' : 'error';
        } else if (afterSep) cls = 'value';
        else cls = 'text';
        break;
      case T.QUOTED: cls = afterSep ? 'value' : 'quoted'; break;
      case T.REGEX: cls = 'regex'; break;
      case T.COLON: case T.EQ: case T.COMMA: cls = 'sep'; break;
      case T.CMP: case T.OR: case T.NOT: case T.AND: cls = 'op'; break;
      case T.LPAREN: case T.RPAREN: cls = 'paren'; break;
      case T.TILDE: cls = 'tilde'; break;
      default: cls = 'text';
    }
    out.push({ pos: t.pos, len: t.len, cls });
    afterSep = t.type === T.COLON || t.type === T.EQ || t.type === T.COMMA || t.type === T.CMP || t.type === T.TILDE;
    // "дом:=3": the '=' after ':' keeps the value context
  }
  return out;
}
