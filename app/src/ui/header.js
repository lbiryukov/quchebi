// Header: zone plate (area, count, date) and offline/pack status.
import { state, on } from './state.js';
import { navigate } from './router.js';
import { summarize } from './zonectl.js';
import { fmtKm2, fmtDate, fmtDateShort } from './format.js';
import { h } from './dom.js';

export function initHeader() {
  const plate = document.getElementById('zone-plate');
  plate.addEventListener('click', () => navigate('zone'));
  const upd = () => renderPlate();
  on('zone', upd);
  on('packReady', upd);
  on('index', upd);
  window.addEventListener('online', renderStatus);
  window.addEventListener('offline', renderStatus);
  on('pack', renderStatus);
  on('settings', renderStatus);
  renderPlate();
  renderStatus();
}

export function renderPlate() {
  const plate = document.getElementById('zone-plate');
  const text = document.getElementById('zone-plate-text');
  if (!plate || !text) return;
  plate.classList.remove('none', 'off');
  const z = state.zone;
  if (!z) {
    plate.classList.add('none');
    text.textContent = 'Зона не задана';
    plate.setAttribute('aria-label', 'Зона не задана. Открыть экран зоны');
    return;
  }
  if (!state.zoneEnabled) {
    plate.classList.add('off');
    text.textContent = `Зона выкл. · ${fmtDate(z.createdAt)}`;
    plate.setAttribute('aria-label', 'Зона временно выключена');
    return;
  }
  const s = summarize(state.masks, z.polygon);
  const n = state.masks ? s.streets : '…';
  text.textContent = `Зона · ${fmtKm2(s.areaKm2)} · ${n} · ${fmtDate(z.createdAt)}`;
  plate.setAttribute('aria-label', `Зона: ${fmtKm2(s.areaKm2)}, ${n} улиц, задана ${fmtDate(z.createdAt)}`);
}

export function renderStatus() {
  const el = document.getElementById('net-status');
  if (!el) return;
  const online = navigator.onLine;
  const m = state.pack?.manifest;
  const packDate = m?.built ? fmtDateShort(m.built) : '';
  const rows = [h('div', h('b', online ? 'онлайн' : 'офлайн'), packDate ? ` · пакет ${packDate}` : '')];
  if (state.settings.gameMode) rows.push(h('div', 'игровой режим'));
  el.replaceChildren(...rows);
}
