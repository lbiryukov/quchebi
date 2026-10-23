// E2E: boot, skeleton search (ka/ru/lat), DSL query + error suggestion, freehand zone drawing
// with synthetic pointer events -> masks -> persistence across reload, offline reload.
// Data: tests/fixtures/pack_mini (copied to public/data by scripts/dev-fixture.mjs when no real
// pack is present) or the real pack; the assertions use names present in both.
import { test, expect } from '@playwright/test';

async function open(page, screen = 'search') {
  await page.goto(`./#/${screen}`);
  await expect(page.locator('#boot')).toBeHidden({ timeout: 45_000 });
}

async function search(page, text) {
  const q = page.locator('#q');
  await q.fill(text);
  await q.press('Enter');
}

function resultRow(page, text) {
  return page.locator('#results .res').filter({ hasText: text }).first();
}

test.describe('Quchebi', () => {
  test('boots and shows the search screen', async ({ page }) => {
    const errors = [];
    page.on('pageerror', (e) => errors.push(String(e)));
    await open(page, 'search');
    await expect(page.locator('.tabbar [data-nav="search"]')).toHaveAttribute('aria-current', 'page');
    await expect(page.locator('#q')).toBeVisible();
    await expect(page.locator('#q')).toHaveAttribute('enterkeyhint', 'search');
    await expect(page.locator('#q')).toHaveAttribute('spellcheck', 'false');
    await expect(page.locator('#zone-plate')).toBeVisible();
    await expect(page.locator('#net-status')).toContainText('пакет');
    expect(errors, errors.join('\n')).toEqual([]);
  });

  test('skeleton search finds the same street in ka, ru and lat', async ({ page }) => {
    await open(page, 'search');
    for (const q of ['ასათიან', 'асатиани', 'asatiani', 'ASATIANI']) {
      await search(page, q);
      await expect(resultRow(page, 'ლადო ასათიანის ქუჩა'), `query ${q}`).toBeVisible();
    }
    // cross-script: Latin skeleton of a Georgian name
    await search(page, 'tabidze');
    await expect(resultRow(page, 'გალაკტიონ ტაბიძის ქუჩა')).toBeVisible();
    // tap a result -> object card with copyable names
    await resultRow(page, 'გალაკტიონ ტაბიძის ქუჩა').click();
    await expect(page.locator('.card-sheet .name-btn.main')).toContainText('გალაკტიონ ტაბიძის ქუჩა');
    await expect(page.locator('.card-sheet')).toContainText('Пересечения');
  });

  test('DSL query, explanation and error with a tap-to-fix suggestion', async ({ page }) => {
    await open(page, 'search');
    await search(page, 'тип:переулок ладо асатиан');
    await expect(page.locator('[data-testid="explain"]')).toBeVisible();
    await expect(resultRow(page, 'ლადო ასათიანის I შესახვევი')).toBeVisible();
    await expect(resultRow(page, 'ლადო ასათიანის II შესახვევი')).toBeVisible();
    const n = Number((await page.locator('#result-count').textContent()).replace(/\D/g, ''));
    expect(n).toBeGreaterThanOrEqual(2);
    // chip toggles the type list in the DSL string (string is the source of truth)
    await page.locator('.chip', { hasText: 'Тупик' }).click();
    await expect(page.locator('#q')).toHaveValue(/тип:переулок,тупик ладо асатиан/);
    await expect(resultRow(page, 'ლადო ასათიანის I ჩიხი').or(resultRow(page, 'ლადო ასათიანის I შესახვევი')).first()).toBeVisible();

    await search(page, 'рйон:мтацминда');
    const err = page.locator('[data-testid="query-error"]');
    await expect(err).toBeVisible();
    const fix = err.locator('button.fix').first();
    await expect(fix).toBeVisible();
    await fix.click();
    await expect(err).toBeHidden();
    await expect(page.locator('#q')).toHaveValue(/^район[:=]мтацминда/);
    await expect(page.locator('[data-testid="explain"]')).toBeVisible();
  });

  test('draw a zone with pointer events, masks apply, reload keeps it', async ({ page }) => {
    await open(page, 'zone');
    await expect(page.locator('[data-testid="zone-draw"]')).toBeVisible();
    // map must exist (WebGL); otherwise drawing cannot unproject
    await page.waitForFunction(() => document.querySelector('.maplibregl-canvas') || document.querySelector('.map-unavailable'), null, { timeout: 30_000 });
    test.skip(await page.locator('.map-unavailable').count() > 0, 'WebGL unavailable in this browser');
    await page.locator('[data-testid="zone-draw"]').click();
    const canvas = page.locator('[data-testid="draw-canvas"]');
    await expect(canvas).toBeVisible();
    const box = await canvas.boundingBox();
    await page.evaluate(({ w, h }) => {
      const c = document.querySelector('[data-testid="draw-canvas"]');
      const r = c.getBoundingClientRect();
      const cx = r.left + w / 2;
      const cy = r.top + h / 2;
      const rad = Math.min(w, h) * 0.38;
      const ev = (type, a) => new PointerEvent(type, {
        bubbles: true, cancelable: true, composed: true, pointerId: 7, pointerType: 'touch', isPrimary: true,
        clientX: cx + rad * Math.cos(a), clientY: cy + rad * Math.sin(a), buttons: type === 'pointerup' ? 0 : 1,
      });
      c.dispatchEvent(ev('pointerdown', 0));
      for (let k = 1; k <= 72; k++) c.dispatchEvent(ev('pointermove', (k / 72) * Math.PI * 2 * 0.97));
      c.dispatchEvent(ev('pointerup', Math.PI * 2 * 0.97));
    }, { w: box.width, h: box.height });

    const preview = page.locator('[data-testid="zone-preview"]');
    await expect(preview).toContainText('В зоне');
    await expect(page.locator('[data-testid="zone-preview-streets"]')).not.toHaveText(/^0 /);
    await page.locator('[data-testid="zone-save"]').click();
    await expect(page.locator('#zone-plate-text')).toContainText('Зона ·');
    await expect(page.locator('[data-testid="zone-summary"]')).toContainText('улиц');

    // zone applies to queries: zone:off never returns fewer results
    await page.locator('.tabbar [data-nav="search"]').click();
    await search(page, 'тип:улица*');
    await expect(page.locator('#result-count')).toBeVisible();
    const inZone = Number((await page.locator('#result-count').textContent()).replace(/\D/g, ''));
    await search(page, 'тип:улица* зона:выкл');
    await expect(page.locator('#result-count')).toBeVisible();
    const all = Number((await page.locator('#result-count').textContent()).replace(/\D/g, ''));
    expect(all).toBeGreaterThanOrEqual(inZone);
    expect(inZone).toBeGreaterThan(0);

    await page.reload();
    await expect(page.locator('#boot')).toBeHidden({ timeout: 45_000 });
    await expect(page.locator('#zone-plate-text')).toContainText('Зона ·');
    await expect(page.locator('#zone-plate-text')).toContainText(/\d{2}\/\d{2}\/\d{4}/);
  });

  test('zone from a teammate code, share code, temporary disable', async ({ page }) => {
    await open(page, 'zone');
    await page.locator('[data-testid="zone-paste"]').click();
    await page.locator('.sheet textarea').fill('Зона Quchebi: Z1.oxl}FoykpG?ooB_jA??noB');
    await page.locator('.sheet .btn.primary').click();
    await expect(page.locator('[data-testid="zone-preview"]')).toContainText('В зоне');
    await page.locator('[data-testid="zone-save"]').click();
    await expect(page.locator('#zone-plate-text')).toContainText('Зона ·');
    // temporary disable: plate switches to "выкл."
    await page.locator('[data-testid="zone-enabled"]').uncheck({ force: true });
    await expect(page.locator('#zone-plate-text')).toContainText('выкл');
    await page.locator('[data-testid="zone-enabled"]').check({ force: true });
    await expect(page.locator('#zone-plate-text')).toContainText('Зона ·');
  });

  test('readiness and log screens', async ({ page }) => {
    await open(page, 'ready');
    const checks = page.locator('[data-testid="checks"]');
    await expect(checks).toContainText('Пакет данных');
    await expect(checks).toContainText(/Версия \S+, собран \d{2}\/\d{2}\/\d{4}/);
    await expect(checks).toContainText('Хранилище');
    await expect(checks).toContainText('Тест в авиарежиме');
    await page.getByRole('button', { name: 'Журнал' }).click();
    await expect(page.locator('[data-testid="log-list"] .log-row').first()).toBeVisible();
    await page.getByRole('button', { name: 'Назад' }).click();
    await page.getByRole('button', { name: 'О приложении' }).click();
    await expect(page.locator('#screen-about')).toContainText('OpenStreetMap');
    await expect(page.locator('#screen-about')).toContainText('Wikidata');
  });

  test('works offline after the first visit', async ({ page, context, browserName }) => {
    test.skip(browserName !== 'chromium', 'Service worker + setOffline is reliable only in Chromium here');
    await open(page, 'search');
    await page.waitForFunction(() => navigator.serviceWorker && navigator.serviceWorker.controller, null, { timeout: 45_000 });
    await page.waitForFunction(async () => (await caches.keys()).some((k) => k.startsWith('pack-v')), null, { timeout: 45_000 });
    await page.waitForFunction(async () => (await caches.keys()).some((k) => k.includes('precache')), null, { timeout: 45_000 });
    await context.setOffline(true);
    try {
      await page.reload();
      await expect(page.locator('#boot')).toBeHidden({ timeout: 45_000 });
      await expect(page.locator('#net-status')).toContainText('офлайн');
      await search(page, 'асатиани');
      await expect(resultRow(page, 'ლადო ასათიანის ქუჩა')).toBeVisible();
    } finally {
      await context.setOffline(false);
    }
  });
});
