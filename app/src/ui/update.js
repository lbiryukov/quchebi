// Service worker registration (vite-plugin-pwa, registerType 'prompt'): update banner,
// "Игровой режим" blocks shell and pack updates until switched off.
import { registerSW } from 'virtual:pwa-register';
import * as db from '../store/db.js';
import { log } from '../log/log.js';
import { state, emit } from './state.js';
import { toast } from './dom.js';

let updateSW = null;

export function initUpdates() {
  const banner = document.getElementById('update-banner');
  document.getElementById('update-now')?.addEventListener('click', () => applyUpdate());
  document.getElementById('update-later')?.addEventListener('click', () => { banner.hidden = true; });
  if (!('serviceWorker' in navigator)) {
    log.warn('pwa', 'service workers are not supported');
    return;
  }
  try {
    updateSW = registerSW({
      immediate: true,
      onNeedRefresh() {
        state.updateWaiting = true;
        log.info('pwa', 'new shell version is waiting', { gameMode: state.settings.gameMode });
        emit('update', true);
        if (!state.settings.gameMode) banner.hidden = false;
      },
      onOfflineReady() {
        log.info('pwa', 'shell precached, offline ready');
        toast('Приложение готово к работе офлайн');
        emit('update', false);
      },
      onRegisteredSW(url, reg) {
        state.swRegistered = true;
        state.swRegistration = reg || null;
        log.info('pwa', 'service worker registered', { url });
        emit('update', false);
      },
      onRegisterError(err) {
        log.error('pwa', 'service worker registration failed', { err: String(err) });
      },
    });
  } catch (err) {
    log.error('pwa', 'registerSW threw', { err: String(err) });
  }
}

export async function applyUpdate() {
  if (state.settings.gameMode) {
    toast('Игровой режим: обновления отложены');
    return;
  }
  if (!updateSW) return;
  log.info('pwa', 'applying shell update');
  document.getElementById('update-banner').hidden = true;
  await updateSW(true);
}

/** Ask the browser to re-check the service worker script (not in game mode). */
export async function checkShellUpdate() {
  if (state.settings.gameMode) return false;
  try {
    await state.swRegistration?.update();
    return true;
  } catch (err) {
    log.warn('pwa', 'sw update check failed', { err: String(err) });
    return false;
  }
}

export async function setGameMode(on) {
  state.settings.gameMode = Boolean(on);
  try {
    await db.setSetting('gameMode', state.settings.gameMode);
  } catch {
    /* ignore */
  }
  log.info('pwa', 'game mode', { on: state.settings.gameMode });
  const banner = document.getElementById('update-banner');
  if (state.settings.gameMode) banner.hidden = true;
  else if (state.updateWaiting) banner.hidden = false;
  emit('settings', state.settings);
}
