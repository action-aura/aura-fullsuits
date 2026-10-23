import { test, expect, type Page } from '@playwright/test';

// The home hero till (directions.md 8.4), for both languages. Numbers are read from the DOM, never hard-coded, so a
// price change in the dictionary cannot make these pass or fail by accident. Money is compared as integer
// millidinars: the sum is exact, and the spec compares text to 3 decimals.

const LANGS = ['ar', 'en'] as const;
const fmt = (m: number) => (m / 1000).toFixed(3);

async function prices(page: Page): Promise<number[]> {
  const texts = await page.locator('[data-item] .num').allTextContents();
  return texts.map((t) => Math.round(parseFloat(t) * 1000));
}

for (const lang of LANGS) {
  test.describe(`live till ${lang}`, () => {
    test.beforeEach(async ({ page }) => {
      await page.setViewportSize({ width: 1440, height: 900 });
      await page.goto(`/${lang}/`);
      await page.waitForLoadState('load');
    });

    test('three items then pay: the phone total is exactly the sum of the three prices', async ({ page }) => {
      const p = await prices(page);
      expect(p.length).toBe(6);
      const picks = [0, 2, 3];
      const items = page.locator('[data-item]');
      for (const i of picks) await items.nth(i).click();
      const expected = fmt(picks.reduce((s, i) => s + p[i], 0));
      await expect(page.locator('[data-total]')).toHaveText(expected);
      await page.locator('[data-pay]').click();
      await expect(page.locator('[data-ptotal]')).toHaveText(expected, { timeout: 5000 });
      await expect(page.locator('[data-pcount]')).toHaveText('1');
      await expect(page.locator('[data-donesum]')).toHaveText(expected);
    });

    test('pay is disabled while the bill is empty, enabled after an item, disabled again after the sale', async ({ page }) => {
      const pay = page.locator('[data-pay]');
      await expect(pay).toBeDisabled();
      await page.locator('[data-item]').first().click();
      await expect(pay).toBeEnabled();
      await pay.click();
      await expect(pay).toBeDisabled();
      await expect(page.locator('[data-total]')).toHaveText('0.000', { timeout: 5000 });
    });

    test('tapping the same item twice shows x2 and doubles the line', async ({ page }) => {
      const p = await prices(page);
      const item = page.locator('[data-item]').nth(1);
      await item.click();
      await expect(page.locator('[data-lines] li .lt-q')).toHaveCount(0);
      await item.click();
      await expect(page.locator('[data-lines] li .lt-q')).toHaveText('×2');
      await expect(page.locator('[data-lines] li')).toHaveCount(1);
      await expect(page.locator('[data-total]')).toHaveText(fmt(p[1] * 2));
    });

    test('the page body has no Arabic-Indic digits, no "!" and no em dash, before and after a sale', async ({ page }) => {
      const check = async (when: string) => {
        const body = await page.evaluate(() => document.body.innerText);
        expect(body, `${when}: Arabic-Indic digits`).not.toMatch(/[٠-٩۰-۹]/);
        expect(body, `${when}: exclamation mark`).not.toContain('!');
        expect(body, `${when}: em dash`).not.toContain('—');
      };
      await check('initial');
      await page.locator('[data-item]').nth(4).click();
      await check('after tap');
      await page.locator('[data-pay]').click();
      await expect(page.locator('[data-ptotal]')).not.toHaveText('0.000', { timeout: 5000 });
      await check('after pay');
    });

    test('the live region announces the total and the finished sale', async ({ page }) => {
      const p = await prices(page);
      const live = page.locator('[data-live]');
      await expect(live).toHaveAttribute('aria-live', 'polite');
      await page.locator('[data-item]').nth(0).click();
      await expect(live).toContainText(fmt(p[0]));
      await page.locator('[data-pay]').click();
      await expect(live).toContainText(fmt(p[0]));
      await expect(page.locator('[data-done]')).toHaveClass(/lt-show/);
    });

    for (const w of [390, 1440]) {
      test(`no horizontal scroll at ${w}, also after a sale`, async ({ page }) => {
        await page.setViewportSize({ width: w, height: 900 });
        await page.goto(`/${lang}/`);
        await page.waitForLoadState('load');
        const sw = () => page.evaluate(() => document.documentElement.scrollWidth);
        expect(await sw()).toBe(w);
        await page.locator('[data-item]').nth(2).click();
        await page.locator('[data-pay]').click();
        await page.waitForTimeout(800);
        expect(await sw()).toBe(w);
      });
    }
  });

  test.describe(`live till ${lang} with reduced motion`, () => {
    test('the total updates at once with no in-between values, and the done banner shows', async ({ page }) => {
      await page.emulateMedia({ reducedMotion: 'reduce' });
      await page.setViewportSize({ width: 1440, height: 900 });
      await page.goto(`/${lang}/`);
      await page.waitForLoadState('load');
      expect(await page.evaluate(() => matchMedia('(prefers-reduced-motion: reduce)').matches), 'emulation is on').toBe(true);
      const p = await prices(page);
      // Record every value the total and the phone total ever show.
      await page.evaluate(() => {
        const seen: Record<string, string[]> = { total: [], ptotal: [] };
        (window as unknown as { __seen: typeof seen }).__seen = seen;
        for (const k of ['total', 'ptotal']) {
          const el = document.querySelector(`[data-${k}]`)!;
          new MutationObserver(() => seen[k].push(el.textContent ?? '')).observe(el, { childList: true, characterData: true, subtree: true });
        }
      });
      await page.locator('[data-item]').nth(3).click();
      await page.locator('[data-item]').nth(2).click();
      const expected = fmt(p[3] + p[2]);
      // Everything in one synchronous evaluate right after the click: with reduced motion the banner is shown and the phone
      // total is already final in the same task, so the check cannot be lost to the 1.6s banner timer on a slow machine.
      const after = await page.locator('[data-pay]').evaluate((btn) => {
        (btn as HTMLButtonElement).click();
        const done = document.querySelector('[data-done]')!;
        const cs = getComputedStyle(done);
        return {
          shown: done.classList.contains('lt-show'), visibility: cs.visibility, opacity: cs.opacity, transform: cs.transform,
          ptotal: document.querySelector('[data-ptotal]')!.textContent, dots: document.querySelectorAll('.lt-fdot').length,
        };
      });
      expect(after.shown, 'done banner is shown').toBe(true);
      expect(after.visibility).toBe('visible');
      expect(after.opacity, 'banner is fully opaque at once').toBe('1');
      expect(['none', 'matrix(1, 0, 0, 1, 0, 0)'], 'banner is at rest, not mid-slide').toContain(after.transform);
      expect(after.ptotal, 'phone total is final at once').toBe(expected);
      expect(after.dots, 'no flying dot').toBe(0);
      await page.waitForTimeout(700);
      const seen = await page.evaluate(() => (window as unknown as { __seen: Record<string, string[]> }).__seen);
      expect(seen.total.length, 'the total was written at all').toBeGreaterThan(0);
      for (const v of seen.total) expect([fmt(p[3]), expected, '0.000'], JSON.stringify(seen)).toContain(v);
      expect(seen.ptotal, 'phone total jumps straight to the sum').toEqual([expected]);
    });
  });
}
