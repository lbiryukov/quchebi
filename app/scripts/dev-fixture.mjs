// Copy the small test pack (tests/fixtures/pack_mini/*) into app/public/data/ for development
// and e2e runs — ONLY if app/public/data/manifest.json does not exist (never overwrite the real
// pack written by the export stage).
// Usage: cd app && node scripts/dev-fixture.mjs [--force-check]
import { existsSync, mkdirSync, readdirSync, copyFileSync, statSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const appDir = join(here, '..');
const src = join(appDir, '..', 'tests', 'fixtures', 'pack_mini');
const dst = join(appDir, 'public', 'data');

function log(msg) {
  console.log(`[dev-fixture] ${new Date().toISOString()} ${msg}`);
}

if (existsSync(join(dst, 'manifest.json'))) {
  log(`app/public/data/manifest.json exists — nothing copied`);
  process.exit(0);
}
if (!existsSync(join(src, 'manifest.json'))) {
  log(`fixture not found: ${src}`);
  process.exit(1);
}
mkdirSync(dst, { recursive: true });
let n = 0;
for (const name of readdirSync(src)) {
  const p = join(src, name);
  if (!statSync(p).isFile() || !name.endsWith('.json')) continue;
  copyFileSync(p, join(dst, name));
  n++;
  log(`copied ${name}`);
}
log(`done: ${n} files -> ${dst}`);
