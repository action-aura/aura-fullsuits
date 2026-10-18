import { test, expect } from '@playwright/test';

// The Retail page carries exactly two composited illustrations under the features bento, each with alt text and a
// caption that calls it an illustration, in the page's own language and with that language's screen; the Clinic page
// carries none. Spec: job tmp spec-delta-photos.md, 2026-10-18.
const CAPTION = { en: /^Illustration: /, ar: /^صورة توضيحية: / };

for (const lang of ['en', 'ar'] as const) {
  test(`/${lang}/aura-retail/ shows two captioned illustrations inside the features section`, async ({ page }) => {
    await page.goto(`/${lang}/aura-retail/`, { waitUntil: 'domcontentloaded' });
    const pair = page.locator('.features #photos.photo-pair');
    await expect(pair).toHaveCount(1);
    const figures = pair.locator('figure.photo');
    await expect(figures).toHaveCount(2);
    for (let i = 0; i < 2; i++) {
      const img = figures.nth(i).locator('img');
      const alt = await img.getAttribute('alt');
      expect(alt && alt.length, `figure ${i} alt`).toBeGreaterThan(20);
      expect(await img.getAttribute('src'), `figure ${i} src`).toMatch(new RegExp(`retail-(grocery|phone-shop)-${lang}`));
      await expect(figures.nth(i).locator('figcaption')).toHaveText(CAPTION[lang]);
    }
  });

  test(`/${lang}/aura-clinic/ has no photo pair`, async ({ page }) => {
    await page.goto(`/${lang}/aura-clinic/`, { waitUntil: 'domcontentloaded' });
    await expect(page.locator('#photos, .photo-pair, figure.photo')).toHaveCount(0);
  });
}
