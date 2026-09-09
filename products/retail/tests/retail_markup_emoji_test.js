/**
 * retail_markup_emoji_test.js -- no raw emoji in rendered markup.
 *
 * WHY THIS EXISTS
 * retail_icons_test.js harvests the values PASSED to _icon()/AuraIcons.render()
 * and proves each resolves to a real icon. That is a good guard and it is
 * structurally blind to the opposite problem: a glyph typed straight into
 * markup, which is never passed to anything and so never harvested.
 *
 * Eighty-one of those had accumulated by 2026-09-09. import-wizard.js had
 * thirty-one and did not call the icon system once; subsystem-retail.js had
 * about forty in modal headings, Import buttons and scan-button labels that
 * bypassed _icon() entirely. Every suite was green throughout. The glyphs
 * rendered in whatever the operating system supplied -- colour emoji on
 * Windows, at sizes nobody chose, and a box on a device without them.
 *
 * So this asks the question the harvester cannot: is there a pictographic
 * character sitting in a string that is obviously markup, outside the icon
 * system entirely?
 *
 * WHAT IT DELIBERATELY DOES NOT FLAG, and why each exclusion is safe
 *
 *   Deliberate typography. U+2212 MINUS SIGN is the accounting minus this
 *   codebase's money rule requires -- _moneyDigits uses it for every negative
 *   amount. The trend triangles, the arrows in prose, the status dots and the
 *   scan glyph are text, not pictographs. A blanket "strip symbol characters"
 *   pass would have corrupted every negative amount on screen, which is
 *   exactly the sort of fix that looks tidy and loses money.
 *
 *   Comments. Both the JS kind and the angle-bracket-bang kind, the latter
 *   because it can sit INSIDE a template literal where slash-slash is only
 *   text. This file's own history explains why: prose describing the emoji
 *   that were removed is not a reason to fail.
 *
 *   Icon fallbacks. Any template interpolation that mentions the icon system
 *   is icon machinery, so a glyph inside it is the fallback argument doing its
 *   job. This is matched STRUCTURALLY rather than by a list of helper names --
 *   an earlier draft listed _icon/_stkaIcon/render and produced twenty-six
 *   false positives, because tabHTML(), tabIcon(), failIcon() and the
 *   `window.AuraIcons ? ... : 'glyph'` ternary are all the same thing wearing
 *   different clothes. A name list would go stale the first time someone adds
 *   a helper; "mentions the icon system" does not.
 *
 * Standalone Node, no framework, no dependency -- this frontend has no build
 * step:
 *
 *   node products/retail/tests/retail_markup_emoji_test.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');

const FRONTEND = path.join(__dirname, '..', 'frontend');

/* Characters that are text, not pictographs, and must survive untouched. */
const KEEP = new Set([
  0x2212, // MINUS SIGN -- the accounting minus in every negative amount
  0x25B2, 0x25BC, // trend triangles on the dashboard deltas
  0x2192, 0x21B3, 0x2190, 0x2191, 0x2193, 0x21A9, // arrows used in prose
  0x2605, // star marking a required column in the import wizard
  0x2713, 0x2715, // check and cross as typographic marks
  0x25CF, 0x25CB, // PIN set / not set dots on the employees list
  0x25B6, 0x25C0,
  0x2317, // the scan glyph on the POS search bar
  0x2500, 0x2550, // box drawing, used for section banners
  0x2026, 0x00D7, 0x2011,
]);

function isPictographic(cp) {
  return (cp >= 0x1F300 && cp <= 0x1FAFF)
    || (cp >= 0x2600 && cp <= 0x27BF)
    || (cp >= 0x2B00 && cp <= 0x2BFF)
    || (cp >= 0x2190 && cp <= 0x21FF)
    || (cp >= 0x2300 && cp <= 0x23FF);
}

/* Anything that names the icon system. Structural, not a helper-name list. */
const ICON_MACHINERY = /AuraIcons|_icon\s*\(|tabIcon|tabHTML|failIcon|Icon\s*\(/;

/* Blank a run of text, preserving newlines so line numbers stay honest. */
function blank(text) {
  return text.replace(/[^\n]/g, ' ');
}

function stripJsComments(src) {
  let out = '';
  let i = 0;
  while (i < src.length) {
    const two = src.slice(i, i + 2);
    if (two === '/*') {
      const end = src.indexOf('*/', i + 2);
      const stop = end < 0 ? src.length : end + 2;
      out += blank(src.slice(i, stop));
      i = stop;
      continue;
    }
    if (two === '//') {
      let j = i;
      while (j < src.length && src[j] !== '\n') j++;
      out += blank(src.slice(i, j));
      i = j;
      continue;
    }
    out += src[i];
    i++;
  }
  return out;
}

function stripHtmlComments(src) {
  const OPEN = '<!--';
  const CLOSE = '--' + '>';
  let out = '';
  let i = 0;
  for (;;) {
    const start = src.indexOf(OPEN, i);
    if (start < 0) { out += src.slice(i); return out; }
    out += src.slice(i, start);
    const end = src.indexOf(CLOSE, start + OPEN.length);
    const stop = end < 0 ? src.length : end + CLOSE.length;
    out += blank(src.slice(start, stop));
    if (end < 0) return out;
    i = stop;
  }
}

/* Every `${ ... }` whose body mentions the icon system, by brace counting. */
function iconInterpolationRanges(src) {
  const ranges = [];
  const re = /\$\{/g;
  let m;
  while ((m = re.exec(src))) {
    let depth = 1;
    let j = m.index + 2;
    for (; j < src.length && depth > 0; j++) {
      if (src[j] === '{') depth++;
      else if (src[j] === '}') depth--;
    }
    if (ICON_MACHINERY.test(src.slice(m.index, j))) ranges.push([m.index, j]);
  }
  return ranges;
}

/* String and template literals that contain an HTML tag -- i.e. markup. */
function markupLiterals(src) {
  const found = [];
  const re = /`(?:[^`\\]|\\.)*`|'(?:[^'\\\n]|\\.)*'|"(?:[^"\\\n]|\\.)*"/g;
  let m;
  while ((m = re.exec(src))) {
    if (/<[a-zA-Z/]/.test(m[0])) found.push({ start: m.index, text: m[0] });
  }
  return found;
}

/** Raw pictographic characters sitting in markup, outside the icon system. */
function scan(rawSource) {
  const src = stripHtmlComments(stripJsComments(rawSource));
  const ranges = iconInterpolationRanges(src);
  const hits = [];
  for (const lit of markupLiterals(src)) {
    for (let k = 0; k < lit.text.length; k++) {
      const abs = lit.start + k;
      const cp = lit.text.codePointAt(k);
      if (!isPictographic(cp) || KEEP.has(cp)) continue;
      if (ranges.some(([a, b]) => abs >= a && abs < b)) continue;
      hits.push({
        line: src.slice(0, abs).split('\n').length,
        glyph: String.fromCodePoint(cp),
        codepoint: 'U+' + cp.toString(16).toUpperCase(),
        context: lit.text.slice(Math.max(0, k - 40), k + 40).replace(/\s+/g, ' '),
      });
    }
  }
  return hits;
}

function frontendSources() {
  // Discovered, never a hard-coded list: a file added later is covered on the
  // day it lands rather than whenever somebody remembers to add it here.
  return fs.readdirSync(FRONTEND)
    .filter((f) => f.endsWith('.js') && f !== 'icons.js')
    .map((f) => path.join(FRONTEND, f));
}

/* ── check 1 ── no raw emoji in any rendered markup ─────────────────────── */
function testNoRawEmojiInRenderedMarkup() {
  const offenders = [];
  for (const file of frontendSources()) {
    for (const hit of scan(fs.readFileSync(file, 'utf8'))) {
      offenders.push(`${path.basename(file)}:${hit.line} ${hit.glyph} `
        + `${hit.codepoint} in "${hit.context}"`);
    }
  }
  assert.deepStrictEqual(offenders, [],
    `${offenders.length} raw emoji sit in rendered markup outside the icon `
    + 'system. Each draws whatever glyph the operating system supplies -- a '
    + 'colour emoji at a size nobody chose on Windows, a box on a device '
    + `without it. Route each through the icon system instead:\n  `
    + offenders.join('\n  '));
  console.log('PASS testNoRawEmojiInRenderedMarkup');
}

/* ── check 2 ── the scanner actually catches one (anti-vacuity) ─────────── */
function testScannerFlagsARawGlyphInMarkup() {
  // Without this, every assertion above passes on a scanner that silently
  // matches nothing -- the exact shape where a guard reports green precisely
  // because it went blind. ENGINEERING.md section 1, shape 5.
  const bad = "const html = `<h3>\u{1F4E6} Adjust Stock</h3>`;";
  const hits = scan(bad);
  assert.strictEqual(hits.length, 1,
    'the scanner did not flag a raw package glyph typed directly into an h3 -- '
    + 'it is not detecting anything, so check 1 proves nothing');
  assert.strictEqual(hits[0].codepoint, 'U+1F4E6');
  console.log('PASS testScannerFlagsARawGlyphInMarkup');
}

/* ── check 3 ── ALLOW HALF: a proper icon fallback is not flagged ───────── */
function testIconFallbacksAreNotFlagged() {
  // The deny half above is the obvious one. A scanner that flagged everything
  // would pass check 2 and make the icon system's own fallback argument
  // unusable, so the allow half has to be proven too.
  const good = "const html = `<h3>${this._icon('package', 18, '\u{1F4E6}')} Stock</h3>`;";
  assert.deepStrictEqual(scan(good), [],
    'a glyph passed as the fallback argument of an icon call was flagged. '
    + 'That is the icon system working, and failing it would force the '
    + 'fallback to be removed -- leaving nothing to draw when icons.js is '
    + 'absent, which is the standalone-harness case it exists for');

  const ternary = "const h = `<span>${window.AuraIcons ? AuraIcons.render('key-round', 18) : '\u{1F511}'}</span>`;";
  assert.deepStrictEqual(scan(ternary), [],
    'the guarded ternary form of an icon fallback was flagged; this shape is '
    + 'used throughout app-shell.js and is correct');
  console.log('PASS testIconFallbacksAreNotFlagged');
}

/* ── check 4 ── deliberate typography survives ──────────────────────────── */
function testDeliberateTypographyIsNotFlagged() {
  // If this ever fails, someone has widened the pictographic ranges to
  // swallow the accounting minus, and the next "tidy up" would silently
  // change how every negative amount prints.
  const money = "const row = `<td>− 12.345 JOD</td><td>▲ 4%</td><td>▼ 2%</td>`;";
  assert.deepStrictEqual(scan(money), [],
    'a deliberate typographic mark was flagged as an emoji. U+2212 is the '
    + 'accounting minus used by _moneyDigits for every negative amount, and '
    + 'the triangles are the dashboard trend indicators -- replacing any of '
    + 'them with an icon would be a regression, not a cleanup');
  console.log('PASS testDeliberateTypographyIsNotFlagged');
}

/* ── check 5 ── it is really reading the frontend ───────────────────────── */
function testItScannedRealFiles() {
  const files = frontendSources();
  assert.ok(files.length >= 5,
    `only ${files.length} frontend source file(s) discovered -- the directory `
    + 'moved or the filter is wrong, and check 1 is scanning almost nothing');
  const withMarkup = files.filter(
    (f) => markupLiterals(stripJsComments(fs.readFileSync(f, 'utf8'))).length > 0);
  assert.ok(withMarkup.length >= 3,
    `markup literals were found in only ${withMarkup.length} file(s); this `
    + 'frontend renders HTML from template literals everywhere, so that means '
    + 'the literal matcher stopped matching and check 1 has nothing to inspect');
  console.log(`PASS testItScannedRealFiles (${files.length} files, `
    + `${withMarkup.length} containing markup)`);
}

const tests = [
  testNoRawEmojiInRenderedMarkup,
  testScannerFlagsARawGlyphInMarkup,
  testIconFallbacksAreNotFlagged,
  testDeliberateTypographyIsNotFlagged,
  testItScannedRealFiles,
];

let failed = 0;
for (const t of tests) {
  try {
    t();
  } catch (err) {
    failed++;
    console.error(`FAIL ${t.name} -- ${err.message}`);
  }
}
if (failed) {
  console.error(`\n${failed} of ${tests.length} checks failed`);
  process.exit(1);
}
console.log(`\nPASS retail_markup_emoji_test.js -- ${tests.length} checks`);
