import { test, expect, type Page } from '@playwright/test';

test.use({
  viewport: { width: 1440, height: 900 },
  launchOptions: { args: ['--use-gl=swiftshader', '--enable-webgl', '--ignore-gpu-blocklist'] },
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

// About is the only page with the jewel, and it lives inside a navy band at the top (Look 1). The old scroll
// "stations" are gone, so the old test that followed them is replaced by this one: with the 3D chunk blocked, the
// still must stay inside the band, the band must be exactly as tall as the transparent hero section over it, and
// nothing may be left of the dark page palette (the page behind the band is the light ivory).
test('About: the still stays inside the navy band when the 3D chunk fails, and the page below is light', async ({ page }) => {
  await page.route('**/scene*.js', (r) => r.abort());
  await page.goto('/en/about/');
  await page.waitForLoadState('load');
  await page.waitForTimeout(1000);
  await expect(page.locator('.jewel')).not.toHaveClass(/is-live/);
  const band = await page.locator('.jewel-band').boundingBox();
  const hero = await page.locator('#hero').boundingBox();
  const still = await page.locator('.jewel-still').boundingBox();
  expect(band && hero && still).toBeTruthy();
  expect(Math.abs(band!.height - hero!.height), 'band is as tall as the hero section').toBeLessThanOrEqual(1);
  expect(still!.y).toBeGreaterThanOrEqual(band!.y - 1);
  expect(still!.y + still!.height).toBeLessThanOrEqual(band!.y + band!.height + 1);
  const bg = await page.locator('.jewel-band').evaluate((e) => getComputedStyle(e).backgroundColor);
  expect(bg).toBe('rgb(22, 35, 61)');
  const below = await page.locator('#story').evaluate((e) => getComputedStyle(document.body).backgroundColor);
  expect(below).toBe('rgb(246, 244, 239)');
  expect(await page.evaluate(() => document.documentElement.hasAttribute('data-jewel'))).toBe(false);
});

type Box = { left: number; top: number; right: number; bottom: number };

// Jewel stills (Plan 3, Task 3): on the still tier, and under the rich tier's 1.1s load cross-fade, the still's mark
// must sit where the canvas mark renders, or the mark jumps. Spec: spec-delta-stills.md, 2026-10-18. Both tiers are
// measured the same way, from pixels: the box of what changes when .jewel is hidden (copy, nav and footer hidden too).
// Two thresholds, because the bead's additive halo cannot be carried by a straight-alpha image: three.js blends it
// SRC_ALPHA, ONE, so the canvas alpha accumulates as a^2 while the colour is c*a, and the readback clamps that; the
// still's halo tail is dimmer by physics. Left, top and bottom are hard geometry (facet edge, apex, feet) at 14/255;
// the right edge is read at 96/255, where only the bead and the ring count in both tiers (measured 2026-10-18: live
// 1277 to 1279, still 1278, stable from 64 through 160; at 14 the live halo reaches 1311 to 1313).
const STILL_TOLERANCE_PX = 16; // measured noise is at most 4px per edge (idle sway and bob between live samples, still vs
                               // live); a 40px clip offset, the old 900px attribute width (60px) or a 58vh frame (about
                               // 50px at top and bottom) all land far outside.
const EDGE_THRESHOLD = 14, RIGHT_THRESHOLD = 96;
const markPixelBox = async (p: Page, tier: 'still' | 'live'): Promise<Box> => {
  // Isolate the tier under test: the rich page keeps its stills in the DOM at opacity 0 behind the canvas and the still
  // page keeps its canvas (display:none). Hiding the other layer explicitly means an aligned one cannot mask a misplaced one.
  const hide = await p.addStyleTag({ content: `main,.nav,.strip,footer{visibility:hidden !important} ${tier === 'live' ? '.jewel-still' : '.jewel-canvas'}{visibility:hidden !important}` });
  await p.waitForTimeout(400);
  const shown = (await p.screenshot()).toString('base64');
  const off = await p.addStyleTag({ content: '.jewel{visibility:hidden !important}' });
  await p.waitForTimeout(400);
  const hidden = (await p.screenshot()).toString('base64');
  await off.evaluate((el) => (el as HTMLElement).remove());
  await hide.evaluate((el) => (el as HTMLElement).remove());
  const box = await p.evaluate(async ([a, b, tEdge, tRight]) => {
    const load = (s: string) => new Promise<HTMLImageElement>((res) => { const i = new Image(); i.onload = () => res(i); i.src = 'data:image/png;base64,' + s; });
    const [ia, ib] = await Promise.all([load(a), load(b)]);
    const cv = document.createElement('canvas'); cv.width = ia.width; cv.height = ia.height;
    const x = cv.getContext('2d')!;
    x.drawImage(ia, 0, 0); const da = x.getImageData(0, 0, cv.width, cv.height).data;
    x.drawImage(ib, 0, 0); const db = x.getImageData(0, 0, cv.width, cv.height).data;
    const edge = [1e9, 1e9, -1, -1], right = [1e9, 1e9, -1, -1];
    const grow = (bx: number[], px: number, py: number) => { if (px < bx[0]) bx[0] = px; if (py < bx[1]) bx[1] = py; if (px > bx[2]) bx[2] = px; if (py > bx[3]) bx[3] = py; };
    for (let i = 0; i < da.length; i += 4) {
      const d = Math.max(Math.abs(da[i] - db[i]), Math.abs(da[i + 1] - db[i + 1]), Math.abs(da[i + 2] - db[i + 2]));
      if (d <= tEdge) continue;
      const px = (i / 4) % cv.width, py = ((i / 4) / cv.width) | 0;
      grow(edge, px, py);
      if (d > tRight) grow(right, px, py);
    }
    return edge[2] < 0 || right[2] < 0 ? null : { left: edge[0], top: edge[1], right: right[2] + 1, bottom: edge[3] + 1 };
  }, [shown, hidden, EDGE_THRESHOLD, RIGHT_THRESHOLD] as const);
  expect(box, `${tier}: nothing changed when .jewel was hidden`).not.toBeNull();
  return box!;
};

test('the still mark sits where the canvas mark renders (About hero, 1440x900)', async ({ browser }) => {
  // Two contexts and a live WebGL render under swiftshader: well past the default 30s. Timing only, no assertion changed.
  test.setTimeout(240_000);
  // Still tier: reduced motion makes pickTier return 'still'; the canvas is display:none and the master still shows.
  const stillCtx = await browser.newContext({ reducedMotion: 'reduce', viewport: { width: 1440, height: 900 } });
  const still = await stillCtx.newPage();
  await still.goto('/en/about/');
  await still.waitForLoadState('load');
  await expect(still.locator('.jewel')).toHaveAttribute('data-tier', 'still');
  // The master still must have loaded: Jewel.astro hides a still that failed to load, which would leave an empty box.
  await expect.poll(() => still.evaluate(() => {
    const img = document.querySelector<HTMLImageElement>('.still-master')!;
    return img.complete && img.naturalWidth > 0 && !img.hidden;
  }), { message: 'still-master loaded', timeout: 10_000 }).toBe(true);
  const stillBox = await markPixelBox(still, 'still');
  await stillCtx.close();

  // Rich tier: the live canvas, after the 1.8s arrival rise has finished.
  const richCtx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const rich = await richCtx.newPage();
  await rich.goto('/en/about/');
  await rich.waitForSelector('.jewel.is-live', { timeout: 20_000 });
  await rich.waitForTimeout(2500);
  const liveBox = await markPixelBox(rich, 'live');
  await richCtx.close();

  // Both must be a mark-sized box (about 444 x 530 here), not two empty or tiny boxes agreeing by accident.
  for (const [name, b] of [['still', stillBox], ['live', liveBox]] as const) {
    expect(b.bottom - b.top, `${name} box height`).toBeGreaterThan(450);
    expect(b.right - b.left, `${name} box width`).toBeGreaterThan(350);
  }
  for (const edge of ['left', 'top', 'right', 'bottom'] as const) {
    expect(Math.abs(stillBox[edge] - liveBox[edge]), `${edge}: still ${JSON.stringify(stillBox)} vs live ${JSON.stringify(liveBox)}`)
      .toBeLessThanOrEqual(STILL_TOLERANCE_PX);
  }
});
