// Builds one share (OG) image per page and language from the built site.
// Run from website/, after `npm run build`: node tools/make-og.mjs
//
// Single source of truth is dist/: each page's <meta property="og:title">, <meta property="og:image">,
// <html lang> and data-world are read from the built HTML (no TypeScript import, so a title change in i18n
// cannot drift from the card). The og:image file name decides the output name, public/og/<lang>-<slug>.png,
// which is exactly the URL Base.astro already emits. tools/og/card.html does the drawing; it reports whether
// the text fit (two lines, 60px safe area, clear of the mark) and this script refuses to write a card that
// did not, rather than ship a clipped title.
import { chromium } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const DIST = path.resolve('dist');
const OUT = path.resolve('public/og');
const CARD = path.resolve('tools/og/card.html');
const LANGS = new Set(['ar', 'en']);
const WIDTH = 1200, HEIGHT = 630;
// WhatsApp stops showing a large link preview somewhere around 300 KB (widely reported, not documented);
// WhatsApp is how most of this site's links will travel, so a card over this budget is a defect, not a warning.
const MAX_KB = 280;

function* walk(dir) {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) yield* walk(p);
    else if (e.name === 'index.html') yield p;
  }
}
const ENT = { amp: '&', lt: '<', gt: '>', quot: '"', apos: "'", '#39': "'", '#x27': "'" };
const decode = (s) => s.replace(/&(amp|lt|gt|quot|apos|#39|#x27);/g, (_, e) => ENT[e]).replace(/&#(\d+);/g, (_, n) => String.fromCodePoint(+n));
const attr = (html, re) => { const m = html.match(re); return m ? decode(m[1]) : null; };

if (!fs.existsSync(DIST)) throw new Error('dist/ not found: run `npm run build` first');
const pages = [];
for (const file of walk(DIST)) {
  const rel = path.relative(DIST, file);
  const parts = rel.split(path.sep);
  // The root index.html is the language chooser; it borrows the English home image and is never a source.
  if (!LANGS.has(parts[0])) { console.log(`skip  ${rel}: not under a language folder`); continue; }
  const html = fs.readFileSync(file, 'utf8');
  const title = attr(html, /<meta property="og:title" content="([^"]*)"/);
  const image = attr(html, /<meta property="og:image" content="([^"]*)"/);
  const lang = attr(html, /<html[^>]*\slang="([^"]*)"/);
  const world = attr(html, /<html[^>]*\sdata-world="([^"]*)"/) ?? 'master';
  if (!title || !image || !lang) throw new Error(`${rel}: missing og:title, og:image or <html lang>`);
  const out = path.basename(new URL(image).pathname);
  if (!/^(ar|en)-[a-z0-9-]+\.png$/.test(out)) throw new Error(`${rel}: unexpected og:image name ${out}`);
  pages.push({ rel, depth: parts.length, title, lang, world, out });
}

// Pages that share one og:image (contact and contact/sent): the shallowest path is the source, the rest are noted.
const sources = new Map();
for (const p of pages.sort((a, b) => a.depth - b.depth || a.rel.localeCompare(b.rel))) {
  const seen = sources.get(p.out);
  if (seen) { console.log(`skip  ${p.rel}: shares ${p.out} with ${seen.rel}`); continue; }
  sources.set(p.out, p);
}

fs.mkdirSync(OUT, { recursive: true });
// The card is a file:// page that reads the still's pixels (to find the mark's box). Chromium treats every
// file:// URL as its own origin, so without this flag the canvas is tainted and getImageData throws.
const browser = await chromium.launch({ args: ['--allow-file-access-from-files'] });
const page = await browser.newPage({ viewport: { width: WIDTH, height: HEIGHT }, deviceScaleFactor: 1 });
const failures = [];
for (const p of sources.values()) {
  const url = pathToFileURL(CARD);
  url.search = new URLSearchParams({ title: p.title, lang: p.lang, world: p.world }).toString();
  await page.goto(url.href);
  await page.waitForFunction(() => window.__og && window.__og.done, null, { timeout: 20_000 });
  const fit = await page.evaluate(() => window.__og);
  if (!fit.fits) {
    const why = fit.error ? `card error: ${fit.error}`
      : !fit.stillOk ? `still missing or empty: public/jewel/still-${p.world}.webp`
      : !fit.markInside ? `mark runs off the card: ${JSON.stringify(fit.mark)}`
      : fit.titleLines > 2 ? `title needs ${fit.titleLines} lines at ${fit.titleSize}px`
      : fit.subLines > 2 ? `descriptor needs ${fit.subLines} lines`
      : fit.overflow.length ? `inside the 60px safe area: ${fit.overflow.join(', ')}`
      : `copy only ${fit.clearance}px clear of the mark`;
    failures.push(`${p.out}: ${why}`);
    continue;
  }
  const file = path.join(OUT, p.out);
  await page.screenshot({ path: file, type: 'png', clip: { x: 0, y: 0, width: WIDTH, height: HEIGHT } });
  const kb = Math.round(fs.statSync(file).size / 1024);
  if (kb > MAX_KB) {
    fs.unlinkSync(file);
    failures.push(`${p.out}: ${kb} KB, over the ${MAX_KB} KB budget`);
    continue;
  }
  console.log(`wrote ${p.out}  ${p.lang}/${p.world}  title ${fit.titleLines} line(s) at ${fit.titleSize}px  ${kb} KB`);
}
await browser.close();
if (failures.length) {
  console.error(`\n${failures.length} card(s) NOT written:\n  ${failures.join('\n  ')}`);
  process.exit(1);
}
console.log(`\n${sources.size} share images in public/og/`);
