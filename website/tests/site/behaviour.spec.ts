import { test, expect, type Page } from '@playwright/test';

test.use({
  viewport: { width: 1440, height: 900 },
  launchOptions: { args: ['--use-gl=swiftshader', '--enable-webgl', '--ignore-gpu-blocklist'] },
});

const jx = (p: Page): Promise<number> =>
  p.evaluate(() => parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--jx')));

const scrollToSection = (p: Page, id: string, dy = 0): Promise<void> =>
  p.evaluate(
    ({ id, dy }) => {
      const el = document.getElementById(id)!;
      const r = el.getBoundingClientRect();
      const top = window.scrollY + r.top + r.height / 2 - window.innerHeight / 2 + dy;
      window.scrollTo({ top, behavior: 'instant' as ScrollBehavior });
    },
    { id, dy },
  );

test('deep load paints the right world', async ({ page }) => {
  await page.goto('/en/#clinic');
  await page.waitForSelector('.jewel.is-live', { timeout: 20_000 });
  await expect.poll(() => page.locator('.jewel').getAttribute('data-scene-mix'), { timeout: 10_000 }).toBe('clinic');
});

test('reduced motion snaps the jewel to a station', async ({ browser }) => {
  const ctx = await browser.newContext({ reducedMotion: 'reduce', viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  await page.goto('/en/');
  await scrollToSection(page, 'retail');
  await page.waitForTimeout(500);
  const a = await jx(page);
  await page.evaluate(() => window.scrollBy({ top: 80, behavior: 'instant' as ScrollBehavior }));
  await page.waitForTimeout(500);
  const b = await jx(page);
  expect(b).toBe(a);
  expect(a).toBeLessThan(0.5);
  await ctx.close();
});

test('sitemap has no duplicate hreflang', async ({ request }) => {
  const res = await request.get('/sitemap-0.xml');
  const xml = await res.text();
  const blocks = xml.match(/<url>[\s\S]*?<\/url>/g) ?? [];
  expect(blocks.length).toBeGreaterThan(0);
  for (const block of blocks) {
    const langs = [...block.matchAll(/hreflang="([^"]+)"/g)].map((m) => m[1]);
    expect(new Set(langs).size, `duplicate hreflang in: ${block}`).toBe(langs.length);
  }
});

test('skip link becomes visible on focus', async ({ page }) => {
  await page.goto('/en/');
  await page.keyboard.press('Tab');
  const skip = page.locator('body a.visually-hidden').first();
  await expect(skip).toBeFocused();
  const box = await skip.boundingBox();
  expect(box).not.toBeNull();
  expect(box!.width).toBeGreaterThan(20);
  expect(box!.height).toBeGreaterThan(10);
});

test('language switch is remembered by the chooser', async ({ browser }) => {
  const ctx = await browser.newContext({ locale: 'ar-JO', viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  await page.goto('/ar/');
  await page.locator('a.nav-lang:visible').first().click();
  await page.waitForURL('**/en/');
  await page.goto('/');
  await page.waitForURL(/\/en\/$/);
  expect(page.url()).toMatch(/\/en\/$/);
  await ctx.close();
});

test('stations still follow scroll when the 3D chunk fails', async ({ page }) => {
  await page.route('**/scene*.js', (r) => r.abort());
  await page.goto('/en/');
  await page.waitForLoadState('load');
  await page.waitForTimeout(1000);
  expect(await jx(page)).toBeGreaterThan(0.5);
  await scrollToSection(page, 'retail');
  await page.waitForTimeout(500);
  expect(await jx(page)).toBeLessThan(0.5);
  await expect(page.locator('.jewel')).not.toHaveClass(/is-live/);
});
