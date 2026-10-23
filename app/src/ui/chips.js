// Chips under the query: they append/toggle DSL tokens; their "on" state is read back from the
// DSL string (the string is the source of truth). Pickers open bottom sheets.
import { log } from '../log/log.js';
import { h, openSheet, clear } from './dom.js';
import { state } from './state.js';
import {
  findAtom, listValues, toggleListValue, setAtom, appendAtom, quoteValue, crossSubqueries,
} from './dsledit.js';

const TYPE_KEYS = ['тип', 't', 'type'];
const HOUSE_KEYS = ['дом', 'h'];
const DISTRICT_KEYS = ['район', 'р', 'd'];
const IN_KEYS = ['в', 'in'];
const WHO_KEYS = ['кто', 'e', 'who'];
const ZONE_KEYS = ['зона', 'zone'];

const TYPE_CHIPS = [
  { label: 'Улица*', value: 'улица*' },
  { label: 'Переулок', value: 'переулок' },
  { label: 'Тупик', value: 'тупик' },
  { label: 'Переулок*', value: 'переулок*' },
  { label: 'Площадь', value: 'площадь' },
];

/**
 * createChips({getQuery, setQuery}) -> {el, update(text)}
 */
export function createChips({ getQuery, setQuery }) {
  const el = h('div.hscroll.chips', { role: 'toolbar', 'aria-label': 'Быстрые фильтры' });
  const buttons = [];

  function chip(label, onClick, isOn) {
    const b = h('button.chip', { type: 'button' }, label);
    b.addEventListener('pointerdown', (e) => e.preventDefault());
    b.addEventListener('click', () => {
      try {
        onClick();
      } catch (err) {
        log.error('chips', 'chip action failed', { label, err: String(err) });
      }
    });
    buttons.push({ b, isOn, label });
    el.append(b);
    return b;
  }

  for (const t of TYPE_CHIPS) {
    chip(t.label, () => setQuery(toggleListValue(getQuery(), TYPE_KEYS, 'тип', t.value)),
      (q) => listValues(q, TYPE_KEYS).includes(t.value));
  }

  const crossChip = chip('× пересекает…', () => openCrossBuilder(getQuery, setQuery),
    (q) => crossSubqueries(q).length > 0);
  const whoChip = chip('Кто…', () => openWhoPicker(getQuery, setQuery), (q) => Boolean(findAtom(q, WHO_KEYS)));
  const houseChip = chip('Дом №', () => openHouseKeypad(getQuery, setQuery), (q) => Boolean(findAtom(q, HOUSE_KEYS)));
  const distChip = chip('Район', () => openAreaPicker(getQuery, setQuery),
    (q) => Boolean(findAtom(q, DISTRICT_KEYS) || findAtom(q, IN_KEYS)));
  const zoneChip = chip('Весь город', () => {
    const q = getQuery();
    if (findAtom(q, ZONE_KEYS)) setQuery(setAtom(q, ZONE_KEYS, ''));
    else setQuery(appendAtom(q, 'зона:выкл'));
  }, (q) => /^(выкл|off)$/i.test(findAtom(q, ZONE_KEYS)?.value || ''));

  function update(q) {
    for (const { b, isOn } of buttons) b.classList.toggle('on', Boolean(isOn(q)));
    const who = findAtom(q, WHO_KEYS);
    whoChip.textContent = who ? `Кто: ${who.value}` : 'Кто…';
    const house = findAtom(q, HOUSE_KEYS);
    houseChip.textContent = house ? `Дом ${house.value}` : 'Дом №';
    const d = findAtom(q, DISTRICT_KEYS) || findAtom(q, IN_KEYS);
    distChip.textContent = d ? `${d.key === 'в' || d.key === 'in' ? 'В' : 'Район'}: ${d.value}` : 'Район';
    const subs = crossSubqueries(q);
    crossChip.textContent = subs.length ? `× ${subs[0].length > 18 ? subs[0].slice(0, 18) + '…' : subs[0]}` : '× пересекает…';
    zoneChip.hidden = !state.zone;
  }

  update(getQuery());
  return { el, update };
}

/* ---------- pickers ---------- */

function whoValue(node) {
  const a = (node.aliases && node.aliases[0]) || node.ru || node.id;
  return quoteValue(String(a).toLowerCase());
}

function catCount(id) {
  try {
    const b = state.index?.catBits?.get(id);
    return b ? b.count() : null;
  } catch {
    return null;
  }
}

export function openWhoPicker(getQuery, setQuery, { onPick } = {}) {
  const s = openSheet({ title: 'Кто: категория эпонима', full: true });
  const tax = state.pack?.eponyms?.taxonomy || [];
  const filter = h('input.field', {
    type: 'search', placeholder: 'Фильтр, напр. поэт', autocapitalize: 'off', autocorrect: 'off', spellcheck: 'false',
  });
  const list = h('div.list-col');
  const pick = (val) => {
    s.close();
    if (onPick) { onPick(val); return; }
    setQuery(setAtom(getQuery(), WHO_KEYS, `кто:${val}`));
  };
  function render() {
    clear(list);
    const f = filter.value.trim().toLowerCase();
    const kinds = state.pack?.eponyms?.kinds || {};
    for (const g of tax) {
      const kids = (g.children || []).filter((c) => !f || (c.ru || '').toLowerCase().includes(f)
        || (c.aliases || []).some((a) => a.toLowerCase().includes(f)));
      const gMatch = !f || (g.ru || '').toLowerCase().includes(f);
      if (!gMatch && kids.length === 0) continue;
      const n = catCount(g.id);
      list.append(h('button.list-btn', { type: 'button', onclick: () => pick(whoValue(g)) },
        h('b', g.ru), n !== null ? h('small', `${n}`) : null));
      for (const c of kids) {
        const cn = catCount(c.id);
        list.append(h('button.list-btn', { type: 'button', style: { marginLeft: '16px', width: 'calc(100% - 16px)' }, onclick: () => pick(whoValue(c)) },
          h('span', c.ru), cn !== null ? h('small', `${cn}`) : null));
      }
    }
    const kindEntries = Object.entries(kinds).filter(([, al]) => !f || al.some((a) => a.includes(f)));
    if (kindEntries.length) {
      list.append(h('div.section-title', 'Вид эпонима'));
      for (const [, al] of kindEntries) {
        list.append(h('button.list-btn', { type: 'button', onclick: () => pick(quoteValue(al[0])) }, h('span', al[0])));
      }
    }
    if (!list.childElementCount) list.append(h('p.muted', 'Ничего не найдено. Можно ввести профессию в запросе: кто:хормейстер'));
  }
  filter.addEventListener('input', render);
  s.body.append(filter, list);
  render();
}

export function openHouseKeypad(getQuery, setQuery) {
  const s = openSheet({ title: 'Номер дома' });
  const cur = findAtom(getQuery(), HOUSE_KEYS);
  let value = cur ? cur.value.replace(/^=/, '').replace(/\?$/, '') : '';
  let exact = cur ? cur.value.startsWith('=') : false;
  let probable = cur ? cur.value.endsWith('?') : false;
  const display = h('input.field.mono', {
    type: 'text', inputmode: 'numeric', 'aria-label': 'Номер дома', value,
    autocapitalize: 'off', autocorrect: 'off', spellcheck: 'false', style: { fontSize: '1.4rem', textAlign: 'center' },
  });
  display.addEventListener('input', () => { value = display.value; });
  const keys = h('div', { style: { display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '8px' } });
  const add = (k) => { value += k; display.value = value; };
  for (const k of ['1', '2', '3', 'а', '4', '5', '6', 'б', '7', '8', '9', 'в', '/', '0', '-', '⌫']) {
    const b = h('button.btn', { type: 'button', style: { minHeight: '52px', fontSize: '1.2rem' } }, k);
    b.addEventListener('click', () => {
      if (k === '⌫') { value = value.slice(0, -1); display.value = value; } else add(k);
    });
    keys.append(b);
  }
  const exactSw = h('input', { type: 'checkbox', checked: exact });
  const probSw = h('input', { type: 'checkbox', checked: probable });
  exactSw.addEventListener('change', () => { exact = exactSw.checked; if (exact) { probable = false; probSw.checked = false; } });
  probSw.addEventListener('change', () => { probable = probSw.checked; if (probable) { exact = false; exactSw.checked = false; } });
  const ok = h('button.btn.primary', { type: 'button' }, 'Применить');
  const remove = h('button.btn', { type: 'button' }, 'Убрать фильтр');
  const special = h('div.btn-row',
    h('button.btn.small', { type: 'button', onclick: () => { s.close(); setQuery(setAtom(getQuery(), HOUSE_KEYS, 'дом:есть')); } }, 'дома есть'),
    h('button.btn.small', { type: 'button', onclick: () => { s.close(); setQuery(setAtom(getQuery(), HOUSE_KEYS, 'дом:нет')); } }, 'домов нет'));
  ok.addEventListener('click', () => {
    const v = value.trim();
    s.close();
    if (!v) { setQuery(setAtom(getQuery(), HOUSE_KEYS, '')); return; }
    const tok = `дом:${exact ? '=' : ''}${v}${probable && !/-/.test(v) ? '?' : ''}`;
    setQuery(setAtom(getQuery(), HOUSE_KEYS, tok));
    log.info('chips', 'house filter set', { tok });
  });
  remove.addEventListener('click', () => { s.close(); setQuery(setAtom(getQuery(), HOUSE_KEYS, '')); });
  s.body.append(
    display,
    keys,
    h('label.switch', exactSw, h('span', 'Строго этот номер (без букв и дробей)')),
    h('label.switch', probSw, h('span', 'Плюс «вероятно есть» (номер между соседними)')),
    special,
    h('div.btn-row', ok, remove),
  );
}

export function openAreaPicker(getQuery, setQuery) {
  const s = openSheet({ title: 'Район или микрорайон', full: true });
  const c = state.pack?.core;
  const filter = h('input.field', {
    type: 'search', placeholder: 'Фильтр: сололаки, ვაკე, vake', autocapitalize: 'off', autocorrect: 'off', spellcheck: 'false',
  });
  const list = h('div.list-col');
  const pick = (keys, key, name) => {
    s.close();
    let q = getQuery();
    q = setAtom(q, DISTRICT_KEYS, '');
    q = setAtom(q, IN_KEYS, '');
    setQuery(name ? setAtom(q, keys, `${key}:${quoteValue(name.toLowerCase())}`) : q);
  };
  function match(x, f) {
    return !f || [x.ru, x.ka, x.en].some((n) => n && n.toLowerCase().includes(f));
  }
  function render() {
    clear(list);
    const f = filter.value.trim().toLowerCase();
    list.append(h('button.list-btn', { type: 'button', onclick: () => pick(DISTRICT_KEYS, 'район', '') }, h('span', 'Без фильтра по району')));
    const ds = (c?.districts || []).filter((d) => match(d, f));
    if (ds.length) list.append(h('div.section-title', 'Официальные районы (район:)'));
    for (const d of ds) {
      list.append(h('button.list-btn', { type: 'button', onclick: () => pick(DISTRICT_KEYS, 'район', d.ru || d.en || d.ka) },
        h('span', d.ru || d.ka), h('small.ka', d.ka || '')));
    }
    const ps = (c?.places || []).map((p, i) => ({ ...p, i })).filter((p) => match(p, f))
      .sort((a, b) => (a.ru || a.ka || '').localeCompare(b.ru || b.ka || '', 'ru'));
    if (ps.length) list.append(h('div.section-title', 'Микрорайоны и кварталы (в:)'));
    for (const p of ps.slice(0, 300)) {
      list.append(h('button.list-btn', { type: 'button', onclick: () => pick(IN_KEYS, 'в', p.ru || p.en || p.ka) },
        h('span', p.ru || p.ka), h('small.ka', p.ka || '')));
    }
  }
  filter.addEventListener('input', render);
  s.body.append(filter, list);
  render();
}

const DIRS = [['', 'любое'], ['с', 'С'], ['св', 'СВ'], ['в', 'В'], ['юв', 'ЮВ'], ['ю', 'Ю'], ['юз', 'ЮЗ'], ['з', 'З'], ['сз', 'СЗ']];

export function openCrossBuilder(getQuery, setQuery) {
  const s = openSheet({ title: 'Пересекает…', full: true });
  const sub = h('input.field.mono', {
    type: 'text', placeholder: 'подзапрос, напр. тип:улица* кто:поэт',
    autocapitalize: 'off', autocorrect: 'off', spellcheck: 'false', enterkeyhint: 'done',
  });
  const existing = crossSubqueries(getQuery());
  if (existing.length) sub.value = existing[0];
  let dir = '';
  const dirSeg = h('div.seg', { role: 'group', 'aria-label': 'Куда уходит от перекрёстка' });
  for (const [v, label] of DIRS) {
    const b = h('button', { type: 'button', 'aria-pressed': String(v === dir) }, label);
    b.addEventListener('click', () => {
      dir = v;
      for (const x of dirSeg.children) x.setAttribute('aria-pressed', String(x === b));
    });
    dirSeg.append(b);
  }
  const quick = h('div.btn-row');
  const addSub = (tok) => { sub.value = sub.value.trim() ? `${sub.value.trim()} ${tok}` : tok; };
  for (const [label, tok] of [['улицы*', 'тип:улица*'], ['переулки*', 'тип:переулок*'], ['сквер', 'тип:сквер'], ['река', 'тип:река'], ['площадь', 'тип:площадь'], ['метро', 'тип:метро']]) {
    quick.append(h('button.btn.small', { type: 'button', onclick: () => addSub(tok) }, label));
  }
  const whoBtn = h('button.btn.small', { type: 'button' }, 'кто…');
  whoBtn.addEventListener('click', () => openWhoPicker(getQuery, setQuery, { onPick: (v) => addSub(`кто:${v}`) }));
  quick.append(whoBtn);
  const ok = h('button.btn.primary', { type: 'button' }, 'Добавить в запрос');
  ok.addEventListener('click', () => {
    const v = sub.value.trim();
    if (!v) { sub.focus(); return; }
    const tok = `x:(${v})${dir ? '~' + dir : ''}`;
    s.close();
    const q = getQuery();
    const x = findAtom(q, ['x', 'пересекает', 'cross']);
    setQuery(x ? setAtom(q, ['x', 'пересекает', 'cross'], tok) : appendAtom(q, tok));
    log.info('chips', 'cross filter set', { tok });
  });
  s.body.append(
    h('p.muted.small', 'Объекты, которые пересекают хотя бы один объект подзапроса. Подзапрос считается по всему городу, точка пересечения — в зоне.'),
    sub, quick,
    h('div.section-title', 'Направление от перекрёстка'),
    dirSeg,
    h('div.btn-row', ok),
  );
}
