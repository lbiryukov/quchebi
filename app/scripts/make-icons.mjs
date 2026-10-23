// Render the app icons (street-plate design) from SVG to PNG with Playwright's Chromium.
// Usage: cd app && node scripts/make-icons.mjs
// Output: public/icons/{icon-192.png, icon-512.png, maskable-512.png, apple-touch-icon.png, favicon-32.png}
import { chromium } from '@playwright/test';
import { readFileSync, mkdirSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const appDir = join(here, '..');
const outDir = join(appDir, 'public', 'icons');
const fontFile = join(appDir, 'node_modules', '@fontsource', 'noto-sans-georgian', 'files', 'noto-sans-georgian-georgian-700-normal.woff2');

const ENAMEL = '#1D3B6E';
const SAPERAVI = '#8A2242';

function log(msg) {
  console.log(`[make-icons] ${new Date().toISOString()} ${msg}`);
}

/** Plate icon. maskable: full-bleed background, content inside the 80% safe circle. */
function svg({ size, maskable }) {
  const s = 512;
  const pad = maskable ? 92 : 20;
  const plate = s - 2 * pad;
  const rx = maskable ? 40 : 84;
  const rim = maskable ? 10 : 14;
  const inset = maskable ? 18 : 26;
  const fontSize = maskable ? 205 : 268;
  const bg = maskable ? `<rect width="${s}" height="${s}" fill="${ENAMEL}"/>` : '';
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" viewBox="0 0 ${s} ${s}">
  ${bg}
  <rect x="${pad}" y="${pad}" width="${plate}" height="${plate}" rx="${rx}" fill="${ENAMEL}"/>
  <rect x="${pad + inset}" y="${pad + inset}" width="${plate - 2 * inset}" height="${plate - 2 * inset}" rx="${rx - inset / 2}"
        fill="none" stroke="#FFFFFF" stroke-width="${rim}"/>
  <text x="256" y="${maskable ? 292 : 300}" text-anchor="middle" font-family="QIcon" font-weight="700"
        font-size="${fontSize}" fill="#FFFFFF">ქ</text>
  <rect x="${pad + inset + 40}" y="${s - pad - inset - (maskable ? 50 : 74)}" width="${plate - 2 * inset - 80}" height="${maskable ? 22 : 30}"
        rx="${maskable ? 11 : 15}" fill="${SAPERAVI}"/>
</svg>`;
}

const targets = [
  { file: 'icon-512.png', size: 512, maskable: false },
  { file: 'icon-192.png', size: 192, maskable: false },
  { file: 'maskable-512.png', size: 512, maskable: true },
  { file: 'maskable-192.png', size: 192, maskable: true },
  { file: 'apple-touch-icon.png', size: 180, maskable: true },
  { file: 'favicon-32.png', size: 32, maskable: false },
];

async function main() {
  mkdirSync(outDir, { recursive: true });
  const font = readFileSync(fontFile).toString('base64');
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({ deviceScaleFactor: 1 });
    for (const t of targets) {
      await page.setViewportSize({ width: t.size, height: t.size });
      await page.setContent(`<!doctype html><html><head><style>
        @font-face { font-family: QIcon; font-weight: 700; src: url(data:font/woff2;base64,${font}) format('woff2'); }
        html, body { margin: 0; padding: 0; background: transparent; }
        svg { display: block; }
      </style></head><body>${svg(t)}</body></html>`);
      await page.evaluate(() => document.fonts.ready);
      const buf = await page.locator('svg').screenshot({ omitBackground: !t.maskable, type: 'png' });
      writeFileSync(join(outDir, t.file), buf);
      log(`wrote ${t.file} (${buf.length} bytes)`);
    }
    writeFileSync(join(here, 'icon-source.svg'), svg({ size: 512, maskable: false }).replace('font-family="QIcon"', 'font-family="Noto Sans Georgian, Sylfaen, sans-serif"'));
  } finally {
    await browser.close();
  }
}

main().catch((err) => {
  console.error('[make-icons] failed:', err);
  process.exit(1);
});
