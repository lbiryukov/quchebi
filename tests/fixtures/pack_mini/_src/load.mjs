// Test helper: load tests/fixtures/pack_mini as a Pack object (same shape as store/pack.js).
//   import { loadMiniPack, MINI_DIR } from '../fixtures/pack_mini/_src/load.mjs';
//   const pack = loadMiniPack();                 // everything loaded, pack.ready resolved
//   const pack = loadMiniPack({ late: false });  // only manifest/core/eponyms; call pack.finish()
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

export const MINI_DIR = join(dirname(fileURLToPath(import.meta.url)), '..');

export function readMini(name) {
  return JSON.parse(readFileSync(join(MINI_DIR, name), 'utf8'));
}

export function readMiniBytes(name) {
  return readFileSync(join(MINI_DIR, name));
}

export function loadMiniPack({ late = true } = {}) {
  const pack = { manifest: readMini('manifest.json'), core: readMini('core.json'), eponyms: readMini('eponyms.json'),
    geom: null, houses: null, areas: null };
  pack.version = pack.manifest.version;
  let resolve;
  pack.ready = new Promise((r) => { resolve = r; });
  pack.finish = () => {
    pack.geom = readMini('geom.json');
    pack.houses = readMini('houses.json');
    pack.areas = readMini('areas.json');
    resolve(pack);
    return pack.ready;
  };
  if (late) pack.finish();
  return pack;
}
