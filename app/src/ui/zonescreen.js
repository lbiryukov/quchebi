// "Зона" screen: draw with a finger, tap corners, edit vertices, union districts, paste a code,
// "+50 м запаса", preview counts, save; temporary disable and share for the saved zone.
import { log } from '../log/log.js';
import { decodePolyline, circlePolygon } from '../zone/geo.js';
import { startFreehand } from '../zone/draw.js';
import { bufferRing, unionAreas, ringAreaKm2, cleanRing, openRing } from '../map/geomutil.js';
import * as db from '../store/db.js';
import { h, toast, promptSheet, confirmSheet, copyText, icon } from './dom.js';
import { state, on } from './state.js';
import { registerScreen, navigate } from './router.js';
import { mountMap, hooks, currentView } from './mapctl.js';
import {
  previewMasks, summarize, saveZone, clearZone, setZoneEnabled, shareZone, zoneCode, decodeCode,
} from './zonectl.js';
import { fmtKm2, fmtDate, count } from './format.js';

const BUFFER_M = 50;

let host;
let panel;
let tools;
let mode = 'idle'; // idle | draw | points | areas | edit | draft
let draft = null; // {base, method, history: [], fixed}
let bufferOn = false;
let freehand = null;
let editor = null;
let areaKind = 'd';
const areaSel = { d: new Set(), p: new Set() };
let areaPolys = null;
let previewTimer = 0;
let lastPreview = null;
let styleListener = null;

export function initZoneScreen() {
  const screen = document.getElementById('screen-zone');
  host = h('div.map-host');
  panel = h('div.zone-panel', { 'data-testid': 'zone-panel' });
  tools = h('div.zone-tools');
  host.append(panel, tools);
  screen.append(host);
  registerScreen('zone', { onShow, onHide });
  on('zone', () => { if (mode === 'idle') render(); });
  on('packReady', () => { areaPolys = null; if (mode !== 'idle') schedulePreview(); });
  render();
}

async function onShow() {
  render();
  const v = await mountMap(host);
  if (!v || state.screen !== 'zone') return;
  hooks.onFeatureTap = null;
  hooks.onAreaTap = onAreaTap;
  if (!styleListener) styleListener = v.on((ev) => {
    if (ev === 'beforeStyle' && editor) {
      // Terra Draw layers do not survive setStyle: keep the polygon as a draft.
      const p = editor.getPolygon();
      stopTools();
      if (p) setDraft(p, draft?.method || 'edit');
    }
  });
  if (mode === 'idle' && state.zone?.polygon) v.fitPolygon(state.zone.polygon);
  applyMapMode();
  if (draft) currentView()?.setDraft(effective());
}

function onHide() {
  stopTools();
  if (mode !== 'draft') mode = 'idle';
  hooks.onAreaTap = null;
  const v = currentView();
  if (v) {
    v.setDraft(null);
    v.showAreas(null);
    v.setMode('browse');
  }
}

/* ---------- helpers ---------- */

function effective() {
  if (!draft) return null;
  return bufferOn ? bufferRing(draft.base, BUFFER_M) : draft.base;
}

function stopTools() {
  freehand?.stop();
  freehand = null;
  editor?.stop();
  editor = null;
}

function applyMapMode() {
  const v = currentView();
  if (!v) return;
  if (mode === 'areas') {
    v.setMode('areas');
    v.showAreas(areaKind, [...areaSel[areaKind]]);
  } else {
    v.showAreas(null);
    v.setMode(mode === 'draw' ? 'draw' : (mode === 'edit' || mode === 'points') ? 'edit' : 'browse');
  }
}

function setDraft(base, method, { pushHistory = true, fixed = false } = {}) {
  const ring = openRing(base);
  if (draft && pushHistory && draft.base) draft.history.push(draft.base);
  if (!draft) draft = { base: ring, method, history: [], fixed };
  else {
    draft.base = ring;
    draft.method = method || draft.method;
    draft.fixed = fixed;
  }
  mode = 'draft';
  currentView()?.setDraft(effective());
  applyMapMode();
  render();
  schedulePreview(0);
}

function schedulePreview(ms = 150) {
  clearTimeout(previewTimer);
  previewTimer = setTimeout(() => {
    const poly = mode === 'edit' && editor ? (editor.getPolygon() || draft?.base) : effective();
    if (!poly) { lastPreview = null; renderPreview(); return; }
    const p = mode === 'edit' && bufferOn ? bufferRing(poly, BUFFER_M) : poly;
    const t0 = performance.now();
    const masks = previewMasks(p);
    lastPreview = { ...summarize(masks, p), ms: Math.round(performance.now() - t0), vertices: p.length };
    renderPreview();
  }, ms);
}

let previewEl = null;
function renderPreview() {
  if (!previewEl) return;
  const p = lastPreview;
  if (!p) {
    previewEl.replaceChildren(h('span.muted', 'Нет контура'));
    return;
  }
  const houses = p.houses === null || p.houses === undefined ? 'адреса загружаются' : count(p.houses, ['адрес', 'адреса', 'адресов']);
  previewEl.replaceChildren(
    h('div', 'В зоне: ', h('b', { 'data-testid': 'zone-preview-streets' }, count(p.streets, ['улица', 'улицы', 'улиц'])), ', ',
      h('b', { 'data-testid': 'zone-preview-houses' }, houses)),
    h('div.small.muted', `${fmtKm2(p.areaKm2)} · ${count(p.vertices, ['вершина', 'вершины', 'вершин'])} · всего объектов ${p.features}${bufferOn ? ' · с запасом 50 м' : ''}`),
  );
}

function bufferSwitch() {
  const cb = h('input', { type: 'checkbox', checked: bufferOn, 'data-testid': 'zone-buffer' });
  cb.addEventListener('change', () => {
    bufferOn = cb.checked;
    log.info('zone', 'buffer toggled', { on: bufferOn });
    if (draft && mode === 'draft') currentView()?.setDraft(effective());
    schedulePreview(0);
  });
  return h('label.switch', cb, h('span', '+50 м запаса (границы на легенде приблизительны)'));
}

/* ---------- render ---------- */

function btn(label, onClick, cls = '', attrs = {}) {
  const b = h(`button.btn${cls}`, { type: 'button', ...attrs }, label);
  b.addEventListener('click', () => {
    try {
      onClick();
    } catch (err) {
      log.error('zone', 'action failed', { label, err: String(err), stack: err?.stack });
      toast(`Ошибка: ${err?.message || err}`);
    }
  });
  return b;
}

function render() {
  if (!panel) return;
  previewEl = null;
  panel.replaceChildren();
  tools.replaceChildren();
  tools.hidden = false;
  if (mode === 'idle') renderIdle();
  else if (mode === 'draw') renderDraw();
  else if (mode === 'points') renderPoints();
  else if (mode === 'areas') renderAreas();
  else if (mode === 'edit') renderEdit();
  else if (mode === 'draft') renderDraft();
}

function renderIdle() {
  const z = state.zone;
  if (z) {
    const s = summarize(state.masks, z.polygon);
    const en = h('input', { type: 'checkbox', checked: state.zoneEnabled, 'data-testid': 'zone-enabled' });
    en.addEventListener('change', () => setZoneEnabled(en.checked));
    panel.append(
      h('h3', 'Зона задана'),
      h('div', { 'data-testid': 'zone-summary' }, `${fmtKm2(s.areaKm2)} · ${count(s.streets, ['улица', 'улицы', 'улиц'])}`,
        s.houses !== null ? ` · ${count(s.houses, ['адрес', 'адреса', 'адресов'])}` : '',
        ` · задана ${fmtDate(z.createdAt)}`, z.buffer ? ' · +50 м' : ''),
      h('label.switch', en, h('span', 'Применять зону ко всем поискам')),
      h('div.btn-row',
        btn(h('span', icon('share'), ' Поделиться кодом'), () => shareZone()),
        btn('Копировать код', () => copyText(zoneCode(), 'Код зоны скопирован'), '.small'),
        btn('Удалить', async () => {
          if (await confirmSheet({ title: 'Удалить зону?', text: 'Поиск будет идти по всему городу, пока вы не зададите новую зону.', okText: 'Удалить', danger: true })) {
            await clearZone();
            render();
          }
        }, '.small.danger')),
    );
  } else {
    panel.append(
      h('h3', 'Зона не задана'),
      h('p.small', 'Нарисуйте зону соревнований один раз — она будет применяться ко всем поискам и сохранится на телефоне.'),
      h('div.btn-row', btn('Весь город (задать позже)', async () => {
        await db.setSetting('zoneSkipped', true).catch(() => {});
        navigate('search');
      }, '.small')),
    );
  }
  bufferOn = Boolean(z?.buffer);
  tools.append(
    h('div.btn-grid',
      btn('Рисовать пальцем', startDraw, '.primary', { 'data-testid': 'zone-draw' }),
      btn('По точкам', startPoints, '', { 'data-testid': 'zone-points' }),
      btn('Из районов', startAreas, '', { 'data-testid': 'zone-areas' }),
      btn('Вставить код', pasteCode, '', { 'data-testid': 'zone-paste' }),
      z ? btn('Править', () => startEdit(z.base || z.polygon, 'edit'), '', { 'data-testid': 'zone-edit' }) : null,
      z ? btn('Показать', () => currentView()?.fitPolygon(z.polygon)) : null),
  );
}

function cancelToIdle() {
  stopTools();
  draft = null;
  mode = 'idle';
  currentView()?.setDraft(null);
  applyMapMode();
  render();
}

function renderDraw() {
  tools.hidden = true;
  panel.append(
    h('h3', 'Рисование'),
    h('p.small', 'Обведите зону одним движением пальца. Карта не двигается; контур замкнётся сам.'),
    h('div.btn-row', btn('Отмена', cancelToIdle, '', { 'data-testid': 'zone-cancel' })),
  );
}

function renderPoints() {
  tools.hidden = true;
  panel.append(
    h('h3', 'По точкам'),
    h('p.small', 'Тапайте по углам зоны. Закончить — тап по первой точке или «Замкнуть».'),
    h('div.btn-row',
      btn('Замкнуть', () => {
        const p = editor?.closePoints();
        if (!p) { toast('Нужно хотя бы три точки'); return; }
        finishPoints(p);
      }, '.primary'),
      btn('Отмена', cancelToIdle)),
  );
}

function renderAreas() {
  const seg = h('div.seg', { role: 'group' });
  for (const [k, label] of [['d', 'Районы'], ['p', 'Микрорайоны']]) {
    const b = h('button', { type: 'button', 'aria-pressed': String(areaKind === k) }, label);
    b.addEventListener('click', () => { areaKind = k; applyMapMode(); render(); });
    seg.append(b);
  }
  const n = areaSel.d.size + areaSel.p.size;
  const names = [
    ...[...areaSel.d].map((i) => state.pack?.core?.districts?.[i]?.ru),
    ...[...areaSel.p].map((i) => state.pack?.core?.places?.[i]?.ru || state.pack?.core?.places?.[i]?.ka),
  ].filter(Boolean);
  panel.append(
    h('h3', 'Из районов'),
    seg,
    h('p.small', n ? `Выбрано: ${names.join(', ')}` : 'Тапните по районам или микрорайонам на карте — они объединятся.'),
    h('div.btn-row',
      btn('Готово', finishAreas, '.primary', { disabled: !n }),
      btn('Сбросить', () => { areaSel.d.clear(); areaSel.p.clear(); currentView()?.setDraft(null); applyMapMode(); render(); }, '', { disabled: !n }),
      btn('Отмена', () => { areaSel.d.clear(); areaSel.p.clear(); cancelToIdle(); })),
  );
  tools.hidden = true;
}

function renderEdit() {
  previewEl = h('div.draft-info', { 'data-testid': 'zone-preview' });
  panel.append(
    h('h3', 'Правка'),
    h('p.small', 'Перетаскивайте вершины. Белый кружок посередине стороны добавляет вершину. Коснитесь вершины и нажмите «Удалить вершину».'),
    previewEl,
    h('div.btn-row',
      btn('Готово', () => {
        const p = editor?.getPolygon();
        stopTools();
        if (!p) { cancelToIdle(); return; }
        let fixed = false;
        let ring = p;
        try {
          const c = cleanRing(p);
          ring = c.ring;
          fixed = c.fixed;
        } catch (err) {
          toast('Контур некорректен — перерисуйте');
          return;
        }
        setDraft(ring, draft?.method === 'edit' ? 'edit' : draft?.method || 'edit', { fixed });
      }, '.primary', { 'data-testid': 'zone-edit-done' }),
      btn('Удалить вершину', () => {
        const r = editor?.deleteActiveVertex();
        if (r?.error) toast(r.error);
        else schedulePreview(0);
      }),
      btn('Отмена', () => {
        stopTools();
        if (draft && draft.method !== 'edit') { mode = 'draft'; currentView()?.setDraft(effective()); applyMapMode(); render(); schedulePreview(0); } else cancelToIdle();
      })),
  );
  renderPreview();
  schedulePreview(0);
  tools.hidden = true;
}

function renderDraft() {
  previewEl = h('div.draft-info', { 'data-testid': 'zone-preview' });
  const area = ringAreaKm2(effective() || []);
  const warn = [];
  if (draft?.fixed) warn.push('Самопересечения контура исправлены автоматически.');
  if (area < 0.3) warn.push('Зона очень маленькая — проверьте, что обвели нужный район.');
  panel.append(
    h('h3', 'Новая зона'),
    previewEl,
    warn.map((w) => h('p.small', { style: { color: 'var(--warn)' } }, w)),
    bufferSwitch(),
  );
  renderPreview();
  tools.append(h('div.btn-grid',
    btn('Сохранить', doSave, '.primary', { 'data-testid': 'zone-save' }),
    btn('Править', () => startEdit(draft.base, draft.method)),
    btn('Отменить', () => {
      if (!draft?.history.length) return;
      draft.base = draft.history.pop();
      currentView()?.setDraft(effective());
      render();
      schedulePreview(0);
    }, '', { disabled: !draft?.history.length }),
    btn('Перерисовать', redo),
    btn('Отмена', cancelToIdle, '', { 'data-testid': 'zone-cancel' })));
}

/* ---------- actions ---------- */

async function ensureMap() {
  const v = currentView() || (await mountMap(host));
  if (!v) toast('Карта недоступна');
  return v;
}

async function startDraw() {
  const v = await ensureMap();
  if (!v) return;
  stopTools();
  mode = 'draw';
  currentView()?.setDraft(null);
  applyMapMode();
  render();
  freehand = startFreehand(v, {
    onDone: (res) => {
      freehand?.stop();
      freehand = null;
      draft = null;
      setDraft(res.polygon, 'draw', { pushHistory: false, fixed: res.fixed });
      toast(`Контур: ${res.vertices} вершин`);
    },
    onError: (msg) => toast(msg, 3500),
  });
}

async function startPoints() {
  const v = await ensureMap();
  if (!v) return;
  stopTools();
  mode = 'points';
  currentView()?.setDraft(null);
  applyMapMode();
  render();
  editor = await makeEditor(v);
  if (mode !== 'points') { editor.stop(); editor = null; return; }
  editor.startPoints();
}

function finishPoints(p) {
  stopTools();
  let ring = p;
  let fixed = false;
  try {
    const c = cleanRing(p);
    ring = c.ring;
    fixed = c.fixed;
  } catch {
    toast('Контур некорректен — попробуйте ещё раз');
    return;
  }
  if (ringAreaKm2(ring) < 0.05) {
    toast('Слишком маленькая зона — отметьте углы шире', 3500);
    startPoints();
    return;
  }
  draft = null;
  setDraft(ring, 'points', { pushHistory: false, fixed });
}

async function makeEditor(v) {
  // Terra Draw is loaded on demand (code-split chunk).
  const { createEditor } = await import('../zone/edit.js');
  const color = (getComputedStyle(document.documentElement).getPropertyValue('--accent').trim() || '#8a2242').slice(0, 7);
  return createEditor(v, {
    color: /^#[0-9a-f]{6}$/i.test(color) ? color : '#8a2242',
    onChange: () => schedulePreview(),
    onPolygonFinished: (p) => { if (mode === 'points') finishPoints(p); },
    onVertex: (latlon) => currentView()?.setVertex(latlon),
  });
}

async function startEdit(polygon, method) {
  const v = await ensureMap();
  if (!v) return;
  stopTools();
  if (!draft) draft = { base: openRing(polygon), method, history: [] };
  else if (draft.base !== polygon) draft.history.push(draft.base);
  if (method === 'edit' && state.zone) bufferOn = Boolean(state.zone.buffer);
  mode = 'edit';
  currentView()?.setDraft(null);
  applyMapMode();
  editor = await makeEditor(v);
  if (mode !== 'edit') { editor.stop(); editor = null; return; }
  if (!editor.startEdit(polygon)) {
    stopTools();
    toast('Не удалось открыть контур для правки');
    mode = draft ? 'draft' : 'idle';
  }
  render();
  v.fitPolygon(polygon, 60);
}

function loadAreaPolys() {
  if (areaPolys) return areaPolys;
  const pack = state.pack;
  const out = { d: [], p: [] };
  const dec = (rec) => {
    const polys = [];
    for (const rings of rec?.poly || []) {
      const rr = rings.map((s) => decodePolyline(s)).filter((r) => r.length >= 3);
      if (rr.length) polys.push(rr);
    }
    return polys;
  };
  (pack?.areas?.districts || []).forEach((rec, i) => { out.d[i] = dec(rec); });
  (pack?.areas?.places || []).forEach((rec, i) => {
    let polys = dec(rec);
    const c = pack.core.places?.[i]?.c;
    if (!polys.length && c) polys = [[circlePolygon([c[0] / 1e5, c[1] / 1e5], 300)]];
    out.p[i] = polys;
  });
  areaPolys = out;
  return out;
}

async function startAreas() {
  if (!state.pack?.areas) {
    toast('Полигоны районов ещё загружаются');
    return;
  }
  const v = await ensureMap();
  if (!v) return;
  stopTools();
  draft = null;
  mode = 'areas';
  applyMapMode();
  render();
}

function onAreaTap(kind, idx) {
  if (mode !== 'areas') return;
  const set = areaSel[kind];
  if (set.has(idx)) set.delete(idx);
  else set.add(idx);
  log.debug('zone', 'area toggled', { kind, idx, on: set.has(idx) });
  const u = currentUnion();
  currentView()?.setDraft(u?.ring || null);
  applyMapMode();
  render();
}

function currentUnion() {
  const ap = loadAreaPolys();
  const polys = [];
  for (const i of areaSel.d) for (const p of ap.d[i] || []) polys.push(p);
  for (const i of areaSel.p) for (const p of ap.p[i] || []) polys.push(p);
  if (!polys.length) return null;
  const t0 = performance.now();
  const u = unionAreas(polys);
  log.info('zone', 'areas union', { parts: polys.length, method: u?.method, partial: u?.partial, ms: Math.round(performance.now() - t0) });
  return u;
}

function finishAreas() {
  const u = currentUnion();
  if (!u) { toast('Не удалось объединить районы'); return; }
  if (u.partial) toast('Выбраны несмежные районы — взята самая большая часть', 4000);
  areaSel.d.clear();
  areaSel.p.clear();
  draft = null;
  setDraft(u.ring, 'areas', { pushHistory: false });
}

async function pasteCode() {
  const text = await promptSheet({
    title: 'Код зоны от напарника', label: 'Вставьте сообщение с кодом (начинается с Z1.)', multiline: true, okText: 'Показать',
  });
  if (text === null) return;
  const poly = decodeCode(text);
  if (!poly || poly.length < 3) {
    toast('Код не распознан', 3000);
    log.warn('zone', 'zone code not recognised', { len: text.length });
    return;
  }
  await ensureMap();
  draft = null;
  setDraft(poly, 'code', { pushHistory: false });
  currentView()?.fitPolygon(poly);
}

function redo() {
  const m = draft?.method;
  draft = null;
  currentView()?.setDraft(null);
  if (m === 'points') startPoints();
  else if (m === 'areas') startAreas();
  else if (m === 'code') pasteCode();
  else startDraw();
}

async function doSave() {
  if (!draft) return;
  const poly = effective();
  if (!poly || poly.length < 3) return;
  await saveZone({ polygon: poly, base: draft.base, buffer: bufferOn ? BUFFER_M : 0, mode: draft.method });
  const s = summarize(state.masks, poly);
  toast(`Зона сохранена: ${count(s.streets, ['улица', 'улицы', 'улиц'])}`);
  draft = null;
  mode = 'idle';
  currentView()?.setDraft(null);
  applyMapMode();
  render();
}
