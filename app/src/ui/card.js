// Object card: names (tap to copy), facts, mini-map, house grid, crossings, eponym, error note.
import * as db from '../store/db.js';
import { log } from '../log/log.js';
import { h, clear, openSheet, copyText, toast, icon } from './dom.js';
import { state, emit, zoneActive } from './state.js';
import { houseQuery } from './dsledit.js';
import {
  feat, kindRu, mainName, shortName, districtLabels, placeLabels, familyOf, eponymOf, eponymByIndex,
  eponymSource, crossingsOf, houseRows, geometryOf, featureKey, taxLabel, isPlace, placeAddress, streetPlaces,
} from './feature.js';
import { fmtLen, fmtYears, fmtDirMask, count } from './format.js';

let sheet = null;
let stack = [];
let current = -1;

export function openCard(i, { push = true } = {}) {
  if (!feat(i)) return;
  if (!sheet) {
    stack = [];
    sheet = openSheet({
      title: '',
      full: true,
      className: 'card-sheet',
      onClose: () => {
        sheet = null;
        stack = [];
        current = -1;
      },
    });
  } else if (push && current >= 0 && current !== i) {
    stack.push(current);
  }
  current = i;
  state.selected = i;
  emit('select', i);
  log.info('card', 'open', { i, name: mainName(i) });
  render(i);
}

function render(i) {
  const f = feat(i);
  sheet.setTitle(shortName(i) || mainName(i));
  const body = sheet.body;
  clear(body);
  body.scrollTop = 0;

  if (stack.length) {
    const back = h('button.btn.small', { type: 'button' }, icon('back'), 'Назад');
    back.addEventListener('click', () => {
      const prev = stack.pop();
      openCard(prev, { push: false });
    });
    body.append(h('div.btn-row', back));
  }

  const place = isPlace(i);
  body.append(namesBlock(i, f));
  body.append(factsBlock(i, f, place));
  if (place) body.append(addressBlock(i));
  body.append(actionsBlock(i, f, place));
  body.append(miniMap(i));
  const ep = eponymBlock(i, f);
  if (ep) body.append(ep);
  if (!place) {
    body.append(housesBlock(i, f));
    const pl = placesBlock(i);
    if (pl) body.append(pl);
    body.append(crossingsBlock(i));
  }
  body.append(noteBlock(i));
}

function nameBtn(label, value, cls = '') {
  const b = h(`button.name-btn${cls}`, { type: 'button', title: 'Нажмите, чтобы скопировать' },
    h('span.lbl', label), h(`span.val${/[Ⴀ-ჿ]/.test(value) ? '.ka' : ''}`, value));
  b.addEventListener('click', () => copyText(value));
  return b;
}

function namesBlock(i, f) {
  const box = h('div.card-names');
  if (f.ka) box.append(nameBtn('ka · нажмите, чтобы скопировать', f.ka, '.main'));
  if (f.ru) box.append(nameBtn('ru', f.ru));
  if (f.en) box.append(nameBtn('en', f.en));
  for (const a of f.alt || []) box.append(nameBtn('другое написание', a));
  for (const o of f.old || []) {
    const years = o.from || o.to ? ` (${o.from || '?'}–${o.to || '?'})` : '';
    const ep = eponymByIndex(o.e);
    const label = `прежнее${years}${ep ? ' · в честь: ' + (ep.ru || ep.ka) : ''}`;
    const val = [o.ka, o.ru, o.lat].filter(Boolean).join(' / ');
    if (val) box.append(nameBtn(label, val));
  }
  return box;
}

function factsBlock(i, f, place) {
  const kv = h('div.kv');
  const add = (k, v) => { if (v) kv.append(h('span', `${k}: `, h('b', v))); };
  add('тип', kindRu(i));
  add('район', districtLabels(i).join(', '));
  add('местность', placeLabels(i).join(', '));
  if (f.len) {
    let len = fmtLen(f.len);
    if (zoneActive() && state.masks.lenInside) len += ` (в зоне ${fmtLen(state.masks.lenInside[i]) || '0 м'})`;
    add('длина', len);
  }
  if (!place) add('домов', f.hn ? String(f.hn) : 'нет');
  if (f.o) add('№', String(f.o));
  const fam = familyOf(i);
  if (fam) add('семья', fam.ru || fam.ka);
  if (state.zone && state.masks) {
    const inZ = state.masks.featDefault?.has?.(i);
    const touch = state.masks.featTouch?.has?.(i);
    add('зона', inZ ? 'в зоне' : touch ? 'задевает зону' : 'вне зоны');
  }
  return kv;
}

function actionsBlock(i, f, place) {
  const row = h('div.btn-row');
  const toMap = h('button.btn.primary', { type: 'button' }, icon('map'), 'На карте');
  toMap.addEventListener('click', () => {
    sheet?.close();
    emit('showOnMap', i);
  });
  row.append(toMap);
  const fam = familyOf(i);
  if (fam) {
    const famBtn = h('button.btn', { type: 'button' }, 'Вся семья');
    famBtn.addEventListener('click', () => {
      sheet?.close();
      const name = (fam.ru || fam.ka || '').replace(/"/g, '');
      emit('setQuery', `семья:"${name}"`);
      emit('navigate', 'search');
    });
    row.append(famBtn);
  }
  if (place) return row; // a point: no crossings
  const crossBtn = h('button.btn', { type: 'button' }, 'Кто её пересекает');
  crossBtn.addEventListener('click', () => {
    sheet?.close();
    const name = (f.ru || f.ka || '').replace(/"/g, '');
    emit('setQuery', `x:(имя:"${name}")`);
    emit('navigate', 'search');
  });
  row.append(crossBtn);
  return row;
}

/* ---------- address of a place, places of a street ---------- */

function addressBlock(i) {
  const box = h('div');
  const a = placeAddress(i);
  box.append(h('div.section-title', 'Адрес'));
  if (!a) {
    box.append(h('p.muted.small', 'Адреса нет в данных.'));
    return box;
  }
  const houses = a.houses.length ? `д. ${a.houses.join(', ')}` : 'номер дома неизвестен';
  const b = h('button.list-btn', { type: 'button', 'data-testid': 'place-address' },
    h('span', h('span', shortName(a.street)), h('div.small.muted', [kindRu(a.street), houses].filter(Boolean).join(' · '))));
  b.addEventListener('click', () => openCard(a.street));
  box.append(h('div.cross-list', b));
  return box;
}

function placesBlock(i) {
  const items = streetPlaces(i);
  if (!items.length) return null;
  const box = h('div');
  box.append(h('div.section-title', `Места · ${items.length}`));
  const list = h('div.cross-list', { 'data-testid': 'street-places' });
  for (const { p, house } of items) {
    const b = h('button.list-btn', { type: 'button' },
      h('span', h('span', house ? `д. ${house} — ${shortName(p)}` : shortName(p)),
        h('div.small.muted', [kindRu(p), house ? '' : 'без номера дома'].filter(Boolean).join(' · '))));
    b.addEventListener('click', () => openCard(p));
    list.append(b);
  }
  box.append(list);
  return box;
}

/* ---------- mini-map (SVG, no WebGL) ---------- */

function miniMap(i) {
  const parts = geometryOf(i);
  const W = 320;
  const H = 200;
  const svgNS = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(svgNS, 'svg');
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
  svg.setAttribute('class', 'mini-map');
  svg.setAttribute('role', 'img');
  svg.setAttribute('aria-label', 'Мини-карта: улица, пересечения и зона');
  const f = feat(i);
  let bb = null;
  const extend = (lat, lon) => {
    if (!Number.isFinite(lat) || !Number.isFinite(lon)) return;
    if (!bb) bb = [lat, lon, lat, lon];
    else {
      bb[0] = Math.min(bb[0], lat); bb[1] = Math.min(bb[1], lon);
      bb[2] = Math.max(bb[2], lat); bb[3] = Math.max(bb[3], lon);
    }
  };
  for (const p of parts) for (const [lat, lon] of p) extend(lat, lon);
  if (!bb && f?.c) extend(f.c[0] / 1e5, f.c[1] / 1e5);
  if (!bb) {
    const t = document.createElementNS(svgNS, 'text');
    t.setAttribute('x', '16'); t.setAttribute('y', '100'); t.setAttribute('fill', 'currentColor');
    t.textContent = 'Геометрия ещё загружается';
    svg.append(t);
    return svg;
  }
  // Pad and keep the aspect ratio (equirectangular with cos(lat)).
  const lat0 = (bb[0] + bb[2]) / 2;
  const kx = Math.cos((lat0 * Math.PI) / 180);
  let wDeg = Math.max((bb[3] - bb[1]) * kx, 0.0015);
  let hDeg = Math.max(bb[2] - bb[0], 0.0015);
  const scale = Math.min(W / (wDeg * 1.5), H / (hDeg * 1.5));
  const cx = (bb[1] + bb[3]) / 2;
  const cy = lat0;
  const px = (lat, lon) => [W / 2 + (lon - cx) * kx * scale, H / 2 - (lat - cy) * scale];
  const path = (pts) => pts.map(([lat, lon], k) => {
    const [x, y] = px(lat, lon);
    return `${k ? 'L' : 'M'}${x.toFixed(1)} ${y.toFixed(1)}`;
  }).join('');
  const style = getComputedStyle(document.documentElement);
  const cLine = style.getPropertyValue('--line').trim() || '#ccc';
  const cMuted = style.getPropertyValue('--muted').trim() || '#666';
  const cAccent = style.getPropertyValue('--accent').trim() || '#8a2242';
  const cEnamel = style.getPropertyValue('--enamel').trim() || '#1d3b6e';
  const cOk = style.getPropertyValue('--ok').trim() || '#2d7a4e';

  const add = (tag, attrs) => {
    const el = document.createElementNS(svgNS, tag);
    for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
    svg.append(el);
    return el;
  };
  if (state.zone?.polygon && state.zoneEnabled) {
    add('path', { d: path(state.zone.polygon) + 'Z', fill: cOk, 'fill-opacity': '0.08', stroke: cOk, 'stroke-width': '2', 'stroke-dasharray': '6 4' });
  }
  const seen = new Set();
  for (const x of crossingsOf(i)) {
    if (seen.has(x.j)) continue;
    seen.add(x.j);
    for (const p of geometryOf(x.j)) {
      if (p.length > 1) add('path', { d: path(p), fill: 'none', stroke: cLine, 'stroke-width': '3', 'stroke-linecap': 'round' });
    }
  }
  for (const p of parts) {
    if (p.length > 1) add('path', { d: path(p), fill: 'none', stroke: cAccent, 'stroke-width': '5', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' });
    else if (p.length === 1) {
      const [x, y] = px(p[0][0], p[0][1]);
      add('circle', { cx: x, cy: y, r: '7', fill: cAccent });
    }
  }
  for (const r of houseRows(i)) {
    const [x, y] = px(r[4] / 1e5, r[5] / 1e5);
    add('circle', { cx: x, cy: y, r: '2.2', fill: cEnamel });
  }
  for (const x of crossingsOf(i)) {
    if (!Number.isFinite(x.lat)) continue;
    const [X, Y] = px(x.lat, x.lon);
    add('circle', { cx: X, cy: Y, r: '3.5', fill: cMuted });
  }
  svg.addEventListener('click', () => { sheet?.close(); emit('showOnMap', i); });
  return svg;
}

/* ---------- eponym ---------- */

function eponymBlock(i) {
  const e = eponymOf(i);
  if (!e) return null;
  const src = eponymSource(e);
  const years = fmtYears(e.b, e.d);
  const cats = (e.cat || []).map(taxLabel);
  return h('div.eponym',
    h('div.section-title', 'В честь кого'),
    h('div.nm', e.ru || e.ka || e.en, years ? h('span.muted', ` · ${years}`) : null),
    e.ka && e.ru ? h('div.ka.small', e.ka) : null,
    e.desc ? h('div', e.desc) : null,
    e.occ && e.occ.length ? h('div.small.muted', e.occ.join(', ')) : null,
    h('div.src', src ? h(`span.pill${src.cls ? '.' + src.cls : ''}`, src.text) : null,
      e.ai && e.src ? h('span.pill', String(e.src)) : null,
      cats.map((c) => h('span.pill', c)),
      e.q ? h('span.pill', e.q) : null));
}

/* ---------- houses ---------- */

function cmpHouse(a, b) {
  return (a[1] - b[1]) || String(a[2]).localeCompare(String(b[2])) || String(a[3]).localeCompare(String(b[3]), 'ru', { numeric: true });
}

function housesBlock(i, f) {
  const box = h('div');
  const rowsRaw = houseRows(i);
  if (!rowsRaw.length) {
    box.append(h('div.section-title', 'Дома'), h('p.muted.small', f.hn ? 'Номера домов загружаются…' : 'Номеров домов нет в данных.'));
    return box;
  }
  const hq = houseQuery(state.query || '');
  const masks = zoneActive() ? state.masks : null;
  const rows = rowsRaw.map((r, k) => ({ r, k })).sort((a, b) => cmpHouse(a.r, b.r));
  const grid = h('div.house-grid', { 'data-testid': 'house-grid' });
  let hits = 0;
  let probableInserted = false;
  const n = hq && Number.isFinite(hq.n) ? hq.n : null;
  const hasN = n !== null && rows.some(({ r }) => r[1] === n || (r[6] && n >= r[1] && n <= r[6]));
  const probable = n !== null && !hasN && isProbable(rowsRaw, n);
  for (const { r, k } of rows) {
    if (probable && !probableInserted && r[1] > n) {
      grid.append(h('div.hn.prob', { title: 'вероятно есть' }, `${n}?`));
      probableInserted = true;
    }
    let cls = '';
    if (n !== null && (r[1] === n || (r[6] && n >= r[1] && n <= r[6]))) {
      if (!hq.lit || String(r[2]) === normLit(hq.lit)) { cls = '.hit'; hits++; }
    }
    let inZone = true;
    try {
      if (masks?.houseIn) inZone = masks.houseIn(i, k);
    } catch { /* masks not ready */ }
    const lm = r.length > 8 && feat(r[8]) ? r[8] : -1;
    const title = (lm >= 0 ? `${r[0]} — ${shortName(lm)}` : r[0]) + (inZone ? '' : ' — вне зоны');
    let cell;
    if (lm >= 0) {
      cell = h(`button.hn.lm${cls}${inZone ? '' : '.out'}`, { type: 'button', title }, r[0]);
      cell.addEventListener('click', () => openCard(lm));
    } else cell = h(`div.hn${cls}${inZone ? '' : '.out'}`, { title }, r[0]);
    grid.append(cell);
  }
  if (probable && !probableInserted) grid.append(h('div.hn.prob', { title: 'вероятно есть' }, `${n}?`));
  box.append(h('div.section-title', `Дома · ${count(rowsRaw.length, ['номер', 'номера', 'номеров'])}${hits ? ` · найдено ${hits}` : ''}`), grid);
  if (masks) box.append(h('p.small.muted', 'Бледные номера — вне зоны.'));
  if (rowsRaw.some((r) => r.length > 8 && feat(r[8]))) box.append(h('p.small.muted', 'Номера в рамке — места (музей, памятник, ...): нажмите, чтобы открыть.'));
  if (probable) box.append(h('p.small.muted', `Номера ${n} нет в данных, но соседние номера той же стороны есть — вероятно, дом существует.`));
  return box;
}

const LIT = { а: 'ა', a: 'ა', б: 'ბ', b: 'ბ', в: 'ვ', v: 'ვ', г: 'გ', g: 'გ', д: 'დ', d: 'დ', е: 'ე', e: 'ე' };
function normLit(l) {
  return LIT[l] || l;
}

/** Same-parity side has numbers below and above n (but not n). */
function isProbable(rows, n) {
  let below = false;
  let above = false;
  for (const r of rows) {
    if (r[1] % 2 !== n % 2) continue;
    if (r[1] < n) below = true;
    if (r[1] > n) above = true;
  }
  return below && above;
}

/* ---------- crossings ---------- */

function crossingsBlock(i) {
  const box = h('div');
  const xs = crossingsOf(i);
  box.append(h('div.section-title', `Пересечения · ${xs.length}`));
  if (!xs.length) {
    box.append(h('p.muted.small', 'Пересечений нет в данных.'));
    return box;
  }
  const list = h('div.cross-list');
  const masks = zoneActive() ? state.masks : null;
  const seen = new Set();
  for (const x of xs) {
    if (seen.has(x.j)) continue;
    seen.add(x.j);
    const inZone = masks?.crossingIn && x.t >= 0 ? masks.crossingIn[x.t] === 1 : null;
    const b = h('button.list-btn', { type: 'button' },
      h('span', h('span', shortName(x.j)), h('div.small.muted', [kindRu(x.j), inZone === false ? 'перекрёсток вне зоны' : ''].filter(Boolean).join(' · '))),
      x.nmask ? h('span.dir', { title: 'куда эта улица уходит от перекрёстка' }, fmtDirMask(x.nmask)) : null);
    b.addEventListener('click', () => openCard(x.j));
    list.append(b);
  }
  box.append(list);
  return box;
}

/* ---------- error note ---------- */

function noteBlock(i) {
  const box = h('div.card-box');
  const ta = h('textarea.field', { placeholder: 'Что не так: название, тип, эпоним, дома…', autocapitalize: 'sentences' });
  const btn = h('button.btn', { type: 'button' }, 'Сохранить заметку');
  btn.addEventListener('click', async () => {
    const text = ta.value.trim();
    if (!text) { ta.focus(); return; }
    try {
      await db.addNote({ featureKey: featureKey(i), name: mainName(i), text, t: Date.now(), version: state.pack?.manifest?.version });
      ta.value = '';
      toast('Заметка сохранена в журнале');
      log.info('card', 'error note saved', { i });
    } catch (err) {
      log.error('card', 'addNote failed', { err: String(err) });
      toast('Не удалось сохранить заметку');
    }
  });
  box.append(h('div.section-title', 'Сообщить об ошибке'),
    h('p.small.muted', 'Заметка хранится на телефоне; её можно скопировать на экране «Журнал».'), ta, h('div.btn-row', btn));
  return box;
}
