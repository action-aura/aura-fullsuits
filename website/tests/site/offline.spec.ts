import { test, expect, type Page } from '@playwright/test';

// The home offline switch (directions.md 8.5), for both languages. The sale amounts are random, so every expected
// number is read back from the DOM rows the page itself drew, never hard-coded; money is compared as integer
// millidinars and written with 3 decimals.

const LANGS = ['ar', 'en'] as const;
const fmt = (m: number) => (m / 1000).toFixed(3);
const TWO: Record<(typeof LANGS)[number], string> = { ar: 'وصلت بيعتين', en: '2 sales arrived' };
const ONE: Record<(typeof LANGS)[number], string> = { ar: 'وصلت بيعة وحدة', en: '1 sale arrived' };
const THREE: Record<(typeof LANGS)[number], string> = { ar: 'وصلت 3 بيعات', en: '3 sales arrived' };
const ELEVEN: Record<(typeof LANGS)[number], string> = { ar: 'وصلت 11 بيعة', en: '11 sales arrived' };

const sw = (p: Page) => p.locator('[data-o-switch]');
const ring = (p: Page) => p.locator('[data-o-ring]');
const ptotal = (p: Page) => p.locator('[data-o-ptotal]');
const rows = (p: Page) => p.locator('[data-o-sales] li:not(.of-empty)');

// Amounts of the rows currently drawn, in millidinars (newest row first).
async function rowAmounts(p: Page): Promise<number[]> {
  const t = await p.locator('[data-o-sales] li:not(.of-empty) .of-amt .num').allTextContents();
  return t.map((s) => Math.round(parseFloat(s) * 1000));
}

async function open(page: Page, lang: string, width = 1440) {
  await page.setViewportSize({ width, height: 900 });
  await page.goto(`/${lang}/`);
  await page.waitForLoadState('load');
}

for (const lang of LANGS) {
  test.describe(`offline switch ${lang}`, () => {
    test.beforeEach(async ({ page }) => { await open(page, lang); });

    test('the switch is a real switch: named, and aria-checked follows every flip', async ({ page }) => {
      const s = page.getByRole('switch');
      await expect(s).toHaveCount(1);
      await expect(s).toHaveAccessibleName(/\S/);
      await expect(s).toHaveAttribute('aria-checked', 'true');
      await s.click();
      await expect(s).toHaveAttribute('aria-checked', 'false');
      await s.click();
      await expect(s).toHaveAttribute('aria-checked', 'true');
      await s.focus();
      await page.keyboard.press('Space');
      await expect(s).toHaveAttribute('aria-checked', 'false');
    });

    test('offline: two sales queue (badge 2, phone unchanged); online: phone rises by exactly both, toast says two', async ({ page }) => {
      await sw(page).click();
      await expect(page.locator('[data-o-chip]')).toHaveClass(/of-chip-off/);
      await ring(page).click();
      await ring(page).click();
      await expect(rows(page)).toHaveCount(2);
      const amounts = await rowAmounts(page);
      await expect(page.locator('[data-o-bn]')).toHaveText('2');
      await expect(page.locator('[data-o-badge]')).toHaveClass(/of-show/);
      await expect(ptotal(page)).toHaveText('0.000');
      await expect(page.locator('[data-o-line]')).toContainText('10:41');
      await page.waitForTimeout(900);
      await expect(ptotal(page), 'phone must not move while offline').toHaveText('0.000');

      await sw(page).click();
      await expect(ptotal(page)).toHaveText(fmt(amounts[0] + amounts[1]), { timeout: 5000 });
      await expect(page.locator('[data-o-pcount]')).toHaveText('2');
      await expect(page.locator('[data-o-toast]')).toHaveText(TWO[lang]);
      await expect(page.locator('[data-o-toast]')).toHaveClass(/of-show/);
      await expect(page.locator('[data-o-badge]')).not.toHaveClass(/of-show/);
      await expect(page.locator('[data-o-sales] .of-st:not(.of-ok)')).toHaveCount(0);
    });

    test('a sale rung while online reaches the phone by itself', async ({ page }) => {
      await ring(page).click();
      const [a] = await rowAmounts(page);
      await expect(ptotal(page)).toHaveText(fmt(a), { timeout: 5000 });
      await expect(page.locator('[data-o-sales] .of-st.of-ok')).toHaveCount(1);
      await expect(page.locator('[data-o-badge]')).not.toHaveClass(/of-show/);
    });

    test('toast wording by count: 1, 3 and 11', async ({ page }) => {
      const cycle = async (n: number) => {
        await sw(page).click();
        for (let i = 0; i < n; i++) await ring(page).click();
        await sw(page).click();
        await expect(page.locator('[data-o-pcount]')).toHaveText(String(cycleTotal += n), { timeout: 8000 });
      };
      let cycleTotal = 0;
      await cycle(1);
      await expect(page.locator('[data-o-toast]')).toHaveText(ONE[lang]);
      await cycle(3);
      await expect(page.locator('[data-o-toast]')).toHaveText(THREE[lang]);
      await cycle(11);
      await expect(page.locator('[data-o-toast]')).toHaveText(ELEVEN[lang]);
    });

    test('the live region announces the switch and the arrival, and the page stays clean of Arabic-Indic digits, "!" and em dash', async ({ page }) => {
      const live = page.locator('[data-o-live]');
      await expect(live).toHaveAttribute('aria-live', 'polite');
      await sw(page).click();
      await expect(live).not.toBeEmpty();
      const off = await live.textContent();
      await ring(page).click();
      await sw(page).click();
      await expect(live).not.toHaveText(off ?? '');
      await expect(live).toHaveText(lang === 'ar' ? 'وصلت بيعة وحدة' : '1 sale arrived', { timeout: 5000 });
      const body = await page.evaluate(() => document.body.innerText);
      expect(body).not.toMatch(/[٠-٩۰-۹]/);
      expect(body).not.toContain('!');
      expect(body).not.toContain('—');
    });

    for (const w of [390, 1440]) {
      test(`no horizontal scroll at ${w}, also offline with a queue and after reconnect`, async ({ page }) => {
        await open(page, lang, w);
        const scroll = () => page.evaluate(() => document.documentElement.scrollWidth);
        expect(await scroll()).toBe(w);
        await sw(page).click();
        await ring(page).click();
        await ring(page).click();
        expect(await scroll()).toBe(w);
        await sw(page).click();
        await page.waitForTimeout(1200);
        expect(await scroll()).toBe(w);
      });
    }

    test('autoplay: runs the story once when left alone in view', async ({ page }) => {
      await page.evaluate(() => {
        const w = window as unknown as { __sw: string[] };
        w.__sw = [];
        const el = document.querySelector('[data-o-switch]')!;
        new MutationObserver(() => w.__sw.push(el.getAttribute('aria-checked') ?? '')).observe(el, { attributes: true, attributeFilter: ['aria-checked'] });
        document.querySelector('[data-offline]')!.scrollIntoView({ block: 'center' });
      });
      // 4s idle + a 5s story, then the last chip still has to fly.
      await expect(rows(page)).toHaveCount(3, { timeout: 16000 });
      await expect(page.locator('[data-o-pcount]')).toHaveText('3', { timeout: 8000 });
      const amounts = await rowAmounts(page);
      await expect(ptotal(page)).toHaveText(fmt(amounts.reduce((a, b) => a + b, 0)), { timeout: 5000 });
      await expect(sw(page)).toHaveAttribute('aria-checked', 'true');
      expect(await page.evaluate(() => (window as unknown as { __sw: string[] }).__sw), 'it went offline and came back').toEqual(['false', 'true']);
      await page.waitForTimeout(2500);
      await expect(rows(page), 'it plays once, not on a loop').toHaveCount(3);
    });

    test('autoplay: a pointerdown in the section cancels it for good', async ({ page }) => {
      await page.evaluate(() => document.querySelector('[data-offline]')!.scrollIntoView({ block: 'center' }));
      await page.waitForTimeout(1000);
      await page.locator('.of h2').click();
      await page.waitForTimeout(7500);
      await expect(rows(page)).toHaveCount(0);
      await expect(sw(page)).toHaveAttribute('aria-checked', 'true');
      await expect(ptotal(page)).toHaveText('0.000');
    });
  });

  test.describe(`offline switch ${lang} with reduced motion`, () => {
    test.beforeEach(async ({ page }) => {
      await page.emulateMedia({ reducedMotion: 'reduce' });
      await open(page, lang);
      expect(await page.evaluate(() => matchMedia('(prefers-reduced-motion: reduce)').matches), 'emulation is on').toBe(true);
    });

    test('no autoplay: left alone in view for 6s, nothing happens', async ({ page }) => {
      await page.evaluate(() => document.querySelector('[data-offline]')!.scrollIntoView({ block: 'center' }));
      await page.waitForTimeout(6000);
      await expect(rows(page)).toHaveCount(0);
      await expect(sw(page)).toHaveAttribute('aria-checked', 'true');
      await expect(ptotal(page)).toHaveText('0.000');
    });

    test('updates are instant: no flying chip, the phone total jumps straight to the sum, the toast is there at once', async ({ page }) => {
      await page.evaluate(() => {
        const seen: string[] = [];
        (window as unknown as { __seen: string[] }).__seen = seen;
        const el = document.querySelector('[data-o-ptotal]')!;
        new MutationObserver(() => seen.push(el.textContent ?? '')).observe(el, { childList: true, characterData: true, subtree: true });
      });
      await sw(page).click();
      await ring(page).click();
      await ring(page).click();
      const amounts = await rowAmounts(page);
      const expected = fmt(amounts[0] + amounts[1]);
      // One synchronous evaluate: with reduced motion everything is final in the same task as the click.
      const after = await sw(page).evaluate((btn) => {
        (btn as HTMLButtonElement).click();
        return {
          ptotal: document.querySelector('[data-o-ptotal]')!.textContent,
          flying: document.querySelectorAll('.of-fly').length,
          toast: document.querySelector('[data-o-toast]')!.textContent,
          badgeShown: document.querySelector('[data-o-badge]')!.classList.contains('of-show'),
        };
      });
      expect(after.ptotal, 'phone total is final at once').toBe(expected);
      expect(after.flying, 'no flying chip').toBe(0);
      expect(after.toast).toBe(TWO[lang]);
      expect(after.badgeShown).toBe(false);
      await page.waitForTimeout(700);
      expect(await page.evaluate(() => (window as unknown as { __seen: string[] }).__seen), 'no in-between values').toEqual([expected]);
    });

    test('the cable and the vertical line do not animate', async ({ page }) => {
      const name = await page.locator('.of-cable path').evaluate((e) => getComputedStyle(e).animationName);
      expect(name).toBe('none');
    });
  });
}
