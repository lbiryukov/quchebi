// City picker ("Выберите район"): a full-screen list of the cities of store/cities.js with the
// offline status of each pack (installed version, size; else the size of the network pack when
// online). Shown at the first start (no city chosen yet) and from the top-bar city chip or the
// "Готовность" screen. The first choice boots the app in place; a switch from inside the app
// remembers the choice and reloads the page (all module-level caches are per page load).
import { CITIES, getCity, currentCityId, rememberCity, CITY_PARAM } from '../store/cities.js';
import { cityPackInfo, fetchManifest } from '../store/pack.js';
import * as db from '../store/db.js';
import { log } from '../log/log.js';
import { h, clear } from './dom.js';
import { fmtBytes, fmtDate } from './format.js';

let openState = null;

function root() {
  return document.getElementById('city-picker');
}

/** URL of the app without ?city= (and with the search screen), for the reload after a switch. */
export function appUrlWithoutCity() {
  const u = new URL(location.href);
  u.searchParams.delete(CITY_PARAM);
  u.hash = '#/search';
  return u.href;
}

function statusLine(info) {
  if (!info) return 'Проверяю…';
  const parts = [];
  if (info.cached) {
    parts.push('Скачан для офлайна');
    if (info.version) parts.push(`версия ${info.version}`);
    if (info.bytes) parts.push(fmtBytes(info.bytes));
    if (info.tiles) parts.push('с подложкой карты');
  } else if (info.netVersion) {
    parts.push('Не скачан');
    parts.push(`версия ${info.netVersion}${info.netBuilt ? ` от ${fmtDate(info.netBuilt)}` : ''}`);
    if (info.netBytes) parts.push(`${fmtBytes(info.netBytes)} при первом запуске`);
  } else if (info.netError) {
    parts.push(navigator.onLine ? 'Данные пока недоступны' : 'Не скачан — для первой загрузки нужен интернет');
  } else {
    parts.push('Не скачан');
  }
  return parts.join(' · ');
}

async function loadInfo(city) {
  const info = await cityPackInfo(city.id);
  if (!info.cached && navigator.onLine) {
    try {
      const m = await fetchManifest({ city: city.id });
      info.netVersion = m.version;
      info.netBuilt = m.built || null;
      info.netBytes = Object.values(m.files || {}).reduce((s, x) => s + ((x && x.bytes) || 0), 0);
    } catch (err) {
      info.netError = String(err?.message || err);
      log.info('picker', 'network manifest unavailable', { city: city.id, err: info.netError });
    }
  }
  return info;
}

function render({ current, cancellable, resolve }) {
  const el = root();
  const list = h('div.picker-list', { role: 'list' });
  for (const city of CITIES) {
    const status = h('span.picker-status', { 'data-testid': `city-status-${city.id}` }, statusLine(null));
    const isCurrent = city.id === current;
    const btn = h('button.picker-item', {
      type: 'button',
      role: 'listitem',
      'data-city': city.id,
      'data-testid': `city-${city.id}`,
      'aria-current': isCurrent ? 'true' : null,
    },
    h('span.picker-title', city.title, isCurrent ? h('span.picker-cur', ' · сейчас') : null),
    h('span.picker-sub', city.sub),
    status);
    btn.addEventListener('click', () => resolve(city.id));
    list.append(btn);
    loadInfo(city).then((info) => { status.textContent = statusLine(info); status.classList.toggle('ok', Boolean(info.cached)); })
      .catch((err) => { status.textContent = 'Статус неизвестен'; log.warn('picker', 'status failed', { city: city.id, err: String(err) }); });
  }
  clear(el).append(h('div.picker-page',
    h('div.plate-big', h('span.ka', 'ქუჩები'), h('span.lat', 'Quchebi')),
    h('h1.picker-h', 'Выберите район'),
    h('p.small.muted', 'Поиск улиц и мест для «Бегущего города». Данные района скачиваются один раз и работают офлайн; выбор можно поменять кнопкой с названием района вверху экрана.'),
    list,
    cancellable ? h('div.btn-row', { style: { justifyContent: 'center' } },
      h('button.btn', { type: 'button', 'data-testid': 'city-cancel', onclick: () => resolve(null) }, 'Назад')) : null));
  el.hidden = false;
  el.querySelector('.picker-item')?.focus?.({ preventScroll: true });
}

/**
 * Show the picker. Resolves with the chosen city id, or null when cancelled (only when
 * `cancellable`). Does not remember anything by itself.
 */
export function pickCity({ cancellable = false } = {}) {
  if (openState) return openState.promise;
  let resolveFn;
  const promise = new Promise((r) => { resolveFn = r; });
  const current = currentCityId();
  openState = { promise };
  log.info('picker', 'shown', { current, cancellable });
  render({
    current,
    cancellable,
    resolve: (id) => {
      root().hidden = true;
      openState = null;
      log.info('picker', 'picked', { id, current });
      resolveFn(id && getCity(id) ? id : null);
    },
  });
  return promise;
}

/** From inside the app: choose a city; a different one is remembered and the page reloads. */
export async function switchCity() {
  const id = await pickCity({ cancellable: true });
  if (!id || id === currentCityId()) return false;
  await rememberCity(id, { db });
  log.info('picker', 'city switched, reloading', { from: currentCityId(), to: id });
  const next = appUrlWithoutCity();
  // a change of the hash alone does not reload the page
  if (next.split('#')[0] === location.href.split('#')[0]) {
    history.replaceState(null, '', next);
    location.reload();
  } else {
    location.replace(next);
  }
  return true;
}
