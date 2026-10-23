import { test, expect, type Page } from '@playwright/test';

// The closing home sections (directions.md 8.6 and 3 items 7-9) for both languages: the setup checklist, the product
// cards with their taped notes, and the close with the big phone number. Expected strings come from the spec, not from
// the page's own dictionary, so a copy change cannot make these pass or fail by accident (the WhatsApp texts are
// compared against the exact Arabic and English messages the owner approved).

const LANGS = ['ar', 'en'] as const;
const WA = 'https://wa.me/962780069485?text=';
const MSG = {
  ar: { clinic: 'مرحبًا، خبّروني لما تفتح أورا كلينك.', gym: 'مرحبًا، خبّروني لما تفتح أورا جيم.' },
  en: { clinic: 'Hello, please tell me when Aura Clinic opens.', gym: 'Hello, please tell me when Aura Gym opens.' },
};

async function open(page: Page, lang: string, width = 1440, height = 900) {
  await page.setViewportSize({ width, height });
  await page.goto(`/${lang}/`);
  await page.waitForLoadState('load');
}

const done = (p: Page) => p.locator('[data-su-row].done');

for (const lang of LANGS) {
  test.describe(`home rest ${lang}`, () => {
    test('checklist: nothing ticked before it is in view, then 5 of 5 and 100% after scrolling to it', async ({ page }) => {
      await open(page, lang);
      await expect(done(page)).toHaveCount(0);
      await expect(page.locator('[data-su-pct]')).toHaveText('0%');
      await page.locator('#setup').scrollIntoViewIfNeeded();
      await expect(done(page)).toHaveCount(5, { timeout: 8000 });
      await expect(page.locator('[data-su-pct]')).toHaveText('100%');
      expect(await page.locator('[data-su-fill]').evaluate((e) => (e as HTMLElement).style.getPropertyValue('--p'))).toBe('1');
      await expect(page.locator('[data-su-svg]')).toHaveClass(/on-5/);
      // the copper line really is full length once done (a transform, measured from the rendered boxes)
      await expect.poll(async () => page.evaluate(() => {
        const f = document.querySelector('[data-su-fill]')!.getBoundingClientRect(), t = document.querySelector('.su-track')!.getBoundingClientRect();
        return Math.round((f.width / t.width) * 100);
      })).toBe(100);
    });

    test('checklist ticks one row at a time, roughly every 700ms, in order', async ({ page }) => {
      await open(page, lang);
      await page.locator('#setup').scrollIntoViewIfNeeded();
      await expect(done(page)).toHaveCount(1, { timeout: 4000 });
      const first = await page.locator('[data-su-row="1"]').evaluate((e) => e.classList.contains('done'));
      expect(first).toBe(true);
      await expect(done(page)).toHaveCount(5, { timeout: 8000 });
    });

    test('reduced motion: all five rows done at once, 100%, and no transition on the line or the drawing', async ({ page }) => {
      await page.emulateMedia({ reducedMotion: 'reduce' });
      await open(page, lang);
      await expect(done(page)).toHaveCount(5);
      await expect(page.locator('[data-su-pct]')).toHaveText('100%');
      expect(await page.locator('[data-su-fill]').evaluate((e) => getComputedStyle(e).transitionDuration)).toBe('0s');
      expect(await page.locator('.su-svg .f').first().evaluate((e) => getComputedStyle(e).transitionDuration.split(',')[0].trim())).toBe('0s');
      await expect(page.locator('[data-su-svg]')).toHaveClass(/on-5/);
    });

    test('Clinic and Gym notes open WhatsApp with the exact prefilled text; Retail links to its page', async ({ page }) => {
      await open(page, lang);
      for (const k of ['clinic', 'gym'] as const) {
        const href = await page.locator(`#${k} .pr-wa`).getAttribute('href');
        expect(href!.startsWith(WA)).toBe(true);
        expect(decodeURIComponent(href!.slice(WA.length))).toBe(MSG[lang][k]);
      }
      expect(await page.locator('#retail .pr-link').getAttribute('href')).toBe(`/${lang}/aura-retail/`);
      await expect(page.locator('#retail .pr-chip')).toHaveText(lang === 'ar' ? 'متوفّر الآن' : 'Available now');
    });

    test('the nav Gym link lands on the Gym card (#gym exists and is in view after following it)', async ({ page }) => {
      await open(page, lang);
      await expect(page.locator('#gym')).toHaveCount(1);
      expect(await page.locator('a.nav-link[data-chapter-link="gym"]').getAttribute('href')).toBe(`/${lang}/#gym`);
      await page.locator('a.nav-link[data-chapter-link="gym"]').click();
      await expect.poll(() => page.evaluate(() => { const r = document.getElementById('gym')!.getBoundingClientRect(); return r.top >= 0 && r.bottom <= innerHeight; })).toBe(true);
      // Gym red (owner confirmed #9E2B25)
      expect(await page.locator('#gym').evaluate((e) => getComputedStyle(e).borderTopColor)).toBe('rgb(158, 43, 37)');
    });

    test('a tap toggles .peek and lifts the note; hover lifts it too; reduced motion: no lift at all', async ({ page }) => {
      await open(page, lang);
      const note = page.locator('#clinic .pr-note');
      const ty = () => note.evaluate((e) => new DOMMatrix(getComputedStyle(e).transform).f);
      await page.locator('#clinic').scrollIntoViewIfNeeded();
      await page.mouse.move(2, 2);
      await page.waitForTimeout(400);
      const rest = await ty();
      await page.locator('#clinic h3').click();
      await expect(page.locator('#clinic')).toHaveClass(/peek/);
      await expect.poll(ty).toBeLessThan(rest - 5);
      await page.locator('#clinic h3').click();
      await expect(page.locator('#clinic')).not.toHaveClass(/peek/);
      await page.mouse.move(2, 2);
      await expect.poll(ty).toBeCloseTo(rest, 0);
      const gymTy = () => page.locator('#gym .pr-note').evaluate((e) => new DOMMatrix(getComputedStyle(e).transform).f);
      const gymRest = await gymTy();
      await page.locator('#gym').hover();
      await expect.poll(gymTy).toBeLessThan(gymRest - 5);
      await page.emulateMedia({ reducedMotion: 'reduce' });
      await page.locator('#clinic h3').click();
      await page.waitForTimeout(300);
      expect(Math.abs((await ty()) - rest)).toBeLessThan(1);
    });

    test('close: tel link, big and as wide as its box, platform line, brand line only where it belongs', async ({ page }) => {
      await open(page, lang);
      const tel = page.locator('a[data-cl-phone]');
      await expect(tel).toHaveAttribute('href', 'tel:+962780069485');
      await expect(tel).toHaveAttribute('dir', 'ltr');
      expect(await tel.evaluate((e) => getComputedStyle(e).fontFamily.toLowerCase().includes('mono'))).toBe(false);
      await expect.poll(async () => page.evaluate(() => {
        const a = document.querySelector('[data-cl-phone]')!.getBoundingClientRect().width, b = document.querySelector('[data-cl-box]')!.getBoundingClientRect().width;
        return Math.abs(a - b) / b < 0.02;
      })).toBe(true);
      const close = (await page.locator('#talk').innerText());
      if (lang === 'en') {
        expect(close).toContain('Go bigger. Go smarter.');
        expect(close).toContain('Pricing depends on your shop. Ask us.');
        expect(close).toContain('Runs on Windows and Android');
      } else {
        expect(close).toContain('كبّر شغلك، واشتغل بذكاء.');
        expect(close).toContain('السعر حسب محلّك، اسألنا.');
        expect(close).toContain('يشتغل على ويندوز وأندرويد');
        expect(await page.locator('body').innerText()).not.toMatch(/go bigger|go smarter/i);
      }
    });

    test('copy rules over the whole home page: no "!", no em-dash, no Arabic-Indic digits', async ({ page }) => {
      await open(page, lang);
      const text = await page.locator('main').innerText();
      expect(text).not.toMatch(/!/);
      expect(text).not.toMatch(/—/);
      expect(text).not.toMatch(/[٠-٩۰-۹]/);
    });

    for (const w of [390, 1440]) {
      test(`no horizontal scroll across the whole home page at ${w}`, async ({ page }) => {
        await open(page, lang, w, w === 390 ? 844 : 900);
        for (const id of ['setup', 'products', 'talk']) await page.locator(`#${id}`).scrollIntoViewIfNeeded();
        await page.waitForTimeout(500);
        const m = await page.evaluate(() => ({ sw: document.documentElement.scrollWidth, iw: innerWidth }));
        expect(m.sw).toBe(m.iw);
      });
    }
  });
}

test('/ar/ has no English brand line anywhere in its HTML', async ({ request }) => {
  const html = await (await request.get('/ar/')).text();
  expect(html).not.toMatch(/go bigger/i);
});
