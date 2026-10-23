// Russian explanation of a parsed query, e.g.
// "Переулки, пересекающие улицы, проспекты и т.п. (кто: поэт) — в зоне".
import { fold } from './lexer.js';
import { COMPASS_RU } from './keys.js';
import { describeHouse } from './housequery.js';

// kind name (ru, synonyms, codes) -> "nominative|genitive|instrumental|group" (plural forms)
const KF = [
  ['улица street', 'улицы|улиц|улицами|street*'],
  ['проспект avenue', 'проспекты|проспектов|проспектами|street*'],
  ['шоссе highway', 'шоссе|шоссе|шоссе|street*'],
  ['дорога road', 'дороги|дорог|дорогами|street*'],
  ['набережная embankment', 'набережные|набережных|набережными|street*'],
  ['подъем ascent', 'подъёмы|подъёмов|подъёмами|street*'],
  ['спуск descent', 'спуски|спусков|спусками|street*'],
  ['аллея alley', 'аллеи|аллей|аллеями|street*'],
  ['ряд row', 'ряды|рядов|рядами|street*'],
  ['переулок lane', 'переулки|переулков|переулками|lane*'],
  ['тупик dead_end', 'тупики|тупиков|тупиками|lane*'],
  ['проезд passage', 'проезды|проездов|проездами|lane*'],
  ['съезд', 'съезды|съездов|съездами|lane*'],
  ['площадь square', 'площади|площадей|площадями|'],
  ['мост bridge', 'мосты|мостов|мостами|'],
  ['тоннель туннель tunnel', 'тоннели|тоннелей|тоннелями|'],
  ['балка ravine', 'балки|балок|балками|'],
  ['ущелье', 'ущелья|ущелий|ущельями|'],
  ['сквер garden', 'скверы|скверов|скверами|'],
  ['сад', 'сады|садов|садами|'],
  ['парк park', 'парки|парков|парками|'],
  ['река river', 'реки|рек|реками|water*'],
  ['ручей stream', 'ручьи|ручьёв|ручьями|water*'],
  ['канал canal', 'каналы|каналов|каналами|water*'],
  ['озеро lake', 'озёра|озёр|озёрами|water*'],
  ['метро станция metro', 'станции метро|станций метро|станциями метро|'],
  ['район district', 'районы|районов|районами|area*'],
  ['микрорайон neighbourhood', 'микрорайоны|микрорайонов|микрорайонами|area*'],
  ['квартал', 'кварталы|кварталов|кварталами|area*'],
  ['местность', 'местности|местностей|местностями|area*'],
  ['прочее other', 'прочие объекты|прочих объектов|прочими объектами|'],
];
const KIND_FORMS = {};
for (const [names, forms] of KF) {
  const [n, g, i, grp] = forms.split('|');
  for (const nm of names.split(' ')) KIND_FORMS[nm] = { nom: n, gen: g, ins: i, grp };
}
const GROUP_FORMS = {
  'street*': { nom: 'улицы, проспекты и т.п.', gen: 'улиц, проспектов и т.п.', ins: 'улицами, проспектами и т.п.' },
  'lane*': { nom: 'переулки, тупики и проезды', gen: 'переулков, тупиков и проездов', ins: 'переулками, тупиками и проездами' },
  'water*': { nom: 'реки, ручьи, каналы и озёра', gen: 'рек, ручьёв, каналов и озёр', ins: 'реками, ручьями, каналами и озёрами' },
  'area*': { nom: 'районы и микрорайоны', gen: 'районов и микрорайонов', ins: 'районами и микрорайонами' },
};
const OBJ = { nom: 'объекты', gen: 'объектов', ins: 'объектами' };
const EPONYM_FILTERS = new Set(['who', 'bio', 'century', 'gender', 'own']);

function cap(s) { return s ? s[0].toUpperCase() + s.slice(1) : s; }
function q(s) { return `«${s}»`; }
function joinAnd(parts) {
  if (parts.length <= 1) return parts.join('');
  return parts.slice(0, -1).join(', ') + ' и ' + parts[parts.length - 1];
}
function joinOr(parts) { return parts.join(' или '); }
/** Preposition "с" before an instrumental phrase ("со скверами"). */
function withS(phrase) {
  return /^[сзшжщ][^аеёиоуыэюя]/i.test(phrase) ? `со ${phrase}` : `с ${phrase}`;
}

/** Plural forms {nom, gen, ins} of one `тип:` value. */
function kindForms(item) {
  const v = fold(item.value);
  const f = KIND_FORMS[v];
  if (item.star) {
    const g = (f && f.grp) || (GROUP_FORMS[v] ? v : null) || (GROUP_FORMS[v + '*'] ? v + '*' : null);
    if (g && GROUP_FORMS[g]) return GROUP_FORMS[g];
  }
  if (GROUP_FORMS[v]) return GROUP_FORMS[v];
  if (f) return f;
  return { nom: `объекты типа ${q(item.raw)}`, gen: `объектов типа ${q(item.raw)}`, ins: `объектами типа ${q(item.raw)}` };
}

function numText(nm, unit = '') {
  if (nm.op === 'range') return `${nm.a}–${nm.b}${unit}`;
  if (nm.op === '=') return `${nm.a}${unit}`;
  const op = { '>': 'больше', '<': 'меньше', '>=': 'не меньше', '<=': 'не больше' }[nm.op];
  return `${op} ${nm.a}${unit}`;
}

function eponymPart(f, negated) {
  const ne = negated ? 'не ' : '';
  switch (f.key) {
    case 'who': return `кто: ${ne}${joinOr(f.items.map((i) => i.raw))}`;
    case 'bio': return `био: ${ne}${joinOr(f.items.map((i) => (i.mode === 'regex' ? `/${i.value}/` : q(i.value))))}`;
    case 'century': return `век: ${ne}${joinOr(f.nums.map((n) => numText(n)))}`;
    case 'gender': return `пол: ${ne}${f.items.map((i) => (i.value === 'f' ? 'женский' : 'мужской')).join(' или ')}`;
    case 'own': return negated
      ? (f.items[0].value === 'yes' ? 'не грузин' : 'не иностранец')
      : f.items.map((i) => (i.value === 'yes' ? 'грузин' : 'иностранец')).join(' или ');
    default: return '';
  }
}

function textPhrase(t) {
  if (t.mode === 'regex') return `с названием по шаблону /${t.value}/`;
  return `с ${q(t.value)} в названии`;
}

function nameItemText(it) {
  switch (it.mode) {
    case 'prefix': return `на ${q(it.value)}`;
    case 'suffix': return `на …${it.value}`;
    case 'exact': return `ровно ${q(it.value)}`;
    case 'regex': return `по шаблону /${it.value}/`;
    default: return `с ${q(it.value)}`;
  }
}

function placeName(it, opts, which) {
  const lbl = opts.labels && opts.labels.get(it);
  return lbl || cap(it.raw);
}

/** Phrase for a single non-type, non-eponym filter (participle / prepositional phrase). */
function filterPhrase(f, opts) {
  switch (f.key) {
    case 'name': return `с названием ${joinOr(f.items.map(nameItemText))}`;
    case 'district': return f.items.length === 1 ? `в районе ${placeName(f.items[0], opts)}` : `в районах ${joinOr(f.items.map((i) => placeName(i, opts)))}`;
    case 'in': return `в ${joinOr(f.items.map((i) => placeName(i, opts)))}`;
    case 'house': return f.items.map((i) => describeHouse(i.q)).join(' или ');
    case 'houses': return `с числом домов ${joinOr(f.nums.map((n) => numText(n)))}`;
    case 'len': return `длиной ${joinOr(f.nums.map((n) => numText(n, ' м')))}`;
    case 'ord': return `№ ${joinOr(f.nums.map((n) => numText(n)))}`;
    case 'cross':
      if (f.dir) return `уходящие на ${COMPASS_RU[f.dir.bit]} от перекрёстка ${withS(subText(f.sub, 'ins', opts))}`;
      return `пересекающие ${subText(f.sub, 'nom', opts)}`;
    case 'near': {
      const r = f.radius ? f.radius.m : 200;
      return f.me ? `в радиусе ${r} м от меня` : `в радиусе ${r} м от ${subText(f.sub, 'gen', opts)}`;
    }
    case 'family':
      if (f.sub.type === 'text') return `из семьи ${q(f.sub.value)}`;
      return `из семей: ${subText(f.sub, 'nom', opts)}`;
    case 'old':
      if (f.sub.type === 'text') return f.sub.mode === 'regex' ? `с прежним названием по шаблону /${f.sub.value}/` : `с прежним названием ${q(f.sub.value)}`;
      return `с прежним названием ${oldSubText(f.sub)}`;
    default: return '';
  }
}

function oldSubText(node) {
  // only eponym filters and text inside было:(…)
  if (node.type === 'text') return node.mode === 'regex' ? `/${node.value}/` : q(node.value);
  if (node.type === 'filter') return `(${eponymPart(node, false) || node.key})`;
  if (node.type === 'not') return `не ${oldSubText(node.item)}`;
  if (node.type === 'and') return node.items.map(oldSubText).join(' ');
  if (node.type === 'or') return node.items.map(oldSubText).join(' или ');
  return '';
}

/** Explanation of a subquery in the given case ('nom' — also accusative plural, 'gen'). */
function subText(node, cs, opts) {
  if (!node) return '';
  if (node.type === 'text') return node.mode === 'regex' ? `/${node.value}/` : q(node.value);
  if (node.type === 'or' && node.items.every((n) => n.type === 'text')) return node.items.map((n) => subText(n, cs, opts)).join(' или ');
  return describe(node, cs, opts, false);
}

/** Describe a node as a noun phrase: subject + modifiers. */
function describe(node, cs, opts, top) {
  if (node.type === 'or') {
    const merged = mergeOr(node);
    if (merged) return describe(merged, cs, opts, top);
    const parts = node.items.map((n) => describe(n, cs, opts, false));
    return parts.join(' или ');
  }
  const items = node.type === 'and' ? node.items : [node];
  const types = [];
  const eponym = [];
  const texts = [];
  const negTexts = [];
  const mods = [];
  for (const it of items) {
    if (it.type === 'filter' && it.key === 'type') types.push(it);
    else if (it.type === 'filter' && EPONYM_FILTERS.has(it.key)) eponym.push(eponymPart(it, false));
    else if (it.type === 'text') texts.push(it);
    else if (it.type === 'not' && it.item.type === 'filter' && EPONYM_FILTERS.has(it.item.key)) eponym.push(eponymPart(it.item, true));
    else if (it.type === 'not' && it.item.type === 'text') negTexts.push(it.item);
    else if (it.type === 'not' && it.item.type === 'filter' && it.item.key === 'type') {
      mods.push(`кроме: ${it.item.items.map((i) => kindForms(i).nom).join(' и ')}`);
    } else if (it.type === 'not') mods.push(`кроме: ${describe(it.item, 'nom', opts, false)}`);
    else if (it.type === 'filter') mods.push(filterPhrase(it, opts));
    else if (it.type === 'and' || it.type === 'or') mods.push(`(${describe(it, 'nom', opts, false)})`);
  }
  let subject;
  if (types.length) {
    subject = types.map((t) => joinAnd(t.items.map((i) => kindForms(i)[cs]))).join(' и ');
  } else {
    subject = OBJ[cs];
  }
  let s = subject;
  if (eponym.length) s += ` (${eponym.join(', ')})`;
  const phrases = [];
  if (texts.length) {
    const words = texts.filter((t) => t.mode !== 'regex');
    const regs = texts.filter((t) => t.mode === 'regex');
    if (words.length) phrases.push(`с ${words.map((t) => q(t.value)).join(' и ')} в названии`);
    for (const r of regs) phrases.push(textPhrase(r));
  }
  if (negTexts.length) phrases.push(`без ${negTexts.map((t) => (t.mode === 'regex' ? `/${t.value}/` : q(t.value))).join(' и ')} в названии`);
  phrases.push(...mods.filter(Boolean));
  // Participial phrases (and "кроме") take a comma after the noun; prepositional ones do not.
  phrases.forEach((ph, i) => {
    const participle = /^(пересекающие|уходящие|кроме)/.test(ph);
    s += (participle || i > 0 ? ', ' : ' ') + ph;
  });
  return s;
}

/** OR of filters with the same enumerated key reads better as one filter with a value list. */
function mergeOr(node) {
  const keys = new Set(['who', 'type', 'in', 'district', 'house', 'bio', 'gender']);
  if (!node.items.every((n) => n.type === 'filter' && n.key === node.items[0].key && keys.has(n.key) && n.items)) return null;
  return { ...node.items[0], items: node.items.flatMap((n) => n.items) };
}

const SORT_RU = { name: 'по имени', len: 'по длине', houses: 'по числу домов', dist: 'по расстоянию', rel: 'по релевантности' };

/**
 * explain(ast, opts) -> Russian sentence.
 * opts: {zone: 'default'|'touch'|'whole'|'off'|null — effective zone mode (null = no zone set),
 *        labels: Map(valueItem -> display name) from the evaluator}
 * Without opts.zone the zone suffix is omitted.
 */
export function explain(ast, opts = {}) {
  if (!ast) return '';
  const root = ast.type === 'query' ? ast : { type: 'query', expr: ast };
  let s = root.expr ? cap(describe(root.expr, 'nom', opts, true)) : 'Все объекты';
  if ('zone' in opts) {
    const z = opts.zone;
    if (z === 'default') s += ' — в зоне';
    else if (z === 'touch') s += ' — задевают зону';
    else if (z === 'whole') s += ' — целиком в зоне';
    else if (z === 'off') s += ' — весь город (зона выключена)';
    else s += ' — весь город';
  }
  if (root.sort) s += `; сортировка ${SORT_RU[root.sort] || ''}`.trimEnd();
  return s;
}
