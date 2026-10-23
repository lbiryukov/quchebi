// "Карта" screen: results, selected street with houses, zone, "Где я" on demand.
import { log } from '../log/log.js';
import { h, icon, toast } from './dom.js';
import { state, emit, on } from './state.js';
import { registerScreen, navigate } from './router.js';
import { mountMap, hooks, currentView } from './mapctl.js';
import { mainName, ruName, kindRu, placeLabel } from './feature.js';
import { openCard } from './card.js';

let host;
let pop;
let badge;
let pendingFocus = -1;
let lastFitted = null;

export function initMapScreen() {
  const screen = document.getElementById('screen-map');
  host = h('div.map-host');
  badge = h('div.map-badge', { 'data-testid': 'map-badge' }, '');
  const fitBtn = h('button.btn.small', { type: 'button', 'aria-label': 'Показать все результаты' }, icon('fit'), 'Результаты');
  fitBtn.addEventListener('click', () => {
    if (!currentView()?.fitResults()) toast('Нет результатов для показа');
  });
  const top = h('div.map-top', badge, fitBtn);
  const meBtn = h('button.btn', { type: 'button', 'aria-label': 'Где я' }, icon('locate'), 'Где я');
  meBtn.addEventListener('click', locateMe);
  const zoneBtn = h('button.btn', { type: 'button', 'aria-label': 'Показать зону' }, 'Зона');
  zoneBtn.addEventListener('click', () => {
    if (state.zone?.polygon) currentView()?.fitPolygon(state.zone.polygon);
    else navigate('zone');
  });
  const fab = h('div.map-fab', { style: { bottom: '90px' } }, meBtn, zoneBtn);
  pop = h('div.map-pop', { hidden: true });
  host.append(top, fab, pop);
  screen.append(host);
  updateBadge();
  on('result', updateBadge);
  on('showOnMap', (i) => {
    pendingFocus = i;
    state.selected = i;
    emit('select', i);
    navigate('map');
    if (state.screen === 'map') focusPending();
  });
  registerScreen('map', { onShow, onHide });
}

function updateBadge() {
  const r = state.result;
  if (!badge) return;
  if (!r || r.error) badge.textContent = state.query ? 'Ошибка в запросе' : 'Нет запроса';
  else badge.textContent = `Найдено: ${r.count}`;
}

async function onShow() {
  const v = await mountMap(host);
  if (!v || state.screen !== 'map') return;
  v.setMode('browse');
  hooks.onFeatureTap = showPopup;
  hooks.onAreaTap = null;
  if (pendingFocus >= 0) focusPending();
  else if (state.result && !state.result.error && state.result.count > 0 && lastFitted !== state.result) {
    // New results since the last visit: show them all.
    lastFitted = state.result;
    v.fitResults();
  }
}

function onHide() {
  if (hooks.onFeatureTap === showPopup) hooks.onFeatureTap = null;
  pop.hidden = true;
}

function focusPending() {
  const v = currentView();
  if (!v || pendingFocus < 0) return;
  const i = pendingFocus;
  pendingFocus = -1;
  v.fitFeature(i);
  showPopup(i);
}

function showPopup(i) {
  if (i < 0) {
    pop.hidden = true;
    return;
  }
  state.selected = i;
  emit('select', i);
  const open = h('button.btn.primary.small', { type: 'button' }, 'Карточка');
  open.addEventListener('click', () => openCard(i));
  const close = h('button.btn.small', { type: 'button' }, 'Закрыть');
  close.addEventListener('click', () => {
    pop.hidden = true;
    state.selected = -1;
    emit('select', -1);
  });
  pop.replaceChildren(
    h('div.ka', mainName(i)),
    ruName(i) !== mainName(i) ? h('div.small', ruName(i)) : null,
    h('div.small.muted', [kindRu(i), placeLabel(i)].filter(Boolean).join(' · ')),
    h('div.btn-row', open, close),
  );
  pop.hidden = false;
}

export function locateMe() {
  if (!navigator.geolocation) {
    toast('Геолокация недоступна');
    return;
  }
  toast('Определяю положение…');
  navigator.geolocation.getCurrentPosition(
    (pos) => {
      state.me = { lat: pos.coords.latitude, lon: pos.coords.longitude, acc: pos.coords.accuracy };
      emit('me', state.me);
      currentView()?.flyTo(state.me.lat, state.me.lon, 16);
      log.info('map', 'position obtained', { acc: Math.round(pos.coords.accuracy) });
      toast(`Точность ±${Math.round(pos.coords.accuracy)} м`);
    },
    (err) => {
      log.warn('map', 'geolocation failed', { code: err.code, msg: err.message });
      toast(err.code === 1 ? 'Нет доступа к геопозиции' : 'Не удалось определить положение');
    },
    { enableHighAccuracy: true, timeout: 15000, maximumAge: 20000 },
  );
}
