// E2E: the district picker at the first start, the city chip in the top bar, and the Moscow
// (Хамовники) pack. The Хамовники test is skipped while app/public/data/khamovniki/manifest.json
// does not exist (the pack is produced by the data pipeline).
import { test, expect } from '@playwright/test';
import { existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const KHAMOVNIKI_MANIFEST = fileURLToPath(new URL('../public/data/khamovniki/manifest.json', import.meta.url));

async function search(page, text) {
  const q = page.locator('#q');
  await q.fill(text);
  await q.press('Enter');
}

function resultRow(page, text) {
  return page.locator('#results .res').filter({ hasText: text }).first();
}

test.describe('Districts', () => {
  test('fresh start: picker -> Tbilisi -> search -> back to the picker from the top bar', async ({ page }) => {
    const errors = [];
    page.on('pageerror', (e) => errors.push(String(e)));
    await page.goto('./');
    const picker = page.locator('#city-picker');
    await expect(picker).toBeVisible({ timeout: 30_000 });
    await expect(picker).toContainText('Выберите район');
    await expect(page.locator('[data-testid="city-tbilisi"]')).toContainText('Тбилиси');
    await expect(page.locator('[data-testid="city-khamovniki"]')).toContainText('Москва. Хамовники');
    // tap targets of at least 44 px
    const box = await page.locator('[data-testid="city-tbilisi"]').boundingBox();
    expect(box.height).toBeGreaterThanOrEqual(44);
    await expect(page.locator('[data-testid="city-status-tbilisi"]')).not.toHaveText('Проверяю…');

    await page.locator('[data-testid="city-tbilisi"]').click();
    await expect(picker).toBeHidden();
    await expect(page.locator('#boot')).toBeHidden({ timeout: 45_000 });
    await expect(page.locator('#city-chip')).toHaveText('Тбилиси');
    expect(await page.evaluate(() => localStorage.getItem('quchebi.city'))).toBe('tbilisi');

    await page.locator('.tabbar [data-nav="search"]').click();
    await search(page, 'асатиани');
    await expect(resultRow(page, 'ლადო ასათიანის ქუჩა')).toBeVisible();

    // the choice is remembered: a reload goes straight to Tbilisi
    await page.reload();
    await expect(page.locator('#boot')).toBeHidden({ timeout: 45_000 });
    await expect(picker).toBeHidden();
    await expect(page.locator('#city-chip')).toHaveText('Тбилиси');

    // one tap on the city chip returns to the picker; "Назад" keeps the current district
    await page.locator('#city-chip').click();
    await expect(picker).toBeVisible();
    await expect(page.locator('[data-testid="city-tbilisi"]')).toHaveAttribute('aria-current', 'true');
    await expect(page.locator('[data-testid="city-status-tbilisi"]')).toContainText('Скачан для офлайна');
    await page.locator('[data-testid="city-cancel"]').click();
    await expect(picker).toBeHidden();
    await expect(page.locator('#city-chip')).toHaveText('Тбилиси');

    // the readiness screen has a row with the district and a switch button
    await page.locator('.tabbar [data-nav="ready"]').click();
    await expect(page.locator('[data-testid="checks"]')).toContainText('Район');
    await page.locator('[data-testid="ready-city"]').click();
    await expect(picker).toBeVisible();
    expect(errors, errors.join('\n')).toEqual([]);
  });

  test('?city= selects the district directly and is remembered', async ({ page }) => {
    await page.goto('./?city=tbilisi#/search');
    await expect(page.locator('#boot')).toBeHidden({ timeout: 45_000 });
    await expect(page.locator('#city-picker')).toBeHidden();
    await expect(page.locator('#city-chip')).toHaveText('Тбилиси');
    await expect(page).not.toHaveURL(/city=/);
    expect(await page.evaluate(() => localStorage.getItem('quchebi.city'))).toBe('tbilisi');
    // Tbilisi keeps the legacy storage names
    await page.waitForFunction(async () => (await caches.keys()).some((k) => k.startsWith('pack-v')), null, { timeout: 45_000 });
  });

  test('Москва. Хамовники: search, card and map', async ({ page }) => {
    test.skip(!existsSync(KHAMOVNIKI_MANIFEST), 'app/public/data/khamovniki/manifest.json is not built yet');
    const errors = [];
    page.on('pageerror', (e) => errors.push(String(e)));
    await page.goto('./');
    await expect(page.locator('#city-picker')).toBeVisible({ timeout: 30_000 });
    await page.locator('[data-testid="city-khamovniki"]').click();
    await expect(page.locator('#boot')).toBeHidden({ timeout: 60_000 });
    await expect(page.locator('#city-chip')).toHaveText('Москва. Хамовники');
    await page.locator('.tabbar [data-nav="search"]').click();
    await search(page, 'толстого');
    const row = resultRow(page, 'улица Льва Толстого');
    await expect(row).toBeVisible();
    await row.click();
    await expect(page.locator('.card-sheet .name-btn.main')).toContainText('улица Льва Толстого');
    // "На карте" closes the card and opens the map screen on the street
    await page.locator('.card-sheet').getByRole('button', { name: 'На карте' }).click();
    await expect(page.locator('#screen-map')).toHaveClass(/active/);
    await page.waitForFunction(() => document.querySelector('.maplibregl-canvas') || document.querySelector('.map-unavailable'), null, { timeout: 30_000 });
    // Moscow keeps its own storage names
    expect(await page.evaluate(async () => (await caches.keys()).some((k) => k.startsWith('pack-khamovniki-v')))).toBe(true);
    expect(errors, errors.join('\n')).toEqual([]);
  });
});
