// Parser tests: a table of >150 query lines (valid, partial and erroneous) with the expected
// AST in compact form (see dsl_helpers.mjs sexpr) or the expected error, plus property tests.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { parse, walk } from '../../app/src/dsl/parser.js';
import { sexpr } from './dsl_helpers.mjs';

// [query, expected sexpr | {err, pos, len, sugg}, {warn}?]
const CASES = [
  ["дом:3","house:any3"],
  ["тип:улица* дом:3","(and type:улица* house:any3)"],
  ["тип:переулок x:(тип:улица* кто:поэт)","(and type:переулок x((and type:улица* who:поэт)))"],
  ["кто:хормейстер дом:24","(and who:хормейстер house:any24)"],
  ["кто:наука дом:2","(and who:наука house:any2)"],
  ["кто:дирижёр было:крылов","(and who:дирижер old('крылов'))"],
  ["было:(био:гэс)","old(bio:гэс)"],
  ["имя:/\\С{5}/","name:regex=\\С{5}"],
  ["x:(тип:сквер рижск)~в дом:16","(and x((and type:сквер 'рижск'))~2 house:any16)"],
  ["x:(чаргали)","x('чаргали')"],
  ["тип:тупик дом:нет в:сололаки","(and type:тупик house:none in:сололаки)"],
  ["кто:писатель пол:ж","(and who:писатель gender:f)"],
  ["тип:улица -кто:персона","(and type:улица (not who:персона))"],
  ["рядом:(тип:метро)~300 тип:переулок*","(and near(type:метро)~300 type:переулок*)"],
  ["семья:важа-пшавела","fam('важа-пшавела')"],
  ["чавчав","'чавчав'"],
  ["ჭავჭ","'ჭავჭ'"],
  ["chavch","'chavch'"],
  ["leselidze","'leselidze'"],
  ["\"26 мая\"","\"26 мая\""],
  ["«26 мая»","\"26 мая\""],
  ["“Leselidze St”","\"Leselidze St\""],
  ["/\\d+/","/\\d+/"],
  ["чав руставели","(and 'чав' 'руставели')"],
  ["ЧАВЧАВАДЗЕ","'ЧАВЧАВАДЗЕ'"],
  ["ёлка","'ёлка'"],
  ["t:переулок","type:переулок"],
  ["type:lane","type:lane"],
  ["тип=тупик","type:тупик"],
  ["n:^ვაჟა","name:prefix=ვაჟა"],
  ["name:ли$","name:suffix=ли"],
  ["имя:^шота$","name:exact=шота"],
  ["имя:\"26 мая\"","name:sub=26 мая"],
  ["р:мтацминда","district:мтацминда"],
  ["d:mtatsminda","district:mtatsminda"],
  ["район:мтацминда,крцаниси","district:мтацминда,крцаниси"],
  ["in:sololaki","in:sololaki"],
  ["в:\"старый город\"","in:старый город"],
  ["e:poet","who:poet"],
  ["who:lit.poet","who:lit.poet"],
  ["кто:царь,полководец","who:царь,полководец"],
  ["bio:гэс","bio:гэс"],
  ["био:\"тбилисской консерватории\"","bio:тбилисской консерватории"],
  ["био:/инженер.*гэс/","bio:/инженер.*гэс/"],
  ["век:19","century:=19"],
  ["c:11-12","century:11-12"],
  ["век:XIX","century:=19"],
  ["век:>18","century:>18"],
  ["пол:м","gender:m"],
  ["g:f","gender:f"],
  ["пол:жен","gender:f"],
  ["свой:да","own:yes"],
  ["ge:нет","own:no"],
  ["дом:=3","house:exact3"],
  ["дом:3а","house:lit3ა"],
  ["дом:3ა","house:lit3ა"],
  ["дом:3a","house:lit3ა"],
  ["дом:3/16","house:sub3/16"],
  ["дом:3а/1","house:sub3ა/1"],
  ["дом:1-9","house:range1-9"],
  ["дом:3?","house:any3?"],
  ["дом:есть","house:has"],
  ["h:нет","house:none"],
  ["дом:3,5,7","house:any3,any5,any7"],
  ["дом:221-4","house:sub221/4"],
  ["домов>30","houses:>30"],
  ["hn=0","houses:=0"],
  ["домов >= 10","houses:>=10"],
  ["домов:10-50","houses:10-50"],
  ["длина>1000","len:>1000"],
  ["len<=50","len:<=50"],
  ["длина>1км","len:>1000"],
  ["x:(кто:поэт)","x(who:поэт)"],
  ["пересекает:(тип:река)","x(type:река)"],
  ["cross:(t:river)~e","x(type:river)~2"],
  ["х:(тип:сквер)~юв","x(type:сквер)~3"],
  ["x:чаргали","x('чаргали')"],
  ["x:чаргали,руставели~с","x((or 'чаргали' 'руставели'))~0"],
  ["рядом:я~500","near(me)~500"],
  ["near:me","near(me)"],
  ["рядом:(тип:метро)","near(type:метро)"],
  ["рядом:метро~1км","near('метро')~1000"],
  ["семья:(кто:поэт)","fam(who:поэт)"],
  ["f:асатиани","fam('асатиани')"],
  ["№:2","ord:=2"],
  ["ord:1-3","ord:1-3"],
  ["no:2","ord:=2"],
  ["было:ленин","old('ленин')"],
  ["old:\"Leselidze St\"","old(\"Leselidze St\")"],
  ["было:/ленин/","old(/ленин/)"],
  ["было:(кто:инженер)","old(who:инженер)"],
  ["было:(кто:поэт | пол:ж)","old((or who:поэт gender:f))"],
  ["было:(-кто:персона)","old((not who:персона))"],
  ["зона:выкл","[zone=off] ∅"],
  ["zone:touch","[zone=touch] ∅"],
  ["зона:целиком дом:3","[zone=whole] house:any3"],
  ["сорт:длина","[sort=len] ∅"],
  ["sort:dist тип:улица","[sort=dist] type:улица"],
  ["кто:поэт сорт:имя","[sort=name] who:поэт"],
  ["кто:царь | кто:полководец","(or who:царь who:полководец)"],
  ["кто:царь или кто:полководец","(or who:царь who:полководец)"],
  ["a b | c d","(or (and 'a' 'b') (and 'c' 'd'))"],
  ["(a | b) c","(and (or 'a' 'b') 'c')"],
  ["a (b | c)","(and 'a' (or 'b' 'c'))"],
  ["-a","(not 'a')"],
  ["!a","(not 'a')"],
  ["не a","(not 'a')"],
  ["не не a","(not (not 'a'))"],
  ["-(кто:поэт | кто:писатель)","(not (or who:поэт who:писатель))"],
  ["тип:улица и кто:поэт","(and type:улица who:поэт)",{"warn":["слово «и» не нужно: пробел между условиями уже означает И"]}],
  ["((a))","'a'"],
  ["a | b | c","(or 'a' 'b' 'c')"],
  ["тип: улица","type:улица"],
  ["тип:переулок, тупик","type:переулок,тупик"],
  ["тип:переулок ,тупик","type:переулок,тупик"],
  ["x:(тип:улица","x(type:улица)",{"warn":["не закрыта скобка «(» — закрыта автоматически"]}],
  ["x:(тип:улица кто:поэт","x((and type:улица who:поэт))",{"warn":["не закрыта скобка «(» — закрыта автоматически"]}],
  ["\"26 мая","\"26 мая\"",{"warn":["не закрыта кавычка — закрыта автоматически"]}],
  ["имя:/ли","name:regex=ли",{"warn":["не закрыт шаблон «/» — закрыт автоматически"]}],
  ["x:((a | b)","x((or 'a' 'b'))",{"warn":["не закрыта скобка «(» — закрыта автоматически"]}],
  ["","∅"],
  ["рйон:мтацминда",{"err":"неизвестный ключ «рйон» — может быть, «район»?","pos":0,"len":4,"sugg":["район"]}],
  ["тпи:улица",{"err":"неизвестный ключ «тпи» — может быть, «тип»?","pos":0,"len":3,"sugg":["тип"]}],
  ["кот:поэт",{"err":"неизвестный ключ «кот» — может быть, «кто»?","pos":0,"len":3,"sugg":["кто","пол","дом"]}],
  ["xyz:1",{"err":"неизвестный ключ «xyz» — может быть, «x»?","pos":0,"len":3,"sugg":["x"]}],
  ["тип:",{"err":"нет значения после «тип:» — тип объекта; * — с родственными","pos":4,"len":0,"sugg":[]}],
  ["кто:",{"err":"нет значения после «кто:» — категория эпонима","pos":4,"len":0,"sugg":[]}],
  ["тип:переулок,",{"err":"после «,» нужно ещё значение для «тип:»","pos":13,"len":0,"sugg":[]}],
  ["a |",{"err":"после «|» нужно ещё условие","pos":2,"len":1,"sugg":[]}],
  ["| a",{"err":"перед «|» нужно условие","pos":0,"len":1,"sugg":[]}],
  ["-",{"err":"после «-» нужно условие, например -кто:персона","pos":0,"len":1,"sugg":[]}],
  ["a -",{"err":"после «-» нужно условие, например -кто:персона","pos":2,"len":1,"sugg":[]}],
  ["()",{"err":"пустые скобки — внутри нужно условие","pos":0,"len":2,"sugg":[]}],
  [")",{"err":"лишняя закрывающая скобка «)»","pos":0,"len":1,"sugg":[""]}],
  ["a )",{"err":"лишняя закрывающая скобка «)»","pos":2,"len":1,"sugg":[""]}],
  ["x:()",{"err":"пустой подзапрос — например x:(тип:улица кто:поэт) или x:чаргали","pos":2,"len":2,"sugg":[]}],
  [",a",{"err":"запятая разделяет значения одного ключа: тип:переулок,тупик","pos":0,"len":1,"sugg":[]}],
  ["~в",{"err":"«~» ставится сразу после значения: x:(…)~в, рядом:(…)~300","pos":0,"len":1,"sugg":[]}],
  [":улица",{"err":"перед «:» нужен ключ, например тип:улица","pos":0,"len":1,"sugg":[]}],
  [">3",{"err":"перед «>» нужен числовой ключ: домов, длина, №, век","pos":0,"len":1,"sugg":[]}],
  ["дом>3",{"err":"сравнение «>» работает только для ключей домов, длина, №, век","pos":3,"len":1,"sugg":[]}],
  ["тип:(улица)",{"err":"у ключа «тип» нет подзапросов — перечислите значения через запятую","pos":4,"len":1,"sugg":[]}],
  ["x:(a)~вверх",{"err":"неизвестное направление «вверх» — можно: с, св, в, юв, ю, юз, з, сз","pos":6,"len":5,"sugg":["с","св","в","юв","ю","юз","з","сз"]}],
  ["x:(a)~восток","x('a')~2"],
  ["x:(a)~",{"err":"после «~» нужно направление: с св в юв ю юз з сз","pos":6,"len":0,"sugg":[]}],
  ["рядом:я~",{"err":"после «~» нужно расстояние в метрах, например ~300","pos":8,"len":0,"sugg":[]}],
  ["рядом:я~абв",{"err":"«абв» — не расстояние; например рядом:я~500","pos":8,"len":3,"sugg":[]}],
  ["рядом:я~9000",{"err":"слишком большое расстояние — не больше 5000 м","pos":8,"len":4,"sugg":["5000"]}],
  ["пол:x",{"err":"неизвестное значение «x» для «пол:» — можно: м, ж","pos":4,"len":1,"sugg":["ж","м"]}],
  ["свой:может",{"err":"неизвестное значение «может» для «свой:» — можно: да, нет","pos":5,"len":5,"sugg":["да","нет"]}],
  ["зона:везде",{"err":"неизвестное значение «везде» для «зона:» — можно: выкл, задевает, целиком, вкл","pos":5,"len":5,"sugg":["выкл","задевает","целиком","вкл"]}],
  ["сорт:цвет",{"err":"неизвестное значение «цвет» для «сорт:» — можно: имя, длина, домов, расстояние","pos":5,"len":4,"sugg":["имя","длина","домов","расстояние"]}],
  ["дом:abc",{"err":"не понимаю номер дома «abc» — примеры: 3, =3, 3а, 3/16, 1-9, 3?, есть, нет","pos":4,"len":3,"sugg":[]}],
  ["дом:3бв",{"err":"не понимаю номер дома «3бв» — примеры: 3, =3, 3а, 3/16, 1-9, 3?, есть, нет","pos":4,"len":3,"sugg":[]}],
  ["век:30",{"err":"век — число от 1 до 21 или диапазон, например век:19 или век:11-12","pos":4,"len":2,"sugg":[]}],
  ["век:абв",{"err":"«абв» — не число","pos":4,"len":3,"sugg":[]}],
  ["домов>abc",{"err":"«abc» — не число","pos":6,"len":3,"sugg":[]}],
  ["было:(тип:улица)",{"err":"в «было:(…)» можно только кто, био, век, пол, свой и текст, а не «тип»","pos":6,"len":3,"sugg":[]}],
  ["x:(зона:выкл)",{"err":"«зона:» действует на весь запрос — укажите его вне подзапросов и без «-»","pos":3,"len":4,"sugg":[]}],
  ["-зона:выкл",{"err":"«зона:» действует на весь запрос — укажите его вне подзапросов и без «-»","pos":1,"len":4,"sugg":[]}],
  ["имя:/(/",{"err":"шаблон /(/ не разбирается — проверьте скобки и символы \\ ( ) [ ] { }","pos":4,"len":3,"sugg":[]}],
  ["/[/",{"err":"шаблон /[/ не разбирается — проверьте скобки и символы \\ ( ) [ ] { }","pos":0,"len":3,"sugg":[]}],
  ["дом:/3/",{"err":"у «дом:» не бывает шаблонов /…/","pos":4,"len":3,"sugg":[]}],
  ["тип:/ули/",{"err":"у ключа «тип» не бывает шаблонов /…/","pos":4,"len":5,"sugg":[]}],
  ["тип:переулок~в",{"err":"«~» бывает только у x:(…) и рядом:(…), а не у «тип:»","pos":12,"len":1,"sugg":[]}],
  ["зона:выкл,целиком",{"err":"у «зона:» одно значение","pos":10,"len":7,"sugg":[]}],
  ["\"\"",{"err":"пустые кавычки","pos":0,"len":2,"sugg":[]}],
];

test('parse table: AST shape, warnings and errors', () => {
  assert.ok(CASES.length >= 150, `only ${CASES.length} cases`);
  for (const [q, want, extra] of CASES) {
    const r = parse(q);
    if (typeof want === 'string') {
      assert.ok(!r.error, `${q}: unexpected error ${r.error && r.error.message}`);
      assert.equal(sexpr(r.ast), want, q);
      assert.deepEqual(r.warnings, extra ? extra.warn : [], `${q}: warnings`);
    } else {
      assert.ok(r.error, `${q}: expected error, got ${r.ast && sexpr(r.ast)}`);
      assert.equal(r.error.message, want.err, q);
      assert.equal(r.error.pos, want.pos, `${q}: pos`);
      assert.equal(r.error.len, want.len, `${q}: len`);
      assert.deepEqual(r.error.suggestions, want.sugg, `${q}: suggestions`);
    }
  }
});

test('error positions are inside the input and suggestions replace the error span', () => {
  const q = 'тип:переулок рйон:мтацминда';
  const { error } = parse(q);
  assert.equal(q.slice(error.pos, error.pos + error.len), 'рйон');
  const fixed = q.slice(0, error.pos) + error.suggestions[0] + q.slice(error.pos + error.len);
  assert.equal(fixed, 'тип:переулок район:мтацминда');
  assert.ok(!parse(fixed).error);
});

test('strict mode rejects unclosed parentheses, quotes and regexes', () => {
  assert.equal(parse('x:(тип:улица', { strict: true }).error.message, 'не закрыта скобка «(»');
  assert.equal(parse('"26 мая', { strict: true }).error.message, 'не закрыта кавычка');
  assert.equal(parse('имя:/ли', { strict: true }).error.message, 'не закрыт шаблон «/»');
  assert.ok(!parse('x:(тип:улица)', { strict: true }).error);
});

test('an unclosed parenthesis in the middle is an error, not auto-closed', () => {
  // "(" closed automatically only at the end of input
  const r = parse('x:(тип:улица | ) a');
  assert.ok(r.error);
});

test('node positions cover the source text', () => {
  const q = 'тип:переулок x:(тип:улица* кто:поэт)~в -дом:3';
  const { ast } = parse(q);
  const spans = [];
  walk(ast, (n) => spans.push([n.type === 'filter' ? n.key : n.type, q.slice(n.pos, n.pos + n.len)]));
  assert.deepEqual(spans, [
    ['and', q],
    ['type', 'тип:переулок'],
    ['cross', 'x:(тип:улица* кто:поэт)~в'],
    ['and', 'тип:улица* кто:поэт'],
    ['type', 'тип:улица*'],
    ['who', 'кто:поэт'],
    ['not', '-дом:3'],
    ['house', 'дом:3'],
  ]);
  const f = ast.expr.items[1];
  assert.equal(q.slice(f.keyPos, f.keyPos + f.keyLen), 'x');
  assert.equal(q.slice(f.dir.pos, f.dir.pos + f.dir.len), 'в');
});

test('regex literals expand \\С and \\Г letter classes for ru and ka', () => {
  const re = parse('имя:/\\С{5}/').ast.expr.items[0].re;
  assert.ok(re.test('Цинамдзгвришвили'));
  assert.ok(re.test('ცინამძღვრიშვილის'));
  assert.ok(!re.test('руставели'));
  const vow = parse('/^\\Г/').ast.expr.re;
  assert.ok(vow.test('ираклия') && vow.test('ერეკლე') && !vow.test('бесики'));
  const inClass = parse('/[\\С\\Г]{3}/').ast.expr.re;
  assert.ok(inClass.test('абв'));
  assert.ok(parse('/Ё/').ast.expr.re.test('елка'), 'ё = е in regexes');
});

test('directives are hoisted to the root and do not leave empty branches', () => {
  assert.equal(sexpr(parse('(зона:выкл) кто:поэт').ast), '[zone=off] who:поэт');
  assert.equal(sexpr(parse('кто:поэт | зона:задевает').ast), '[zone=touch] who:поэт');
  assert.equal(sexpr(parse('зона:вкл').ast), '[zone=on] ∅');
  const { ast } = parse('сорт:длина тип:улица');
  assert.equal(ast.directives.length, 1);
  assert.equal(ast.directives[0].key, 'sort');
});

test('parse never throws on arbitrary input and errors stay within bounds', () => {
  const alphabet = ['тип', 'кто', ':', '=', '(', ')', '|', ',', '~', '-', '!', '"', '/', ' ', 'в', 'x', '3', '>', '<', 'не', 'или', 'улица', '*', '?', '^', '$', '№', 'ა', '\С'];
  let seed = 12345;
  const rnd = () => { seed = (seed * 1103515245 + 12345) & 0x7fffffff; return seed / 0x7fffffff; };
  for (let k = 0; k < 3000; k++) {
    let q = '';
    const len = 1 + Math.floor(rnd() * 12);
    for (let j = 0; j < len; j++) q += alphabet[Math.floor(rnd() * alphabet.length)];
    let r;
    assert.doesNotThrow(() => { r = parse(q); }, q);
    if (r.error) {
      assert.ok(r.error.pos >= 0 && r.error.pos + r.error.len <= q.length, `${q}: ${JSON.stringify(r.error)}`);
      assert.equal(typeof r.error.message, 'string');
      assert.ok(Array.isArray(r.error.suggestions));
    } else {
      assert.equal(r.ast.type, 'query');
    }
  }
});

test('ё folds to е in keys and values, case-insensitive keys', () => {
  assert.equal(sexpr(parse('КТО:Дирижёр').ast), 'who:дирижер');
  assert.equal(sexpr(parse('ТИП:Улица*').ast), 'type:улица*');
  assert.equal(sexpr(parse('Зона:ВЫКЛ').ast), '[zone=off] ∅');
});
