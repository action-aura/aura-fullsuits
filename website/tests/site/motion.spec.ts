import { test, expect, type Page } from '@playwright/test';

// E1 (2026-10-23): crisp scroll motion. These guard the NEW behaviour: no blur anywhere, world colour changes in a short
// band and is pure the rest of the time, the jewel steps aside on phones, the tap press state, reduced motion.

const scrollTo = (p: Page, y: number): Promise<void> => p.evaluate((y) => window.scrollTo({ top: y, behavior: 'instant' as ScrollBehavior }), y);
const maxScroll = (p: Page): Promise<number> => p.evaluate(() => document.documentElement.scrollHeight - innerHeight);
const centreOn = (p: Page, sel: string): Promise<void> =>
  p.evaluate((sel) => {
    const r = document.querySelector(sel)!.getBoundingClientRect();
    window.scrollTo({ top: window.scrollY + r.top + r.height / 2 - window.innerHeight / 2, behavior: 'instant' as ScrollBehavior });
  }, sel);

// Every element with a CSS blur: filter or backdrop-filter other than none. Includes ::before/::after.
const blurred = (p: Page): Promise<string[]> =>
  p.evaluate(() => {
    const out: string[] = [];
    for (const el of Array.from(document.querySelectorAll('body, body *'))) {
      for (const pseudo of [null, '::before', '::after']) {
        const cs = getComputedStyle(el, pseudo);
        const f = `${cs.filter} ${cs.getPropertyValue('backdrop-filter')}`;
        if (/blur/.test(f)) out.push(`${el.tagName.toLowerCase()}.${(el as HTMLElement).className}${pseudo ?? ''}: ${f}`);
      }
    }
    return out;
  });

for (const [name, vp, mobile] of [['desktop 1440', { width: 1440, height: 900 }, false], ['phone 390', { width: 390, height: 844 }, true]] as const) {
  for (const url of ['/ar/', '/en/', '/ar/aura-retail/']) {
    test(`no element has a CSS blur at any scroll position: ${url} ${name}`, async ({ browser }) => {
      const ctx = await browser.newContext({ viewport: vp, isMobile: mobile, hasTouch: mobile });
      const page = await ctx.newPage();
      await page.goto(url);
      await page.waitForLoadState('load');
      const max = await maxScroll(page);
      for (let i = 0; i <= 12; i++) {
        await scrollTo(page, Math.round((max * i) / 12));
        await page.waitForTimeout(110); // mid-reveal on purpose: the old blur was only on while the reveal was in flight
        expect(await blurred(page), `blur at ${i}/12`).toEqual([]);
      }
      await ctx.close();
    });
  }
}

// The mark's box on phones, from the vars the Jewel script sets: still frame is 152.4vw * --js wide, the mark itself is
// .95vw * --js wide and 9.14/7.66 times that tall, its apex at --jay (components.css, stations.ts phoneHeroPose).
type Box = { left: number; top: number; right: number; bottom: number };
const phoneMark = (p: Page, pad = 12): Promise<{ box: Box; opacity: number }> =>
  p.evaluate((pad) => {
    const cs = getComputedStyle(document.documentElement);
    const v = (k: string) => parseFloat(cs.getPropertyValue(k));
    const w = 0.95 * innerWidth * v('--js'), h = (9.14 / 7.66) * w, apex = v('--jay') * innerHeight, cx = innerWidth / 2;
    return {
      box: { left: cx - w / 2 - pad, top: apex - pad, right: cx + w / 2 + pad, bottom: apex + h + pad },
      opacity: parseFloat(getComputedStyle(document.querySelector('.jewel')!).opacity),
    };
  }, pad);

// Visible text boxes of the page's copy: tight text ranges for text blocks, the element box for buttons and pills.
const textBoxes = (p: Page): Promise<{ what: string; box: Box }[]> =>
  p.evaluate(() => {
    const out: { what: string; box: Box }[] = [];
    const eff = (el: Element) => { let o = 1; for (let e: Element | null = el; e; e = e.parentElement) o *= parseFloat(getComputedStyle(e).opacity); return o; };
    for (const el of Array.from(document.querySelectorAll('main h1, main h2, main .lead, main p, main li, main .chapter-link, main .status-pill, main .ctas a'))) {
      if (eff(el) < 0.1) continue;
      const isBtn = el.matches('.chapter-link, .status-pill, .ctas a');
      let r: DOMRect;
      if (isBtn) r = el.getBoundingClientRect();
      else { const rg = document.createRange(); rg.selectNodeContents(el); r = rg.getBoundingClientRect(); }
      if (r.width < 2 || r.height < 2 || r.bottom < 0 || r.top > innerHeight) continue;
      out.push({ what: `${el.tagName.toLowerCase()}.${(el as HTMLElement).className} "${(el.textContent ?? '').trim().slice(0, 24)}"`, box: { left: r.left, top: r.top, right: r.right, bottom: r.bottom } });
    }
    return out;
  });
const hits = (a: Box, b: Box): boolean => !(a.right <= b.left || a.left >= b.right || a.bottom <= b.top || a.top >= b.bottom);

for (const url of ['/ar/', '/en/']) {
  test(`phone 390: the jewel never overlaps chapter text; the whole hero mark is visible: ${url}`, async ({ browser }) => {
    test.setTimeout(90_000);
    const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
    const page = await ctx.newPage();
    await page.goto(url);
    await page.waitForLoadState('load');
    await page.waitForTimeout(700);

    // Hero: the mark is shown in full (opaque, inside the viewport) and sits below every piece of hero copy.
    const hero = await phoneMark(page);
    expect(hero.opacity, 'hero mark opacity').toBeGreaterThan(0.95);
    expect(hero.box.bottom - 12, 'mark bottom is inside the viewport').toBeLessThanOrEqual(844);
    expect(hero.box.top + 12, 'mark apex is below the strip and nav').toBeGreaterThan(102);
    for (const t of await textBoxes(page)) expect(hits(hero.box, t.box), `hero: mark ${JSON.stringify(hero.box)} meets ${t.what} ${JSON.stringify(t.box)}`).toBe(false);

    // Then sample the whole page: wherever the mark is still visible at all, nothing of the copy is under it.
    const max = await maxScroll(page);
    let sampled = 0;
    for (let y = 0; y <= max; y += 90) {
      await scrollTo(page, y);
      await page.waitForTimeout(330); // the .2s opacity ease on .jewel, plus the reveals
      const m = await phoneMark(page);
      sampled++;
      if (m.opacity < 0.05) continue;
      for (const t of await textBoxes(page)) {
        expect(hits(m.box, t.box), `y=${y} opacity ${m.opacity}: mark ${JSON.stringify(m.box)} meets ${t.what} ${JSON.stringify(t.box)}`).toBe(false);
      }
    }
    expect(sampled).toBeGreaterThan(10);
    // Past the hero the mark is gone altogether (not just clear of the text).
    await centreOn(page, '#retail');
    await page.waitForTimeout(500);
    expect((await phoneMark(page)).opacity).toBe(0);
    await ctx.close();
  });
}

// Desktop: the mark never meets a chapter's copy either. Same box maths as behaviour.spec.ts markBox (the vars place the
// mark; 58vh * --js tall, 7.66/9.14 as wide), checked at each chapter's centre where the copy is fully in.
test('desktop 1440: the small jewel in a chapter sits on the side opposite the copy and never meets it', async ({ browser }) => {
  test.setTimeout(90_000);
  for (const lang of ['en', 'ar']) {
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    const page = await ctx.newPage();
    await page.goto(`/${lang}/`);
    await page.waitForLoadState('load');
    await page.waitForTimeout(800);
    for (const id of ['aura', 'retail', 'clinic', 'gym', 'talk']) {
      await centreOn(page, `#${id}`);
      await page.waitForTimeout(900);
      const r = await page.evaluate((id) => {
        const cs = getComputedStyle(document.documentElement);
        const v = (k: string) => parseFloat(cs.getPropertyValue(k));
        const h = 0.58 * innerHeight * v('--js'), w = (7.66 / 9.14) * h, cx = v('--jx') * innerWidth, cy = v('--jy') * innerHeight, pad = 40;
        const mark = { left: cx - w / 2 - pad, top: cy - h / 2 - pad, right: cx + w / 2 + pad, bottom: cy + h / 2 + pad };
        const rg = document.createRange(); rg.selectNodeContents(document.querySelector(`#${id} .copy`)!);
        const c = rg.getBoundingClientRect();
        return { mark, copy: { left: c.left, top: c.top, right: c.right, bottom: c.bottom }, scale: v('--js') };
      }, id);
      expect(hits(r.mark, r.copy), `${lang} #${id}: mark ${JSON.stringify(r.mark)} meets copy ${JSON.stringify(r.copy)}`).toBe(false);
      if (id !== 'aura' && id !== 'talk') expect(r.scale, `${lang} #${id} scale`).toBeLessThanOrEqual(0.65);
    }
    await ctx.close();
  }
});

// World colour: the field at the middle of a chapter is exactly that world's token, not a blend; and across the page the
// blended stretches are short.
const hexToRgb = (hex: string): string => {
  const n = parseInt(hex.replace('#', ''), 16);
  return `rgb(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255})`;
};
test('world field at mid-chapter equals that world token, with no other world showing', async ({ page }) => {
  test.setTimeout(90_000);
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/en/');
  await page.waitForLoadState('load');
  await page.waitForTimeout(600);
  for (const w of ['master', 'retail', 'clinic', 'gym'] as const) {
    await centreOn(page, w === 'master' ? '#aura' : `#${w}`);
    await page.waitForTimeout(700);
    const r = await page.evaluate((w) => {
      const root = document.documentElement, cs = getComputedStyle(root);
      const layer = (k: string) => getComputedStyle(document.querySelector(`.bg-${k}`)!);
      return {
        dataWorld: root.dataset.world,
        fieldB: cs.getPropertyValue('--field-b').trim(),
        own: { opacity: layer(w).opacity, image: layer(w).backgroundImage },
        others: (['master', 'retail', 'clinic', 'gym'] as const).filter((k) => k !== w).map((k) => ({ k, opacity: layer(k).opacity, off: document.querySelector(`.bg-${k}`)!.hasAttribute('data-off') })),
      };
    }, w);
    expect(r.dataWorld).toBe(w);
    expect(r.own.opacity, `${w} layer opacity`).toBe('1');
    expect(r.own.image, `${w} gradient carries the token ${r.fieldB}`).toContain(hexToRgb(r.fieldB));
    for (const o of r.others) expect(parseFloat(o.opacity) === 0 || o.off, `${w}: ${o.k} still showing (${o.opacity})`).toBe(true);
  }
});

test('world blends are short: no stretch of scroll longer than a quarter viewport shows two worlds at once', async ({ page }) => {
  // E2: the home page is about 4000px longer (three demos), so this 15px sweep has about 60% more samples: budget raised
  // from 120s to 300s. The sweep and every assertion are unchanged.
  test.setTimeout(300_000);
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/en/');
  await page.waitForLoadState('load');
  await page.waitForTimeout(600);
  const max = await maxScroll(page);
  let run = 0, longest = 0, total = 0, boundaries = 0;
  for (let y = 0; y <= max; y += 15) {
    await scrollTo(page, y);
    await page.waitForTimeout(40);
    const shares = await page.evaluate(() => ['master', 'retail', 'clinic', 'gym'].map((w) => parseFloat(document.documentElement.style.getPropertyValue(`--w-${w}`))));
    const blended = shares.filter((s) => s > 0.02 && s < 0.98).length >= 2;
    if (blended) { run += 15; total += 15; } else { if (run > 0) boundaries++; longest = Math.max(longest, run); run = 0; }
  }
  expect(boundaries, 'the home has several world changes').toBeGreaterThanOrEqual(4);
  expect(longest, `longest blended stretch ${longest}px`).toBeLessThanOrEqual(0.25 * 900);
  expect(total).toBeLessThanOrEqual(boundaries * 0.25 * 900);
});

// /ar is Arabic only. Allowed Latin: the English language link, the email, product/vendor proper names that have no
// Arabic form on a legal or FAQ page (Cloudflare, Turnstile, Meta, CSV, hash, IP), the 07X phone placeholder.
// Also allowed: X and Z (the names of the two standard till reports) and the 07X XXX XXXX phone placeholder pieces.
const ALLOWED = /^(english|cloudflare|turnstile|meta|csv|hash|ip|x+|z|07x|info@actionaura\.me)$/i;
for (const slug of ['', 'aura-retail/', 'aura-clinic/', 'about/', 'contact/', 'faq/', 'privacy/', 'terms/']) {
  test(`/ar/${slug} shows no Latin words beyond the allowed list`, async ({ page }) => {
    await page.goto(`/ar/${slug}`);
    await page.waitForLoadState('load');
    // The honeypot field (.hp) is aria-hidden and off-screen: not visible text.
    const text = await page.evaluate(() => { const c = document.body.cloneNode(true) as HTMLElement; c.querySelectorAll('.hp, script, style, noscript').forEach((e) => e.remove()); return c.textContent ?? ''; }) + '\n' + await page.evaluate(() => '' + Array.from(document.querySelectorAll('input[placeholder], textarea[placeholder]')).map((e) => e.getAttribute('placeholder')).join('\n'));
    const words = (text.match(/[A-Za-z0-9][A-Za-z0-9@._'-]*[A-Za-z0-9]|[A-Za-z]/g) ?? []).filter((w) => /[A-Za-z]/.test(w) && !ALLOWED.test(w));
    expect([...new Set(words)], 'Latin words on the Arabic page').toEqual([]);
  });
}

test('/en home has no Arabic second-language line and single-language status pills', async ({ page }) => {
  await page.goto('/en/');
  expect(await page.locator('.ar-title').count()).toBe(0);
  const pills = await page.locator('.status-pill').allInnerTexts();
  expect(pills.length).toBeGreaterThan(0);
  for (const t of pills) expect(t, 'pill text').not.toMatch(/[؀-ۿ]/);
  await page.goto('/ar/');
  for (const t of await page.locator('.status-pill').allInnerTexts()) expect(t, 'pill text').not.toMatch(/[A-Za-z]/);
});

// Phone strip + anchors.
test('phone 390: the strip shows city, phone and WhatsApp, is 36 to 40px, scrolls away, and the nav settles at the top', async ({ browser }) => {
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
  const page = await ctx.newPage();
  for (const [lang, city, wa] of [['ar', 'عمّان، الأردن', 'واتساب'], ['en', 'Amman, Jordan', 'WhatsApp']]) {
    await page.goto(`/${lang}/`);
    await page.waitForLoadState('load');
    const s = page.locator('.phone-strip');
    await expect(s).toBeVisible();
    const box = (await s.boundingBox())!;
    expect(box.height).toBeGreaterThanOrEqual(36);
    expect(box.height).toBeLessThanOrEqual(40);
    expect(box.y).toBe(0);
    await expect(s).toContainText(city);
    await expect(s.locator('a[href="tel:+962780069485"]')).toContainText('+962 78 006 9485');
    await expect(s.locator('a[href^="https://wa.me/962780069485"]')).toContainText(wa);
    expect(await s.evaluate((el) => getComputedStyle(el).backgroundColor)).toBe('rgb(11, 19, 34)');
    expect(await s.evaluate((el) => getComputedStyle(el).position)).not.toBe('fixed');
    expect(await page.locator('.nav').evaluate((el) => el.getBoundingClientRect().top)).toBe(38);
    await scrollTo(page, 400);
    await page.waitForTimeout(150);
    expect((await s.boundingBox())!.y + (await s.boundingBox())!.height, 'strip is off screen').toBeLessThanOrEqual(0);
    expect(await page.locator('.nav').evaluate((el) => el.getBoundingClientRect().top)).toBe(0);
  }
  // desktop: no strip
  await ctx.close();
});

test('desktop 1440: no phone strip', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/ar/');
  await expect(page.locator('.phone-strip')).toBeHidden();
});

test('phone 390: an anchor jump (#retail) lands with its title clear of the nav', async ({ browser }) => {
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
  const page = await ctx.newPage();
  await page.goto('/ar/#retail');
  await page.waitForLoadState('load');
  await page.waitForTimeout(1200);
  const top = await page.evaluate(() => document.querySelector('#retail h2')!.getBoundingClientRect().top);
  expect(top).toBeGreaterThan(72);
  await ctx.close();
});

// Tap: the press state.
test('tap: a button scales to .97 on pointerdown and shows a glow in the world accent; released, it returns', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/en/');
  await page.waitForLoadState('load');
  await page.waitForTimeout(500);
  const btn = page.locator('.hero .wa-pill');
  const scale = () => btn.evaluate((el) => getComputedStyle(el).scale);
  expect(await scale(), 'at rest').toBe('1');
  const box = (await btn.boundingBox())!;
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await page.waitForTimeout(90);
  // the glow lasts 480ms: it is still there 90ms after the press
  const glow = await page.evaluate(() => {
    const g = document.querySelector('.tap-glow');
    return g ? { has: true, bg: getComputedStyle(g).backgroundImage, pos: getComputedStyle(g).position } : { has: false, bg: '', pos: '' };
  });
  expect(glow.has, 'tap glow exists on pointerdown').toBe(true);
  expect(glow.bg).toContain('radial-gradient');
  expect(glow.pos).toBe('fixed');
  await page.waitForTimeout(200);
  expect(await scale(), 'pressed').toBe('0.97');
  await page.mouse.up();
  await page.waitForTimeout(260);
  expect(await scale(), 'released').toBe('1');
});

test('tap: the press transition is 120ms ease-out and focus rings stay', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/en/');
  const tr = await page.locator('.hero .wa-pill').evaluate((el) => { const cs = getComputedStyle(el); return { d: cs.transitionDuration, p: cs.transitionProperty }; });
  expect(tr.p).toContain('scale');
  expect(tr.d).toContain('0.12s');
  await page.keyboard.press('Tab'); await page.keyboard.press('Tab');
  await page.locator('.hero .wa-pill').focus();
  const ring = await page.locator('.hero .wa-pill').evaluate((el) => getComputedStyle(el).outlineStyle);
  expect(ring).not.toBe('none');
});

// Reduced motion: final states, nothing moves.
test('reduced motion: copy is in its final state at once, nothing transitions movement, no glow', async ({ browser }) => {
  test.setTimeout(90_000);
  const ctx = await browser.newContext({ reducedMotion: 'reduce', viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  await page.goto('/en/');
  await page.waitForLoadState('load');
  const states = await page.evaluate(() => Array.from(document.querySelectorAll('.reveal > *')).map((el) => { const cs = getComputedStyle(el); return { o: cs.opacity, t: cs.transform, f: cs.filter }; }));
  expect(states.length).toBeGreaterThan(3);
  for (const s of states) { expect(s.o).toBe('1'); expect(s.t).toBe('none'); expect(s.f).toBe('none'); }
  const moving = () => page.evaluate(() => document.getAnimations()
    .filter((a) => a instanceof CSSTransition && a.playState === 'running' && /transform|scale|translate|filter|rotate/.test((a as CSSTransition).transitionProperty))
    .map((a) => (a as CSSTransition).transitionProperty));
  const max = await maxScroll(page);
  for (let i = 0; i <= 8; i++) {
    await scrollTo(page, Math.round((max * i) / 8));
    await page.waitForTimeout(80);
    expect(await moving(), `movement transition at ${i}/8`).toEqual([]);
  }
  await centreOn(page, '#retail');
  const btn = page.locator('.hero .wa-pill');
  await scrollTo(page, 0);
  const box = (await btn.boundingBox())!;
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await page.waitForTimeout(250);
  expect(await btn.evaluate((el) => getComputedStyle(el).scale)).toBe('none');
  expect(await page.locator('.tap-glow').count()).toBe(0);
  await page.mouse.up();
  await ctx.close();
});
