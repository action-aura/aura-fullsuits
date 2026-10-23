import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import { readdirSync, statSync, existsSync } from 'node:fs';
import { join, resolve } from 'node:path';

test.use({
  launchOptions: { args: ['--use-gl=swiftshader', '--enable-webgl', '--ignore-gpu-blocklist'] },
});

const DIST = resolve(process.cwd(), 'dist');

// Pages come from the built dist, same source of truth as the crawl, so a new page is covered automatically.
function listPages(): string[] {
  const out: string[] = [];
  const walk = (dir: string) => {
    for (const name of readdirSync(dir)) {
      const full = join(dir, name);
      if (statSync(full).isDirectory()) walk(full);
      else if (name === 'index.html') {
        const rel = full.slice(DIST.length).replace(/\\/g, '/').replace(/index\.html$/, '');
        if (rel.startsWith('/ar/') || rel.startsWith('/en/')) out.push(rel);
      }
    }
  };
  if (existsSync(DIST)) walk(DIST);
  return out.sort();
}

const PAGES = listPages();
const VIEWPORTS = [390, 1440];
const TAGS = ['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa'];

test('dist pages were found', () => {
  expect(PAGES.length, 'run `npm run build` first').toBeGreaterThanOrEqual(16);
});

test.describe('axe', () => {
  for (const p of PAGES) {
    for (const w of VIEWPORTS) {
      test(`${p} @${w} has no axe violations`, async ({ page }) => {
        test.setTimeout(60_000);
        await page.setViewportSize({ width: w, height: 900 });
        await page.goto(p);
        await page.waitForLoadState('load');
        // Measure the settled page: at 'load' the copy reveal (opacity) and the world layers are mid-transition,
        // and axe misreads half-faded text against a half-faded field. Same wait as shots.spec.
        await page.evaluate(() => Promise.race([
          Promise.all(document.getAnimations()
            .filter((a) => a.effect?.getTiming().iterations !== Infinity)
            .map((a) => a.finished.catch(() => undefined))),
          new Promise((r) => setTimeout(r, 10_000)),
        ]));
        // Layers that fade out keep visibility for .6s (transition delay), which getAnimations does not report.
        await page.waitForTimeout(1000);
        const results = await new AxeBuilder({ page })
          .withTags(TAGS)
          .exclude('.jewel')
          .exclude('iframe[src*="challenges.cloudflare.com"]')
          .analyze();
        const readable = results.violations.map(
          (v) =>
            `${v.id} [${v.impact}] ${v.help}\n` +
            v.nodes.map((n) => {
              const d = (n.any[0]?.data ?? {}) as { fgColor?: string; bgColor?: string; contrastRatio?: number };
              return `    ${n.target.join(' ')}${d.fgColor ? `  fg ${d.fgColor} bg ${d.bgColor} ${d.contrastRatio}:1` : ''}`;
            }).join('\n'),
        );
        expect(readable, `${p} @${w}:\n${readable.join('\n')}`).toEqual([]);
      });
    }
  }
});

const FIELD_IDS = ['demo-name', 'demo-phone', 'demo-type', 'demo-business', 'demo-branches', 'demo-message', 'demo-send'];

test.describe('keyboard', () => {
  for (const p of ['/en/contact/', '/ar/contact/']) {
    test(`${p} tab order, skip link first, visible focus at every stop`, async ({ page }) => {
      test.setTimeout(60_000);
      await page.setViewportSize({ width: 1440, height: 900 });
      await page.goto(p);
      await page.waitForLoadState('load');

      type Stop = { id: string; tag: string; href: string; visible: boolean; detail: string; desc: string; inCf: boolean; isBody: boolean };
      const stops: Stop[] = [];
      for (let i = 0; i < 60; i++) {
        await page.keyboard.press('Tab');
        const stop = await page.evaluate((): Stop => {
          const el = document.activeElement as HTMLElement;
          const read = () => {
            const cs = getComputedStyle(el);
            return { style: cs.outlineStyle, width: cs.outlineWidth, shadow: cs.boxShadow };
          };
          const focused = read();
          // Cloudflare's Turnstile widget (iframe + wrapper divs, none of them ours) takes several Tab stops between the
          // message box and the send button. Its elements cannot take focus back after blur(), so the blur/focus probe
          // below dropped focus to <body> and the next Tab restarted from the skip link: the loop never got past the
          // widget and "demo-send reached by Tab" failed although a real keyboard walk reaches it in ~24 stops
          // (measured 2026-10-23). While focus is inside Turnstile's cross-origin frame the parent reports <body>
          // (measured on /ar/contact/: 4 body stops, then 3 .cf-turnstile divs). The probe is skipped for those stops
          // only; every stop of ours is still probed. A <body> stop is excused after the walk ONLY when it sits in one
          // unbroken run with a .cf-turnstile stop; anywhere else focus on <body> is a real focus loss and fails.
          const inCf = !!el.closest('.cf-turnstile');
          const isBody = el === document.body;
          const thirdParty = inCf || isBody;
          let resting = focused;
          if (!thirdParty) {
            el.blur();
            resting = read();
            el.focus();
          }
          const outlineOk = focused.style !== 'none' && focused.width !== '0px';
          const shadowOk = focused.shadow !== resting.shadow;
          return {
            id: el.id,
            tag: el.tagName.toLowerCase(),
            href: el.getAttribute('href') ?? '',
            visible: inCf || outlineOk || shadowOk,
            inCf,
            isBody,
            detail: `focused[outline ${focused.style} ${focused.width}; shadow ${focused.shadow}] resting[outline ${resting.style} ${resting.width}; shadow ${resting.shadow}]`,
            desc: `${el.tagName.toLowerCase()}${el.id ? '#' + el.id : ''}${el.className ? '.' + String(el.className).split(/\s+/).join('.') : ''}`,
          };
        });
        stops.push(stop);
        if (stop.id === 'demo-send') break;
      }

      expect(stops[0].href, 'first tab stop is the skip link').toBe('#main');

      const order = FIELD_IDS.map((id) => stops.findIndex((s) => s.id === id));
      FIELD_IDS.forEach((id, i) => expect(order[i], `${id} reached by Tab`).toBeGreaterThanOrEqual(0));
      for (let i = 1; i < order.length; i++) {
        expect(order[i], `${FIELD_IDS[i]} comes after ${FIELD_IDS[i - 1]}`).toBeGreaterThan(order[i - 1]);
      }

      // Excuse <body> stops only inside a contiguous run of body/Turnstile stops that contains a Turnstile stop.
      for (let i = 0; i < stops.length; ) {
        if (!stops[i].isBody && !stops[i].inCf) { i++; continue; }
        let j = i;
        while (j < stops.length && (stops[j].isBody || stops[j].inCf)) j++;
        const cfRun = stops.slice(i, j).some((s) => s.inCf);
        if (cfRun) for (let k = i; k < j; k++) stops[k].visible = true;
        i = j;
      }
      const invisible = stops.filter((s) => !s.visible).map((s) => `${s.desc} ${s.detail}`);
      expect(invisible, `stops without a visible focus indicator:\n${invisible.join('\n')}`).toEqual([]);
    });
  }
});
