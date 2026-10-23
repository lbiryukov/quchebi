// DSL query field: transparent textarea over a highlighted mirror, autocomplete row.
import { highlight } from '../dsl/highlight.js';
import { complete } from '../dsl/complete.js';
import { log } from '../log/log.js';
import { h, icon } from './dom.js';
import { state, evalCtx } from './state.js';

const CLS = {
  key: 'k', sep: 'sep', op: 'op', tilde: 'op', paren: 'paren',
  regex: 'str', quoted: 'str', error: 'err', value: '', text: '',
};

function escapeHtml(s) {
  return s.replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

/** Build highlighted HTML for `text`; `errSpan` = {pos, len} of a parse error. */
export function highlightHtml(text, errSpan) {
  let spans = [];
  try {
    spans = highlight(text) || [];
  } catch (err) {
    log.warn('query', 'highlight failed', { err: String(err) });
  }
  const cls = new Array(text.length).fill('');
  for (const s of spans) {
    const c = CLS[s.cls] ?? '';
    for (let i = s.pos; i < Math.min(text.length, s.pos + s.len); i++) cls[i] = c;
  }
  const err = new Array(text.length).fill(false);
  if (errSpan && errSpan.pos >= 0) {
    const from = Math.min(errSpan.pos, Math.max(0, text.length - 1));
    const to = Math.min(text.length, errSpan.pos + Math.max(1, errSpan.len || 0));
    for (let i = from; i < to; i++) err[i] = true;
  }
  let html = '';
  let i = 0;
  while (i < text.length) {
    let j = i + 1;
    while (j < text.length && cls[j] === cls[i] && err[j] === err[i]) j++;
    const piece = escapeHtml(text.slice(i, j));
    const c = [cls[i], err[i] ? 'err' : ''].filter(Boolean).join(' ');
    html += c ? `<span class="${c}">${piece}</span>` : piece;
    i = j;
  }
  // Trailing zero-width space keeps the mirror height equal when text ends with a newline.
  return html + '​';
}

/**
 * Create the query field. Options: onInput(text), onSubmit(text), onHistory(), onSave().
 * Returns {el, input, getValue, setValue(text, {cursor, silent}), setError(span), focus, suggestEl}.
 */
export function createQueryInput({ onInput, onSubmit, onHistory, onSave }) {
  const mirror = h('div.q-mirror', { 'aria-hidden': 'true' });
  const input = h('textarea.q-input', {
    rows: 1,
    id: 'q',
    name: 'q',
    'aria-label': 'Запрос',
    placeholder: 'Название или запрос, напр. тип:переулок кто:поэт',
    autocapitalize: 'off',
    autocomplete: 'off',
    autocorrect: 'off',
    spellcheck: 'false',
    enterkeyhint: 'search',
    inputmode: 'search',
  });
  const wrap = h('div.q-wrap', mirror, input);
  const clearBtn = h('button.q-btn', { type: 'button', 'aria-label': 'Очистить запрос', title: 'Очистить' }, icon('clear'));
  const histBtn = h('button.q-btn', { type: 'button', 'aria-label': 'История и сохранённые', title: 'История' }, icon('history'));
  const saveBtn = h('button.q-btn', { type: 'button', 'aria-label': 'Сохранить запрос', title: 'Сохранить' }, icon('star'));
  const row = h('div.q-row', histBtn, wrap, saveBtn, clearBtn);
  const suggestEl = h('div.hscroll.suggest', { 'aria-label': 'Подсказки' });

  let errSpan = null;
  let sugTimer = 0;

  function render() {
    mirror.innerHTML = highlightHtml(input.value, errSpan);
    autoGrow();
  }

  function autoGrow() {
    input.style.height = 'auto';
    const hgt = Math.min(120, Math.max(44, input.scrollHeight));
    input.style.height = `${hgt}px`;
    mirror.scrollTop = input.scrollTop;
  }

  function updateSuggestions() {
    clearTimeout(sugTimer);
    sugTimer = setTimeout(renderSuggestions, 60);
  }

  function renderSuggestions() {
    suggestEl.replaceChildren();
    if (!state.index || document.activeElement !== input) {
      suggestEl.hidden = true;
      return;
    }
    let items = [];
    try {
      items = complete(input.value, input.selectionStart ?? input.value.length, state.index, evalCtx()) || [];
    } catch (err) {
      log.warn('query', 'complete failed', { err: String(err) });
    }
    items = items.slice(0, 20);
    suggestEl.hidden = items.length === 0;
    for (const s of items) {
      const b = h('button.sug', { type: 'button' }, s.label, s.detail ? h('small', String(s.detail)) : null);
      // pointerdown + preventDefault keeps focus (and the keyboard) in the textarea.
      b.addEventListener('pointerdown', (e) => e.preventDefault());
      b.addEventListener('click', () => applySuggestion(s));
      suggestEl.append(b);
    }
  }

  function applySuggestion(s) {
    const text = input.value;
    const cur = input.selectionStart ?? text.length;
    const from = Number.isInteger(s.from) ? s.from : cur;
    const to = Number.isInteger(s.to) ? s.to : cur;
    let ins = s.insert ?? s.label;
    const after = text.slice(to);
    // Add a separating space after a completed value (not after "key:" or an opening paren).
    if (!/[:=(,~]$/.test(ins) && !/^\s/.test(after)) ins += ' ';
    const next = text.slice(0, from) + ins + after;
    setValue(next, { cursor: from + ins.length });
    log.debug('query', 'suggestion applied', { label: s.label });
    input.focus();
  }

  function setValue(text, { cursor, silent = false } = {}) {
    input.value = text;
    const c = cursor ?? text.length;
    try {
      input.setSelectionRange(c, c);
    } catch {
      /* not focusable yet */
    }
    render();
    if (!silent) onInput?.(text);
    updateSuggestions();
  }

  input.addEventListener('input', () => {
    render();
    onInput?.(input.value);
    updateSuggestions();
  });
  input.addEventListener('scroll', () => { mirror.scrollTop = input.scrollTop; });
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      onSubmit?.(input.value);
      input.blur();
    }
  });
  input.addEventListener('focus', updateSuggestions);
  input.addEventListener('click', updateSuggestions);
  input.addEventListener('keyup', (e) => {
    if (e.key.startsWith('Arrow') || e.key === 'Home' || e.key === 'End') updateSuggestions();
  });
  input.addEventListener('blur', () => setTimeout(() => {
    if (document.activeElement !== input) suggestEl.hidden = true;
  }, 150));
  clearBtn.addEventListener('click', () => { setValue(''); input.focus(); });
  histBtn.addEventListener('click', () => onHistory?.());
  saveBtn.addEventListener('click', () => onSave?.());

  suggestEl.hidden = true;
  render();

  return {
    el: row,
    input,
    suggestEl,
    saveBtn,
    getValue: () => input.value,
    setValue,
    setError(span) {
      errSpan = span;
      render();
    },
    focus: () => input.focus(),
    refreshSuggestions: updateSuggestions,
  };
}
