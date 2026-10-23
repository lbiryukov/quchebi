// Lexer and highlighter tests.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { tokenize, TOKEN as T, fold } from '../../app/src/dsl/lexer.js';
import { highlight } from '../../app/src/dsl/highlight.js';
import { damerau, closest, closestKeys, findKey, plural } from '../../app/src/dsl/keys.js';

const types = (s) => tokenize(s).map((t) => t.type);
const brief = (s) => tokenize(s).filter((t) => t.type !== T.EOF).map((t) => `${t.type}:${t.value}`);

test('tokens with positions for mixed scripts', () => {
  const s = 'тип:переулок x:(ვაჟა chav*)~в';
  const toks = tokenize(s);
  assert.deepEqual(toks.map((t) => [t.type, t.value, t.pos, t.len]), [
    ['word', 'тип', 0, 3], ['colon', ':', 3, 1], ['word', 'переулок', 4, 8],
    ['word', 'x', 13, 1], ['colon', ':', 14, 1], ['lparen', '(', 15, 1], ['word', 'ვაჟა', 16, 4],
    ['word', 'chav*', 21, 5], ['rparen', ')', 26, 1], ['tilde', '~', 27, 1], ['word', 'в', 28, 1],
    ['eof', '', 29, 0],
  ]);
  for (const t of toks) if (t.type !== T.EOF) assert.equal(s.slice(t.pos, t.pos + t.len), t.type === 'word' ? t.value : s.slice(t.pos, t.pos + t.len));
});

test('whitespace flag', () => {
  const toks = tokenize('a  b(c');
  assert.deepEqual(toks.map((t) => t.ws), [true, true, false, false, false]);
});

test('hyphen inside a word, unary minus at the start', () => {
  assert.deepEqual(brief('семья:важа-пшавела'), ['word:семья', 'colon::', 'word:важа-пшавела']);
  assert.deepEqual(brief('-кто:персона'), ['not:-', 'word:кто', 'colon::', 'word:персона']);
  assert.deepEqual(brief('!a'), ['not:!', 'word:a']);
  assert.deepEqual(brief('дом:1-9'), ['word:дом', 'colon::', 'word:1-9']);
});

test('или / не / и as separate words become operators, not before a colon', () => {
  assert.deepEqual(types('a или b'), ['word', 'or', 'word', 'eof']);
  assert.deepEqual(types('не a'), ['not', 'word', 'eof']);
  assert.deepEqual(types('ИЛИ'), ['or', 'eof']);
  assert.deepEqual(types('a и b'), ['word', 'and', 'word', 'eof']);
  assert.deepEqual(types('не:x'), ['word', 'colon', 'word', 'eof']);
  assert.deepEqual(types('неделя'), ['word', 'eof']);
  const t = tokenize('a или b')[1];
  assert.equal(t.kw, 'или');
});

test('quoted strings: straight, typographic, unclosed', () => {
  assert.deepEqual(brief('"26 мая"'), ['quoted:26 мая']);
  assert.deepEqual(brief('«26 мая»'), ['quoted:26 мая']);
  assert.deepEqual(brief('“Leselidze St”'), ['quoted:Leselidze St']);
  assert.deepEqual(brief('„a b“'), ['quoted:a b']);
  const t = tokenize('"26 ма')[0];
  assert.equal(t.unclosed, true);
  assert.equal(t.value, '26 ма');
  assert.equal(t.len, 6);
});

test('regex literals keep escapes and swallow flags', () => {
  assert.deepEqual(brief('/\\С{5}/'), ['regex:\\С{5}']);
  assert.deepEqual(brief('/a\\/b/'), ['regex:a\\/b']);
  assert.deepEqual(brief('/abc/i x'), ['regex:abc', 'word:x']);
  const t = tokenize('/ab')[0];
  assert.equal(t.unclosed, true);
  assert.deepEqual(brief('3/16'), ['word:3/16']);
});

test('comparison operators, = and unicode ≥ ≤', () => {
  assert.deepEqual(brief('домов>=30'), ['word:домов', 'cmp:>=', 'word:30']);
  assert.deepEqual(brief('домов<3'), ['word:домов', 'cmp:<', 'word:3']);
  assert.deepEqual(brief('длина≥100'), ['word:длина', 'cmp:>=', 'word:100']);
  assert.deepEqual(brief('длина≤100'), ['word:длина', 'cmp:<=', 'word:100']);
  assert.deepEqual(brief('тип=улица'), ['word:тип', 'eq:=', 'word:улица']);
  assert.deepEqual(brief('дом:=3'), ['word:дом', 'colon::', 'eq:=', 'word:3']);
});

test('special word characters: * ? ^ $ № .', () => {
  assert.deepEqual(brief('тип:улица* дом:3? имя:^ა имя:ли$ №:2 a.b'), [
    'word:тип', 'colon::', 'word:улица*', 'word:дом', 'colon::', 'word:3?', 'word:имя', 'colon::', 'word:^ა',
    'word:имя', 'colon::', 'word:ли$', 'word:№', 'colon::', 'word:2', 'word:a.b',
  ]);
});

test('empty and whitespace-only input', () => {
  assert.deepEqual(types(''), ['eof']);
  assert.deepEqual(types('     '), ['eof']);
  assert.deepEqual(types(null), ['eof']);
});

test('fold: lowercase and ё -> е', () => {
  assert.equal(fold('ЁЛКА Дирижёр'), 'елка дирижер');
});

test('Damerau–Levenshtein distance and closest keys', () => {
  assert.equal(damerau('рйон', 'район'), 1);
  assert.equal(damerau('тпи', 'тип'), 1); // transposition
  assert.equal(damerau('abc', 'abc'), 0);
  assert.equal(damerau('', 'abc'), 3);
  assert.ok(damerau('совсем', 'другое', 2) > 2);
  assert.deepEqual(closestKeys('рйон'), ['район']);
  assert.deepEqual(closestKeys('сорд'), ['сорт']);
  assert.deepEqual(closestKeys('qqqqqq'), []);
  assert.deepEqual(closest('поэд', ['поэт', 'полководец', 'поэтесса']), ['поэт']);
  assert.deepEqual(closest('поэ', ['поэт', 'полководец', 'поэтесса']), ['поэт', 'поэтесса']);
});

test('findKey accepts all synonyms', () => {
  const pairs = [['имя', 'name'], ['n', 'name'], ['t', 'type'], ['р', 'district'], ['d', 'district'], ['in', 'in'],
    ['e', 'who'], ['who', 'who'], ['bio', 'bio'], ['c', 'century'], ['g', 'gender'], ['ge', 'own'], ['h', 'house'],
    ['hn', 'houses'], ['x', 'cross'], ['х', 'cross'], ['пересекает', 'cross'], ['cross', 'cross'], ['near', 'near'],
    ['f', 'family'], ['fam', 'family'], ['№', 'ord'], ['ord', 'ord'], ['no', 'ord'], ['old', 'old'], ['len', 'len'],
    ['zone', 'zone'], ['sort', 'sort'], ['ТИП', 'type']];
  for (const [w, id] of pairs) assert.equal(findKey(w)?.id, id, w);
  assert.equal(findKey('рйон'), null);
});

test('plural forms', () => {
  const f = ['объект', 'объекта', 'объектов'];
  assert.deepEqual([0, 1, 2, 5, 11, 21, 22, 25, 111, 101].map((n) => plural(n, f)),
    ['объектов', 'объект', 'объекта', 'объектов', 'объектов', 'объект', 'объекта', 'объектов', 'объектов', 'объект']);
});

test('highlight classes', () => {
  const s = 'тип:переулок,тупик рйон:x -кто:поэт | "26 мая" /\\С/ x:(a)~в';
  const spans = highlight(s).map((h) => `${h.cls}:${s.slice(h.pos, h.pos + h.len)}`);
  assert.deepEqual(spans, [
    'key:тип', 'sep::', 'value:переулок', 'sep:,', 'value:тупик', 'error:рйон', 'sep::', 'value:x',
    'op:-', 'key:кто', 'sep::', 'value:поэт', 'op:|', 'quoted:"26 мая"', 'regex:/\\С/',
    'key:x', 'sep::', 'paren:(', 'text:a', 'paren:)', 'tilde:~', 'value:в',
  ]);
});
