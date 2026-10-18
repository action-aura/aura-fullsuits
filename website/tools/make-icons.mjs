// Rasterises public/favicon.svg to the PNG sizes the head and manifest link.
// Run from website/: node tools/make-icons.mjs
import { chromium } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

const svg = fs.readFileSync(path.resolve('public/favicon.svg'), 'utf8');
const sizes = { 'favicon-32.png': 32, 'apple-touch-icon.png': 180, 'icon-192.png': 192, 'icon-512.png': 512 };
const browser = await chromium.launch();
for (const [file, n] of Object.entries(sizes)) {
  const page = await browser.newPage({ viewport: { width: n, height: n }, deviceScaleFactor: 1 });
  await page.setContent(`<html><body style="margin:0;background:transparent">${svg.replace('<svg', `<svg width="${n}" height="${n}"`)}</body></html>`);
  await page.screenshot({ path: path.resolve('public', file), omitBackground: true, clip: { x: 0, y: 0, width: n, height: n } });
  await page.close();
}
await browser.close();
console.log('icons written');
