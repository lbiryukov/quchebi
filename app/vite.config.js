import { defineConfig } from 'vite';
import { VitePWA } from 'vite-plugin-pwa';
import { readFileSync } from 'node:fs';

const pkg = JSON.parse(readFileSync(new URL('./package.json', import.meta.url), 'utf8'));

// base './' keeps every URL relative, so the build works on GitHub Pages under /quchebi/
// and from any other sub-path. Data (public/data) and the basemap (public/tiles) are copied
// to dist/ but NOT precached: store/pack.js downloads them into Cache Storage on demand.
export default defineConfig({
  base: './',
  define: {
    __APP_VERSION__: JSON.stringify(pkg.version),
    __BUILD_TIME__: JSON.stringify(new Date().toISOString()),
  },
  build: {
    target: ['es2020', 'safari15'],
    chunkSizeWarningLimit: 1200,
    sourcemap: false,
    // No manualChunks: MapLibre (map/map.js) and Terra Draw (zone/edit.js) are dynamic imports,
    // so they become separate chunks and are not loaded before the map is opened.
  },
  server: { port: 5173, strictPort: false },
  preview: { port: 4173, strictPort: true },
  plugins: [
    VitePWA({
      strategies: 'injectManifest',
      srcDir: 'src',
      filename: 'sw.js',
      registerType: 'prompt',
      injectRegister: false,
      manifest: false, // public/manifest.webmanifest is the source of truth
      includeAssets: [],
      injectManifest: {
        globPatterns: ['**/*.{js,css,html,woff2,png,svg,webmanifest}'],
        globIgnores: ['data/**', 'tiles/**', '**/*.pmtiles', 'sw.js', 'workbox-*.js'],
        maximumFileSizeToCacheInBytes: 6 * 1024 * 1024,
      },
      devOptions: { enabled: false, type: 'module' },
    }),
  ],
});
