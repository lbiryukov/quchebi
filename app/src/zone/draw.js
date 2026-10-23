// Freehand zone drawing: a canvas overlay above the map (touch-action:none,
// overscroll-behavior:none), Pointer Events with pointer capture, auto-closing, simplify-js in
// screen pixels, unproject to lat/lon, self-intersection repair, accidental-touch protection.
import simplify from 'simplify-js';
import { log } from '../log/log.js';
import { cleanRing, ringAreaKm2 } from '../map/geomutil.js';

export const MIN_AREA_KM2 = 0.05;
export const DRAW_DEFAULTS = { tolerance: 4, minVertices: 3, maxVertices: 60, minAreaKm2: MIN_AREA_KM2 };

/**
 * Turn a screen stroke into a zone polygon.
 * points: [{x, y}] in CSS px; unproject(x, y) -> [lat, lon].
 * Returns {polygon, vertices, areaKm2, fixed, tolerance} or {error}.
 */
export function finalizeStroke(points, unproject, opts = {}) {
  const o = { ...DRAW_DEFAULTS, ...opts };
  if (!points || points.length < 6) return { error: 'Контур слишком короткий — обведите зону одним движением.' };
  let pathLen = 0;
  for (let i = 1; i < points.length; i++) pathLen += Math.hypot(points[i].x - points[i - 1].x, points[i].y - points[i - 1].y);
  if (pathLen < 80) return { error: 'Слишком короткий штрих — похоже на случайное касание. Перерисуйте.' };

  // Auto-close: the ring always returns to the first point.
  const closed = [...points, points[0]];
  let tol = o.tolerance;
  let simp = simplify(closed, tol, true);
  while (simp.length - 1 > o.maxVertices && tol < 60) {
    tol *= 1.35;
    simp = simplify(closed, tol, true);
  }
  let ringPx = simp.slice(0, -1);
  if (ringPx.length < o.minVertices) return { error: 'Слишком мало точек — перерисуйте контур крупнее.' };

  let ring = ringPx.map((p) => unproject(p.x, p.y));
  let fixed = false;
  try {
    const c = cleanRing(ring);
    ring = c.ring;
    fixed = c.fixed;
  } catch (err) {
    return { error: 'Контур пересекает сам себя и не исправляется — перерисуйте.' };
  }
  const area = ringAreaKm2(ring);
  if (!(area > o.minAreaKm2)) {
    return { error: `Слишком маленькая зона (${area.toFixed(3).replace('.', ',')} км²) — перерисуйте.`, areaKm2: area };
  }
  return { polygon: ring, vertices: ring.length, areaKm2: area, fixed, tolerance: tol };
}

/**
 * Start freehand drawing over the map view. Returns {stop()}.
 * opts: onDone(result), onError(message), hint (string).
 */
export function startFreehand(view, { onDone, onError, hint = 'Обведите зону одним движением пальца' } = {}) {
  const host = view.map.getContainer();
  const canvas = document.createElement('canvas');
  canvas.className = 'draw-canvas';
  canvas.setAttribute('aria-label', 'Поле для рисования зоны');
  canvas.dataset.testid = 'draw-canvas';
  const hintEl = document.createElement('div');
  hintEl.className = 'draw-hint';
  hintEl.textContent = hint;
  host.append(canvas, hintEl);
  const ctx = canvas.getContext('2d');
  const style = getComputedStyle(document.documentElement);
  const color = style.getPropertyValue('--accent').trim() || '#8a2242';

  let dpr = 1;
  function resize() {
    const r = host.getBoundingClientRect();
    dpr = window.devicePixelRatio || 1;
    canvas.width = Math.max(1, Math.round(r.width * dpr));
    canvas.height = Math.max(1, Math.round(r.height * dpr));
    canvas.style.width = `${r.width}px`;
    canvas.style.height = `${r.height}px`;
    redraw();
  }
  const ro = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(resize) : null;
  ro?.observe(host);

  let active = null; // pointerId
  let pts = [];
  let raf = 0;

  function redraw() {
    raf = 0;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    if (pts.length < 2) return;
    ctx.lineWidth = 4;
    ctx.lineJoin = 'round';
    ctx.lineCap = 'round';
    ctx.strokeStyle = color;
    ctx.fillStyle = color + '22';
    ctx.beginPath();
    ctx.moveTo(pts[0].x, pts[0].y);
    for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i].x, pts[i].y);
    ctx.stroke();
    // closing segment preview
    ctx.save();
    ctx.setLineDash([8, 8]);
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(pts[pts.length - 1].x, pts[pts.length - 1].y);
    ctx.lineTo(pts[0].x, pts[0].y);
    ctx.stroke();
    ctx.restore();
    ctx.beginPath();
    ctx.moveTo(pts[0].x, pts[0].y);
    for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i].x, pts[i].y);
    ctx.closePath();
    ctx.fill();
  }
  const schedule = () => { if (!raf) raf = requestAnimationFrame(redraw); };

  function local(e) {
    const r = canvas.getBoundingClientRect();
    return { x: e.clientX - r.left, y: e.clientY - r.top };
  }

  function addPoint(p) {
    const last = pts[pts.length - 1];
    if (!last || Math.hypot(p.x - last.x, p.y - last.y) >= 2) pts.push(p);
  }

  function onDown(e) {
    if (active !== null) return; // ignore extra fingers
    if (e.pointerType === 'mouse' && e.button !== 0) return;
    e.preventDefault();
    active = e.pointerId;
    try {
      canvas.setPointerCapture(e.pointerId);
    } catch {
      /* synthetic events may not support capture */
    }
    pts = [local(e)];
    hintEl.hidden = true;
    schedule();
  }

  function onMove(e) {
    if (e.pointerId !== active) return;
    e.preventDefault();
    const list = typeof e.getCoalescedEvents === 'function' ? e.getCoalescedEvents() : null;
    if (list && list.length) for (const ce of list) addPoint(local(ce));
    else addPoint(local(e));
    schedule();
  }

  function onUp(e) {
    if (e.pointerId !== active) return;
    e.preventDefault();
    active = null;
    try {
      canvas.releasePointerCapture(e.pointerId);
    } catch {
      /* ignore */
    }
    const t0 = performance.now();
    const res = finalizeStroke(pts, (x, y) => view.unproject(x, y));
    log.info('zone', 'freehand stroke finished', {
      points: pts.length, ok: !res.error, vertices: res.vertices, areaKm2: res.areaKm2,
      fixed: res.fixed, ms: Math.round(performance.now() - t0),
    });
    if (res.error) {
      pts = [];
      schedule();
      hintEl.hidden = false;
      onError?.(res.error);
      return;
    }
    onDone?.(res);
  }

  function onCancel(e) {
    if (e.pointerId !== active) return;
    active = null;
    pts = [];
    schedule();
    hintEl.hidden = false;
  }

  // Block iOS rubber-band/scroll while drawing (touch-action alone is not always enough).
  const blockTouch = (e) => e.preventDefault();
  canvas.addEventListener('pointerdown', onDown);
  canvas.addEventListener('pointermove', onMove);
  canvas.addEventListener('pointerup', onUp);
  canvas.addEventListener('pointercancel', onCancel);
  canvas.addEventListener('touchmove', blockTouch, { passive: false });
  canvas.addEventListener('touchstart', blockTouch, { passive: false });
  canvas.addEventListener('contextmenu', blockTouch);
  resize();
  log.info('zone', 'freehand drawing started');

  return {
    canvas,
    stop() {
      ro?.disconnect();
      if (raf) cancelAnimationFrame(raf);
      canvas.remove();
      hintEl.remove();
      log.debug('zone', 'freehand drawing stopped');
    },
    clear() {
      pts = [];
      schedule();
      hintEl.hidden = false;
    },
  };
}
