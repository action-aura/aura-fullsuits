// Scroll-jank + logo-visibility probe for the BUILT site (run `npm run build && npm run preview` first).
//
//   node tools/measure-scroll.mjs <label> [--lang en|ar] [--mode desk|phone] [--reduced] [--url http://127.0.0.1:4321]
//   node tools/measure-scroll.mjs <prefix> --all [--reduced]      (en/ar x desk/phone -> <prefix>-<mode>-<lang>)
//
// Writes tmp/measure/<label>.json (+ screenshots <label>-y###.png). The numbers are COMPARATIVE: headless Chromium on
// software GL (SwiftShader) is far slower than a laptop with a GPU, so only the same probe run before and after a
// change means anything. See docs/design-system.md and the v3 spec section 4 for the thresholds.
//
// What it records while wheel-scrolling the whole home page (~3.5 px/ms on desktop): rAF frame gaps, long tasks, a
// DevTools trace (self time by Layout / UpdateLayoutTree / Paint / Raster / FunctionCall, forced layouts), LCP, CLS,
// and the opacity of `.jewel` every 4 steps (the logo-visible share). With --reduced the context asks for
// prefers-reduced-motion and the run asserts that no frame is longer than 50 ms (the scene then renders only on change).
import { chromium, devices } from '@playwright/test';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import { fileURLToPath } from 'node:url';

const argv = process.argv.slice(2);
const flag = (n) => argv.includes(`--${n}`);
const opt = (n, d) => { const i = argv.indexOf(`--${n}`); return i >= 0 ? argv[i + 1] : d; };
const label0 = argv[0] && !argv[0].startsWith('--') ? argv[0] : 'run';
const base = opt('url', 'http://127.0.0.1:4321');
const reduced = flag('reduced');
const OUT = 'tmp/measure';
fs.mkdirSync(OUT, { recursive: true });

if (flag('all')) {
  // One process per run: a fresh browser each time, so no run inherits another's caches or GPU state.
  let bad = 0;
  for (const mode of ['desk', 'phone']) for (const lang of ['en', 'ar']) {
    const r = spawnSync(process.execPath, [fileURLToPath(import.meta.url), `${label0}-${mode}-${lang}`, '--lang', lang, '--mode', mode, '--url', base, ...(reduced ? ['--reduced'] : [])], { stdio: 'inherit' });
    if (r.status !== 0) bad++;
  }
  process.exit(bad ? 1 : 0);
}

const lang = opt('lang', 'en');
const mode = opt('mode', 'desk');
const phone = mode === 'phone';
const url = `${base}/${lang}/`;
const label = label0;

const browser = await chromium.launch({ headless: true, args: ['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'] });
const ctxOpts = phone ? { ...devices['Pixel 7'] } : { viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 };
const ctx = await browser.newContext({ ...ctxOpts, ...(reduced ? { reducedMotion: 'reduce' } : {}) });
const page = await ctx.newPage();
await page.addInitScript(() => {
  const w = window;
  w.__lt = []; w.__lcp = null; w.__frames = [];
  try { new PerformanceObserver((l) => { for (const e of l.getEntries()) w.__lt.push({ s: e.startTime, d: e.duration }); }).observe({ type: 'longtask', buffered: true }); } catch {}
  try { new PerformanceObserver((l) => { for (const e of l.getEntries()) w.__lcp = { t: e.startTime, size: e.size, el: e.element ? (e.element.tagName + '.' + e.element.className) : null, url: e.url || null }; }).observe({ type: 'largest-contentful-paint', buffered: true }); } catch {}
  try { w.__cls = 0; new PerformanceObserver((l) => { for (const e of l.getEntries()) if (!e.hadRecentInput) w.__cls += e.value; }).observe({ type: 'layout-shift', buffered: true }); } catch {}
});
await page.goto(url, { waitUntil: 'load' });
await page.waitForTimeout(2500); // the scene mounts after load; let the arrival finish

const tier = await page.evaluate(() => document.querySelector('.jewel')?.dataset.tier);
const live = await page.evaluate(() => document.querySelector('.jewel')?.classList.contains('is-live'));
const maxY = await page.evaluate(() => document.documentElement.scrollHeight - innerHeight);

await page.evaluate(() => {
  const w = window; w.__frames = []; let last = performance.now();
  const tick = (t) => { w.__frames.push(t - last); last = t; if (w.__rec) requestAnimationFrame(tick); };
  w.__rec = true; requestAnimationFrame(tick);
  w.__lt.length = 0;
});
await browser.startTracing(page, { screenshots: false, categories: ['devtools.timeline', 'disabled-by-default-devtools.timeline', 'blink.user_timing'] });

const step = phone ? 90 : 140, every = 40; // px per wheel tick, ms between ticks
const samples = [];
const shotAt = new Set([0.08, 0.2, 0.33, 0.47, 0.6, 0.75, 0.9].map((f) => Math.round(maxY * f / step)));
let i = 0;
for (let y = 0; y < maxY; y += step, i++) {
  await page.mouse.wheel(0, step);
  await page.waitForTimeout(every);
  if (i % 4 === 0) {
    samples.push(await page.evaluate(() => {
      const jewel = document.querySelector('.jewel');
      return { y: Math.round(scrollY), op: jewel ? +getComputedStyle(jewel).opacity : null, world: document.documentElement.dataset.world };
    }));
  }
  if (shotAt.has(i)) await page.screenshot({ path: `${OUT}/${label}-y${String(i).padStart(3, '0')}.png` });
}
await page.waitForTimeout(400);
const traceBuf = await browser.stopTracing();
const res = await page.evaluate(() => { window.__rec = false; return { frames: window.__frames, lt: window.__lt, lcp: window.__lcp, cls: window.__cls }; });

// Trace summary: self-time by event name; forced (synchronous) layouts = Layout events that carry a JS stack.
const tr = JSON.parse(traceBuf.toString());
const ev = tr.traceEvents.filter((e) => e.ph === 'X' && e.dur);
const sum = {};
for (const e of ev) sum[e.name] = (sum[e.name] || 0) + e.dur / 1000;
const forced = ev.filter((e) => e.name === 'Layout' && e.args?.beginData?.stackTrace).length;
const layouts = ev.filter((e) => e.name === 'Layout').length;
const pick = ['Layout', 'UpdateLayoutTree', 'PrePaint', 'Paint', 'Layerize', 'UpdateLayerTree', 'Commit', 'FunctionCall', 'RunTask', 'HitTest', 'RasterTask', 'ImageDecodeTask'];
const summary = Object.fromEntries(pick.filter((k) => sum[k]).map((k) => [k, +sum[k].toFixed(1)]));

const f = res.frames.slice(5);
const fps = f.length ? 1000 / (f.reduce((a, b) => a + b, 0) / f.length) : 0;
const vis = samples.filter((s) => s.op !== null && s.op > 0.5).length;
const out = {
  label, url, mode, lang, reduced, tier, live, scrollHeight: maxY, lcp: res.lcp, cls: +(res.cls || 0).toFixed(4),
  frames: { n: f.length, fps: +fps.toFixed(1), over33: f.filter((g) => g > 33).length, over50: f.filter((g) => g > 50).length, over100: f.filter((g) => g > 100).length, max: +Math.max(...f).toFixed(0) },
  longTasks: { n: res.lt.length, totalMs: +res.lt.reduce((a, b) => a + b.d, 0).toFixed(0), max: +Math.max(0, ...res.lt.map((l) => l.d)).toFixed(0) },
  trace: { ...summary, layouts, forcedLayouts: forced },
  logo: { samples: samples.length, visible: vis, visibleShare: +(vis / samples.length).toFixed(2), map: samples.map((s) => `${s.y}:${s.op?.toFixed(2)}:${s.world?.[0]}`).join(' ') },
};
fs.writeFileSync(`${OUT}/${label}.json`, JSON.stringify(out, null, 1));
console.log(JSON.stringify({ ...out, logo: { ...out.logo, map: undefined } }));
await browser.close();
if (reduced && out.frames.over50 > 0) { console.error(`reduced motion: ${out.frames.over50} frame(s) over 50 ms`); process.exit(1); }
