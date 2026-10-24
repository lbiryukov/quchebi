// Search screen: explanation line + count, errors with tap-to-fix suggestions, results grouped
// by family; the dock holds autocomplete, chips and the DSL field.
import { run } from '../dsl/eval.js';
import * as db from '../store/db.js';
import { log } from '../log/log.js';
import { h, clear, openSheet, promptSheet, toast } from './dom.js';
import { state, emit, on, evalCtx } from './state.js';
import { createQueryInput } from './query-input.js';
import { createChips } from './chips.js';
import { crossSubqueries, houseQuery } from './dsledit.js';
import {
  feat, kindRu, mainName, ruName, shortName, placeLabel, eponymTag, crossingsOf, houseRows, isPlace, placeAddressText,
} from './feature.js';
import { fmtLen, count } from './format.js';
import { openCard } from './card.js';

const PAGE_GROUPS = 120;
const EXAMPLES = [
  'дом:3',
  'тип:улица* дом:3',
  'тип:переулок x:(тип:улица* кто:поэт)',
  'кто:писатель пол:ж',
  'рядом:(тип:метро)~300 тип:переулок*',
  'семья:важа-пшавела',
];

let qi = null;
let chips = null;
let out = null;
let runTimer = 0;
let shownGroups = PAGE_GROUPS;
let lastGroups = [];
let crossInfo = null; // {bits} of the x:(...) subquery for "× street · poet" tags

export function initSearch() {
  out = document.getElementById('search-out');
  const dock = document.getElementById('search-dock');
  qi = createQueryInput({
    onInput: (text) => {
      state.query = text;
      chips?.update(text);
      scheduleRun();
    },
    onSubmit: (text) => {
      runNow();
      if (text.trim()) db.addHistory(text.trim()).catch((err) => log.warn('search', 'addHistory failed', { err: String(err) }));
    },
    onHistory: openHistory,
    onSave: saveCurrent,
  });
  chips = createChips({
    getQuery: () => qi.getValue(),
    setQuery: (text) => {
      qi.setValue(text);
      log.debug('search', 'query set by chip', { text });
    },
  });
  dock.replaceChildren(qi.suggestEl, chips.el, qi.el);

  on('zone', () => { chips.update(qi.getValue()); scheduleRun(0); });
  on('index', () => scheduleRun(0));
  on('packReady', () => scheduleRun(0));
  on('me', () => { if (/рядом|near|расстояние/.test(qi.getValue())) scheduleRun(0); });
  on('setQuery', (text) => { qi.setValue(text); });
  renderEmpty();
}

export function setQuery(text, { focus = false } = {}) {
  qi?.setValue(text);
  if (focus) qi?.focus();
}

export function getQuery() {
  return qi ? qi.getValue() : '';
}

function scheduleRun(ms = 140) {
  clearTimeout(runTimer);
  runTimer = setTimeout(runNow, ms);
}

export function runNow() {
  clearTimeout(runTimer);
  const text = qi ? qi.getValue() : state.query;
  state.query = text;
  if (!state.index) return;
  if (!text.trim()) {
    state.result = null;
    qi.setError(null);
    emit('result', null);
    renderEmpty();
    return;
  }
  const t0 = performance.now();
  let r;
  try {
    r = run(text, state.index, evalCtx());
  } catch (err) {
    log.error('search', 'run() threw', { text, err: String(err), stack: err?.stack });
    r = { error: { pos: 0, len: 0, message: `Внутренняя ошибка: ${err?.message || err}`, suggestions: [] } };
  }
  const ms = performance.now() - t0;
  state.result = r;
  // A value still being typed ("кто:", "x:(") is not an error yet: show a soft hint instead.
  const incomplete = Boolean(r.error) && /[:=(,~|]\s*$/.test(text) && r.error.pos >= text.replace(/\s+$/, '').length - 1;
  r.incomplete = incomplete;
  qi.setError(r.error && !incomplete ? { pos: r.error.pos, len: r.error.len } : null);
  crossInfo = null;
  if (!r.error) {
    computeCrossInfo(text);
    log.debug('search', 'query evaluated', { text, count: r.count, ms: Math.round(ms * 10) / 10 });
  } else {
    log.info('search', 'query error', { text, error: r.error.message });
  }
  shownGroups = PAGE_GROUPS;
  render(r);
  emit('result', r);
}

function computeCrossInfo(text) {
  const subs = crossSubqueries(text);
  if (!subs.length) return;
  try {
    const r = run(`(${subs[0]}) зона:выкл`, state.index, evalCtx());
    if (!r.error && r.bits) crossInfo = { bits: r.bits };
  } catch (err) {
    log.warn('search', 'cross subquery failed', { err: String(err) });
  }
}

/* ---------- rendering ---------- */

function renderEmpty() {
  if (!out) return;
  clear(out);
  const box = h('div.empty-state',
    h('p', 'Введите название на любом языке (ქუჩა, улица, kucha) или запрос.'),
    h('div.hint-list', EXAMPLES.map((ex) => h('button.btn.small', { type: 'button', onclick: () => setQuery(ex) }, ex))));
  out.append(box);
  db.getHistory().then((hist) => {
    if (!hist || !hist.length || qi.getValue().trim()) return;
    box.append(h('div.section-title', 'Недавние'),
      h('div.hint-list', hist.slice(0, 6).map((q) => h('button.btn.small', { type: 'button', onclick: () => setQuery(q) }, q))));
  }).catch(() => {});
}

function render(r) {
  clear(out);
  if (r.error && r.incomplete) {
    out.append(h('p.hint-line', { 'data-testid': 'query-hint' }, `${r.error.message}. Выберите значение в подсказках над чипами.`));
    return;
  }
  if (r.error) {
    out.append(errorBox(r.error));
    return;
  }
  const n = r.count ?? 0;
  out.append(h('p.explain-line', { 'data-testid': 'explain' }, r.explain || 'Результаты', ' — ', h('b', { id: 'result-count' }, n.toLocaleString('ru-RU')),
    r.timeMs !== undefined ? h('span.small', ` · ${Math.max(0, Math.round(r.timeMs))} мс`) : null));
  for (const w of r.warnings || []) out.append(h('p.warn-line', w));
  if (!n) {
    out.append(h('div.empty-state', h('p', 'Ничего не найдено.'),
      state.zone && state.zoneEnabled ? h('p.small', 'Учтите: действует зона. Чип «Весь город» ищет без неё.') : null));
    return;
  }
  lastGroups = groupByFamily(r.order);
  const list = h('div.results', { id: 'results' });
  out.append(list);
  renderGroups(list, 0, Math.min(shownGroups, lastGroups.length));
  if (lastGroups.length > shownGroups) {
    const more = h('button.btn', { type: 'button' }, `Показать ещё (${lastGroups.length - shownGroups} групп)`);
    const row = h('div.more-row', more);
    more.addEventListener('click', () => {
      const from = shownGroups;
      shownGroups += PAGE_GROUPS;
      renderGroups(list, from, Math.min(shownGroups, lastGroups.length));
      if (shownGroups >= lastGroups.length) row.remove();
      else more.textContent = `Показать ещё (${lastGroups.length - shownGroups} групп)`;
    });
    out.append(row);
  }
}

function errorBox(err) {
  const fixes = h('div.fixes');
  for (const s of (err.suggestions || []).slice(0, 6)) {
    const b = h('button.btn.small.fix', { type: 'button' }, s);
    b.addEventListener('click', () => {
      const text = qi.getValue();
      const next = text.slice(0, err.pos) + s + text.slice(err.pos + (err.len || 0));
      log.info('search', 'error suggestion applied', { from: text, to: next });
      qi.setValue(next, { cursor: err.pos + s.length });
      runNow();
    });
    fixes.append(b);
  }
  return h('div.err-box', { role: 'alert', 'data-testid': 'query-error' },
    h('div.msg', err.message || 'Ошибка в запросе'),
    err.suggestions && err.suggestions.length ? h('div.small', 'Может быть:') : null,
    fixes);
}

/** Group display order by family, keeping the first-appearance order of families. */
function groupByFamily(order) {
  const groups = [];
  const byFam = new Map();
  const famOf = state.index?.familyOf;
  for (const i of order) {
    const fam = famOf ? famOf[i] : (feat(i)?.fam ?? -1);
    if (fam === undefined || fam < 0) {
      groups.push({ fam: -1, items: [i] });
      continue;
    }
    let g = byFam.get(fam);
    if (!g) {
      g = { fam, items: [] };
      byFam.set(fam, g);
      groups.push(g);
    }
    g.items.push(i);
  }
  // Inside a family: the parent street first, then lanes by ordinal.
  for (const g of groups) {
    if (g.items.length < 2) continue;
    g.items.sort((a, b) => rankInFamily(a) - rankInFamily(b));
  }
  return groups;
}

function rankInFamily(i) {
  const f = feat(i);
  if (!f) return 1e9;
  const isChild = f.par !== undefined || f.o !== undefined;
  return (isChild ? 1000 : 0) + (f.o || 0) * 10 + (f.k || 0) / 100;
}

function renderGroups(list, from, to) {
  const hq = houseQuery(qi.getValue());
  const frag = document.createDocumentFragment();
  for (let gi = from; gi < to; gi++) {
    const g = lastGroups[gi];
    const wrap = h('div.group');
    if (g.items.length > 1) {
      const fam = state.pack?.core?.fam?.[g.fam];
      if (fam) wrap.append(h('div.group-head', `Семья: ${fam.ru || fam.ka} · ${g.items.length}`));
    }
    // Indent members only under the family's own street (not when only lanes matched).
    const headIsParent = rankInFamily(g.items[0]) < 1000;
    g.items.forEach((i, k) => wrap.append(resultRow(i, headIsParent && k > 0, hq)));
    frag.append(wrap);
  }
  list.append(frag);
}

function resultRow(i, child, hq) {
  const f = feat(i);
  const tags = h('div.tags');
  const kr = kindRu(i);
  if (kr) tags.append(h('span', kr));
  const pl = placeLabel(i);
  if (pl) tags.append(h('span', pl));
  if (f?.len) tags.append(h('span', fmtLen(f.len)));
  const et = eponymTag(i);
  if (et) tags.append(h('span.c', et));
  if (crossInfo) {
    const xs = crossingsOf(i).filter((x) => crossInfo.bits.has(x.j));
    const seen = new Set();
    let shown = 0;
    for (const x of xs) {
      if (seen.has(x.j)) continue;
      seen.add(x.j);
      if (shown < 2) {
        const tag = eponymTag(x.j);
        tags.append(h('span.c', `× ${shortName(x.j)}${tag ? ' · ' + tag : ''}`));
      }
      shown++;
    }
    if (shown > 2) tags.append(h('span.c', `+${shown - 2}`));
  }
  if (isPlace(i)) {
    // a place has no houses of its own: its kind (above) and its address instead of "домов нет"
    const adr = placeAddressText(i);
    if (adr) tags.append(h('span', adr));
  } else tags.append(houseTag(i, f, hq));
  const btn = h(`button.res${child ? '.child' : ''}`, { type: 'button', dataset: { i: String(i) } },
    h('span.ka', mainName(i)),
    ruName(i) && ruName(i) !== mainName(i) ? h('span.ru', ruName(i)) : null,
    tags);
  btn.addEventListener('click', () => {
    const q = qi.getValue().trim();
    if (q) db.addHistory(q).catch(() => {});
    openCard(i);
  });
  return btn;
}

function houseTag(i, f, hq) {
  const hn = f?.hn || 0;
  if (!hn) return h('span', 'домов нет');
  if (hq && hq.n !== null && hq.n !== undefined) {
    const rows = houseRows(i).filter((r) => r[1] === hq.n || (r[6] && hq.n >= r[1] && hq.n <= r[6]));
    if (rows.length) return h('span.h', `дом ${rows.slice(0, 4).map((r) => r[0]).join(', ')}`);
    if (hq.probable) return h('span', `дом ${hq.n}? вероятно`);
  }
  if (hn <= 4 && state.packReady) {
    const rows = houseRows(i);
    if (rows.length) return h('span', `дома ${rows.map((r) => r[0]).join(', ')}`);
  }
  return h('span', count(hn, ['дом', 'дома', 'домов']));
}

/* ---------- history and saved queries ---------- */

async function openHistory() {
  const s = openSheet({ title: 'История и сохранённые', full: true });
  const [hist, saved] = await Promise.all([
    db.getHistory().catch(() => []),
    db.getSaved().catch(() => []),
  ]);
  const savedList = h('div.list-col');
  const renderSaved = (items) => {
    clear(savedList);
    if (!items.length) savedList.append(h('p.muted.small', 'Нет сохранённых запросов. Кнопка со звёздой сохраняет текущий.'));
    for (const it of items) {
      const del = h('button.btn.small.ghost', { type: 'button', 'aria-label': 'Удалить' }, 'Удалить');
      del.addEventListener('click', async (e) => {
        e.stopPropagation();
        await db.removeSaved(it.q);
        renderSaved(await db.getSaved());
      });
      const row = h('div', { style: { display: 'flex', gap: '6px' } },
        h('button.list-btn', { type: 'button', onclick: () => { s.close(); setQuery(it.q); } },
          h('span', h('b', it.name || it.q), it.name ? h('div.small.mono.muted', it.q) : null)),
        del);
      savedList.append(row);
    }
  };
  renderSaved(saved || []);
  const histList = h('div.list-col');
  if (!hist || !hist.length) histList.append(h('p.muted.small', 'История пуста.'));
  for (const q of (hist || []).slice(0, 60)) {
    histList.append(h('button.list-btn.mono', { type: 'button', onclick: () => { s.close(); setQuery(q); } }, h('span', q)));
  }
  const clearBtn = h('button.btn.small', { type: 'button' }, 'Очистить историю');
  clearBtn.addEventListener('click', async () => {
    try {
      if (db.clearHistory) await db.clearHistory();
      clear(histList).append(h('p.muted.small', 'История пуста.'));
    } catch (err) {
      log.warn('search', 'clearHistory failed', { err: String(err) });
    }
  });
  s.body.append(h('div.section-title', 'Сохранённые'), savedList, h('div.section-title', 'История'), histList, h('div.btn-row', clearBtn));
}

async function saveCurrent() {
  const q = qi.getValue().trim();
  if (!q) {
    toast('Сначала введите запрос');
    return;
  }
  const name = await promptSheet({ title: 'Сохранить запрос', label: q, placeholder: 'Название (необязательно)', okText: 'Сохранить' });
  if (name === null) return;
  try {
    await db.addSaved({ q, name: name.trim() });
    toast('Запрос сохранён');
    log.info('search', 'query saved', { q });
  } catch (err) {
    log.error('search', 'addSaved failed', { err: String(err) });
    toast('Не удалось сохранить');
  }
}
