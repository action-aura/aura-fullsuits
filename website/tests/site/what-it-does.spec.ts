import { test, expect, type Page } from '@playwright/test';

// "What it does" inside the home Retail chapter (E2; ported from feat/website-live-till), for both languages. Desktop (>=900px): the stage is pinned (position: sticky inside a tall
// track) and scroll progress picks the active item; the list items are also buttons that jump to their slice. Phone: no
// pinning, four chip tabs with arrow-key navigation (RTL-correct). Scroll targets are computed here from the DOM geometry,
// NOT by calling the page script's own mapping, so a broken mapping cannot hide behind itself.

const LANGS = ['ar', 'en'] as const;
const KEYS = ['sell', 'stock', 'owed', 'branches'];

const tab = (p: Page, i: number) => p.locator(`[data-wd-tab="${i}"]`);
const activeImg = (p: Page) => p.locator('[data-wd-panel][data-active="true"] img');

async function open(page: Page, lang: string, width: number, height = 900) {
  await page.setViewportSize({ width, height });
  await page.goto(`/${lang}/`);
  await page.waitForLoadState('load');
}

// Scroll so the middle of item i's slice of the pinned range is at the top of the stage.
async function scrollToSlice(page: Page, i: number) {
  await page.evaluate((k) => {
    const track = document.querySelector<HTMLElement>('[data-wd-track]')!;
    const stick = track.firstElementChild as HTMLElement;
    const top = track.getBoundingClientRect().top + scrollY;
    const range = track.offsetHeight - stick.offsetHeight;
    scrollTo(0, top + ((k + 0.5) / 4) * range);
  }, i);
}

for (const lang of LANGS) {
  test.describe(`what it does ${lang} desktop`, () => {
    test.beforeEach(async ({ page }) => { await open(page, lang, 1440); });

    test('the stage is pinned: sticky, and stays in view across the whole track', async ({ page }) => {
      await expect(page.locator('.wd-stick')).toHaveCSS('position', 'sticky');
      const track = await page.evaluate(() => { const t = document.querySelector<HTMLElement>('[data-wd-track]')!; return t.offsetHeight / innerHeight; });
      expect(track).toBeGreaterThanOrEqual(2.5);
      for (const i of [0, 3]) {
        await scrollToSlice(page, i);
        // the page may smooth-scroll, so wait for the stage to settle at its pinned offset
        await expect.poll(() => page.evaluate(() => Math.round(document.querySelector('.wd-stick')!.getBoundingClientRect().top)), { message: `stage top at item ${i}` }).toBeLessThan(100);
        const r = await page.evaluate(() => document.querySelector('.wd-stick')!.getBoundingClientRect().toJSON());
        expect(r.top).toBeGreaterThanOrEqual(0);
        expect(r.bottom).toBeGreaterThan(500);
      }
    });

    test('scrolling through the section activates the four items in order and swaps the visible screenshot', async ({ page }) => {
      const seen: string[] = [];
      for (let i = 0; i < 4; i++) {
        await scrollToSlice(page, i);
        await expect(tab(page, i)).toHaveAttribute('aria-selected', 'true');
        for (let k = 0; k < 4; k++) if (k !== i) await expect(tab(page, k)).toHaveAttribute('aria-selected', 'false');
        const src = (await activeImg(page).getAttribute('src'))!;
        expect(src, `item ${i} image`).toContain(`${KEYS[i]}-${lang}`);
        seen.push(src);
        // after the cross-fade only the active image is rendered visible
        await expect(activeImg(page)).toBeVisible();
        await expect(page.locator('[data-wd-panel][data-active="false"] img:visible')).toHaveCount(0);
      }
      expect(new Set(seen).size).toBe(4);
      // and back up
      await scrollToSlice(page, 1);
      await expect(tab(page, 1)).toHaveAttribute('aria-selected', 'true');
    });

    test('clicking an item activates it and jumps the page to its slice', async ({ page }) => {
      await page.locator('#what').scrollIntoViewIfNeeded();
      for (const i of [2, 0, 3, 1]) {
        await tab(page, i).click();
        await expect(tab(page, i)).toHaveAttribute('aria-selected', 'true');
        await expect(activeImg(page)).toHaveAttribute('src', new RegExp(`${KEYS[i]}-${lang}`));
        const where = await page.evaluate(() => {
          const t = document.querySelector<HTMLElement>('[data-wd-track]')!;
          const range = t.offsetHeight - (t.firstElementChild as HTMLElement).offsetHeight;
          return (scrollY - (t.getBoundingClientRect().top + scrollY)) / range;
        });
        expect(Math.floor(where * 4), `scroll slice after clicking ${i}`).toBe(i);
      }
    });

    test('images carry alt text and width/height; the cross-fade is an opacity transition', async ({ page }) => {
      const imgs = await page.locator('.wd-img').evaluateAll((els) => els.map((e) => ({
        alt: e.getAttribute('alt') ?? '', w: Number(e.getAttribute('width')), h: Number(e.getAttribute('height')), lazy: e.getAttribute('loading'),
      })));
      expect(imgs).toHaveLength(4);
      for (const im of imgs) {
        expect(im.alt.length).toBeGreaterThan(15);
        expect(im.w).toBeGreaterThan(0);
        expect(im.h).toBeGreaterThan(0);
        expect(im.lazy).toBe('lazy');
      }
      const p = page.locator('.wd-panel').first();
      await expect(p).toHaveCSS('transition-property', /opacity/);
      await expect(p).not.toHaveCSS('transition-duration', '0s');
    });
  });

  test.describe(`what it does ${lang} phone`, () => {
    test.beforeEach(async ({ page }) => { await open(page, lang, 390, 844); });

    test('no pinning, no horizontal scroll, tabs are real tabs with one visible panel', async ({ page }) => {
      await expect(page.locator('.wd-stick')).toHaveCSS('position', 'static');
      const sw = await page.evaluate(() => document.documentElement.scrollWidth);
      expect(sw).toBe(390);
      await expect(page.getByRole('tablist')).toHaveCount(1);
      await expect(page.getByRole('tab')).toHaveCount(4);
      await expect(page.getByRole('tabpanel')).toHaveCount(1); // inactive panels are display:none, so only one is exposed
      const h = await page.evaluate(() => document.querySelector<HTMLElement>('[data-wd-track]')!.offsetHeight / innerHeight);
      expect(h).toBeLessThan(1.6);
    });

    test('clicking a chip swaps the panel and its screenshot', async ({ page }) => {
      await page.locator('#what').scrollIntoViewIfNeeded();
      for (const i of [3, 1, 2, 0]) {
        await tab(page, i).click();
        await expect(tab(page, i)).toHaveAttribute('aria-selected', 'true');
        await expect(activeImg(page)).toBeVisible();
        await expect(activeImg(page)).toHaveAttribute('src', new RegExp(`${KEYS[i]}-${lang}`));
        await expect(page.getByRole('tabpanel')).toHaveCount(1);
      }
    });

    test('arrow keys follow the reading direction (Arabic: ArrowLeft is next, English: ArrowRight is next)', async ({ page }) => {
      await page.locator('#what').scrollIntoViewIfNeeded();
      const next = lang === 'ar' ? 'ArrowLeft' : 'ArrowRight';
      const prev = lang === 'ar' ? 'ArrowRight' : 'ArrowLeft';
      await tab(page, 0).focus();
      await page.keyboard.press(next);
      await expect(tab(page, 1)).toHaveAttribute('aria-selected', 'true');
      await expect(tab(page, 1)).toBeFocused();
      await page.keyboard.press(next);
      await expect(tab(page, 2)).toHaveAttribute('aria-selected', 'true');
      await page.keyboard.press(prev);
      await expect(tab(page, 1)).toHaveAttribute('aria-selected', 'true');
      await page.keyboard.press(prev);
      await page.keyboard.press(prev); // wraps to the last
      await expect(tab(page, 3)).toHaveAttribute('aria-selected', 'true');
      // the chips really are laid out in that direction: the next tab is visually further along the reading direction
      const x = await page.evaluate(() => [...document.querySelectorAll('[data-wd-tab]')].map((e) => e.getBoundingClientRect().left));
      if (lang === 'ar') expect(x[0]).toBeGreaterThan(x[1]); else expect(x[0]).toBeLessThan(x[1]);
    });
  });

  test.describe(`what it does ${lang} reduced motion`, () => {
    test('no transition on the panels or images: the swap is instant', async ({ page }) => {
      await page.emulateMedia({ reducedMotion: 'reduce' });
      await open(page, lang, 1440);
      await expect(page.locator('.wd-panel').first()).toHaveCSS('transition-duration', '0s');
      await expect(page.locator('.wd-img').first()).toHaveCSS('transition-duration', '0s');
      await page.locator('#what').scrollIntoViewIfNeeded();
      await tab(page, 2).click();
      // no waiting for a fade: the very next frame already shows the new state
      const st = await page.evaluate(() => {
        const a = document.querySelector('[data-wd-panel][data-active="true"]') as HTMLElement;
        return { idx: a.dataset.wdPanel, op: getComputedStyle(a).opacity, vis: getComputedStyle(a).visibility };
      });
      expect(st).toEqual({ idx: '2', op: '1', vis: 'visible' });
    });
  });
}
