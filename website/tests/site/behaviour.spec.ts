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

// Product pages: the mark dissolves at the two full-width sections (day rail, features grid) and condenses back,
// clear of the copy, at the details chapter and the demo CTA. Spec: spec-delta-cards-details.md, 2026-10-18.
type Box = { left: number; top: number; right: number; bottom: number };
const centreOn = (p: Page, sel: string): Promise<void> =>
  p.evaluate((sel) => {
    const r = document.querySelector(sel)!.getBoundingClientRect();
    window.scrollTo({ top: window.scrollY + r.top + r.height / 2 - window.innerHeight / 2, behavior: 'instant' as ScrollBehavior });
  }, sel);
const cssVar = (p: Page, name: string): Promise<string> =>
  p.evaluate((name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim(), name);
const jewelOpacity = (p: Page): Promise<string> =>
  p.evaluate(() => getComputedStyle(document.querySelector('.jewel')!).opacity);
// The mark's box from the station vars (scene.js: 9.14 units tall = 58vh at scale 1, 7.66 wide, centred at jx/jy),
// padded 48px: measured 2026-10-18, the rendered mark reaches at most 34px above and 43px to the node side of it.
const markBox = (p: Page, pad = 48): Promise<Box> =>
  p.evaluate((pad) => {
    const cs = getComputedStyle(document.documentElement);
    const v = (k: string) => parseFloat(cs.getPropertyValue(k));
    const h = 0.58 * innerHeight * v('--js'), w = (7.66 / 9.14) * h, cx = v('--jx') * innerWidth, cy = v('--jy') * innerHeight;
    return { left: cx - w / 2 - pad, top: cy - h / 2 - pad, right: cx + w / 2 + pad, bottom: cy + h / 2 + pad };
  }, pad);
const elementBox = async (p: Page, sel: string): Promise<Box> => {
  const b = (await p.locator(sel).boundingBox())!;
  return { left: b.x, top: b.y, right: b.x + b.width, bottom: b.y + b.height };
};
const intersects = (a: Box, b: Box): boolean => !(a.right <= b.left || a.left >= b.right || a.bottom <= b.top || a.top >= b.bottom);

for (const url of ['/en/aura-retail/', '/ar/aura-clinic/']) {
  test(`product jewel dissolves at the rail and the grid, returns clear of the copy: ${url}`, async ({ page }) => {
    await page.goto(url);
    await page.waitForLoadState('load');
    await page.waitForTimeout(800);
    for (const sel of ['.rail-wrap', '.features']) {
      await centreOn(page, sel);
      await page.waitForTimeout(1000); // the .4s opacity ease, then settle
      expect(await cssVar(page, '--jhide'), `${sel} --jhide`).toBe('1');
      // Poll, do not sample once: under swiftshader WebGL a frame can take longer than the fixed wait, so the .4s
      // transition may not have started yet. The target value is unchanged; only the deadline moved.
      await expect.poll(() => jewelOpacity(page), { message: `${sel} .jewel opacity`, timeout: 8000 }).toBe('0');
    }
    for (const sel of ['#details', '.cta-block']) {
      await centreOn(page, sel);
      await page.waitForTimeout(1000);
      expect(await cssVar(page, '--jhide'), `${sel} --jhide`).toBe('0');
      await expect.poll(() => jewelOpacity(page), { message: `${sel} .jewel opacity`, timeout: 8000 }).toBe('1');
      const mark = await markBox(page);
      const copy = await elementBox(page, `${sel} .copy`);
      expect(intersects(mark, copy), `${sel}: mark ${JSON.stringify(mark)} meets copy ${JSON.stringify(copy)}`).toBe(false);
    }
    // The CTA station is the fifth and last: .9 at 73%/56%, x mirrored on the Arabic page.
    expect(parseFloat(await cssVar(page, '--js'))).toBeCloseTo(0.9, 2);
    expect(parseFloat(await cssVar(page, '--jy'))).toBeCloseTo(0.56, 2);
    expect(Math.abs(parseFloat(await cssVar(page, '--jx')) - 0.5)).toBeCloseTo(0.23, 2);
  });
}
