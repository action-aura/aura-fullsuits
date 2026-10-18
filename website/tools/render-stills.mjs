// Renders the four jewel stills (public/jewel/still-<world>.webp) from the live WebGL scene of the built site.
// Run from website/:  npm run build && node tools/render-stills.mjs && npm run build
// It starts `npm run preview` on 4321 unless one is already answering, and stops the one it started. `--only=<world>`
// renders one still. Exit code 1 if any still failed a check (nothing is written for that world).
//
// Spec: spec-delta-stills.md (2026-10-18) and docs/design-system.md section 5. The capture is the hero rest pose of
// /en/ at 1440x900: station .73/.54, scale 1, scroll 0, the mix forced to one world. Everything but the canvas is
// hidden and the page background is transparent, so the screenshot carries the canvas alpha. The frame is 702x648 CSS
// px (13:12) centred on the station point (1051, 486), captured at device scale 2 -> 1404x1296 px; .jewel-still shows
// it at 72vh * --js tall (72/58 = frame height / mark height), so the mark inside lands where the canvas mark renders.
//
// Rest pose: the page's requestAnimationFrame is wrapped so every frame advances scene time by 20*pi s, the common
// period of the idle bob (0.9 rad/s), the sway (0.45) and the node pulse (2.2): all three are exactly zero on every
// frame, the arrival is complete from the second frame, and scene.js clamps the step to dt .1 s so the mix and station
// eases converge in about 18 frames. The key-light swing (period 9 s, 1.4 s long) is checked at the capture frame. The
// yaw ease is 5% per frame whatever dt is, so 90 frames run before the first capture (0.95^90 < 1%).
//
// The bead's additive halo cannot be carried by a straight-alpha image (three.js blends it SRC_ALPHA, ONE: canvas alpha
// is a^2, colour c*a, and the readback clamps). The still's glow tail is a little tighter than the live one by physics.
import { chromium } from '@playwright/test';
import { spawn, execSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';

const URL = 'http://localhost:4321/en/';
const OUT_DIR = path.resolve('public/jewel');
const WORLDS = ['master', 'retail', 'clinic', 'gym'];
const VIEWPORT = { width: 1440, height: 900 };
const STATION = { jx: '0.73', jy: '0.54', js: '1' };        // the hero station as Jewel.astro writes it to <html>
const FRAME = { x: 700, y: 162, width: 702, height: 648 };   // centre (1051, 486) = (.73 * 1440, .54 * 900), rounded; 72vh tall; 13:12
const SCALE = 2;                                             // device scale factor: the file is 1404x1296
const QUALITY = 0.85;                                        // 51 / 51 / 104 / 53 KB measured; .90 puts Clinic at 128 KB
const BUDGET = 120 * 1024;
const REST_MS = 20000 * Math.PI;
const SETTLE_FRAMES = 20;                                    // mix ease at dt .1: 0.59^18 < 4e-4, the snap threshold in scene.js
const WARMUP_FRAMES = 90;                                    // yaw ease, 5% per frame: 0.95^90 < 1%

const only = process.argv.find((a) => a.startsWith('--only='))?.slice(7);
const worlds = only ? WORLDS.filter((w) => w === only) : WORLDS;
if (!worlds.length) { console.error(`unknown world: ${only}`); process.exit(2); }

const mixOf = (w) => Object.fromEntries(WORLDS.map((k) => [k, k === w ? 1 : 0]));
const HIDE = 'html,body{background:transparent !important} body *:not(.jewel):not(.jewel *){visibility:hidden !important} .jewel-still{visibility:hidden !important} .jewel-canvas{visibility:visible !important}';

// Runs in the page before any of its scripts: the rest clock, and a guard that drops the page's own worldmix events so
// the forced mix sticks (apply.ts dispatches again on load, fonts.ready and every resize of main).
const init = (REST_MS) => {
  const real = window.requestAnimationFrame.bind(window);
  const clock = { frames: 0, lastTs: -1, now: 0 };
  window.__auraClock = clock;
  window.requestAnimationFrame = (cb) => real((ts) => {
    if (ts !== clock.lastTs) { clock.lastTs = ts; clock.frames++; clock.now = clock.frames * REST_MS; }
    cb(clock.now);
  });
  const dispatch = EventTarget.prototype.dispatchEvent;
  EventTarget.prototype.dispatchEvent = function (e) { return e.type === 'worldmix' && !e.__auraOwn ? true : dispatch.call(this, e); };
  window.__auraSetMix = (mix) => {
    const root = document.documentElement;
    root.__auraMix = mix;
    const e = new CustomEvent('worldmix', { detail: mix });
    e.__auraOwn = true;
    dispatch.call(root, e);
  };
  // Key light: every 9 s of scene time it swings for 1.4 s. At rest when (t mod 9) >= 1.4; 2.0 leaves three frames.
  window.__auraKeyAtRest = () => ((clock.now / 1000) % 9) >= 2.0;
};

const framesNow = (page) => page.evaluate(() => window.__auraClock.frames);
const waitFrames = async (page, n) => {
  const start = await framesNow(page);
  await page.waitForFunction(([s, n]) => window.__auraClock.frames >= s + n, [start, n]);
};
const waitTotalFrames = (page, n) => page.waitForFunction((n) => window.__auraClock.frames >= n, n);

// PNG screenshot -> WebP, encoded by the browser (no dependency). Returns base64.
const encodeWebp = (page, png, quality) => page.evaluate(async ([b64, q]) => {
  const img = await new Promise((res, rej) => { const i = new Image(); i.onload = () => res(i); i.onerror = rej; i.src = 'data:image/png;base64,' + b64; });
  const cv = document.createElement('canvas'); cv.width = img.width; cv.height = img.height;
  cv.getContext('2d').drawImage(img, 0, 0);
  const blob = await new Promise((res) => cv.toBlob(res, 'image/webp', q));
  const bytes = new Uint8Array(await blob.arrayBuffer());
  let s = ''; for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  return btoa(s);
}, [png.toString('base64'), quality]);

// Decodes a WebP and reports its size, the four corner alphas and the box of alpha > 0.
const inspect = (page, webp) => page.evaluate(async (b64) => {
  const img = await new Promise((res, rej) => { const i = new Image(); i.onload = () => res(i); i.onerror = rej; i.src = 'data:image/webp;base64,' + b64; });
  const cv = document.createElement('canvas'); cv.width = img.width; cv.height = img.height;
  const x = cv.getContext('2d'); x.drawImage(img, 0, 0);
  const d = x.getImageData(0, 0, cv.width, cv.height).data, W = cv.width, H = cv.height;
  let l = 1e9, t = 1e9, r = -1, b = -1;
  for (let i = 3; i < d.length; i += 4) {
    if (!d[i]) continue;
    const p = (i - 3) / 4, px = p % W, py = (p / W) | 0;
    if (px < l) l = px; if (px > r) r = px; if (py < t) t = py; if (py > b) b = py;
  }
  const at = (px, py) => d[(py * W + px) * 4 + 3];
  return { width: W, height: H, box: r < 0 ? null : [l, t, r + 1, b + 1], corners: [at(0, 0), at(W - 1, 0), at(0, H - 1), at(W - 1, H - 1)] };
}, webp.toString('base64'));

const serverUp = async () => { try { return (await fetch(URL)).ok; } catch { return false; } };
let preview = null;
if (!(await serverUp())) {
  preview = spawn('npm', ['run', 'preview'], { shell: true, stdio: 'ignore' });
  for (let i = 0; i < 60 && !(await serverUp()); i++) await new Promise((r) => setTimeout(r, 500));
  if (!(await serverUp())) { console.error('preview did not answer on 4321'); process.exit(1); }
}
const stopPreview = () => {
  if (!preview) return;
  try { if (process.platform === 'win32') execSync(`taskkill /PID ${preview.pid} /T /F`, { stdio: 'ignore' }); else preview.kill(); } catch {}
};

let failed = false;
const browser = await chromium.launch({ args: ['--use-gl=swiftshader', '--enable-unsafe-swiftshader', '--enable-webgl', '--ignore-gpu-blocklist'] });
try {
  const page = await browser.newPage({ viewport: VIEWPORT, deviceScaleFactor: SCALE });
  page.setDefaultTimeout(300_000);
  await page.addInitScript(init, REST_MS);
  await page.goto(URL, { waitUntil: 'load' });
  await page.waitForFunction(() => document.querySelector('.jewel')?.classList.contains('is-live'));
  const vars = await page.evaluate(() => {
    const cs = getComputedStyle(document.documentElement);
    return { jx: cs.getPropertyValue('--jx').trim(), jy: cs.getPropertyValue('--jy').trim(), js: cs.getPropertyValue('--js').trim() };
  });
  if (vars.jx !== STATION.jx || vars.jy !== STATION.jy || vars.js !== STATION.js) {
    throw new Error(`hero station is ${JSON.stringify(vars)}; FRAME was computed for ${JSON.stringify(STATION)}. Recompute FRAME.`);
  }
  fs.mkdirSync(OUT_DIR, { recursive: true });
  for (const world of worlds) {
    await page.evaluate((mix) => window.__auraSetMix(mix), mixOf(world));
    await waitFrames(page, SETTLE_FRAMES);
    await waitTotalFrames(page, WARMUP_FRAMES);
    const sceneMix = await page.evaluate(() => document.querySelector('.jewel').dataset.sceneMix);
    if (sceneMix !== world) throw new Error(`scene mix is ${sceneMix}, wanted ${world}`);
    const hide = await page.addStyleTag({ content: HIDE });
    await waitFrames(page, 2);
    while (!(await page.evaluate(() => window.__auraKeyAtRest()))) await waitFrames(page, 1);
    const png = await page.screenshot({ omitBackground: true, clip: FRAME });
    await hide.evaluate((el) => el.remove());
    const webp = Buffer.from(await encodeWebp(page, png, QUALITY), 'base64');
    const info = await inspect(page, webp);
    const problems = [];
    if (webp.length > BUDGET) problems.push(`${webp.length} B is over the ${BUDGET} B budget`);
    if (info.width !== FRAME.width * SCALE || info.height !== FRAME.height * SCALE) problems.push(`size ${info.width}x${info.height}`);
    if (info.corners.some((a) => a !== 0)) problems.push(`corner alpha ${info.corners.join(',')}`);
    if (!info.box) problems.push('no opaque pixels');
    else if (info.box[0] < 1 || info.box[1] < 1 || info.box[2] > info.width - 1 || info.box[3] > info.height - 1) problems.push(`mark touches the frame edge: ${JSON.stringify(info.box)}`);
    if (problems.length) { failed = true; console.error(`still-${world}.webp NOT written: ${problems.join('; ')}`); continue; }
    fs.writeFileSync(path.join(OUT_DIR, `still-${world}.webp`), webp);
    console.log(`still-${world}.webp  ${webp.length} B  ${info.width}x${info.height}  mark alpha box ${JSON.stringify(info.box)}`);
  }
} finally {
  await browser.close();
  stopPreview();
}
process.exit(failed ? 1 : 0);
