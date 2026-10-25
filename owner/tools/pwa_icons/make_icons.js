// One-shot, dev-side generator for the Owner installable-app icons (NOT shipped,
// NOT a runtime dependency). Re-run only if the mark or the colours change:
//
//   PLAYWRIGHT_MODULE=<path to a node_modules/playwright> node owner/tools/pwa_icons/make_icons.js owner/app/static/img/brand
//
// (2026-10-25 run used the website worktree's own Playwright install.)
//
// Geometry: the flat Aura Owner mark, owner/app/static/img/brand/action-aura-mark.svg.
// Colours: Action-Aura-Brand-Guide.md -- flat master on a dark surface = ivory A
// (#F6F4EF) + the product's accent ring, Owner's "light on dark" accent #E8CD7A,
// on the brand dark ground #0E1729. No 3D Owner artwork exists in the repo
// (aura-owner-dark.svg was never checked in), so the flat mark is used at every
// size; regenerate from the 3D source if the owner supplies it.
const fs = require('fs');
const path = require('path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');

const out = process.argv[2];
if (!out) throw new Error('usage: node make_icons.js <output dir>');

const A = 'M60 12.6 L98 104 L80 104 L60 56 L40 104 L22 104 Z';
const RING = 'M 24 69 A 36 8 0 0 0 96 69';

function svg(size, { rounded, scale }) {
  // The mark's box in its own 120-unit space is centred on x 60, y ~58.3.
  const s = (scale * size) / 512;
  const tx = size / 2 - 60 * s;
  const ty = size / 2 - 58.3 * s;
  const ground = rounded
    ? `<rect width="${size}" height="${size}" rx="${(112 * size) / 512}" fill="#0E1729"/>`
    : `<rect width="${size}" height="${size}" fill="#0E1729"/>`;
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" viewBox="0 0 ${size} ${size}">${ground}
  <g transform="translate(${tx} ${ty}) scale(${s})">
    <path d="${A}" fill="#F6F4EF"/>
    <path d="${RING}" transform="rotate(-12 60 69)" fill="none" stroke="#E8CD7A" stroke-width="9" stroke-linecap="round"/>
  </g></svg>`;
}

const jobs = [
  // "any" icons: rounded square, transparent corners.
  ['icon-192.png', 192, { rounded: true, scale: 3.6 }],
  ['icon-512.png', 512, { rounded: true, scale: 3.6 }],
  // Maskable: full-bleed; the mark stays inside the 40%-radius safe circle
  // (half-diagonal of its box at scale 3.0 is ~178px, under 204.8px).
  ['icon-maskable-512.png', 512, { rounded: false, scale: 3.0 }],
  // iOS rounds its own corners and does not want transparency: full-bleed.
  ['apple-touch-icon.png', 180, { rounded: false, scale: 3.3 }],
];

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage();
  for (const [name, size, opts] of jobs) {
    await page.setViewportSize({ width: size, height: size });
    await page.setContent(`<html><body style="margin:0;background:transparent">${svg(size, opts)}</body></html>`);
    await page.locator('svg').screenshot({ path: path.join(out, name), omitBackground: true });
  }
  await browser.close();
  fs.writeSync(1, `wrote ${jobs.length} icons to ${out}\n`);
})();
