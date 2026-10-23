// Zone persistence and masks: restore at boot, save/clear, temporary disable, preview counts,
// share code. Masks are computed once per saved zone (computeZoneMasks), queries only read them.
import { computeZoneMasks } from '../zone/masks.js';
import { encodeZone, tryDecodeZone } from '../zone/code.js';
import * as db from '../store/db.js';
import { log } from '../log/log.js';
import { state, emit } from './state.js';
import { ringAreaKm2 } from '../map/geomutil.js';
import { copyText, toast } from './dom.js';

const LS_KEY = 'quchebi.zone';

export async function restoreZone() {
  let z = null;
  try {
    z = await db.getZone();
  } catch (err) {
    log.warn('zone', 'db.getZone failed', { err: String(err) });
  }
  if (!z) {
    // db.js mirrors to localStorage itself; this is a second safety net.
    try {
      const raw = localStorage.getItem(LS_KEY);
      if (raw) z = JSON.parse(raw);
    } catch {
      /* ignore */
    }
  }
  if (z && Array.isArray(z.polygon) && z.polygon.length >= 3) {
    state.zone = z;
    log.info('zone', 'zone restored', { vertices: z.polygon.length, createdAt: z.createdAt, mode: z.mode });
  } else {
    state.zone = null;
  }
  try {
    const en = await db.getSetting('zoneEnabled', true);
    state.zoneEnabled = en !== false;
  } catch {
    state.zoneEnabled = true;
  }
  return state.zone;
}

/** Compute masks for the saved zone (after the index exists). */
export function recomputeMasks() {
  if (!state.zone || !state.index) {
    state.masks = null;
    emit('zone', state.zone);
    return null;
  }
  const t0 = performance.now();
  try {
    state.masks = computeZoneMasks(state.index, state.zone.polygon, { rule: 'default' });
    log.info('zone', 'masks computed', {
      ms: Math.round(performance.now() - t0), features: state.masks.counts?.features, houses: state.masks.counts?.houses,
    });
  } catch (err) {
    state.masks = null;
    log.error('zone', 'computeZoneMasks failed', { err: String(err), stack: err?.stack });
  }
  emit('zone', state.zone);
  return state.masks;
}

/** After pack.ready: geometry/houses arrived, refresh counts. */
export function onPackReady() {
  if (!state.zone) return;
  if (state.masks?.refreshHouses) {
    try {
      state.masks.refreshHouses();
      emit('zone', state.zone);
      return;
    } catch (err) {
      log.warn('zone', 'refreshHouses failed, recomputing', { err: String(err) });
    }
  }
  recomputeMasks();
}

export function previewMasks(polygon) {
  if (!state.index || !polygon || polygon.length < 3) return null;
  try {
    return computeZoneMasks(state.index, polygon, { rule: 'default' });
  } catch (err) {
    log.warn('zone', 'preview masks failed', { err: String(err) });
    return null;
  }
}

/** {areaKm2, features, streets, houses} for a masks object. */
export function summarize(masks, polygon) {
  const out = { areaKm2: masks?.areaKm2 ?? (polygon ? ringAreaKm2(polygon) : 0), features: 0, streets: 0, houses: null };
  if (!masks) return out;
  out.features = masks.counts?.features ?? (masks.featDefault?.count?.() || 0);
  out.houses = masks.counts?.houses ?? null;
  const kinds = state.pack?.core?.kinds || [];
  const fs = state.pack?.core?.f || [];
  try {
    masks.featDefault?.forEach((i) => {
      const g = kinds[fs[i]?.k]?.group;
      if (g === 'street*' || g === 'lane*') out.streets++;
    });
  } catch {
    out.streets = out.features;
  }
  return out;
}

export async function saveZone({ polygon, base, buffer = 0, mode = 'draw', name = '' }) {
  const rec = { polygon, base: base || polygon, buffer, mode, name, createdAt: Date.now() };
  try {
    await db.setZone(rec);
  } catch (err) {
    log.error('zone', 'db.setZone failed', { err: String(err) });
    toast('Не удалось сохранить зону в базе — сохранена только копия');
  }
  try {
    localStorage.setItem(LS_KEY, JSON.stringify(rec));
  } catch {
    /* quota or blocked */
  }
  state.zone = rec;
  state.zoneEnabled = true;
  db.setSetting('zoneEnabled', true).catch(() => {});
  log.info('zone', 'zone saved', { vertices: polygon.length, buffer, mode });
  recomputeMasks();
  return rec;
}

export async function clearZone() {
  try {
    await db.clearZone();
  } catch (err) {
    log.warn('zone', 'db.clearZone failed', { err: String(err) });
  }
  try {
    localStorage.removeItem(LS_KEY);
  } catch {
    /* ignore */
  }
  state.zone = null;
  state.masks = null;
  log.info('zone', 'zone cleared');
  emit('zone', null);
}

export async function setZoneEnabled(on) {
  state.zoneEnabled = Boolean(on);
  try {
    await db.setSetting('zoneEnabled', state.zoneEnabled);
  } catch {
    /* ignore */
  }
  log.info('zone', 'zone toggled', { on: state.zoneEnabled });
  emit('zone', state.zone);
}

export function zoneCode(polygon = state.zone?.polygon) {
  return polygon ? encodeZone(polygon) : '';
}

export function decodeCode(text) {
  try {
    return tryDecodeZone(text);
  } catch {
    return null;
  }
}

export async function shareZone() {
  const code = zoneCode();
  if (!code) return;
  const text = `Зона Quchebi: ${code}`;
  if (navigator.share) {
    try {
      await navigator.share({ title: 'Зона Quchebi', text });
      log.info('zone', 'zone shared via Web Share');
      return;
    } catch (err) {
      if (err?.name === 'AbortError') return;
      log.warn('zone', 'web share failed, copying', { err: String(err) });
    }
  }
  await copyText(text, 'Код зоны скопирован');
}
