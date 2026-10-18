import { test, expect } from '@playwright/test';

const PAGES = ['/ar/', '/en/'];

test.describe('every page head', () => {
  for (const p of PAGES) {
    test(`${p} has lang, dir, title, description, canonical, reciprocal hreflang, og`, async ({ page }) => {
      await page.goto(p);
      const lang = p.split('/')[1];
      await expect(page.locator('html')).toHaveAttribute('lang', lang);
      await expect(page.locator('html')).toHaveAttribute('dir', lang === 'ar' ? 'rtl' : 'ltr');
      expect((await page.title()).length).toBeGreaterThan(10);
      const desc = await page.locator('meta[name=description]').getAttribute('content');
      expect(desc && desc.length).toBeGreaterThan(50);
      await expect(page.locator('link[rel=canonical]')).toHaveAttribute('href', `https://actionaura.me${p}`);
      const other = lang === 'ar' ? 'en' : 'ar';
      await expect(page.locator(`link[hreflang=${other}]`)).toHaveAttribute('href', `https://actionaura.me/${other}/`);
      await expect(page.locator('link[hreflang=x-default]')).toHaveAttribute('href', 'https://actionaura.me/en/');
      await expect(page.locator('meta[property="og:image"]')).toHaveCount(1);
    });
  }

  test('titles and descriptions are unique across pages', async ({ page }) => {
    const seen = new Set<string>();
    for (const p of PAGES) {
      await page.goto(p);
      const key = (await page.title()) + '|' + (await page.locator('meta[name=description]').getAttribute('content'));
      expect(seen.has(key), `${p} duplicates another page`).toBe(false);
      seen.add(key);
    }
  });

  test('every <img> has an alt attribute', async ({ page }) => {
    for (const p of PAGES) {
      await page.goto(p);
      const missing = await page.locator('img:not([alt])').count();
      expect(missing, p).toBe(0);
    }
  });

  test('chooser sends an arabic device to /ar/ and ignores junk storage', async ({ browser }) => {
    const ctx = await browser.newContext({ locale: 'ar-JO' });
    const page = await ctx.newPage();
    await page.addInitScript(() => { try { localStorage.setItem('aa-lang', 'fr'); } catch {} });
    await page.goto('/');
    await page.waitForURL('**/ar/');
    await ctx.close();
  });

  test('page has the WhatsApp link with the right number in both languages', async ({ page }) => {
    for (const p of PAGES) {
      await page.goto(p);
      const href = await page.locator('a[href^="https://wa.me/962780069485"]').first().getAttribute('href');
      expect(href).toContain('wa.me/962780069485');
    }
  });
});
