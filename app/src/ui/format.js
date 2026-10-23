// Formatting helpers for Russian UI text. Dates are DD/MM/YYYY.

const pad2 = (n) => String(n).padStart(2, '0');

/** Date | number(ms) | ISO string -> "DD/MM/YYYY". Strings already in DD/MM/YYYY pass through. */
export function fmtDate(v) {
  if (v === null || v === undefined || v === '') return '';
  if (typeof v === 'string' && /^\d{2}\/\d{2}\/\d{4}$/.test(v)) return v;
  const d = v instanceof Date ? v : new Date(v);
  if (Number.isNaN(d.getTime())) return String(v);
  return `${pad2(d.getDate())}/${pad2(d.getMonth() + 1)}/${d.getFullYear()}`;
}

/** "DD/MM" short form. */
export function fmtDateShort(v) {
  const s = fmtDate(v);
  return s.length >= 5 ? s.slice(0, 5) : s;
}

export function fmtTime(ms) {
  const d = new Date(ms);
  return `${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}`;
}

export function fmtDateTime(ms) {
  return `${fmtDate(ms)} ${fmtTime(ms)}`;
}

function decimal(x, digits) {
  return x.toFixed(digits).replace('.', ',');
}

export function fmtKm2(x) {
  if (!Number.isFinite(x)) return '—';
  if (x < 0.1) return `${decimal(x, 2)} км²`;
  if (x < 10) return `${decimal(x, 1)} км²`;
  return `${Math.round(x)} км²`;
}

export function fmtLen(m) {
  if (!Number.isFinite(m) || m <= 0) return '';
  if (m < 1000) return `${Math.round(m)} м`;
  return `${decimal(m / 1000, m < 10000 ? 1 : 0)} км`;
}

export function fmtBytes(b) {
  if (!Number.isFinite(b)) return '—';
  if (b < 1024) return `${b} Б`;
  if (b < 1024 * 1024) return `${decimal(b / 1024, 0)} КБ`;
  if (b < 1024 * 1024 * 1024) return `${decimal(b / 1024 / 1024, 1)} МБ`;
  return `${decimal(b / 1024 / 1024 / 1024, 2)} ГБ`;
}

/** plural(5, ['улица', 'улицы', 'улиц']) -> 'улиц' */
export function plural(n, forms) {
  const a = Math.abs(n) % 100;
  const b = a % 10;
  if (a > 10 && a < 20) return forms[2];
  if (b > 1 && b < 5) return forms[1];
  if (b === 1) return forms[0];
  return forms[2];
}

export function count(n, forms) {
  return `${n.toLocaleString('ru-RU')} ${plural(n, forms)}`;
}

export function fmtYears(b, d) {
  if (!b && !d) return '';
  return `${b || '?'}–${d || ''}`.replace(/–$/, '–…');
}

/** Russian compass names for direction-mask bits 0..7 (N, NE, E, SE, S, SW, W, NW). */
export const COMPASS = ['С', 'СВ', 'В', 'ЮВ', 'Ю', 'ЮЗ', 'З', 'СЗ'];

export function fmtDirMask(mask) {
  const out = [];
  for (let b = 0; b < 8; b++) if (mask & (1 << b)) out.push(COMPASS[b]);
  return out.join('/');
}
