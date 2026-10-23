import { test, expect, type Page } from '@playwright/test';

// E2 (2026-10-23): the playable parts of the home Retail chapter. The logic specs (live-till, offline, what-it-does) are ported
// from feat/website-live-till. These guard what is NEW in this site: the demos live inside the retail world (field colour),
// the jewel steps out of their way, the cards enter once with rise + scale, taps feel pressed, and the copy rules hold.

const scrollTo = (p: Page, y: number): Promise<void> => p.evaluate((y) => window.scrollTo({ top: y, behavior: 'instant' as ScrollBehavior }), y);
const centreOn = (p: Page, sel: string): Promise<void> =>
  p.evaluate((sel) => {
    const r = document.querySelector(sel)!.getBoundingClientRect();
    window.scrollTo({ top: window.scrollY + r.top + r.height / 2 - window.innerHeight / 2, behavior: 'instant' as ScrollBehavior });
  }, sel);

// Wait until the reveal-card of the section holding `sel` has finished entering (opacity 1, no transform). The entry is a .7s transition;
// on a loaded machine it can still be running a second after the page was scrolled there, and a mouse aimed at a card that
// is still rising lands on whatever is under that point.
const settled = (p: Page, sel: string): Promise<void> =>
  expect.poll(() => p.evaluate((sel) => {
    const card = document.querySelector(sel)!.closest('section')!.querySelector('.reveal-card') as HTMLElement;
    const cs = getComputedStyle(card);
    return `${cs.opacity} ${cs.transform}`;
  }, sel), { message: `${sel}: card finished entering`, timeout: 15_000 }).toBe('1 none');

// The retail world's field token (tokens.css, design-system section 2): the middle stop of the retail field.
const RETAIL_FIELD_B = 'rgb(28, 21, 17)'; // #1C1511

for (const [name, vp, mobile] of [['desktop 1440', { width: 1440, height: 900 }, false], ['phone 390', { width: 390, height: 844 }, true]] as const) {
  test(`the field behind each demo is the retail world token, and nothing else shows: ${name}`, async ({ browser }) => {
    test.setTimeout(90_000);
    for (const lang of ['en', 'ar']) {
      const ctx = await browser.newContext({ viewport: vp, isMobile: mobile, hasTouch: mobile });
      const page = await ctx.newPage();
      await page.goto(`/${lang}/`);
      await page.waitForLoadState('load');
      await page.waitForTimeout(600);
      for (const id of ['till', 'offline', 'what']) {
        // The pinned "what" section is 300vh tall on desktop: its middle is the stage, not the page edge.
        await centreOn(page, `#${id}`);
        // The world shares are written from a scroll-driven rAF and eased by a .12s opacity transition: on a loaded machine
        // that can lag the scroll by a second or more, so wait for the retail field to be fully in, then read everything.
        await expect.poll(() => page.evaluate(() => `${document.documentElement.dataset.world} ${getComputedStyle(document.querySelector('.bg-retail')!).opacity}`), { timeout: 20_000 }).toBe('retail 1');
        const r = await page.evaluate((id) => {
          const root = document.documentElement;
          const layer = (k: string) => getComputedStyle(document.querySelector(`.bg-${k}`)!);
          const sec = getComputedStyle(document.querySelector(`#${id}`)!);
          return {
            world: root.dataset.world,
            own: { opacity: layer('retail').opacity, image: layer('retail').backgroundImage },
            others: (['master', 'clinic', 'gym'] as const).map((k) => ({ k, opacity: layer(k).opacity, off: document.querySelector(`.bg-${k}`)!.hasAttribute('data-off') })),
            sectionField: sec.getPropertyValue('--field-b').trim(),
            sectionAccent: sec.getPropertyValue('--accent').trim().toUpperCase(),
          };
        }, id);
        const why = `${lang} #${id} ${name}`;
        expect(r.world, `${why}: page world`).toBe('retail');
        expect(r.own.opacity, `${why}: retail field opacity`).toBe('1');
        expect(r.own.image, `${why}: retail field gradient carries the token`).toContain(RETAIL_FIELD_B);
        for (const o of r.others) expect(parseFloat(o.opacity) === 0 || o.off, `${why}: ${o.k} field still showing (${o.opacity})`).toBe(true);
        expect(r.sectionField.toUpperCase(), `${why}: the demo's own --field-b`).toBe('#1C1511');
        expect(r.sectionAccent, `${why}: the demo's own accent is the retail copper`).toBe('#F0B87A');
      }
      await ctx.close();
    }
  });
}

// The jewel steps out of the way of every demo. Box maths as motion.spec.ts / behaviour.spec.ts (the vars place the mark:
// 58vh * --js tall, 7.66/9.14 as wide, centre at --jx/--jy), with 40px of padding for the ring and the bead.
type Box = { left: number; top: number; right: number; bottom: number };
const hits = (a: Box, b: Box): boolean => !(a.right <= b.left || a.left >= b.right || a.bottom <= b.top || a.top >= b.bottom);

test('desktop 1440: wherever the jewel is still visible it overlaps no demo card and no demo copy, from the retail title to the clinic', async ({ browser }) => {
  test.setTimeout(240_000);
  for (const lang of ['en', 'ar']) {
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    const page = await ctx.newPage();
    await page.goto(`/${lang}/`);
    await page.waitForLoadState('load');
    await page.waitForTimeout(800);
    const span = await page.evaluate(() => {
      const top = (s: string) => document.querySelector(s)!.getBoundingClientRect().top + scrollY;
      return { from: top('#retail') + 100, to: top('#clinic') + 100 };
    });
    let sampled = 0, seenCards = 0, visibleSomewhere = 0;
    for (let y = span.from; y <= span.to; y += 45) {
      await scrollTo(page, y);
      await page.waitForTimeout(260); // the .2s opacity ease on .jewel
      const s = await page.evaluate(() => {
        const cs = getComputedStyle(document.documentElement);
        const v = (k: string) => parseFloat(cs.getPropertyValue(k));
        const h = 0.58 * innerHeight * v('--js'), w = (7.66 / 9.14) * h, cx = v('--jx') * innerWidth, cy = v('--jy') * innerHeight, pad = 40;
        const mark = { left: cx - w / 2 - pad, top: cy - h / 2 - pad, right: cx + w / 2 + pad, bottom: cy + h / 2 + pad };
        const opacity = parseFloat(getComputedStyle(document.querySelector('.jewel')!).opacity);
        // Cards: the till with its phone, the offline demo, the what-it-does tabs and the visible screenshot frame; copy: the
        // headings and text of each demo.
        // A box that has not been revealed yet (opacity 0 up the tree) is not there to be overlapped.
        const eff = (el: Element) => { let o = 1; for (let e: Element | null = el; e; e = e.parentElement) o *= parseFloat(getComputedStyle(e).opacity); return o; };
        const sels = ['.lt-till-wrap', '.of-demo', '.wd-list', '.wd-panel[data-active="true"] .wd-frame', '.demo-copy > *', '.wd-head > *'];
        const boxes: { what: string; box: Box }[] = [];
        for (const sel of sels) {
          for (const el of Array.from(document.querySelectorAll(sel))) {
            if (eff(el) < 0.1) continue;
            const r = el.getBoundingClientRect();
            if (r.width < 2 || r.height < 2 || r.bottom < 0 || r.top > innerHeight) continue;
            boxes.push({ what: sel, box: { left: r.left, top: r.top, right: r.right, bottom: r.bottom } });
          }
        }
        return { mark, opacity, boxes };
      });
      sampled++;
      seenCards += s.boxes.length ? 1 : 0;
      if (s.opacity < 0.05) continue;
      visibleSomewhere++;
      for (const b of s.boxes) {
        // The retail chapter's own copy (#retail) is not a demo; only demo boxes are listed above.
        expect(hits(s.mark, b.box), `${lang} y=${Math.round(y)} jewel opacity ${s.opacity}: mark ${JSON.stringify(s.mark)} meets ${b.what} ${JSON.stringify(b.box)}`).toBe(false);
      }
    }
    expect(sampled, 'sampled the whole retail run').toBeGreaterThan(60);
    expect(seenCards, 'demo cards were on screen in many samples').toBeGreaterThan(40);
    expect(visibleSomewhere, 'the jewel IS visible at the start (guards against a probe that never sees the mark)').toBeGreaterThan(0);
    await ctx.close();
  }
});

// Entry: each card rises and grows once. Before it is in view: opacity 0, translated down, scaled .96. After: at rest.
test('demo cards enter once with rise + scale .96 -> 1 (transform and opacity only)', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/en/');
  await page.waitForLoadState('load');
  const before = await page.evaluate(() => {
    const el = document.querySelector('#till .demo-stage')!;
    const cs = getComputedStyle(el);
    return { cls: el.className, o: cs.opacity, t: cs.transform, tp: cs.transitionProperty };
  });
  expect(before.cls).toContain('reveal-card');
  expect(before.cls).not.toMatch(/\bin\b/);
  expect(before.o).toBe('0');
  // matrix(a, b, c, d, tx, ty): scale .96 and 28px down
  const m = before.t.match(/matrix\(([^)]+)\)/)!;
  const [a, , , d, , ty] = m[1].split(',').map(Number);
  expect(a).toBeCloseTo(0.96, 2);
  expect(d).toBeCloseTo(0.96, 2);
  expect(ty).toBeGreaterThan(10);
  expect(before.tp).toMatch(/opacity/);
  expect(before.tp).toMatch(/transform/);
  expect(before.tp).not.toMatch(/filter|width|height|top|left|margin/);
  await centreOn(page, '#till');
  await settled(page, '#till .lt-till-wrap');
  const after = await page.evaluate(() => { const cs = getComputedStyle(document.querySelector('#till .demo-stage')!); return { o: cs.opacity, t: cs.transform }; });
  expect(after.o).toBe('1');
  expect(after.t).toBe('none');
  // once: leaving and coming back does not hide it again
  await scrollTo(page, 0);
  await page.waitForTimeout(300);
  expect(await page.evaluate(() => getComputedStyle(document.querySelector('#till .demo-stage')!).opacity)).toBe('1');
});

test('reduced motion: the demo cards are at rest from the first frame, nothing transitions', async ({ browser }) => {
  const ctx = await browser.newContext({ reducedMotion: 'reduce', viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  await page.goto('/en/');
  await page.waitForLoadState('load');
  const states = await page.evaluate(() => Array.from(document.querySelectorAll('.reveal-card')).map((el) => { const cs = getComputedStyle(el); return { o: cs.opacity, t: cs.transform, d: cs.transitionDuration }; }));
  expect(states.length).toBe(5); // till, offline, what, clinic booking, gym check-in (E3)
  for (const s of states) { expect(s.o).toBe('1'); expect(s.t).toBe('none'); expect(s.d).toBe('0s'); }
  await ctx.close();
});

// Tap: the demo controls use the same press as the rest of the site (tap.ts adds .is-pressed + the glow).
test('tap: demo buttons scale to .97 on pointerdown and glow in the retail accent; released, they return', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/en/');
  await page.waitForLoadState('load');
  for (const sel of ['#till [data-item]', '#offline [data-o-ring]', '#offline [data-o-switch]', '#what [data-wd-tab="1"]']) {
    await centreOn(page, sel.split(' ')[0]);
    await settled(page, sel);
    const btn = page.locator(sel).first();
    const scale = () => btn.evaluate((el) => getComputedStyle(el).scale);
    expect(await scale(), `${sel} at rest`).toBe('1');
    const box = (await btn.boundingBox())!;
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    await page.mouse.down();
    // 170ms: past the 120ms press transition, well inside the 480ms glow. 220ms lost the glow when the machine was loaded.
    await page.waitForTimeout(170);
    expect(await scale(), `${sel} pressed`).toBe('0.97');
    const glow = await page.evaluate(() => { const g = document.querySelector('.tap-glow'); return g ? getComputedStyle(g).backgroundImage : ''; });
    expect(glow, `${sel} glow`).toContain('radial-gradient');
    await page.mouse.up();
    await page.waitForTimeout(300);
    expect(await scale(), `${sel} released`).toBe('1');
    await page.waitForTimeout(300);
  }
});

test('reduced motion: demo buttons do not scale and no glow appears', async ({ browser }) => {
  const ctx = await browser.newContext({ reducedMotion: 'reduce', viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  await page.goto('/en/');
  await page.waitForLoadState('load');
  await centreOn(page, '#till');
  await page.waitForTimeout(300);
  await settled(page, '#till .lt-till-wrap');
  const btn = page.locator('#till [data-item]').first();
  const box = (await btn.boundingBox())!;
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await page.waitForTimeout(250);
  expect(await btn.evaluate((el) => getComputedStyle(el).scale)).toBe('none');
  expect(await page.locator('.tap-glow').count()).toBe(0);
  await page.mouse.up();
  await ctx.close();
});

// Copy rules for everything new: a "sale done" banner (never a receipt), no drawer or printer claim, each language
// alone in its page (the /ar Latin-word sweep lives in motion.spec.ts), prices only as marked examples.
test('copy: banner not receipt, no drawer or printer claim, one language per page, example prices are footnoted', async ({ page }) => {
  for (const [lang, banner, foot] of [['ar', 'تمت البيعة', 'الأسعار في هذا المثال توضيحية'], ['en', 'Sale done', 'Prices in this example are for illustration']] as const) {
    await page.goto(`/${lang}/`);
    await page.waitForLoadState('load');
    // The sale-done banner is hidden until a sale, so innerText cannot see it: read its textContent.
    const text = await page.evaluate(() => ['#till', '#offline', '#what'].map((s) => (document.querySelector(s) as HTMLElement).innerText + ' ' + Array.from(document.querySelectorAll(`${s} img`)).map((i) => i.getAttribute('alt')).join(' ')).join('\n') + '\n' + document.querySelector('#till .lt-done')!.textContent);
    expect(text, `${lang} receipt/drawer/printer`).not.toMatch(/receipt|drawer|printer|إيصال|درج|طابعة|طباعة/i);
    expect(text, `${lang} banner`).toContain(banner);
    expect(text, `${lang} footnote`).toContain(foot);
    if (lang === 'en') expect(text, 'no Arabic letters on /en').not.toMatch(/[؀-ۿ]/);
  }
});
