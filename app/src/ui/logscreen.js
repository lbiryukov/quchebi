// "Журнал": event log (copy for debugging), local error notes from object cards.
import { getLogs, clearLogs, exportLogs } from '../log/log.js';
import * as db from '../store/db.js';
import { log } from '../log/log.js';
import { h, copyText, confirmSheet, toast } from './dom.js';
import { registerScreen, navigate } from './router.js';
import { state } from './state.js';
import { fmtDateTime } from './format.js';
import { findByKey } from './feature.js';
import { openCard } from './card.js';

let root;
let levelFilter = 'all';

export function initLogScreen() {
  root = document.getElementById('screen-log');
  registerScreen('log', { onShow: render });
}

async function render() {
  const page = h('div.page');
  root.replaceChildren(page);
  const back = h('button.btn.small', { type: 'button', onclick: () => navigate('ready') }, 'Назад');
  page.append(h('div.btn-row', back), h('h2', 'Журнал'));

  // Notes
  let notes = [];
  try { notes = (await db.getNotes()) || []; } catch (err) { log.warn('log', 'getNotes failed', { err: String(err) }); }
  const notesBox = h('div.card-box', h('h3', `Заметки об ошибках · ${notes.length}`));
  if (!notes.length) notesBox.append(h('p.small.muted', 'Нет заметок. Их можно оставить в карточке объекта.'));
  for (const n of notes.slice().sort((a, b) => (b.t || 0) - (a.t || 0))) {
    const open = h('button.list-btn', { type: 'button' },
      h('span', h('b', n.name || n.featureKey), h('div.small', n.text), h('div.small.muted', fmtDateTime(n.t || 0))));
    open.addEventListener('click', () => {
      const i = findByKey(n.featureKey);
      if (i >= 0) openCard(i);
      else toast('Объект не найден в текущем пакете');
    });
    const del = h('button.btn.small.ghost', { type: 'button' }, 'Удалить');
    del.addEventListener('click', async () => {
      if (db.removeNote) await db.removeNote(n.t);
      render();
    });
    notesBox.append(h('div', { style: { display: 'flex', gap: '6px' } }, open, del));
  }
  if (notes.length) {
    notesBox.append(h('div.btn-row', h('button.btn.small', {
      type: 'button',
      onclick: () => copyText(JSON.stringify({ version: state.pack?.manifest?.version, notes }, null, 1), 'Заметки скопированы'),
    }, 'Копировать заметки (JSON)')));
  }
  page.append(notesBox);

  // Logs
  let logs = [];
  try { logs = (await getLogs()) || []; } catch (err) { log.warn('log', 'getLogs failed', { err: String(err) }); }
  const seg = h('div.seg', { role: 'group', 'aria-label': 'Уровень' });
  for (const [v, label] of [['all', 'Все'], ['warn', 'Предупреждения'], ['error', 'Ошибки']]) {
    const b = h('button', { type: 'button', 'aria-pressed': String(levelFilter === v) }, label);
    b.addEventListener('click', () => { levelFilter = v; render(); });
    seg.append(b);
  }
  const copyBtn = h('button.btn.small.primary', { type: 'button' }, 'Копировать журнал');
  copyBtn.addEventListener('click', async () => {
    let text = '';
    try { text = await exportLogs(); } catch { text = logs.map(fmtRow).join('\n'); }
    const env = `Quchebi ${state.pack?.manifest?.version || ''} · ${navigator.userAgent}\n`;
    copyText(env + text, 'Журнал скопирован');
  });
  const clearBtn = h('button.btn.small', { type: 'button' }, 'Очистить');
  clearBtn.addEventListener('click', async () => {
    if (await confirmSheet({ title: 'Очистить журнал?', text: 'Записи журнала будут удалены. Заметки останутся.', okText: 'Очистить', danger: true })) {
      await clearLogs();
      render();
    }
  });
  const list = h('div.log-list', { 'data-testid': 'log-list' });
  const rank = { debug: 0, info: 1, warn: 2, error: 3 };
  const min = levelFilter === 'warn' ? 2 : levelFilter === 'error' ? 3 : 0;
  const shown = logs.filter((r) => (rank[r.level] ?? 1) >= min).slice(-400).reverse();
  for (const r of shown) list.append(h(`div.log-row.${r.level || 'info'}`, fmtRow(r)));
  if (!shown.length) list.append(h('p.small.muted', 'Записей нет.'));
  page.append(h('div.card-box', h('h3', `События · ${logs.length}`), seg, h('div.btn-row', copyBtn, clearBtn), list));
}

function fmtRow(r) {
  let data = '';
  if (r.data !== undefined) {
    try { data = ` ${JSON.stringify(r.data)}`; } catch { data = ''; }
  }
  return `${fmtDateTime(r.t || 0)} ${String(r.level || '').padEnd(5)} [${r.tag}] ${r.msg}${data}`;
}
