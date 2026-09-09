/**
 * THE ARABIC WEBFONT IS BUNDLED, DECLARED, AND ORDERED CORRECTLY -- OR A
 * CUSTOMER-FACING SCREEN SILENTLY RENDERS BOXES OR FALLS BACK TO A DIFFERENT
 * TYPEFACE FOR EVERY ARABIC CHARACTER IN THE PRODUCT.
 *
 * WHY THIS TEST EXISTS
 * Three IBM Plex Sans Arabic weights (400/600/700) were subset to Arabic
 * ranges ONLY -- deliberately no Latin glyphs -- and wired into main.css
 * alongside the existing Plus Jakarta Sans (a Latin-only subset). The design
 * this relies on is: a browser resolves font-family PER GLYPH, not per
 * string. Because neither face can serve the other script, a single stack
 *
 *   --font-base: 'Plus Jakarta Sans', 'IBM Plex Sans Arabic', <system>;
 *
 * renders Latin from Jakarta and Arabic from Plex with NO :lang() rule, NO
 * body.rtl font override, and NO JavaScript -- which matters because a
 * mixed string like "SKU-4417 متوفر" or a JOD total inside an Arabic
 * sentence needs BOTH scripts correct in the same DOM node, something a
 * language-conditional rule cannot do without misjudging which script owns
 * that node.
 *
 * That design has exactly two ways to quietly break, and both are silent in
 * a code review that only reads main.css top to bottom:
 *
 *   1. ORDER. If 'IBM Plex Sans Arabic' ever moved before 'Plus Jakarta
 *      Sans', nothing crashes and no visual difference appears for Arabic
 *      text -- Plex has no Latin, so it still loses every Latin glyph to
 *      Jakarta by fallback. The regression is invisible until the day
 *      someone adds a Latin-capable face to the stack, at which point every
 *      Latin character in the product (which is most of it: SKUs, JOD
 *      amounts, English UI copy) silently changes typeface. This test
 *      catches the precondition before that day arrives.
 *   2. A LANGUAGE-CONDITIONAL RULE. Any `:lang()`, `[lang=]` or `body.rtl`
 *      selector that sets font-family reintroduces exactly the per-string
 *      branching the per-glyph stack was built to avoid, and breaks on the
 *      first mixed-script string it meets.
 *
 * WHAT THIS FILE CHECKS
 *   1. The three plex-ar-*.woff2 files exist on disk and are not truncated
 *      or placeholder-sized (a zero-byte or near-empty file would still
 *      "exist" and pass a naive fs.existsSync check).
 *   2. main.css declares an @font-face for each of the three weights, each
 *      pointing at the matching plex-ar-<weight>.woff2 file.
 *   3. In --font-base, 'IBM Plex Sans Arabic' appears strictly AFTER 'Plus
 *      Jakarta Sans' -- order, not mere presence, because order is the
 *      entire mechanism (see failure mode 1 above).
 *   4. Anti-vacuity: no `:lang(`, `[lang=` or `body.rtl` rule anywhere in
 *      main.css or rtl.css sets a font-family. If one is added later this
 *      check must fail, because it means the per-glyph design was abandoned
 *      for a per-string one (see failure mode 2 above).
 *
 * No test framework is configured for this vanilla-JS, build-step-free
 * frontend (see CLAUDE.md), so this runs standalone on Node built-ins:
 *
 *   node products/retail/tests/retail_arabic_font_test.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');

const FRONTEND = path.join(__dirname, '..', 'frontend');
const FONTS_DIR = path.join(FRONTEND, 'fonts');
const MAIN_CSS_PATH = path.join(FRONTEND, 'css', 'main.css');
const RTL_CSS_PATH = path.join(FRONTEND, 'css', 'rtl.css');

const ARABIC_WEIGHTS = ['400', '600', '700'];
// A truncated or placeholder font file would still pass fs.existsSync, so
// require a real byte floor. The three shipped subsets measure 28.8KB,
// 31.2KB and 28.9KB (>28000 bytes each); 15000 gives headroom for future
// re-subsetting while still catching an empty or stub file.
const MIN_FONT_BYTES = 15000;

/* ── check 1 ── the three Arabic subset font files exist and are real ───── */
function testArabicFontFilesExistAndAreNotTruncated() {
  for (const weight of ARABIC_WEIGHTS) {
    const filePath = path.join(FONTS_DIR, `plex-ar-${weight}.woff2`);
    assert.ok(
      fs.existsSync(filePath),
      `plex-ar-${weight}.woff2 is missing from ${FONTS_DIR} -- Arabic text ` +
      `at weight ${weight} has no bundled font to load and a customer on a ` +
      'machine without IBM Plex Sans Arabic installed system-wide will see ' +
      'a fallback typeface (or missing glyphs) instead of the designed one.');
    const size = fs.statSync(filePath).size;
    assert.ok(
      size > MIN_FONT_BYTES,
      `plex-ar-${weight}.woff2 is only ${size} bytes (need > ` +
      `${MIN_FONT_BYTES}) -- a file this small is truncated or a placeholder ` +
      'stub, not a real Arabic glyph subset, and a customer would get a ' +
      'font that fails to parse or renders no Arabic glyphs at all, not ' +
      'merely a wrong-looking one.');
  }
  console.log(
    `PASS testArabicFontFilesExistAndAreNotTruncated (${ARABIC_WEIGHTS.length} weights)`);
}

/* ── check 2 ── main.css declares an @font-face per weight, correctly wired ── */
function testMainCssDeclaresArabicFontFacePerWeight() {
  const css = fs.readFileSync(MAIN_CSS_PATH, 'utf8');
  for (const weight of ARABIC_WEIGHTS) {
    // Match an @font-face block for this family+weight regardless of how the
    // declaration is wrapped across lines, then require it to name the
    // matching woff2 file inside its own block (not merely appear somewhere
    // else in the file).
    const blockRe = new RegExp(
      `@font-face\\s*\\{[^}]*font-family:\\s*'IBM Plex Sans Arabic'[^}]*` +
      `font-weight:\\s*${weight}[^}]*\\}`,
      's');
    const match = blockRe.exec(css);
    assert.ok(
      match,
      `main.css has no @font-face block for 'IBM Plex Sans Arabic' at ` +
      `font-weight:${weight} -- without this declaration the browser has ` +
      'no source to load for that weight and will silently substitute a ' +
      'system font for every Arabic character rendered at that weight, ' +
      'with no error visible to the user or in the console.');
    assert.ok(
      match[0].includes(`plex-ar-${weight}.woff2`),
      `the 'IBM Plex Sans Arabic' font-weight:${weight} @font-face block ` +
      `does not reference plex-ar-${weight}.woff2 -- it points somewhere ` +
      'else (or nowhere), so the browser will fail to load the intended ' +
      `file for weight ${weight} and Arabic text at that weight falls back ` +
      'to a system font instead of the bundled, offline-verified one.');
  }
  console.log(
    `PASS testMainCssDeclaresArabicFontFacePerWeight (${ARABIC_WEIGHTS.length} weights)`);
}

/* ── check 3 ── --font-base orders Jakarta BEFORE Plex Arabic ───────────── */
function testFontBaseOrdersLatinFaceBeforeArabicFace() {
  const css = fs.readFileSync(MAIN_CSS_PATH, 'utf8');
  const fontBaseMatch = /--font-base:\s*([^;]+);/.exec(css);
  assert.ok(
    fontBaseMatch,
    'main.css has no --font-base declaration at all -- every element that ' +
    'reads var(--font-base) (which is most of the product\'s text) has no ' +
    'font stack and falls back to the browser default typeface.');
  const stack = fontBaseMatch[1];

  const jakartaIndex = stack.indexOf("'Plus Jakarta Sans'");
  const arabicIndex = stack.indexOf("'IBM Plex Sans Arabic'");
  assert.ok(
    jakartaIndex !== -1,
    "--font-base no longer lists 'Plus Jakarta Sans' -- Latin text across " +
    'the entire product loses its bundled offline font and falls back to ' +
    "whatever system font is installed on the customer's machine.");
  assert.ok(
    arabicIndex !== -1,
    "--font-base no longer lists 'IBM Plex Sans Arabic' -- Arabic text " +
    'across the entire product loses its bundled offline font and falls ' +
    "back to whatever system font is installed on the customer's machine " +
    '(which may render unjoined or missing glyphs).');

  // This is the load-bearing assertion in this file. The two faces cover
  // disjoint scripts, so swapping their order produces NO visual change for
  // Arabic text today -- Plex still has no Latin to lose to Jakarta. The
  // regression is invisible until a future Latin-capable face is added
  // after Plex, at which point every Latin glyph in the product (SKUs, JOD
  // amounts, English copy) silently changes typeface with no error and no
  // failing test anywhere else in this suite.
  assert.ok(
    jakartaIndex < arabicIndex,
    "'IBM Plex Sans Arabic' appears BEFORE 'Plus Jakarta Sans' in " +
    '--font-base (or one is missing) -- order is the entire mechanism that ' +
    'lets one stack serve both scripts with no :lang() rule. With Jakarta ' +
    'first, a customer sees today\'s correct rendering purely by accident ' +
    "of Plex's Latin coverage being empty; the day a Latin-capable face is " +
    'inserted after Plex, every Latin character in the product (SKUs, JOD ' +
    'totals, English UI text) silently renders in the wrong typeface.');
  console.log('PASS testFontBaseOrdersLatinFaceBeforeArabicFace');
}

/* ── check 4 ── anti-vacuity: no language-conditional font-family rule ──── */
function testNoLanguageConditionalRuleSetsFontFamily() {
  // A `{...}` block whose selector matches one of these patterns and whose
  // body sets font-family means the per-glyph design (one stack, resolved
  // by the browser per character) has been abandoned for a per-string one --
  // which breaks the first string that mixes an English SKU or a JOD amount
  // into an Arabic sentence, because the whole node gets one font choice
  // instead of letting each glyph resolve on its own.
  const CONDITIONAL_SELECTOR_RE = /(:lang\(|\[lang=|body\.rtl)/;
  const files = [MAIN_CSS_PATH, RTL_CSS_PATH].filter((p) => fs.existsSync(p));
  assert.ok(files.length > 0, 'expected main.css and/or rtl.css to exist');

  const offenders = [];
  for (const filePath of files) {
    const css = fs.readFileSync(filePath, 'utf8');
    const name = path.relative(FRONTEND, filePath);
    // Strip comments first (first-close-wins, matching the CSS parser) so
    // prose that merely discusses body.rtl or :lang() -- as several comments
    // in this codebase already do -- cannot trip the check.
    const stripped = stripCssComments(css);
    // Walk block by block: selector text is everything between the previous
    // `}`/`;`/start and the next `{`.
    let match;
    const blockRe = /([^{}]*)\{([^{}]*)\}/g;
    while ((match = blockRe.exec(stripped))) {
      const selector = match[1];
      const body = match[2];
      if (CONDITIONAL_SELECTOR_RE.test(selector) && /font-family\s*:/.test(body)) {
        offenders.push(`${name}: selector \`${selector.trim()}\` sets font-family`);
      }
    }
  }
  assert.deepStrictEqual(
    offenders, [],
    `found ${offenders.length} language-conditional rule(s) setting ` +
    'font-family: ' + offenders.join('; ') +
    '. This abandons the per-glyph design that lets one --font-base stack ' +
    'serve both Arabic and Latin correctly with no :lang() rule -- a ' +
    'per-string override like this picks ONE font for the whole element, ' +
    'so a mixed string such as "SKU-4417 متوفر" or a JOD total inside an ' +
    'Arabic sentence renders its other script wrong or not at all.');
  console.log('PASS testNoLanguageConditionalRuleSetsFontFamily');
}

/* Strip CSS comments exactly as a browser parser does: each comment runs
   from its opener to the FIRST closer after it. Replaced with spaces (not
   deleted) so this stays a pure anti-vacuity guard, independent of the
   comment-boundary logic already covered by retail_design_css_parse_test.js. */
function stripCssComments(text) {
  const OPEN = '/*';
  const CLOSE = '*' + '/';
  let out = '';
  let i = 0;
  for (;;) {
    const start = text.indexOf(OPEN, i);
    if (start < 0) { out += text.slice(i); return out; }
    out += text.slice(i, start);
    const end = text.indexOf(CLOSE, start + 2);
    const commentBody = end < 0 ? text.slice(start) : text.slice(start, end + 2);
    out += commentBody.replace(/[^\n]/g, ' ');
    if (end < 0) return out;
    i = end + 2;
  }
}

const tests = [
  testArabicFontFilesExistAndAreNotTruncated,
  testMainCssDeclaresArabicFontFacePerWeight,
  testFontBaseOrdersLatinFaceBeforeArabicFace,
  testNoLanguageConditionalRuleSetsFontFamily,
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
console.log(`\nPASS retail_arabic_font_test.js -- ${tests.length} checks`);
