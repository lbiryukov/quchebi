// DOM helpers: element builder, toast, clipboard, sheets (bottom dialogs).
import { log } from '../log/log.js';

/**
 * h('div.cls#id', {attrs}, ...children). Children may be strings, nodes, arrays, null/false.
 * Attribute keys starting with "on" are event listeners; "dataset" and "style" accept objects.
 */
export function h(spec, attrs, ...children) {
  const m = /^([a-z0-9-]+)?((?:[.#][\w-]+)*)$/i.exec(spec) || [null, 'div', ''];
  const el = document.createElement(m[1] || 'div');
  for (const part of (m[2] || '').match(/[.#][\w-]+/g) || []) {
    if (part[0] === '.') el.classList.add(part.slice(1));
    else el.id = part.slice(1);
  }
  if (attrs && (typeof attrs !== 'object' || attrs instanceof Node || Array.isArray(attrs))) {
    children.unshift(attrs);
    attrs = null;
  }
  if (attrs) {
    for (const [k, v] of Object.entries(attrs)) {
      if (v === undefined || v === null || v === false) continue;
      if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2).toLowerCase(), v);
      else if (k === 'dataset') Object.assign(el.dataset, v);
      else if (k === 'style' && typeof v === 'object') Object.assign(el.style, v);
      else if (k === 'class') el.className = v;
      else if (k === 'html') el.innerHTML = v;
      else if (k in el && typeof v !== 'string' && k !== 'list') el[k] = v;
      else el.setAttribute(k, v === true ? '' : String(v));
    }
  }
  append(el, children);
  return el;
}

function append(el, children) {
  for (const c of children) {
    if (c === null || c === undefined || c === false) continue;
    if (Array.isArray(c)) append(el, c);
    else el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
}

export function clear(el) {
  while (el.firstChild) el.removeChild(el.firstChild);
  return el;
}

export const $ = (sel, root = document) => root.querySelector(sel);

/** Inline SVG icon from a small set (stroke icons, 24x24). */
const ICONS = {
  close: '<path d="M6 6l12 12M18 6 6 18"/>',
  clear: '<path d="M7 7l10 10M17 7 7 17"/>',
  history: '<path d="M4 12a8 8 0 1 0 2.4-5.7"/><path d="M4 4v4h4"/><path d="M12 8v4l3 2"/>',
  star: '<path d="m12 3.5 2.6 5.3 5.9.9-4.3 4.1 1 5.8L12 16.9l-5.2 2.7 1-5.8-4.3-4.1 5.9-.9z"/>',
  locate: '<circle cx="12" cy="12" r="3.5"/><path d="M12 2.5v3M12 18.5v3M2.5 12h3M18.5 12h3"/><circle cx="12" cy="12" r="7"/>',
  copy: '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V5a1 1 0 0 0-1-1H5a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h3"/>',
  share: '<path d="M12 3v12M7 8l5-5 5 5"/><path d="M5 13v6a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-6"/>',
  layers: '<path d="m12 3 9 5-9 5-9-5z"/><path d="m3 13 9 5 9-5"/>',
  fit: '<path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/>',
  back: '<path d="M15 5l-7 7 7 7"/>',
  map: '<path d="M3 6.5 9 4l6 2.5L21 4v13.5L15 20l-6-2.5L3 20z"/>',
};

export function icon(name) {
  const span = document.createElement('span');
  span.style.display = 'contents';
  span.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[name] || ''}</svg>`;
  return span.firstChild;
}

let toastTimer = 0;
export function toast(text, ms = 2200) {
  const el = document.getElementById('toast');
  if (!el) return;
  el.textContent = text;
  el.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove('show'), ms);
}

/** Copy text to clipboard with a fallback for older WebKit; shows a toast. */
export async function copyText(text, label = 'Скопировано') {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
    } else {
      const ta = document.createElement('textarea');
      ta.value = text;
      ta.setAttribute('readonly', '');
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.append(ta);
      ta.select();
      document.execCommand('copy');
      ta.remove();
    }
    toast(`${label}: ${text.length > 40 ? text.slice(0, 40) + '…' : text}`);
    log.info('ui', 'copied to clipboard', { len: text.length });
    return true;
  } catch (err) {
    log.warn('ui', 'clipboard copy failed', { err: String(err) });
    toast('Не удалось скопировать');
    return false;
  }
}

/* ---------- sheets ---------- */

const sheetStack = [];

/**
 * Open a bottom sheet. Returns {el, body, close}. Android "back" closes the top sheet
 * (one history entry per sheet).
 */
export function openSheet({ title, full = false, onClose, className = '' } = {}) {
  const root = document.getElementById('sheet-root');
  const body = h('div.sheet-body');
  const closeBtn = h('button.btn.ghost', { type: 'button', 'aria-label': 'Закрыть' }, icon('close'));
  const head = h('div.sheet-head', h('h2', title || ''), closeBtn);
  const sheet = h(`div.sheet${full ? '.full' : ''}`, { role: 'dialog', 'aria-modal': 'true', 'aria-label': title || '' }, head, body);
  if (className) sheet.classList.add(...className.split(' '));
  const backdrop = h('div.sheet-backdrop', sheet);
  let closed = false;

  const entry = {
    el: sheet,
    body,
    head,
    close: (fromPop = false) => {
      if (closed) return;
      closed = true;
      backdrop.remove();
      const i = sheetStack.indexOf(entry);
      if (i >= 0) sheetStack.splice(i, 1);
      if (!fromPop && sheetStack.length === 0 && history.state && history.state.sheet) {
        ignorePops++;
        history.back();
      }
      try {
        onClose?.();
      } catch (err) {
        log.error('ui', 'sheet onClose failed', { err: String(err) });
      }
    },
    setTitle: (t) => { head.querySelector('h2').textContent = t; },
  };
  closeBtn.addEventListener('click', () => entry.close());
  backdrop.addEventListener('click', (e) => { if (e.target === backdrop) entry.close(); });
  root.append(backdrop);
  // One history entry for the whole stack: Android "back" closes the top sheet.
  if (sheetStack.length === 0 && !(history.state && history.state.sheet)) {
    history.pushState({ sheet: 1 }, '', location.href);
  }
  sheetStack.push(entry);
  return entry;
}

let ignorePops = 0;

export function closeAllSheets() {
  while (sheetStack.length) sheetStack[sheetStack.length - 1].close(sheetStack.length > 1);
}

export function sheetsOpen() {
  return sheetStack.length;
}

window.addEventListener('popstate', (e) => {
  if (ignorePops > 0) {
    ignorePops--;
    if (sheetStack.length && !(history.state && history.state.sheet)) history.pushState({ sheet: 1 }, '', location.href);
    return;
  }
  const top = sheetStack[sheetStack.length - 1];
  if (top) {
    e.stopImmediatePropagation?.();
    top.close(true);
    if (sheetStack.length) history.pushState({ sheet: 1 }, '', location.href);
  }
});

document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && sheetStack.length) sheetStack[sheetStack.length - 1].close();
});

/** Simple prompt-like sheet with a text field. Resolves with the value or null. */
export function promptSheet({ title, label, value = '', placeholder = '', multiline = false, inputmode, okText = 'Готово' }) {
  return new Promise((resolve) => {
    let done = false;
    const s = openSheet({ title, onClose: () => { if (!done) resolve(null); } });
    const field = multiline
      ? h('textarea.field', { placeholder, autocapitalize: 'off', autocorrect: 'off', spellcheck: 'false' })
      : h('input.field', { type: 'text', placeholder, autocapitalize: 'off', autocorrect: 'off', spellcheck: 'false', inputmode, enterkeyhint: 'done' });
    field.value = value;
    const ok = h('button.btn.primary', { type: 'button' }, okText);
    const submit = () => { done = true; const v = field.value; s.close(); resolve(v); };
    ok.addEventListener('click', submit);
    if (!multiline) field.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); submit(); } });
    s.body.append(label ? h('label.small.muted', label) : null, field, h('div.btn-row', ok));
    setTimeout(() => field.focus(), 50);
  });
}

/** Confirm sheet. Resolves true/false. */
export function confirmSheet({ title, text, okText = 'Да', cancelText = 'Отмена', danger = false }) {
  return new Promise((resolve) => {
    let done = false;
    const s = openSheet({ title, onClose: () => { if (!done) resolve(false); } });
    const ok = h(`button.btn.${danger ? 'danger' : 'primary'}`, { type: 'button' }, okText);
    const cancel = h('button.btn', { type: 'button' }, cancelText);
    ok.addEventListener('click', () => { done = true; s.close(); resolve(true); });
    cancel.addEventListener('click', () => { done = true; s.close(); resolve(false); });
    s.body.append(h('p', text), h('div.btn-row', ok, cancel));
  });
}
