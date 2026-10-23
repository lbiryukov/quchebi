// Theme handling: 'auto' follows the system; 'light'/'dark' are forced via html[data-theme].
import { state, emit } from './state.js';

const mq = window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null;

export function effectiveTheme() {
  const t = state.settings.theme;
  if (t === 'light' || t === 'dark') return t;
  return mq && mq.matches ? 'dark' : 'light';
}

export function applyTheme(theme) {
  state.settings.theme = theme;
  const root = document.documentElement;
  if (theme === 'light' || theme === 'dark') root.setAttribute('data-theme', theme);
  else root.removeAttribute('data-theme');
  try {
    if (theme === 'light' || theme === 'dark') localStorage.setItem('quchebi.theme', theme);
    else localStorage.removeItem('quchebi.theme');
  } catch {
    /* storage may be blocked */
  }
  const meta = document.querySelectorAll('meta[name="theme-color"]');
  const color = effectiveTheme() === 'dark' ? '#0E131B' : '#1D3B6E';
  meta.forEach((m) => {
    if (theme === 'auto') m.setAttribute('content', m.media.includes('dark') ? '#0E131B' : '#1D3B6E');
    else m.setAttribute('content', color);
  });
  emit('theme', effectiveTheme());
}

mq?.addEventListener?.('change', () => {
  if (state.settings.theme === 'auto') emit('theme', effectiveTheme());
});
