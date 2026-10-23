// Terra Draw on MapLibre: "По точкам" (polygon mode: tap the corners) and "Править"
// (select mode: drag vertices, tap a midpoint to insert, delete the active vertex by button).
import { TerraDraw, TerraDrawPolygonMode, TerraDrawSelectMode } from 'terra-draw';
import { TerraDrawMapLibreGLAdapter } from 'terra-draw-maplibre-gl-adapter';
import { log } from '../log/log.js';
import { openRing } from '../map/geomutil.js';

// Terra Draw validates coordinate precision (max 9 decimals); 7 decimals = 1 cm.
const r7 = (x) => Math.round(x * 1e7) / 1e7;
const toLngLat = (ring) => ring.map(([lat, lon]) => [r7(lon), r7(lat)]);
const toLatLon = (coords) => coords.map(([lon, lat]) => [lat, lon]);

function dedupe(coords) {
  const out = [];
  for (const c of coords) {
    const last = out[out.length - 1];
    if (!last || Math.abs(last[0] - c[0]) > 1e-9 || Math.abs(last[1] - c[1]) > 1e-9) out.push(c);
  }
  while (out.length > 1) {
    const a = out[0];
    const b = out[out.length - 1];
    if (Math.abs(a[0] - b[0]) < 1e-9 && Math.abs(a[1] - b[1]) < 1e-9) out.pop();
    else break;
  }
  return out;
}

/**
 * createEditor(view, {color, onChange(polygon), onPolygonFinished(polygon), onVertex(latlon|null)})
 * -> {startPoints(), closePoints(), startEdit(polygon), deleteActiveVertex(), getPolygon(), stop(), active}
 */
export function createEditor(view, { color = '#8a2242', onChange, onPolygonFinished, onVertex } = {}) {
  let draw = null;
  let featureId = null;
  let activeVertex = -1;
  let mode = null;
  let changeTimer = 0;
  const map = view.map;

  const polyStyles = {
    fillColor: color, fillOpacity: 0.15, outlineColor: color, outlineWidth: 3,
    closingPointColor: color, closingPointWidth: 10, closingPointOutlineColor: '#ffffff', closingPointOutlineWidth: 2,
    coordinatePointColor: color, coordinatePointWidth: 7, coordinatePointOutlineColor: '#ffffff', coordinatePointOutlineWidth: 2,
  };
  const selStyles = {
    selectedPolygonColor: color, selectedPolygonFillOpacity: 0.15, selectedPolygonOutlineColor: color, selectedPolygonOutlineWidth: 3,
    selectionPointColor: color, selectionPointWidth: 9, selectionPointOutlineColor: '#ffffff', selectionPointOutlineWidth: 2,
    midPointColor: '#ffffff', midPointOutlineColor: color, midPointWidth: 6, midPointOutlineWidth: 2,
  };

  function ensure() {
    if (draw) return draw;
    draw = new TerraDraw({
      adapter: new TerraDrawMapLibreGLAdapter({ map }),
      modes: [
        new TerraDrawPolygonMode({ pointerDistance: 36, styles: polyStyles }),
        new TerraDrawSelectMode({
          pointerDistance: 36,
          allowManualDeselection: false,
          styles: selStyles,
          flags: {
            polygon: {
              feature: {
                draggable: false,
                coordinates: { midpoints: true, draggable: true, deletable: true },
              },
            },
          },
        }),
      ],
    });
    draw.start();
    draw.on('change', (ids, type) => {
      if (mode !== 'select' || type === 'styling') return;
      clearTimeout(changeTimer);
      changeTimer = setTimeout(() => {
        const p = getPolygon();
        if (p) onChange?.(p);
      }, 120);
    });
    draw.on('finish', (id, ctx) => {
      if (ctx?.mode === 'polygon' || mode === 'polygon') {
        featureId = id;
        const p = getPolygon();
        log.info('zone', 'points polygon finished', { vertices: p?.length });
        if (p) onPolygonFinished?.(p);
      }
    });
    map.getCanvasContainer().addEventListener('pointerdown', onPointerDown, true);
    log.info('zone', 'terra draw started');
    return draw;
  }

  function onPointerDown(e) {
    if (mode !== 'select') return;
    const p = getPolygon();
    if (!p) return;
    const rect = map.getCanvasContainer().getBoundingClientRect();
    const x = e.clientX - rect.left;
    const y = e.clientY - rect.top;
    let best = -1;
    let bestD = 30;
    p.forEach((v, k) => {
      const s = view.project(v);
      const d = Math.hypot(s.x - x, s.y - y);
      if (d < bestD) { bestD = d; best = k; }
    });
    if (best >= 0) {
      activeVertex = best;
      onVertex?.(p[best], best);
    }
  }

  function getPolygon() {
    if (!draw) return null;
    let feats = [];
    try {
      feats = draw.getSnapshot();
    } catch {
      return null;
    }
    let f = featureId ? feats.find((x) => x.id === featureId) : null;
    if (!f) f = feats.find((x) => x.geometry?.type === 'Polygon' && x.properties?.mode === 'polygon');
    if (!f) return null;
    const coords = f.geometry.type === 'Polygon' ? f.geometry.coordinates[0] : f.geometry.coordinates;
    const ring = dedupe(toLatLon(coords));
    return ring.length >= 3 ? ring : null;
  }

  return {
    get active() { return Boolean(draw); },
    get mode() { return mode; },
    startPoints() {
      ensure();
      draw.clear();
      featureId = null;
      activeVertex = -1;
      mode = 'polygon';
      draw.setMode('polygon');
      onVertex?.(null);
    },
    /** Close an unfinished "По точкам" polygon from the button. */
    closePoints() {
      if (!draw) return null;
      const feats = draw.getSnapshot();
      const f = feats.find((x) => x.properties?.mode === 'polygon' && (x.geometry?.type === 'Polygon' || x.geometry?.type === 'LineString'));
      if (!f) return null;
      const coords = f.geometry.type === 'Polygon' ? f.geometry.coordinates[0] : f.geometry.coordinates;
      const ring = dedupe(toLatLon(coords));
      return ring.length >= 3 ? ring : null;
    },
    startEdit(polygon) {
      ensure();
      draw.clear();
      mode = 'select';
      draw.setMode('select');
      const ring = toLngLat(openRing(polygon));
      const res = draw.addFeatures([{
        type: 'Feature',
        properties: { mode: 'polygon' },
        geometry: { type: 'Polygon', coordinates: [[...ring, ring[0]]] },
      }]);
      const ok = res?.[0];
      if (!ok || ok.valid === false) {
        log.warn('zone', 'terra draw rejected polygon', { reason: ok?.reason, vertices: ring.length });
        return false;
      }
      featureId = ok.id;
      try {
        draw.selectFeature(featureId);
      } catch (err) {
        log.warn('zone', 'selectFeature failed', { err: String(err) });
      }
      activeVertex = -1;
      onVertex?.(null);
      return true;
    },
    activeVertex: () => activeVertex,
    deleteActiveVertex() {
      const p = getPolygon();
      if (!p || activeVertex < 0 || activeVertex >= p.length) return { error: 'Сначала коснитесь вершины' };
      if (p.length <= 3) return { error: 'У зоны должно остаться не меньше трёх вершин' };
      const next = p.filter((_, k) => k !== activeVertex);
      const ring = toLngLat(next);
      try {
        draw.updateFeatureGeometry(featureId, { type: 'Polygon', coordinates: [[...ring, ring[0]]] });
        draw.deselectFeature(featureId);
        draw.selectFeature(featureId);
      } catch (err) {
        log.warn('zone', 'vertex delete via update failed, re-adding', { err: String(err) });
        this.startEdit(next);
      }
      activeVertex = -1;
      onVertex?.(null);
      onChange?.(next);
      return { polygon: next };
    },
    getPolygon,
    stop() {
      clearTimeout(changeTimer);
      if (!draw) return;
      try {
        map.getCanvasContainer().removeEventListener('pointerdown', onPointerDown, true);
        draw.stop();
      } catch (err) {
        log.warn('zone', 'terra draw stop failed', { err: String(err) });
      }
      draw = null;
      featureId = null;
      mode = null;
      onVertex?.(null);
      log.debug('zone', 'terra draw stopped');
    },
  };
}
