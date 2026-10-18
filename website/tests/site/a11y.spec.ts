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

      type Stop = { id: string; tag: string; href: string; visible: boolean; desc: string };
      const stops: Stop[] = [];
      for (let i = 0; i < 60; i++) {
        await page.keyboard.press('Tab');
        const stop = await page.evaluate((): Stop => {
          const el = document.activeElement as HTMLElement;
          const cs = getComputedStyle(el);
          const outlineOk = cs.outlineStyle !== 'none' && cs.outlineWidth !== '0px';
          const shadowOk = cs.boxShadow !== 'none';
          return {
            id: el.id,
            tag: el.tagName.toLowerCase(),
            href: el.getAttribute('href') ?? '',
            visible: outlineOk || shadowOk,
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

      const invisible = stops.filter((s) => !s.visible).map((s) => s.desc);
      expect(invisible, `stops without a visible focus indicator:\n${invisible.join('\n')}`).toEqual([]);
    });
  }
});
