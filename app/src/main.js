// Boot: logging, storage.persist(), settings/theme, zone restore, pack load, index build,
// screens and router, service worker. Logic lives in search/dsl/zone/store; UI in ui/ and map/.
import '@fontsource/noto-sans-georgian/georgian-400.css';
import '@fontsource/noto-sans-georgian/georgian-700.css';
import './ui/styles.css';

import { log } from './log/log.js';
import { loadPack } from './store/pack.js';
import { buildIndex } from './search/index.js';
import { prepare } from './dsl/eval.js';
import * as db from './store/db.js';

import { state, emit } from './ui/state.js';
import { applyTheme } from './ui/theme.js';
import { initRouter, navigate } from './ui/router.js';
import { initHeader } from './ui/header.js';
import { initSearch } from './ui/search.js';
import { initMapScreen } from './ui/mapscreen.js';
import { initZoneScreen } from './ui/zonescreen.js';
import { initReady } from './ui/ready.js';
import { initLogScreen } from './ui/logscreen.js';
import { initAbout } from './ui/about.js';
import { initUpdates } from './ui/update.js';
import { restoreZone, recomputeMasks, onPackReady } from './ui/zonectl.js';
import { prefetchAssets } from './ui/assets.js';
import { resetCaches } from './ui/feature.js';
import { h } from './ui/dom.js';

const bootEl = document.getElementById('boot');
const bootText = document.getElementById('boot-text');
const bootBar = document.getElementById('boot-bar');

function setBoot(text, fraction) {
  if (bootText) bootText.textContent = text;
  if (bootBar && Number.isFinite(fraction)) bootBar.style.width = `${Math.round(Math.max(0, Math.min(1, fraction)) * 100)}%`;
}

function installGlobalHandlers() {
  window.addEventListener('error', (e) => {
    log.error('window', e.message || 'error', { src: e.filename, line: e.lineno, col: e.colno, stack: e.error?.stack });
  });
  window.addEventListener('unhandledrejection', (e) => {
    log.error('window', 'unhandled rejection', { reason: String(e.reason), stack: e.reason?.stack });
  });
}

/** Keep the app frame equal to the visual viewport so the bottom dock stays above the keyboard. */
function initViewport() {
  const vv = window.visualViewport;
  const app = document.getElementById('app');
  const root = document.documentElement;
  const update = () => {
    const hgt = vv ? vv.height : window.innerHeight;
    root.style.setProperty('--app-h', `${Math.round(hgt)}px`);
    const kb = vv ? window.innerHeight - vv.height > 140 : false;
    app.classList.toggle('kb-open', kb);
    app.style.transform = vv && vv.offsetTop ? `translateY(${Math.round(vv.offsetTop)}px)` : '';
  };
  vv?.addEventListener('resize', update);
  vv?.addEventListener('scroll', update);
  window.addEventListener('resize', update);
  update();
}

async function loadSettings() {
  try {
    state.settings.theme = (await db.getSetting('theme', 'auto')) || 'auto';
    state.settings.gameMode = Boolean(await db.getSetting('gameMode', false));
  } catch (err) {
    log.warn('boot', 'settings unavailable', { err: String(err) });
  }
  applyTheme(state.settings.theme);
}

async function requestPersist() {
  try {
    if (!navigator.storage?.persist) return;
    const already = await navigator.storage.persisted();
    const granted = already || (await navigator.storage.persist());
    log.info('boot', 'storage persistence', { already, granted });
  } catch (err) {
    log.warn('boot', 'storage.persist failed', { err: String(err) });
  }
}

function showBootError(err) {
  const offline = !navigator.onLine;
  bootEl.replaceChildren(
    h('div.plate-big', h('span.ka', 'ქუჩები'), h('span.lat', 'Quchebi')),
    h('p', h('b', 'Не удалось загрузить данные.')),
    h('p.small', offline
      ? 'Нет сети, а пакет данных ещё не скачан. Подключитесь к интернету для первой загрузки.'
      : String(err?.message || err)),
    h('div.btn-row', { style: { justifyContent: 'center' } },
      h('button.btn.primary', { type: 'button', onclick: () => location.reload() }, 'Повторить'),
      h('button.btn', { type: 'button', onclick: () => { bootEl.hidden = true; initRouter(); navigate('log'); } }, 'Журнал')),
  );
}

async function boot() {
  const t0 = performance.now();
  installGlobalHandlers();
  initViewport();
  log.info('boot', 'start', { app: __APP_VERSION__, ua: navigator.userAgent, online: navigator.onLine, base: document.baseURI });

  await loadSettings();
  requestPersist();
  initUpdates();

  initHeader();
  initSearch();
  initMapScreen();
  initZoneScreen();
  initReady();
  initLogScreen();
  initAbout();

  await restoreZone();
  emit('zone', state.zone);

  let pack;
  try {
    setBoot('Загрузка данных…', 0.05);
    pack = await loadPack({
      onProgress: (p) => {
        const f = Number.isFinite(p?.fraction) ? p.fraction : 0;
        const what = p?.phase === 'download' ? 'Скачивание' : p?.phase === 'verify' ? 'Проверка' : p?.phase === 'parse' ? 'Разбор' : 'Загрузка';
        setBoot(`${what} данных${p?.file ? ': ' + p.file : ''}…`, 0.05 + f * 0.8);
      },
    });
  } catch (err) {
    log.error('boot', 'loadPack failed', { err: String(err), stack: err?.stack });
    showBootError(err);
    return;
  }
  state.pack = pack;
  resetCaches();
  emit('pack', pack);
  log.info('boot', 'pack loaded', { version: pack.manifest?.version, features: pack.core?.f?.length });

  try {
    setBoot('Построение индекса…', 0.9);
    const ti = performance.now();
    state.index = buildIndex(pack);
    log.info('boot', 'index built', { n: state.index.n, ms: Math.round(performance.now() - ti) });
  } catch (err) {
    log.error('boot', 'buildIndex failed', { err: String(err), stack: err?.stack });
    showBootError(err);
    return;
  }
  emit('index', state.index);
  // Warm the DSL caches (incl. the fuzzy token index) when the browser is idle.
  const idle = window.requestIdleCallback || ((fn) => setTimeout(fn, 300));
  const warm = () => {
    try {
      const tp = performance.now();
      prepare(state.index, { all: true });
      log.info('boot', 'dsl caches prepared', { ms: Math.round(performance.now() - tp) });
    } catch (err) {
      log.warn('boot', 'dsl prepare failed', { err: String(err) });
    }
  };
  idle(warm, { timeout: 2000 });

  const ready = pack.ready && typeof pack.ready.then === 'function' ? pack.ready : Promise.resolve();
  ready.then(() => {
    try {
      state.index.attach?.();
    } catch (err) {
      log.error('boot', 'index.attach failed', { err: String(err), stack: err?.stack });
    }
    state.packReady = true;
    log.info('boot', 'pack fully ready (geom, houses, areas)', { ms: Math.round(performance.now() - t0) });
    if (state.zone) {
      if (state.masks) onPackReady();
      else recomputeMasks();
    }
    emit('packReady');
  }).catch((err) => {
    log.error('boot', 'pack.ready failed', { err: String(err), stack: err?.stack });
  });

  bootEl.hidden = true;
  let skipped = false;
  try {
    skipped = Boolean(await db.getSetting('zoneSkipped', false));
  } catch { /* ignore */ }
  if (!state.zone && !skipped && !location.hash) location.hash = '#/zone';
  initRouter();
  log.info('boot', 'ui ready', { ms: Math.round(performance.now() - t0), screen: state.screen });

  // Fonts/sprites of the basemap are small: cache them in the background when online, unless
  // the data pack lists them in manifest.tiles (then store/pack.js downloads them itself).
  const packHasFonts = Object.keys(pack.manifest?.tiles?.files || {}).some((k) => k.includes('fonts/'));
  if (navigator.onLine && !state.settings.gameMode && !packHasFonts) {
    setTimeout(() => {
      prefetchAssets().catch((err) => log.info('boot', 'basemap assets prefetch skipped', { err: String(err) }));
    }, 4000);
  }
}

boot().catch((err) => {
  log.error('boot', 'fatal', { err: String(err), stack: err?.stack });
  showBootError(err);
});
