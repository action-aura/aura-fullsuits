import { test, expect, type Page } from '@playwright/test';

// E3 (2026-10-23): the Clinic booking preview (#book) and the Gym check-in preview (#checkin) on the home page, in both
// languages. Logic assertions read the numbers the page shows; the guards are mutation-proved (see the E3 commit):
// an expired member that counts as a visit, or a booking that counts twice, turns these red.

const LANGS = ['ar', 'en'] as const;
const scrollTo = (p: Page, y: number): Promise<void> => p.evaluate((y) => window.scrollTo({ top: y, behavior: 'instant' as ScrollBehavior }), y);
const centreOn = (p: Page, sel: string): Promise<void> =>
  p.evaluate((sel) => {
    const r = document.querySelector(sel)!.getBoundingClientRect();
    window.scrollTo({ top: window.scrollY + r.top + r.height / 2 - window.innerHeight / 2, behavior: 'instant' as ScrollBehavior });
  }, sel);
// The card enters over .7s; a click aimed at a card that is still rising lands on whatever is under that point.
const settled = (p: Page, sel: string): Promise<void> =>
  expect.poll(() => p.evaluate((sel) => {
    const cs = getComputedStyle(document.querySelector(sel)!.closest('section')!.querySelector('.reveal-card')!);
    return `${cs.opacity} ${cs.transform}`;
  }, sel), { message: `${sel}: card finished entering`, timeout: 15_000 }).toBe('1 none');

async function open(page: Page, lang: string, sel: string, vp = { width: 1440, height: 900 }) {
  await page.setViewportSize(vp);
  await page.goto(`/${lang}/`);
  await page.waitForLoadState('load');
  await centreOn(page, sel);
  await settled(page, `${sel} .dc`);
}
const num = async (p: Page, sel: string): Promise<number> => Number((await p.locator(sel).first().textContent())!.trim());

for (const lang of LANGS) {
  test.describe(`clinic booking ${lang}`, () => {
    test('three slots are booked and are not buttons; five are free buttons; the count starts at 3', async ({ page }) => {
      await open(page, lang, '#book');
      expect(await page.locator('#book .bk-slot').count()).toBe(8);
      expect(await page.locator('#book .bk-slot.is-booked').count()).toBe(3);
      expect(await page.locator('#book .bk-slot.is-booked button').count(), 'booked slots hold no button').toBe(0);
      expect(await page.locator('#book [data-bk-free]').count()).toBe(5);
      expect(await num(page, '#book [data-bk-count]')).toBe(3);
      // times run 10:00 to 17:30, Western digits
      const times = await page.locator('#book .bk-time').allTextContents();
      expect(times[0]).toBe('10:00');
      expect(times[7]).toBe('17:30');
      for (const t of times) expect(t).toMatch(/^[0-9]{2}:[0-9]{2}$/);
    });

    test('booking a free slot fills it and raises the count by exactly 1, once per slot', async ({ page }) => {
      await open(page, lang, '#book');
      for (let k = 0; k < 5; k++) {
        const before = await num(page, '#book [data-bk-count]');
        const free = page.locator('#book [data-bk-free]');
        expect(await free.count()).toBe(5 - k);
        // pin the slot by its time first: a locator built from the button would re-resolve to the NEXT free slot once this one fills
        const slot = await free.first().locator('xpath=ancestor::li').getAttribute('data-slot');
        const li = page.locator(`#book [data-slot="${slot}"]`);
        await free.first().click();
        await expect(li).toHaveClass(/is-booked/);
        expect(await li.locator('button').count(), 'the filled slot is no longer a button').toBe(0);
        expect(await num(page, '#book [data-bk-count]'), `booking ${k + 1}`).toBe(before + 1);
        const live = (await page.locator('[data-bk-live]').textContent())!;
        expect(live, 'aria-live announces the booking with the new count').toContain(String(before + 1));
        // the side file shows the patient just booked
        const filled = (await li.locator('.bk-fill b').textContent())!;
        expect(await page.locator('[data-bk-name]').textContent()).toBe(filled);
        expect(await page.locator('#book .dc .bk-slot.is-booked').count()).toBe(4 + k);
      }
      expect(await page.locator('#book [data-bk-free]').count()).toBe(0);
      expect(await num(page, '#book [data-bk-count]')).toBe(8);
    });

    test('keyboard: Enter and Space book slots, focus stays in the list, aria-live speaks', async ({ page }) => {
      await open(page, lang, '#book');
      const first = page.locator('#book [data-bk-free]').first();
      await first.focus();
      await expect(first).toBeFocused();
      await page.keyboard.press('Enter');
      expect(await num(page, '#book [data-bk-count]')).toBe(4);
      // focus moved to the filled slot, so Tab goes on to the next free slot
      await page.keyboard.press('Tab');
      const next = page.locator('#book [data-bk-free]:focus');
      await expect(next).toHaveCount(1);
      await page.keyboard.press('Space');
      expect(await num(page, '#book [data-bk-count]')).toBe(5);
      expect(((await page.locator('[data-bk-live]').textContent()) ?? '').length).toBeGreaterThan(5);
    });

    test('the day card names today in the page language, with Western digits everywhere', async ({ page }) => {
      await open(page, lang, '#book');
      const wd = new Date().toLocaleDateString(lang === 'ar' ? 'ar-u-nu-latn' : 'en', { weekday: 'long' });
      const day = (await page.locator('[data-bk-day]').textContent())!;
      expect(day).toContain(wd);
      expect(day).toContain(lang === 'ar' ? 'اليوم' : 'Today');
      expect(await page.locator('#book').innerText()).not.toMatch(/[٠-٩]/);
    });

    test('reduced motion: booking is instant, no animation classes', async ({ browser }) => {
      const ctx = await browser.newContext({ reducedMotion: 'reduce', viewport: { width: 1440, height: 900 } });
      const page = await ctx.newPage();
      await page.goto(`/${lang}/`);
      await page.waitForLoadState('load');
      await centreOn(page, '#book');
      await page.waitForTimeout(300);
      await page.locator('#book [data-bk-free]').first().click();
      // no wait: every number is final in the same task as the click
      expect(await num(page, '#book [data-bk-count]')).toBe(4);
      const r = await page.evaluate(() => ({
        fill: document.querySelectorAll('#book .bk-in').length,
        bump: document.querySelectorAll('#book .dc-bump').length,
        swap: document.querySelectorAll('#book .bk-swap').length,
        anim: getComputedStyle(document.querySelector('#book .bk-fill:not([data-patient])') ?? document.body).animationName,
      }));
      expect(r.fill + r.bump + r.swap).toBe(0);
      expect(r.anim).toBe('none');
      await ctx.close();
    });
  });

  test.describe(`gym check-in ${lang}`, () => {
    const cards = (p: Page) => p.locator('#checkin [data-member]');
    const door = (p: Page) => p.locator('[data-ck-door]');

    test('three member cards; the preview says it is coming soon; no prices', async ({ page }) => {
      await open(page, lang, '#checkin');
      expect(await cards(page).count()).toBe(3);
      await expect(page.locator('#checkin .dc-soon')).toHaveText(lang === 'ar' ? 'قريبًا' : 'Coming soon');
      expect(await num(page, '#checkin [data-ck-visits]')).toBe(0);
      expect(await page.locator('#checkin').innerText()).not.toMatch(/JOD|USD|\$|د\.أ|دينار|price|سعر/i);
    });

    test('valid cards welcome the member with the right days and raise visits by 1; the expired card is rose and raises nothing', async ({ page }) => {
      await open(page, lang, '#checkin');
      const names = lang === 'ar' ? ['عمر', 'دانة', 'كريم'] : ['Omar', 'Dana', 'Karim'];
      const days = lang === 'ar' ? ['باقي 12 يوم', 'باقي 3 أيام'] : ['12 days left', '3 days left'];
      const welcome = (n: string, d: string) => (lang === 'ar' ? `أهلين يا ${n} · ${d}` : `Welcome, ${n} · ${d}`);
      const expired = lang === 'ar' ? 'الاشتراك منتهي · جدّد عند الاستقبال' : 'Subscription expired · renew at the front desk';

      await cards(page).nth(0).click();
      await expect(door(page)).toHaveAttribute('data-state', 'ok');
      expect(await page.locator('[data-ck-msg]').textContent()).toBe(welcome(names[0], days[0]));
      expect(await num(page, '[data-ck-visits]')).toBe(1);

      await cards(page).nth(1).click();
      expect(await page.locator('[data-ck-msg]').textContent()).toBe(welcome(names[1], days[1]));
      expect(await num(page, '[data-ck-visits]')).toBe(2);

      await cards(page).nth(2).click();
      await expect(door(page)).toHaveAttribute('data-state', 'expired');
      expect(await page.locator('[data-ck-msg]').textContent()).toBe(expired);
      expect(await num(page, '[data-ck-visits]'), 'expired member must NOT count as a visit').toBe(2);
      await cards(page).nth(2).click();
      expect(await num(page, '[data-ck-visits]'), 'a second expired tap still counts nothing').toBe(2);
      expect(((await page.locator('[data-ck-live]').textContent()) ?? '')).toBe(expired);

      await cards(page).nth(0).click();
      expect(await num(page, '[data-ck-visits]'), 'a valid tap after an expired one counts again').toBe(3);
      // green on valid, rose on expired: the door colours differ and neither is the idle colour
      const col = async () => door(page).evaluate((el) => getComputedStyle(el).color);
      const okCol = await col();
      await cards(page).nth(2).click();
      await page.waitForTimeout(350);
      expect(await col()).not.toBe(okCol);
    });

    test('keyboard: Tab reaches each card, Enter and Space check in, focus ring is visible', async ({ page }) => {
      await open(page, lang, '#checkin');
      await cards(page).nth(0).focus();
      await page.keyboard.press('Enter');
      expect(await num(page, '[data-ck-visits]')).toBe(1);
      await page.keyboard.press('Tab');
      await expect(cards(page).nth(1)).toBeFocused();
      const outline = await cards(page).nth(1).evaluate((el) => getComputedStyle(el).outlineStyle + ' ' + getComputedStyle(el).outlineWidth);
      expect(outline).toBe('solid 2px');
      await page.keyboard.press('Space');
      expect(await num(page, '[data-ck-visits]')).toBe(2);
      await page.keyboard.press('Tab');
      await expect(cards(page).nth(2)).toBeFocused();
      await page.keyboard.press('Enter');
      expect(await num(page, '[data-ck-visits]'), 'expired by keyboard counts nothing').toBe(2);
    });

    test('reduced motion: the result is final in the same task as the tap, no animation', async ({ browser }) => {
      const ctx = await browser.newContext({ reducedMotion: 'reduce', viewport: { width: 1440, height: 900 } });
      const page = await ctx.newPage();
      await page.goto(`/${lang}/`);
      await page.waitForLoadState('load');
      await centreOn(page, '#checkin');
      await page.waitForTimeout(300);
      await cards(page).nth(0).click();
      expect(await num(page, '[data-ck-visits]')).toBe(1);
      expect(await page.evaluate(() => document.querySelectorAll('#checkin .dc-bump, #checkin .ck-flip').length)).toBe(0);
      await ctx.close();
    });
  });

  // Copy rules for everything new.
  test(`copy ${lang}: no "!", no em-dash, no reminder or message claims, one language, Western digits`, async ({ page }) => {
    await page.goto(`/${lang}/`);
    await page.waitForLoadState('load');
    const text = await page.evaluate(() => ['#book', '#checkin'].map((s) => document.querySelector<HTMLElement>(s)!.textContent + ' ' + Array.from(document.querySelectorAll(`${s} [aria-label]`)).map((e) => e.getAttribute('aria-label')).join(' ')).join('\n'));
    expect(text).not.toMatch(/!|—|–/);
    expect(text).not.toMatch(/remind|sms|whatsapp|notif|text message|تذكير|رسائل|رسالة|واتساب|إشعار/i);
    expect(text).not.toMatch(/[٠-٩]/);
    if (lang === 'en') expect(text, 'no Arabic letters on /en').not.toMatch(/[؀-ۿ]/);
    else expect(text, 'no Latin letters on /ar').not.toMatch(/[A-Za-z]/);
    expect(text).toContain(lang === 'ar' ? 'هذا عرض توضيحي.' : 'This is a demo.');
  });
}

// No horizontal scroll with the previews open (idle and after interaction).
for (const [name, vp, mobile] of [['phone 390', { width: 390, height: 844 }, true], ['desktop 1440', { width: 1440, height: 900 }, false]] as const) {
  test(`no horizontal scroll at ${name}, idle and after use, both languages`, async ({ browser }) => {
    test.setTimeout(90_000);
    for (const lang of LANGS) {
      const ctx = await browser.newContext({ viewport: vp, isMobile: mobile, hasTouch: mobile });
      const page = await ctx.newPage();
      await page.goto(`/${lang}/`);
      await page.waitForLoadState('load');
      const over = () => page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      expect(await over(), `${lang} ${name} idle`).toBeLessThanOrEqual(0);
      await centreOn(page, '#book'); await settled(page, '#book .dc');
      await page.locator('#book [data-bk-free]').first().click();
      await centreOn(page, '#checkin'); await settled(page, '#checkin .dc');
      await page.locator('#checkin [data-member]').nth(2).click();
      expect(await over(), `${lang} ${name} after use`).toBeLessThanOrEqual(0);
      // every control stays inside the viewport width
      const wide = await page.evaluate(() => Array.from(document.querySelectorAll('#book .dc *, #checkin .dc *')).filter((e) => { const r = e.getBoundingClientRect(); return r.right > innerWidth + 1 || r.left < -1; }).length);
      expect(wide, `${lang} ${name}: elements outside the viewport`).toBe(0);
      await ctx.close();
    }
  });
}

// The field behind each preview is its own world token, and nothing else shows.
for (const [name, vp, mobile] of [['desktop 1440', { width: 1440, height: 900 }, false], ['phone 390', { width: 390, height: 844 }, true]] as const) {
  test(`the field behind the clinic and gym previews is their own world token: ${name}`, async ({ browser }) => {
    test.setTimeout(90_000);
    const want = { book: { world: 'clinic', field: '#EEF4F8', accent: '#3D7FB5' }, checkin: { world: 'gym', field: '#1A0C12', accent: '#F5A3A8' } } as const;
    for (const lang of LANGS) {
      const ctx = await browser.newContext({ viewport: vp, isMobile: mobile, hasTouch: mobile });
      const page = await ctx.newPage();
      await page.goto(`/${lang}/`);
      await page.waitForLoadState('load');
      await page.waitForTimeout(500);
      for (const id of ['book', 'checkin'] as const) {
        await centreOn(page, `#${id}`);
        const w = want[id];
        await expect.poll(() => page.evaluate((k) => `${document.documentElement.dataset.world} ${getComputedStyle(document.querySelector(`.bg-${k}`)!).opacity}`, w.world), { timeout: 20_000 }).toBe(`${w.world} 1`);
        const r = await page.evaluate((id) => {
          const sec = getComputedStyle(document.querySelector(`#${id}`)!);
          const others = (['master', 'retail', 'clinic', 'gym'] as const).map((k) => ({ k, o: getComputedStyle(document.querySelector(`.bg-${k}`)!).opacity, off: document.querySelector(`.bg-${k}`)!.hasAttribute('data-off') }));
          return { field: sec.getPropertyValue('--field-b').trim().toUpperCase(), accent: sec.getPropertyValue('--accent').trim().toUpperCase(), others };
        }, id);
        expect(r.field, `${lang} #${id}: the preview's own --field-b`).toBe(w.field);
        expect(r.accent, `${lang} #${id}: the preview's own accent`).toBe(w.accent);
        for (const o of r.others) if (o.k !== w.world) expect(parseFloat(o.o) === 0 || o.off, `${lang} #${id}: ${o.k} field still showing (${o.o})`).toBe(true);
      }
      await ctx.close();
    }
  });
}

// The jewel steps out of the way of both previews (same box maths as retail-demos.spec.ts).
type Box = { left: number; top: number; right: number; bottom: number };
const hits = (a: Box, b: Box): boolean => !(a.right <= b.left || a.left >= b.right || a.bottom <= b.top || a.top >= b.bottom);

test('desktop 1440: wherever the jewel is still visible it overlaps neither preview card nor its copy, from the clinic title to the close', async ({ browser }) => {
  test.setTimeout(300_000);
  for (const lang of LANGS) {
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    const page = await ctx.newPage();
    await page.goto(`/${lang}/`);
    await page.waitForLoadState('load');
    await page.waitForTimeout(800);
    const span = await page.evaluate(() => {
      const top = (s: string) => document.querySelector(s)!.getBoundingClientRect().top + scrollY;
      return { from: top('#clinic') + 100, to: top('#talk') + 100 };
    });
    let sampled = 0, seenCards = 0, visibleSomewhere = 0;
    for (let y = span.from; y <= span.to; y += 45) {
      await scrollTo(page, y);
      await page.waitForTimeout(260);
      const s = await page.evaluate(() => {
        const cs = getComputedStyle(document.documentElement);
        const v = (k: string) => parseFloat(cs.getPropertyValue(k));
        const h = 0.58 * innerHeight * v('--js'), w = (7.66 / 9.14) * h, cx = v('--jx') * innerWidth, cy = v('--jy') * innerHeight, pad = 40;
        const mark = { left: cx - w / 2 - pad, top: cy - h / 2 - pad, right: cx + w / 2 + pad, bottom: cy + h / 2 + pad };
        const opacity = parseFloat(getComputedStyle(document.querySelector('.jewel')!).opacity);
        const eff = (el: Element) => { let o = 1; for (let e: Element | null = el; e; e = e.parentElement) o *= parseFloat(getComputedStyle(e).opacity); return o; };
        const boxes: { what: string; box: Box }[] = [];
        for (const sel of ['#book .dc', '#checkin .dc', '#book .demo-copy > *', '#checkin .demo-copy > *']) {
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
        expect(hits(s.mark, b.box), `${lang} y=${Math.round(y)} jewel opacity ${s.opacity}: mark ${JSON.stringify(s.mark)} meets ${b.what} ${JSON.stringify(b.box)}`).toBe(false);
      }
    }
    expect(sampled, 'sampled the whole run').toBeGreaterThan(40);
    expect(seenCards, 'preview boxes were on screen in many samples').toBeGreaterThan(20);
    expect(visibleSomewhere, 'the jewel IS visible somewhere in the run').toBeGreaterThan(0);
    await ctx.close();
  }
});

// Tap: the preview controls use the site's press (tap.ts adds .is-pressed + the glow in the world accent).
test('tap: slot and member buttons scale to .97 on pointerdown, glow, and return', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/en/');
  await page.waitForLoadState('load');
  for (const sel of ['#book [data-bk-free]', '#checkin [data-member]']) {
    await centreOn(page, sel.split(' ')[0]);
    await settled(page, sel);
    const btn = page.locator(sel).first();
    const scale = () => btn.evaluate((el) => getComputedStyle(el).scale);
    expect(await scale(), `${sel} at rest`).toBe('1');
    const box = (await btn.boundingBox())!;
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    await page.mouse.down();
    await page.waitForTimeout(170); // past the 120ms press, inside the 480ms glow
    expect(await scale(), `${sel} pressed`).toBe('0.97');
    expect(await page.evaluate(() => { const g = document.querySelector('.tap-glow'); return g ? getComputedStyle(g).backgroundImage : ''; })).toContain('radial-gradient');
    await page.mouse.up();
    await page.waitForTimeout(300);
    expect(await scale(), `${sel} released`).toBe('1');
    await page.waitForTimeout(300);
  }
});

// The banner fix (E3): the "sale done" banner covers the whole bill panel, so no bill line can be half-clipped under it.
test('till banner covers the whole bill panel while it is shown (no clipped line is visible)', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/en/');
  await page.waitForLoadState('load');
  await centreOn(page, '#till');
  await settled(page, '#till .lt-till-wrap');
  for (const i of [0, 1, 2]) await page.locator('#till [data-item]').nth(i).click();
  await page.locator('#till [data-pay]').click();
  const geo = await page.evaluate(() => {
    const b = document.querySelector('#till .lt-done')!.getBoundingClientRect(), bill = document.querySelector('#till .lt-bill')!.getBoundingClientRect();
    return { dl: b.left - bill.left, dt: b.top - bill.top, dr: bill.right - b.right, db: bill.bottom - b.bottom };
  });
  for (const k of ['dl', 'dt', 'dr', 'db'] as const) expect(Math.abs(geo[k]), `banner edge ${k}`).toBeLessThanOrEqual(1);
  // the banner is opaque once shown, so lines underneath cannot show through
  await expect.poll(() => page.evaluate(() => getComputedStyle(document.querySelector('#till .lt-done')!).opacity)).toBe('1');
  expect(await page.evaluate(() => getComputedStyle(document.querySelector('#till .lt-done')!).backgroundColor)).not.toBe('rgba(0, 0, 0, 0)');
});
