import { test, expect, type ConsoleMessage } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

const SHOTS = path.resolve('shots');
const LANGS = ['ar', 'en'] as const;
const CORE_WIDTHS = [390, 1440];
const WIDE_WIDTHS = [768, 1024];
const PAGES: { slug: string; widths: number[] }[] = [
  { slug: '', widths: [...CORE_WIDTHS, ...WIDE_WIDTHS] },
  { slug: 'aura-retail', widths: [...CORE_WIDTHS, ...WIDE_WIDTHS] },
  { slug: 'aura-clinic', widths: CORE_WIDTHS },
  { slug: 'about', widths: CORE_WIDTHS },
  { slug: 'faq', widths: CORE_WIDTHS },
  { slug: 'contact', widths: [...CORE_WIDTHS, ...WIDE_WIDTHS] },
  { slug: 'privacy', widths: CORE_WIDTHS },
  { slug: 'terms', widths: CORE_WIDTHS },
];
const MOTIONS = [
  { tag: 'full', reducedMotion: 'no-preference' as const },
  { tag: 'reduce', reducedMotion: 'reduce' as const },
];

// Assets produced in Plan 3: a 404 on these is expected until then.
const EXPECTED_MISSING = ['/jewel/', '/favicon', '/apple-touch-icon', '/site.webmanifest', '/og/'];

function ignorable(msg: ConsoleMessage): boolean {
  const text = msg.text();
  if (/GL Driver Message|GPU stall|swiftshader/i.test(text)) return true;
  if (/Failed to load resource/i.test(text)) {
    const url = msg.location().url;
    if (!url) return false;
    let p: string;
    try { p = new URL(url).pathname; } catch { return false; }
    return EXPECTED_MISSING.some((prefix) => p.startsWith(prefix));
  }
  return false;
}

for (const lang of LANGS) {
  for (const pageDef of PAGES) {
    const pageName = pageDef.slug || 'home';
    const pageUrl = pageDef.slug ? `/${lang}/${pageDef.slug}/` : `/${lang}/`;
    for (const width of pageDef.widths) {
      for (const motion of MOTIONS) {
        test(`shots ${lang} ${pageName} ${width} ${motion.tag}`, async ({ browser }) => {
          test.setTimeout(300_000);
          fs.mkdirSync(SHOTS, { recursive: true });
          const ctx = await browser.newContext({
            baseURL: 'http://localhost:4321',
            viewport: { width, height: 900 },
            deviceScaleFactor: 1,
            reducedMotion: motion.reducedMotion,
          });
          const page = await ctx.newPage();
          const errors: string[] = [];
          page.on('console', (m) => {
            if (m.type() === 'error' && !ignorable(m)) errors.push(`[console] ${m.text()} @ ${m.location().url}`);
          });
          page.on('pageerror', (e) => errors.push(`[pageerror] ${e.message}`));

          await page.goto(pageUrl, { waitUntil: 'load' });
          await page.waitForTimeout(1500);

          const ids = await page.$$eval('[data-w]', (els) =>
            els.map((el, i) => el.id || `s${i}`),
          );
          expect(ids.length, `no [data-w] sections on ${pageUrl}`).toBeGreaterThan(0);

          for (let i = 0; i < ids.length; i++) {
            const base = `${lang}-${pageName}-${width}-${motion.tag}-${ids[i]}`;
            await page.evaluate((idx) => {
              const el = document.querySelectorAll('[data-w]')[idx] as HTMLElement;
              const r = el.getBoundingClientRect();
              const y = r.top + window.scrollY + r.height / 2 - window.innerHeight / 2;
              window.scrollTo({ top: Math.max(0, y), behavior: 'instant' as ScrollBehavior });
            }, i);
            await page.waitForTimeout(3000);

            await page.screenshot({ path: path.join(SHOTS, `${base}.png`) });

            const items = await page.evaluate((idx) => {
              const sec = document.querySelectorAll('[data-w]')[idx];
              const out: { kind: 'heading' | 'lead'; color: string; box: { x: number; y: number; width: number; height: number; top: number; left: number; right: number; bottom: number } }[] = [];
              sec.querySelectorAll('h1, h2, .lead').forEach((el) => {
                const r = el.getBoundingClientRect();
                const hit = r.width > 0 && r.height > 0 && r.right > 0 && r.bottom > 0 && r.left < innerWidth && r.top < innerHeight;
                if (!hit) return;
                out.push({
                  kind: el.matches('.lead') ? 'lead' : 'heading',
                  color: getComputedStyle(el).color,
                  box: { x: r.x, y: r.y, width: r.width, height: r.height, top: r.top, left: r.left, right: r.right, bottom: r.bottom },
                });
              });
              return out;
            }, i);

            const style = await page.addStyleTag({ content: 'main,.nav,footer{visibility:hidden !important}' });
            await page.waitForTimeout(150);
            await page.screenshot({ path: path.join(SHOTS, `${base}-bg.png`) });
            await style.evaluate((el) => (el as HTMLElement).remove());

            fs.writeFileSync(path.join(SHOTS, `${base}.json`), JSON.stringify({ items }, null, 2));
          }

          const overflow = await page.evaluate(() => ({ sw: document.documentElement.scrollWidth, iw: innerWidth }));
          expect(overflow.sw, `horizontal scroll: scrollWidth ${overflow.sw} > innerWidth ${overflow.iw}`).toBeLessThanOrEqual(overflow.iw);
          expect(errors, errors.join('\n')).toEqual([]);
          await ctx.close();
        });
      }
    }
  }
}
