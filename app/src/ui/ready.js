// "Готовность" screen: shell, pack, basemap, zone, storage persistence, install hint,
// airplane-mode test, game mode, theme; links to "Журнал" and "О приложении".
import { checkForUpdate, downloadPack, packStatus } from '../store/pack.js';
import * as db from '../store/db.js';
import { log } from '../log/log.js';
import { h, toast, confirmSheet } from './dom.js';
import { state, on } from './state.js';
import { registerScreen, navigate } from './router.js';
import { applyUpdate, checkShellUpdate, setGameMode } from './update.js';
import { applyTheme } from './theme.js';
import { assetStatus, prefetchAssets } from './assets.js';
import { summarize } from './zonectl.js';
import { fmtDate, fmtBytes, fmtKm2, count } from './format.js';

let root;
let installEvent = null;
let busy = false;

window.addEventListener('beforeinstallprompt', (e) => {
  e.preventDefault();
  installEvent = e;
  log.info('pwa', 'install prompt available');
});

export function initReady() {
  root = document.getElementById('screen-ready');
  registerScreen('ready', { onShow: render });
  on('update', () => { if (state.screen === 'ready') render(); });
  on('zone', () => { if (state.screen === 'ready') render(); });
}

function isStandalone() {
  return window.matchMedia?.('(display-mode: standalone)').matches || window.navigator.standalone === true;
}

function isIOS() {
  return /iPad|iPhone|iPod/.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
}

function check(st, title, detail, actions) {
  return h('div.check', { 'data-check': title },
    h('span.st', { class: `st ${st}`, 'aria-label': st === 'ok' ? 'готово' : st === 'warn' ? 'внимание' : 'проблема' }),
    h('div.ttl', title),
    detail ? h('div.det', detail) : null,
    actions && actions.length ? h('div.act', actions) : null);
}

function abtn(label, fn, cls = '') {
  const b = h(`button.btn.small${cls}`, { type: 'button' }, label);
  b.addEventListener('click', async () => {
    if (busy) return;
    try {
      await fn(b);
    } catch (err) {
      log.error('ready', 'action failed', { label, err: String(err), stack: err?.stack });
      toast(`Ошибка: ${err?.message || err}`, 3500);
    }
  });
  return b;
}

async function render() {
  if (!root) return;
  const page = h('div.page');
  page.append(h('h2', 'Готовность к игре'),
    h('p.small.muted', 'Перед стартом все пункты должны быть зелёными. Затем проверьте работу в авиарежиме.'));
  const checks = h('div.checks', { 'data-testid': 'checks' });
  page.append(checks);
  root.replaceChildren(page);

  const [shell, pack, tiles, storage] = await Promise.all([
    shellCheck(), packCheck(), tilesCheck(), storageCheck(),
  ]);
  checks.append(shell, pack, tiles, zoneCheck(), storage, installCheck(), await airplaneCheck());
  page.append(settingsBox(), h('div.btn-row',
    h('button.btn', { type: 'button', onclick: () => navigate('log') }, 'Журнал'),
    h('button.btn', { type: 'button', onclick: () => navigate('about') }, 'О приложении')));
}

async function shellCheck() {
  if (!('serviceWorker' in navigator)) return check('bad', 'Оболочка', 'Браузер не поддерживает офлайн-режим (Service Worker).');
  let precached = false;
  try {
    precached = (await caches.keys()).some((k) => k.includes('precache'));
  } catch { /* ignore */ }
  const controlled = Boolean(navigator.serviceWorker.controller);
  const actions = [];
  if (state.updateWaiting) {
    actions.push(abtn(state.settings.gameMode ? 'Обновление отложено (игровой режим)' : 'Обновить приложение', () => applyUpdate(), '.primary'));
  } else if (!state.settings.gameMode && navigator.onLine) {
    actions.push(abtn('Проверить обновление', async () => {
      await checkShellUpdate();
      toast(state.updateWaiting ? 'Есть обновление' : 'Обновлений нет');
    }));
  }
  if (controlled && precached) return check('ok', 'Оболочка', `Приложение сохранено на устройстве${state.updateWaiting ? ' · ждёт обновление' : ''}.`, actions);
  if (precached) return check('warn', 'Оболочка', 'Сохранена, но ещё не управляет страницей — закройте и откройте приложение.', actions);
  return check('warn', 'Оболочка', 'Ещё сохраняется. Подождите минуту при включённом интернете.', actions);
}

async function packCheck() {
  const m = state.pack?.manifest;
  let st = null;
  try {
    st = await packStatus();
  } catch (err) {
    log.warn('ready', 'packStatus failed', { err: String(err) });
  }
  const files = st?.files ? Object.values(st.files) : [];
  const cached = files.filter((f) => f.cached).length;
  const actions = [];
  if (!state.settings.gameMode && navigator.onLine) {
    actions.push(abtn('Проверить обновление пакета', async (b) => {
      b.disabled = true;
      const u = await checkForUpdate();
      b.disabled = false;
      if (!u?.available) { toast(`Пакет актуален${u?.version ? ' (' + u.version + ')' : ''}`); return; }
      if (await confirmSheet({ title: 'Новый пакет данных', text: `Доступна версия ${u.version}. Скачать сейчас?`, okText: 'Скачать' })) {
        await runDownload({ includeTiles: false });
      }
    }));
  }
  if (!m) return check('bad', 'Пакет данных', 'Не загружен.', actions);
  const counts = m.counts || {};
  const detail = [
    `Версия ${m.version}, собран ${fmtDate(m.built)}, снимок OSM ${fmtDate(m.osm)}.`,
    counts.features ? `${count(counts.features, ['объект', 'объекта', 'объектов'])}, ${count(counts.houses || 0, ['адрес', 'адреса', 'адресов'])}.` : '',
    files.length ? `В кэше ${cached} из ${files.length} файлов.` : '',
    state.packReady ? '' : 'Геометрия и дома ещё загружаются…',
  ].filter(Boolean).join(' ');
  const ok = files.length ? cached === files.length : true;
  return check(ok && state.packReady ? 'ok' : 'warn', 'Пакет данных', detail, actions);
}

async function tilesCheck() {
  let st = null;
  let assets = null;
  try { st = await packStatus(); } catch { /* ignore */ }
  try { assets = await assetStatus(); } catch { /* ignore */ }
  // The PMTiles archive comes either with the data pack (manifest.tiles, store/pack.js) or,
  // when the pack does not list it, from tiles/tiles-manifest.json (assets.js).
  const tl = st?.tiles ? Object.entries(st.tiles).filter(([k]) => k.endsWith('.pmtiles')) : [];
  const viaPack = tl.length > 0;
  const tilesCached = viaPack ? tl.every(([, v]) => v.cached) : Boolean(assets?.pmtiles?.cached);
  const assetsOk = assets ? assets.cached === assets.total : true;
  const parts = [];
  if (viaPack || assets?.pmtiles) {
    const mb = assets?.pmtiles?.bytes ? ` (${fmtBytes(assets.pmtiles.bytes)})` : '';
    parts.push(tilesCached ? `Подложка Тбилиси сохранена${mb}.` : `Подложка не скачана${mb} — без неё офлайн видны только улицы.`);
  } else {
    parts.push('Подложки нет — карта покажет только наши улицы.');
  }
  if (assets) parts.push(`Шрифты и значки карты: ${assets.cached} из ${assets.total} (${fmtBytes(assets.bytes)}).`);
  const actions = [];
  if (navigator.onLine && (!tilesCached || !assetsOk) && (viaPack || assets)) {
    actions.push(abtn('Скачать подложку', async () => {
      // With manifest.tiles the pack download also brings fonts/sprites (sha256-checked).
      if (viaPack) await runDownload({ includeTiles: true });
      else await runAssets({ withTiles: !tilesCached });
      try {
        (await import('../map/basemap.js')).resetBasemapCache();
      } catch { /* map not loaded */ }
      render();
    }, '.primary'));
  }
  const ok = tilesCached && assetsOk;
  return check(ok ? 'ok' : 'warn', 'Подложка карты', parts.join(' '), actions);
}

async function runAssets({ withTiles }) {
  busy = true;
  const bar = h('div.progress', h('i'));
  const label = h('div.small', 'Скачивание подложки…');
  const box = h('div.card-box', label, bar);
  root.querySelector('.page')?.prepend(box);
  try {
    const r = await prefetchAssets({
      withTiles,
      onProgress: ({ done, total, file, loaded, bytes }) => {
        const f = bytes && loaded !== undefined && file?.endsWith('.pmtiles') ? loaded / bytes : done / Math.max(1, total);
        bar.firstChild.style.width = `${Math.round(f * 100)}%`;
        label.textContent = file?.endsWith('.pmtiles')
          ? `Подложка: ${fmtBytes(loaded || 0)} из ${fmtBytes(bytes || 0)}`
          : `Шрифты и значки: ${done} из ${total}`;
      },
    });
    toast(r.failed ? `Не скачано файлов: ${r.failed}` : 'Подложка сохранена', 3000);
  } finally {
    busy = false;
    box.remove();
  }
}

async function runDownload({ includeTiles }) {
  busy = true;
  const bar = h('div.progress', h('i'));
  const label = h('div.small', 'Скачивание…');
  const box = h('div.card-box', label, bar);
  root.querySelector('.page')?.prepend(box);
  try {
    const man = await downloadPack({
      includeTiles,
      onProgress: (p) => {
        const f = Number.isFinite(p.fraction) ? p.fraction : p.total ? p.loaded / p.total : 0;
        bar.firstChild.style.width = `${Math.round(f * 100)}%`;
        label.textContent = `${p.phase === 'verify' ? 'Проверка' : 'Скачивание'} ${p.file || ''} · ${Math.round(f * 100)}%`;
      },
    });
    log.info('ready', 'pack downloaded', { version: man?.version, includeTiles });
    busy = false;
    if (await confirmSheet({ title: 'Готово', text: 'Данные скачаны. Перезапустить приложение, чтобы применить?', okText: 'Перезапустить' })) {
      location.reload();
    } else render();
  } finally {
    busy = false;
    box.remove();
  }
}

function zoneCheck() {
  const z = state.zone;
  if (!z) return check('warn', 'Зона', 'Не задана — поиск идёт по всему городу.', [abtn('Задать зону', () => navigate('zone'), '.primary')]);
  const s = summarize(state.masks, z.polygon);
  return check(state.zoneEnabled ? 'ok' : 'warn', 'Зона',
    `${fmtKm2(s.areaKm2)}, ${count(s.streets, ['улица', 'улицы', 'улиц'])}, задана ${fmtDate(z.createdAt)}${state.zoneEnabled ? '' : ' · временно выключена'}.`,
    [abtn('Открыть', () => navigate('zone'))]);
}

async function storageCheck() {
  let persisted = false;
  let est = null;
  try { persisted = (await navigator.storage?.persisted?.()) || false; } catch { /* ignore */ }
  try { est = await navigator.storage?.estimate?.(); } catch { /* ignore */ }
  const detail = [
    persisted ? 'Браузер обещал не удалять данные.' : 'Браузер может удалить данные при нехватке места.',
    est ? `Занято ${fmtBytes(est.usage || 0)} из ${fmtBytes(est.quota || 0)}.` : '',
    !persisted && isIOS() ? 'На iPhone установите приложение на экран «Домой» — тогда данные не стираются через 7 дней.' : '',
  ].filter(Boolean).join(' ');
  const actions = persisted ? [] : [abtn('Запросить сохранение', async () => {
    const ok = await navigator.storage?.persist?.();
    log.info('ready', 'storage.persist()', { ok });
    toast(ok ? 'Хранилище закреплено' : 'Браузер отказал — установите приложение на экран «Домой»', 3500);
    render();
  })];
  return check(persisted ? 'ok' : 'warn', 'Хранилище', detail, actions);
}

function installCheck() {
  if (isStandalone()) return check('ok', 'Установка', 'Запущено как приложение с экрана «Домой».');
  if (isIOS()) {
    return check('warn', 'Установка', 'Safari: кнопка «Поделиться» → «На экран „Домой“». Затем откройте Quchebi с иконки, скачайте данные и задайте зону уже там.');
  }
  const actions = installEvent ? [abtn('Установить', async () => {
    installEvent.prompt();
    const r = await installEvent.userChoice;
    log.info('pwa', 'install prompt result', { outcome: r?.outcome });
    installEvent = null;
    render();
  }, '.primary')] : [];
  return check('warn', 'Установка', 'Меню браузера → «Установить приложение» или «Добавить на главный экран».', actions);
}

async function airplaneCheck() {
  let at = null;
  try { at = await db.getSetting('airplaneTestAt', null); } catch { /* ignore */ }
  const detail = at
    ? `Проверено ${fmtDate(at)}.`
    : 'Включите авиарежим, закройте приложение и откройте снова: поиск, карта и зона должны работать.';
  return check(at && !navigator.onLine ? 'ok' : at ? 'ok' : 'warn', 'Тест в авиарежиме', `${detail}${navigator.onLine ? '' : ' Сейчас сети нет — самое время проверить.'}`, [
    abtn('Проверено, всё работает', async () => {
      await db.setSetting('airplaneTestAt', Date.now());
      render();
    }),
  ]);
}

function settingsBox() {
  const game = h('input', { type: 'checkbox', checked: state.settings.gameMode, 'data-testid': 'game-mode' });
  game.addEventListener('change', async () => { await setGameMode(game.checked); render(); });
  const seg = h('div.seg', { role: 'group', 'aria-label': 'Тема' });
  for (const [v, label] of [['auto', 'Как в системе'], ['light', 'Светлая'], ['dark', 'Тёмная']]) {
    const b = h('button', { type: 'button', 'aria-pressed': String(state.settings.theme === v) }, label);
    b.addEventListener('click', async () => {
      applyTheme(v);
      await db.setSetting('theme', v).catch(() => {});
      for (const x of seg.children) x.setAttribute('aria-pressed', String(x === b));
    });
    seg.append(b);
  }
  return h('div.card-box',
    h('h3', 'Настройки'),
    h('label.switch', game, h('span', 'Игровой режим — не обновлять приложение и данные до выключения')),
    h('div', h('div.small.muted', 'Тема'), seg));
}
