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
    // Four sections with WebGL under swiftshader and up to 8s polls each: past 30s when workers share the CPU.
    test.setTimeout(150_000);
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
  const hide = await p.addStyleTag({ content: `main,.nav,footer{visibility:hidden !important} ${tier === 'live' ? '.jewel-still' : '.jewel-canvas'}{visibility:hidden !important}` });
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

test('the still mark sits where the canvas mark renders (hero, 1440x900)', async ({ browser }) => {
  // Two contexts and a live WebGL render under swiftshader: well past the default 30s. Timing only, no assertion changed.
  test.setTimeout(240_000);
  // Still tier: reduced motion makes pickTier return 'still'; the canvas is display:none and the master still shows.
  const stillCtx = await browser.newContext({ reducedMotion: 'reduce', viewport: { width: 1440, height: 900 } });
  const still = await stillCtx.newPage();
  await still.goto('/en/');
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
  await rich.goto('/en/');
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
