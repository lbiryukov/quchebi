// Check that a PMTiles archive opens with the client library (pmtiles 4.5.0 from app/node_modules).
// Usage: node pipeline/tiles/check_js.mjs <file.pmtiles> [lon,lat,z]   (default 44.80,41.69,14)
// Prints one JSON line {ok, header, metadata, tile, fileSource}; exit code 1 on failure.
import { open, readFile } from 'node:fs/promises';
import { basename } from 'node:path';
import { PMTiles, FileSource } from '../../app/node_modules/pmtiles/dist/esm/index.js';

const [file, at = '44.80,41.69,14'] = process.argv.slice(2);
if (!file) {
  console.error('usage: node check_js.mjs <file.pmtiles> [lon,lat,z]');
  process.exit(2);
}
const [lon, lat, z] = at.split(',').map(Number);

// Range source over a local file, same interface as the library's FetchSource/FileSource.
class NodeFileSource {
  constructor(path) { this.path = path; this.reads = 0; }
  getKey() { return this.path; }
  async getBytes(offset, length) {
    const fh = await open(this.path, 'r');
    try {
      const buf = Buffer.alloc(length);
      const { bytesRead } = await fh.read(buf, 0, length, offset);
      this.reads++;
      const out = buf.subarray(0, bytesRead);
      return { data: out.buffer.slice(out.byteOffset, out.byteOffset + out.byteLength) };
    } finally {
      await fh.close();
    }
  }
}

const lon2x = (lo, zz) => Math.floor((lo + 180) / 360 * 2 ** zz);
const lat2y = (la, zz) => {
  const r = la * Math.PI / 180;
  return Math.floor((1 - Math.log(Math.tan(r) + 1 / Math.cos(r)) / Math.PI) / 2 * 2 ** zz);
};

// Minimal MVT reader: layer names and feature counts (Tile.layers = 3, Layer.name = 1,
// Layer.features = 2).
function varint(b, p) {
  let r = 0, s = 1, x;
  do { x = b[p.i++]; r += (x & 0x7f) * s; s *= 128; } while (x & 0x80);
  return r;
}
function fields(b, start, end, cb) {
  const p = { i: start };
  while (p.i < end) {
    const key = varint(b, p), f = Math.floor(key / 8), t = key & 7;
    if (t === 0) cb(f, t, varint(b, p));
    else if (t === 2) { const n = varint(b, p); cb(f, t, [p.i, p.i + n]); p.i += n; }
    else if (t === 5) p.i += 4;
    else if (t === 1) p.i += 8;
    else throw new Error('unsupported wire type ' + t);
  }
  if (p.i !== end) throw new Error('field overrun');
}
function mvtLayers(u8) {
  const layers = {};
  fields(u8, 0, u8.length, (f, t, v) => {
    if (f !== 3 || t !== 2) return;
    let name = '', features = 0;
    fields(u8, v[0], v[1], (f2, t2, v2) => {
      if (f2 === 1 && t2 === 2) name = new TextDecoder().decode(u8.subarray(v2[0], v2[1]));
      if (f2 === 2 && t2 === 2) features++;
    });
    layers[name] = features;
  });
  return layers;
}

const out = { ok: false, file };
try {
  const src = new NodeFileSource(file);
  const p = new PMTiles(src);
  const h = await p.getHeader();
  out.header = {
    specVersion: h.specVersion, tileType: h.tileType, tileCompression: h.tileCompression,
    minZoom: h.minZoom, maxZoom: h.maxZoom, bounds: [h.minLon, h.minLat, h.maxLon, h.maxLat],
    center: [h.centerLon, h.centerLat, h.centerZoom], addressedTiles: h.numAddressedTiles,
    tileEntries: h.numTileEntries, tileContents: h.numTileContents, clustered: h.clustered,
  };
  const meta = await p.getMetadata();
  out.metadata = {
    vector_layers: (meta.vector_layers || []).map((l) => l.id),
    attribution: meta.attribution, version: meta.version, quchebi: meta.quchebi,
  };
  const tj = await p.getTileJson('pmtiles://check');
  if (!tj.vector_layers || !tj.vector_layers.length) throw new Error('tilejson without vector_layers');

  const x = lon2x(lon, z), y = lat2y(lat, z);
  const t = await p.getZxy(z, x, y);
  if (!t) throw new Error(`tile ${z}/${x}/${y} not found`);
  const u8 = new Uint8Array(t.data);
  const layers = mvtLayers(u8);
  if (!Object.keys(layers).length) throw new Error('tile has no MVT layers');
  out.tile = { z, x, y, bytes: u8.length, layers };

  // The browser-style FileSource (Blob slices) must give the same tile.
  const buf = await readFile(file);
  const fp = new PMTiles(new FileSource(new File([buf], basename(file))));
  const t2 = await fp.getZxy(z, x, y);
  const same = t2 && Buffer.compare(Buffer.from(t2.data), Buffer.from(u8)) === 0;
  if (!same) throw new Error('FileSource returned a different tile');
  out.fileSource = 'same tile';

  // Outside the bounds/zoom range there must be no tile.
  out.outside = (await p.getZxy(h.maxZoom + 1, 0, 0)) === undefined ? 'none' : 'unexpected';
  out.reads = src.reads;
  out.ok = true;
} catch (e) {
  out.error = String(e && e.stack || e);
}
console.log(JSON.stringify(out));
process.exit(out.ok ? 0 : 1);
