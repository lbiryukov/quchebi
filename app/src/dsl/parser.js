// DSL parser: recursive descent over tokens -> AST (docs/dsl.md "Grammar").
//
// parse(text, {strict}) -> {ast, warnings} | {error: {pos, len, message, suggestions}}
//
// AST root: {type: 'query', expr: Node|null, zone: null|'off'|'touch'|'whole'|'default',
//            sort: null|'name'|'len'|'houses'|'dist'|'rel', directives: [...], warnings: [...]}
// Nodes:
//   {type: 'or'|'and', items: Node[], pos, len, paren?}
//   {type: 'not', item: Node, pos, len}
//   {type: 'text', mode: 'word'|'quoted'|'regex', value, pos, len, re?}
//   {type: 'filter', key, keyRaw, pos, len, keyPos, keyLen, ...payload}  (payload per key below)
//
// Filter payloads:
//   name                     items: [{mode: 'sub'|'prefix'|'suffix'|'exact'|'regex', value, raw, pos, len, re?}]
//   type|district|in|who     items: [{value, raw, pos, len, star?}]
//   bio                      items: [{mode: 'word'|'quoted'|'regex', value, raw, pos, len, re?}]
//   century|houses|len|ord   nums:  [{op: '='|'>'|'<'|'>='|'<='|'range', a, b?, raw, pos, len}]
//   gender|own               items: [{value: canonical, raw, pos, len}]
//   house                    items: [{q: HouseQuery, raw, pos, len}]
//   cross                    sub: Node, dir: {bit, raw, pos, len} | null
//   near                     sub: Node | null, me: boolean, radius: {m, raw, pos, len} | null
//   family|old               sub: Node
//   zone|sort are hoisted into root.zone / root.sort (directives).
import { tokenize, TOKEN as T, fold } from './lexer.js';
import { findKey, closestKeys, closest, FIXED, COMPASS, COMPASS_SHOW, NUM_KEYS, EPONYM_KEYS, DIRECTIVE_KEYS } from './keys.js';
import { compileRegex } from './regex.js';
import { parseHouseValue } from './housequery.js';

class ParseError extends Error {
  constructor(pos, len, message, suggestions = []) {
    super(message);
    this.pos = pos;
    this.len = len;
    this.suggestions = suggestions;
  }
}

const ROMAN = { i: 1, v: 5, x: 10, l: 50, c: 100 };
function romanToInt(s) {
  const t = s.toLowerCase();
  if (!/^[ivxlc]+$/.test(t)) return null;
  let total = 0;
  for (let i = 0; i < t.length; i++) {
    const v = ROMAN[t[i]];
    const nx = ROMAN[t[i + 1]] || 0;
    total += v < nx ? -v : v;
  }
  return total;
}

/** Parse a number with optional unit (м, m, км, km). Returns metres/number or null. */
export function parseNumber(raw, { units = false, roman = false } = {}) {
  const s = fold(raw).replace(',', '.');
  let m = /^(\d+(?:\.\d+)?)$/.exec(s);
  if (m) return +m[1];
  if (units) {
    m = /^(\d+(?:\.\d+)?)(м|m|км|km)$/.exec(s);
    if (m) return m[2] === 'км' || m[2] === 'km' ? +m[1] * 1000 : +m[1];
  }
  if (roman) return romanToInt(s);
  return null;
}

const SUB_HELP = {
  cross: 'x:(тип:улица кто:поэт) или x:чаргали',
  near: 'рядом:(тип:метро)~300 или рядом:я~500',
  family: 'семья:важа-пшавела или семья:(…)',
  old: 'было:ленин или было:(кто:инженер)',
};

class Parser {
  constructor(text, opts) {
    this.text = text;
    this.toks = tokenize(text);
    this.p = 0;
    this.strict = !!opts.strict;
    this.warnings = [];
    this.directives = [];
  }

  peek(k = 0) { return this.toks[Math.min(this.p + k, this.toks.length - 1)]; }
  next() { const t = this.toks[this.p]; if (this.p < this.toks.length - 1) this.p++; return t; }
  at(type) { return this.peek().type === type; }

  warn(msg) { if (!this.warnings.includes(msg)) this.warnings.push(msg); }

  /** A WORD token that starts a filter: followed (no whitespace) by ':' / '=' / comparison. */
  isKeyStart(k = 0) {
    const t = this.peek(k);
    if (t.type !== T.WORD) return false;
    const nx = this.peek(k + 1);
    if (nx.ws) {
      // "домов > 30": allowed for numeric keys only
      return nx.type === T.CMP && !!findKey(t.value) && NUM_KEYS.has(findKey(t.value).id);
    }
    return nx.type === T.COLON || nx.type === T.EQ || nx.type === T.CMP;
  }

  parseQuery() {
    const expr = this.parseOr({ top: true, sub: null });
    const t = this.peek();
    if (t.type === T.RPAREN) throw new ParseError(t.pos, 1, 'лишняя закрывающая скобка «)»', ['']);
    if (t.type !== T.EOF) throw new ParseError(t.pos, t.len, `не понял «${this.text.slice(t.pos, t.pos + t.len)}»`);
    const root = { type: 'query', expr, zone: null, sort: null, directives: this.directives, warnings: this.warnings };
    for (const d of this.directives) {
      if (d.key === 'zone') root.zone = d.value;
      else if (d.key === 'sort') root.sort = d.value;
    }
    return root;
  }

  /** or_expr. Returns a node, or null when every branch held only directives (зона:, сорт:). */
  parseOr(ctx) {
    const start = this.peek().pos;
    const items = [];
    const before = this.directives.length;
    let br = this.parseAnd(ctx);
    let brDirective = this.directives.length > before;
    if (br) items.push(br);
    while (this.at(T.OR)) {
      const orTok = this.next();
      const word = orTok.kw || '|';
      if (!br && !brDirective) throw new ParseError(orTok.pos, orTok.len, `перед «${word}» нужно условие`);
      const nt = this.peek();
      if (nt.type === T.EOF || nt.type === T.RPAREN || nt.type === T.OR) {
        throw new ParseError(orTok.pos, orTok.len, `после «${word}» нужно ещё условие`);
      }
      const b2 = this.directives.length;
      br = this.parseAnd(ctx);
      brDirective = this.directives.length > b2;
      if (br) items.push(br);
    }
    if (items.length === 0) return null;
    if (items.length === 1) return items[0];
    const end = this.toks[this.p - 1];
    return { type: 'or', items, pos: start, len: end.pos + end.len - start };
  }

  parseAnd(ctx) {
    const start = this.peek().pos;
    const items = [];
    for (;;) {
      const t = this.peek();
      if (t.type === T.EOF || t.type === T.RPAREN || t.type === T.OR) break;
      if (t.type === T.AND) {
        this.next();
        this.warn('слово «и» не нужно: пробел между условиями уже означает И');
        continue;
      }
      const node = this.parseUnary(ctx);
      if (node) items.push(node);
    }
    if (items.length === 0) return null;
    if (items.length === 1) return items[0];
    const end = this.toks[this.p - 1];
    return { type: 'and', items, pos: start, len: end.pos + end.len - start };
  }

  parseUnary(ctx) {
    const t = this.peek();
    if (t.type === T.NOT) {
      this.next();
      const nt = this.peek();
      if (nt.type === T.EOF || nt.type === T.RPAREN || nt.type === T.OR) {
        throw new ParseError(t.pos, t.len, `после «${t.kw || t.value}» нужно условие, например -кто:персона`);
      }
      const item = this.parseUnary({ ...ctx, top: false });
      if (!item) throw new ParseError(t.pos, t.len, `после «${t.kw || t.value}» нужно условие`);
      return { type: 'not', item, pos: t.pos, len: item.pos + item.len - t.pos };
    }
    return this.parseAtom(ctx);
  }

  parseAtom(ctx) {
    const t = this.peek();
    switch (t.type) {
      case T.LPAREN: {
        this.next();
        const before = this.directives.length;
        const inner = this.parseOr(ctx);
        const close = this.peek();
        let end;
        if (close.type === T.RPAREN) { this.next(); end = close.pos + 1; } else {
          this.unclosedParen(t);
          end = this.text.length;
        }
        if (!inner) {
          if (this.directives.length > before) return null;
          throw new ParseError(t.pos, end - t.pos, 'пустые скобки — внутри нужно условие');
        }
        inner.paren = true;
        return inner;
      }
      case T.WORD:
        if (this.isKeyStart()) return this.parseFilter(ctx);
        this.next();
        return this.textNode(t);
      case T.QUOTED:
      case T.REGEX:
        this.next();
        return this.textNode(t);
      case T.COMMA:
        throw new ParseError(t.pos, 1, 'запятая разделяет значения одного ключа: тип:переулок,тупик');
      case T.TILDE:
        throw new ParseError(t.pos, 1, '«~» ставится сразу после значения: x:(…)~в, рядом:(…)~300');
      case T.COLON:
      case T.EQ:
        throw new ParseError(t.pos, 1, `перед «${t.value}» нужен ключ, например тип:улица`);
      case T.CMP:
        throw new ParseError(t.pos, t.len, `перед «${t.value}» нужен числовой ключ: домов, длина, №, век`);
      default:
        throw new ParseError(t.pos, t.len || 1, `не понял «${t.value}»`);
    }
  }

  unclosedParen(openTok) {
    if (this.strict || !this.at(T.EOF)) throw new ParseError(openTok.pos, 1, 'не закрыта скобка «(»', []);
    this.warn('не закрыта скобка «(» — закрыта автоматически');
  }

  checkUnclosed(t) {
    if (!t.unclosed) return;
    const what = t.type === T.REGEX ? 'шаблон «/»' : 'кавычка';
    if (this.strict) throw new ParseError(t.pos, t.len, `не закрыт${t.type === T.REGEX ? '' : 'а'} ${what}`);
    this.warn(`не закрыт${t.type === T.REGEX ? '' : 'а'} ${what} — закрыт${t.type === T.REGEX ? '' : 'а'} автоматически`);
  }

  textNode(t) {
    this.checkUnclosed(t);
    if (t.type === T.REGEX) {
      const c = compileRegex(t.value);
      if (c.error) throw new ParseError(t.pos, t.len, c.error);
      return { type: 'text', mode: 'regex', value: t.value, re: c.re, pos: t.pos, len: t.len };
    }
    const mode = t.type === T.QUOTED ? 'quoted' : 'word';
    if (mode === 'quoted' && !t.value.trim()) throw new ParseError(t.pos, t.len, 'пустые кавычки');
    return { type: 'text', mode, value: t.value, pos: t.pos, len: t.len };
  }

  // ---- filters ----------------------------------------------------------------------------

  parseFilter(ctx) {
    const keyTok = this.next();
    const key = findKey(keyTok.value);
    if (!key) {
      const sugg = closestKeys(keyTok.value);
      const msg = sugg.length
        ? `неизвестный ключ «${keyTok.value}» — может быть, «${sugg[0]}»?`
        : `неизвестный ключ «${keyTok.value}» — для поиска текста с «:» возьмите его в кавычки`;
      throw new ParseError(keyTok.pos, keyTok.len, msg, sugg);
    }
    const base = { type: 'filter', key: key.id, keyRaw: keyTok.value, keyPos: keyTok.pos, keyLen: keyTok.len, pos: keyTok.pos };
    const sep = this.next(); // ':' | '=' | cmp
    if (DIRECTIVE_KEYS.has(key.id) && (!ctx.top || ctx.sub)) {
      throw new ParseError(keyTok.pos, keyTok.len, `«${key.ru}:» действует на весь запрос — укажите его вне подзапросов и без «-»`);
    }
    if (ctx.sub === 'old' && !EPONYM_KEYS.has(key.id)) {
      throw new ParseError(keyTok.pos, keyTok.len, `в «было:(…)» можно только кто, био, век, пол, свой и текст, а не «${key.ru}»`);
    }
    let node;
    if (sep.type === T.CMP) {
      if (!NUM_KEYS.has(key.id)) {
        throw new ParseError(sep.pos, sep.len, `сравнение «${sep.value}» работает только для ключей домов, длина, №, век`);
      }
      node = { ...base, nums: [this.parseCmpNumber(key, sep.value, sep)] };
    } else {
      node = this.parseValue(key, base, sep, ctx);
    }
    if (!node) return null; // directive
    const end = this.toks[this.p - 1];
    node.len = end.pos + end.len - node.pos;
    return node;
  }

  /** Is there no value after the separator? */
  missingValue(sep) {
    const t = this.peek();
    if (t.type === T.EOF || t.type === T.RPAREN || t.type === T.OR) return true;
    if (t.ws && (t.type === T.NOT || this.isKeyStart())) return true;
    return false;
  }

  parseValue(key, base, sep, ctx) {
    if (this.missingValue(sep)) {
      throw new ParseError(sep.pos + sep.len, 0, `нет значения после «${base.keyRaw}${sep.value}» — ${key.desc}`);
    }
    const t = this.peek();
    if (t.type === T.LPAREN) {
      if (key.value !== 'sub') {
        throw new ParseError(t.pos, 1, `у ключа «${key.ru}» нет подзапросов — перечислите значения через запятую`);
      }
      return this.parseSubValue(key, base, ctx);
    }
    switch (key.value) {
      case 'num': return this.parseNumValue(key, base);
      case 'sub': return this.parseSubValue(key, base, ctx);
      default: break;
    }
    let exactHouse = false;
    if (key.id === 'house' && t.type === T.EQ) {
      // дом:=3 — exact form; glue '=' to the following word
      this.next();
      const w = this.peek();
      if (w.type !== T.WORD || w.ws) throw new ParseError(t.pos, 1, 'после «=» нужен номер дома, например дом:=3');
      exactHouse = true;
    }
    const items = this.parseList(key);
    if (key.id === 'name') return { ...base, items: items.map((it) => this.nameItem(it)) };
    if (key.id === 'bio') return { ...base, items: items.map((it) => this.bioItem(it)) };
    if (key.id === 'house') return { ...base, items: items.map((it, i) => this.houseItem(it, i === 0 && exactHouse)) };
    if (key.value === 'fixed') {
      const vals = items.map((it) => this.fixedItem(key, it));
      if (DIRECTIVE_KEYS.has(key.id)) {
        if (vals.length > 1) throw new ParseError(vals[1].pos, vals[1].len, `у «${key.ru}:» одно значение`);
        this.directives.push({ key: key.id, value: vals[0].value, raw: vals[0].raw, pos: base.pos, len: vals[0].pos + vals[0].len - base.pos });
        this.checkTilde(key);
        return null;
      }
      return { ...base, items: vals };
    }
    // enum keys resolved later against the index
    const out = items.map((it) => {
      if (it.type === T.REGEX) throw new ParseError(it.pos, it.len, `у ключа «${key.ru}» не бывает шаблонов /…/`);
      const raw = it.value.trim();
      let v = fold(raw);
      let star = false;
      if (key.id === 'type' && v.endsWith('*')) { star = true; v = v.replace(/\*+$/, ''); }
      if (!v) throw new ParseError(it.pos, it.len, `пустое значение у «${key.ru}:»`);
      return { value: v, raw, pos: it.pos, len: it.len, star };
    });
    return { ...base, items: out };
  }

  checkTilde(key) {
    if (this.at(T.TILDE)) {
      const t = this.peek();
      throw new ParseError(t.pos, 1, `«~» бывает только у x:(…) и рядом:(…), а не у «${key.ru}:»`);
    }
  }

  /** item { "," item } — whitespace around commas is tolerated. */
  parseList(key) {
    const items = [];
    for (;;) {
      const t = this.peek();
      if (t.type === T.WORD || t.type === T.QUOTED || t.type === T.REGEX) {
        this.next();
        this.checkUnclosed(t);
        items.push(t);
      } else {
        const prev = this.toks[this.p - 1];
        throw new ParseError(t.type === T.EOF ? prev.pos + prev.len : t.pos, t.type === T.EOF ? 0 : t.len,
          `после «,» нужно ещё значение для «${key.ru}:»`);
      }
      // a value glued to a following ':' would be a key — handled by the caller loop
      if (this.at(T.COMMA)) { this.next(); continue; }
      break;
    }
    this.checkTilde(key);
    return items;
  }

  nameItem(t) {
    if (t.type === T.REGEX) {
      const c = compileRegex(t.value);
      if (c.error) throw new ParseError(t.pos, t.len, c.error);
      return { mode: 'regex', value: t.value, raw: t.value, re: c.re, pos: t.pos, len: t.len };
    }
    let v = t.value;
    if (t.type === T.QUOTED) return { mode: 'sub', value: v, raw: v, quoted: true, pos: t.pos, len: t.len };
    const pre = v.startsWith('^');
    const suf = v.endsWith('$') && v.length > 1;
    if (pre) v = v.slice(1);
    if (suf) v = v.slice(0, -1);
    if (!v) throw new ParseError(t.pos, t.len, 'пустое имя — например имя:^ვაჟა или имя:ли$');
    const mode = pre && suf ? 'exact' : pre ? 'prefix' : suf ? 'suffix' : 'sub';
    return { mode, value: v, raw: t.value, pos: t.pos, len: t.len };
  }

  bioItem(t) {
    if (t.type === T.REGEX) {
      const c = compileRegex(t.value);
      if (c.error) throw new ParseError(t.pos, t.len, c.error);
      return { mode: 'regex', value: t.value, raw: t.value, re: c.re, pos: t.pos, len: t.len };
    }
    return { mode: t.type === T.QUOTED ? 'quoted' : 'word', value: t.value, raw: t.value, pos: t.pos, len: t.len };
  }

  houseItem(t, exact) {
    if (t.type === T.REGEX) throw new ParseError(t.pos, t.len, 'у «дом:» не бывает шаблонов /…/');
    const raw = (exact ? '=' : '') + t.value;
    const r = parseHouseValue(raw);
    const pos = exact ? t.pos - 1 : t.pos;
    const len = exact ? t.len + 1 : t.len;
    if (r.error) throw new ParseError(pos, len, r.error, []);
    return { q: r.q, raw, pos, len };
  }

  fixedItem(key, t) {
    const v = fold(t.value.trim());
    const table = FIXED[key.id];
    const canon = table.values[v];
    if (!canon) {
      const sugg = closest(v, Object.keys(table.values).filter((x) => table.show.includes(x)).concat(table.show), { limit: 5 });
      const show = sugg.length ? sugg : table.show;
      throw new ParseError(t.pos, t.len, `неизвестное значение «${t.value}» для «${key.ru}:» — можно: ${table.show.join(', ')}`, show);
    }
    return { value: canon, raw: t.value, pos: t.pos, len: t.len };
  }

  // ---- numbers ----------------------------------------------------------------------------

  parseCmpNumber(key, op, opTok) {
    const t = this.peek();
    if (t.type !== T.WORD) {
      throw new ParseError(opTok.pos + opTok.len, 0, `после «${op}» нужно число`);
    }
    this.next();
    const v = parseNumber(t.value, { units: key.id === 'len', roman: key.id === 'century' });
    if (v === null) throw new ParseError(t.pos, t.len, `«${t.value}» — не число`);
    this.checkTilde(key);
    return { op, a: v, raw: op + t.value, pos: opTok.pos, len: t.pos + t.len - opTok.pos };
  }

  parseNumValue(key, base) {
    const t = this.peek();
    if (t.type === T.CMP) { this.next(); return { ...base, nums: [this.parseCmpNumber(key, t.value, t)] }; }
    if (t.type === T.EQ) { this.next(); return { ...base, nums: [this.parseCmpNumber(key, '=', t)] }; }
    const items = this.parseList(key);
    const nums = items.map((it) => {
      if (it.type !== T.WORD) throw new ParseError(it.pos, it.len, `у «${key.ru}:» значение — число или диапазон, например ${key.id === 'century' ? '19 или 11-12' : '10-50'}`);
      const opts = { units: key.id === 'len', roman: key.id === 'century' };
      const m = /^([^-]+)-([^-]+)$/.exec(it.value);
      if (m) {
        const a = parseNumber(m[1], opts);
        const b = parseNumber(m[2], opts);
        if (a === null || b === null) throw new ParseError(it.pos, it.len, `«${it.value}» — не диапазон чисел`);
        return { op: 'range', a: Math.min(a, b), b: Math.max(a, b), raw: it.value, pos: it.pos, len: it.len };
      }
      const a = parseNumber(it.value, opts);
      if (a === null) throw new ParseError(it.pos, it.len, `«${it.value}» — не число`);
      return { op: '=', a, raw: it.value, pos: it.pos, len: it.len };
    });
    if (key.id === 'century') {
      for (const nm of nums) {
        const hi = nm.b ?? nm.a;
        if (nm.a < 1 || hi > 21) throw new ParseError(nm.pos, nm.len, 'век — число от 1 до 21 или диапазон, например век:19 или век:11-12');
      }
    }
    return { ...base, nums };
  }

  // ---- subqueries -------------------------------------------------------------------------

  parseSubValue(key, base, ctx) {
    const t = this.peek();
    const subCtx = { top: false, sub: key.id === 'old' ? 'old' : (ctx.sub === 'old' ? 'old' : 'rel') };
    let sub = null;
    let me = false;
    if (t.type === T.LPAREN) {
      this.next();
      sub = this.parseOr(subCtx);
      const close = this.peek();
      if (close.type === T.RPAREN) this.next(); else this.unclosedParen(t);
      if (!sub) throw new ParseError(t.pos, Math.max(1, this.toks[this.p - 1].pos + 1 - t.pos), `пустой подзапрос — например ${SUB_HELP[key.id]}`);
    } else if (t.type === T.WORD || t.type === T.QUOTED || t.type === T.REGEX) {
      if (key.id === 'near' && t.type === T.WORD && ['я', 'me'].includes(fold(t.value))) {
        this.next();
        me = true;
      } else {
        // x:чаргали == x:(чаргали); a comma list is an OR of texts
        const items = this.parseListNoTilde();
        const nodes = items.map((it) => this.textNode(it));
        sub = nodes.length === 1 ? nodes[0]
          : { type: 'or', items: nodes, pos: nodes[0].pos, len: nodes[nodes.length - 1].pos + nodes[nodes.length - 1].len - nodes[0].pos };
      }
    } else {
      throw new ParseError(t.pos, t.len || 0, `у «${key.ru}:» значение — подзапрос в скобках: ${SUB_HELP[key.id]}`);
    }
    const node = { ...base, sub };
    if (key.id === 'near') { node.me = me; node.radius = null; }
    if (key.id === 'cross') node.dir = null;
    if (this.at(T.TILDE)) {
      const tl = this.next();
      const v = this.peek();
      if (key.id !== 'cross' && key.id !== 'near') {
        throw new ParseError(tl.pos, 1, `«~» бывает только у x:(…) и рядом:(…), а не у «${key.ru}:»`);
      }
      if (v.type !== T.WORD || v.ws) {
        throw new ParseError(tl.pos + 1, 0, key.id === 'cross'
          ? `после «~» нужно направление: ${COMPASS_SHOW.join(' ')}`
          : 'после «~» нужно расстояние в метрах, например ~300');
      }
      this.next();
      if (key.id === 'cross') {
        const bit = COMPASS[fold(v.value)];
        if (bit === undefined) {
          const sugg = closest(v.value, COMPASS_SHOW, { maxDist: 1 });
          throw new ParseError(v.pos, v.len, `неизвестное направление «${v.value}» — можно: ${COMPASS_SHOW.join(', ')}`, sugg.length ? sugg : COMPASS_SHOW.slice());
        }
        node.dir = { bit, raw: v.value, pos: v.pos, len: v.len };
      } else {
        const m = parseNumber(v.value, { units: true });
        if (m === null || m <= 0) throw new ParseError(v.pos, v.len, `«${v.value}» — не расстояние; например рядом:я~500`);
        if (m > 5000) throw new ParseError(v.pos, v.len, 'слишком большое расстояние — не больше 5000 м', ['5000']);
        node.radius = { m, raw: v.value, pos: v.pos, len: v.len };
      }
    }
    return node;
  }

  parseListNoTilde() {
    const items = [];
    for (;;) {
      const t = this.peek();
      if (t.type === T.WORD || t.type === T.QUOTED || t.type === T.REGEX) { this.next(); items.push(t); } else {
        const prev = this.toks[this.p - 1];
        throw new ParseError(prev.pos + prev.len, 0, 'после «,» нужно ещё значение');
      }
      if (this.at(T.COMMA)) { this.next(); continue; }
      break;
    }
    return items;
  }
}

/**
 * Parse a query line. Lenient by default: an unclosed "(", quote or regex at the end of input is
 * closed automatically (with a warning). With {strict: true} that is an error.
 */
export function parse(text, opts = {}) {
  const src = String(text ?? '');
  try {
    const p = new Parser(src, opts);
    const ast = p.parseQuery();
    ast.text = src;
    return { ast, warnings: ast.warnings };
  } catch (e) {
    if (e instanceof ParseError) {
      return { error: { pos: e.pos, len: e.len, message: e.message, suggestions: e.suggestions || [] } };
    }
    throw e;
  }
}

/** Walk every node of an AST (including subqueries), calling fn(node, parent). */
export function walk(node, fn, parent = null) {
  if (!node) return;
  if (node.type === 'query') { walk(node.expr, fn, node); return; }
  fn(node, parent);
  if (node.type === 'or' || node.type === 'and') for (const it of node.items) walk(it, fn, node);
  else if (node.type === 'not') walk(node.item, fn, node);
  else if (node.type === 'filter' && node.sub) walk(node.sub, fn, node);
}
