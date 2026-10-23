// Hash router for the six screens (#/search, #/map, #/zone, #/ready, #/log, #/about).
import { log } from '../log/log.js';
import { state, emit, on } from './state.js';

const SCREENS = ['search', 'map', 'zone', 'ready', 'log', 'about'];
const NAV_OF = { search: 'search', map: 'map', zone: 'zone', ready: 'ready', log: 'ready', about: 'ready' };
const handlers = new Map();
let current = null;

export function registerScreen(name, { onShow, onHide } = {}) {
  handlers.set(name, { onShow, onHide });
}

export function navigate(name) {
  if (!SCREENS.includes(name)) name = 'search';
  if (location.hash !== `#/${name}`) location.hash = `#/${name}`;
  else apply();
}

function nameFromHash() {
  const m = /^#\/(\w+)/.exec(location.hash || '');
  return m && SCREENS.includes(m[1]) ? m[1] : 'search';
}

export function apply() {
  const name = nameFromHash();
  if (name === current) return;
  const prev = current;
  current = name;
  state.screen = name;
  for (const s of SCREENS) {
    const el = document.getElementById(`screen-${s}`);
    if (el) el.classList.toggle('active', s === name);
  }
  document.querySelectorAll('.tabbar [data-nav]').forEach((b) => {
    if (b.dataset.nav === NAV_OF[name]) b.setAttribute('aria-current', 'page');
    else b.removeAttribute('aria-current');
  });
  const dock = document.getElementById('search-dock');
  if (dock) dock.hidden = name !== 'search';
  try {
    if (prev) handlers.get(prev)?.onHide?.();
    handlers.get(name)?.onShow?.();
  } catch (err) {
    log.error('router', 'screen handler failed', { name, err: String(err), stack: err?.stack });
  }
  log.debug('router', 'screen', { name });
  emit('screen', name);
}

export function initRouter() {
  document.querySelectorAll('.tabbar [data-nav]').forEach((b) => {
    b.addEventListener('click', () => navigate(b.dataset.nav));
  });
  window.addEventListener('hashchange', apply);
  on('navigate', navigate);
  apply();
}
