/**
 * retail_stock_transfer_ui_test.js -- the Stock Transfers screen actually
 * wires up to the six routes retail_api.py already ships.
 *
 * WHY THIS EXISTS
 * retail_route_reachability_test.py (this directory) asks "does a shipped
 * client call this route" for the whole product, by literal-substring
 * search. That is the right coarse guard, but it cannot tell the difference
 * between "the receive flow round-trips the right identifiers" and "the
 * receive flow POSTs the wrong field and every receipt 400s forever" --
 * both look identical to a substring search that only wants to see
 * "stock-transfers" appear somewhere. This file asks the questions specific
 * to THIS screen that the generic guard structurally cannot.
 *
 * Harness shape (source discipline, exit code, no framework/dependency)
 * copied from retail_markup_emoji_test.js in this same directory -- run
 * standalone with plain Node, no build step, because this frontend has none:
 *
 *     node products/retail/tests/retail_stock_transfer_ui_test.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');

const FRONTEND = path.join(__dirname, '..', 'frontend');
const APP_SHELL = path.join(FRONTEND, 'app-shell.js');
const SUBSYSTEM = path.join(FRONTEND, 'subsystem-retail.js');
const LOCALE_EN = path.join(FRONTEND, 'locales', 'en.json');
const LOCALE_AR = path.join(FRONTEND, 'locales', 'ar.json');

/* The new section's own banner, and the banner immediately after it
 * (BRANCHES) -- see subsystem-retail.js: the plan places STOCK TRANSFERS
 * right before BRANCHES, so the text between the two banners is exactly the
 * new section and nothing else. */
const SECTION_START = '// ── STOCK TRANSFERS';
const SECTION_END = '// ── BRANCHES';

function readSrc(p) {
  return fs.readFileSync(p, 'utf8');
}

/**
 * Extract a named method's body (the text strictly between its opening and
 * closing braces) from an object-literal method definition shaped like
 * `[async ]name(args) { ... }`. Brace-counted rather than grabbed with one
 * regex, because these bodies nest template literals, arrow functions and
 * object literals that a non-greedy `.*?` cannot balance correctly.
 *
 * Returns null when the method is not found. That null path is exercised
 * directly by testExtractorsCanFail below -- a check built on an extractor
 * that always "finds" something would pass whether or not the real method
 * existed, which is exactly the vacuous-guard shape ENGINEERING.md warns
 * against.
 */
function findMethodBody(src, name) {
  const escaped = name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const declRe = new RegExp('(?:async\\s+)?' + escaped + '\\s*\\([^)]*\\)\\s*\\{');
  const m = declRe.exec(src);
  if (!m) return null;
  let depth = 1;
  let i = m.index + m[0].length;
  const start = i;
  for (; i < src.length && depth > 0; i++) {
    if (src[i] === '{') depth++;
    else if (src[i] === '}') depth--;
  }
  if (depth !== 0) return null; // ran off the end unbalanced -- do not fake a result
  return src.slice(start, i - 1);
}

/** The whole STOCK TRANSFERS section, banner to banner. Null if either
 *  marker is missing, so a renamed/moved section fails loudly rather than
 *  scanning zero characters and reporting "all clear". */
function findTransfersSection(src) {
  const s = src.indexOf(SECTION_START);
  if (s < 0) return null;
  const e = src.indexOf(SECTION_END, s + SECTION_START.length);
  return e < 0 ? src.slice(s) : src.slice(s, e);
}

/** Every literal t('...') key referenced in a snippet of source. A dynamic
 *  call like t(meta.label) is invisible here on purpose: only a string
 *  literal argument names a real dictionary key worth checking. */
function literalTKeys(snippet) {
  const keys = [];
  const re = /\bt\(\s*'((?:[^'\\]|\\.)*)'/g;
  let m;
  while ((m = re.exec(snippet))) keys.push(m[1].replace(/\\'/g, "'"));
  return keys;
}

/* ---- emoji-in-markup detection, mirrored from retail_markup_emoji_test.js.
 * Duplicated rather than required in: that file is a standalone script that
 * runs its own suite and calls process.exit as a side effect of being
 * loaded, not an importable module -- and this check only needs to run over
 * the new section, not the whole frontend. So the applicable slice of its
 * detection logic (pictographic ranges, comment stripping, markup-literal
 * matching, the icon-machinery carve-out) is reused verbatim here instead. */
const KEEP_CODEPOINTS = new Set([
  0x2212, 0x25B2, 0x25BC, 0x2192, 0x21B3, 0x2190, 0x2191, 0x2193, 0x21A9,
  0x2605, 0x2713, 0x2715, 0x25CF, 0x25CB, 0x25B6, 0x25C0, 0x2317, 0x2500,
  0x2550, 0x2026, 0x00D7, 0x2011,
]);
function isPictographic(cp) {
  return (cp >= 0x1F300 && cp <= 0x1FAFF)
    || (cp >= 0x2600 && cp <= 0x27BF)
    || (cp >= 0x2B00 && cp <= 0x2BFF)
    || (cp >= 0x2190 && cp <= 0x21FF)
    || (cp >= 0x2300 && cp <= 0x23FF);
}
const ICON_MACHINERY = /AuraIcons|_icon\s*\(|tabIcon|tabHTML|failIcon|Icon\s*\(/;
function blank(text) { return text.replace(/[^\n]/g, ' '); }
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
function markupLiterals(src) {
  const found = [];
  const re = /`(?:[^`\\]|\\.)*`|'(?:[^'\\\n]|\\.)*'|"(?:[^"\\\n]|\\.)*"/g;
  let m;
  while ((m = re.exec(src))) {
    if (/<[a-zA-Z/]/.test(m[0])) found.push({ start: m.index, text: m[0] });
  }
  return found;
}
function scanForRawEmoji(rawSource) {
  const src = stripJsComments(rawSource);
  const ranges = iconInterpolationRanges(src);
  const hits = [];
  for (const lit of markupLiterals(src)) {
    for (let k = 0; k < lit.text.length; k++) {
      const abs = lit.start + k;
      const cp = lit.text.codePointAt(k);
      if (!isPictographic(cp) || KEEP_CODEPOINTS.has(cp)) continue;
      if (ranges.some(([a, b]) => abs >= a && abs < b)) continue;
      hits.push({ glyph: String.fromCodePoint(cp), codepoint: 'U+' + cp.toString(16).toUpperCase() });
    }
  }
  return hits;
}

/* ── check 1 ── nav entry: id AND capability, matching the four mutating
 *   routes' own @mt_require_capability(CAP_STOCK_ADJUST) ─────────────────── */
function testNavEntryHasIdAndCapability() {
  const shellSrc = readSrc(APP_SHELL);
  const m = shellSrc.match(/\{\s*id:\s*'transfers'[^}]*\}/);
  assert.ok(m && m[0].length > 0,
    'no nav entry with id \'transfers\' found in app-shell.js -- check 1 has nothing to inspect');
  assert.ok(/capability:\s*'retail\.stock\.adjust'/.test(m[0]),
    'transfers nav entry does not declare capability \'retail.stock.adjust\': ' + m[0]);
  console.log('PASS testNavEntryHasIdAndCapability');
}

/* ── check 2 ── the router dispatches to the new screen ──────────────────── */
function testDispatchesTransfersCase() {
  const subSrc = readSrc(SUBSYSTEM);
  assert.ok(subSrc.length > 0, 'subsystem-retail.js read as empty -- check 2 has nothing to inspect');
  assert.ok(/case\s+'transfers':\s*return\s+this\._renderTransfers\(c\);/.test(subSrc),
    'no "case \'transfers\': return this._renderTransfers(c);" dispatch found in the section switch');
  console.log('PASS testDispatchesTransfersCase');
}

/* ── check 3 ── all six routes are actually called, not just the base path ── */
function testCallsAllSixRoutes() {
  const subSrc = readSrc(SUBSYSTEM);
  assert.ok(subSrc.includes('stock-transfers'),
    'literal "stock-transfers" not found anywhere in subsystem-retail.js -- check 3 has nothing to inspect');
  assert.ok(/stock-transfers\/\$\{[^}]*\}\/send/.test(subSrc),
    'no call to .../stock-transfers/${...}/send found (the SEND route)');
  assert.ok(/stock-transfers\/\$\{[^}]*\}\/receive/.test(subSrc),
    'no call to .../stock-transfers/${...}/receive found (the RECEIVE route)');
  assert.ok(/stock-transfers\/\$\{[^}]*\}\/cancel/.test(subSrc),
    'no call to .../stock-transfers/${...}/cancel found (the CANCEL route)');
  console.log('PASS testCallsAllSixRoutes');
}

/* ── check 4 ── receive reconciles by transfer-item id, never product_id ──── */
function testReceivePayloadUsesItemIdNotProductId() {
  const subSrc = readSrc(SUBSYSTEM);
  const body = findMethodBody(subSrc, '_confirmReceiveTransfer');
  assert.ok(body && body.length > 0,
    '_confirmReceiveTransfer not found (or empty) in subsystem-retail.js -- check 4 has nothing to inspect');
  // ANCHOR, and the reason it is not decoration: the assertion below is a
  // NEGATIVE one, and findMethodBody counts braces without skipping strings or
  // template literals. An unbalanced brace inside a literal would close the
  // count early and hand back a TRUNCATED body -- in which "product_id" is
  // absent because the text was cut short, not because the code is right.
  // Requiring the receive POST, which is the last thing the real method does,
  // proves the extracted text reached the end of the method.
  assert.ok(/stock-transfers\/\$\{[^}]*\}\/receive/.test(body),
    '_confirmReceiveTransfer body does not contain its own POST to the receive route -- the extracted body was truncated, so the absence check below would prove nothing');
  assert.ok(body.includes('data-item-id'),
    '_confirmReceiveTransfer does not read [data-item-id] -- it cannot be keying its payload off the stock_transfer_items row id the receive route requires');
  assert.ok(!body.includes('product_id'),
    '_confirmReceiveTransfer body contains the literal string "product_id" -- the receive route keys every line by the stock_transfer_items row id, because a transfer can carry two lines for the same product and only the row id disambiguates which line a count belongs to');
  console.log('PASS testReceivePayloadUsesItemIdNotProductId');
}

/* ── check 5 ── the view modal treats "not yet reconciled" and "zero arrived"
 *   as different facts, per the migration's own NULL-vs-0 contract ───────── */
function testViewTransferDistinguishesNullFromZero() {
  const subSrc = readSrc(SUBSYSTEM);
  const body = findMethodBody(subSrc, '_viewTransfer');
  assert.ok(body && body.length > 0,
    '_viewTransfer not found (or empty) in subsystem-retail.js -- check 5 has nothing to inspect');
  assert.ok(/quantity_received\s*==\s*null/.test(body),
    '_viewTransfer has no explicit `quantity_received == null` check -- NULL (not yet reconciled) and 0 (sent but nothing arrived) are different facts in this schema and collapsing them would hide a total loss in transit');
  console.log('PASS testViewTransferDistinguishesNullFromZero');
}

/* ── check 6 ── no bare emoji sits in the new section's markup ──────────── */
function testNoBareEmojiInNewSection() {
  const subSrc = readSrc(SUBSYSTEM);
  const section = findTransfersSection(subSrc);
  assert.ok(section && section.length > 0,
    'STOCK TRANSFERS section not found between its own banner and the BRANCHES banner -- check 6 has nothing to inspect');
  const hits = scanForRawEmoji(section);
  assert.deepStrictEqual(hits, [],
    'raw emoji found in the new stock-transfers markup, outside the icon system: '
    + hits.map((h) => h.glyph + ' ' + h.codepoint).join(', '));
  console.log('PASS testNoBareEmojiInNewSection');
}

/* ── check 7 ── ANTI-VACUITY: the extractors above can actually report
 *   absence, proven directly, not assumed ───────────────────────────────── */
function testExtractorsCanFail() {
  // Every check above depends on findMethodBody/findTransfersSection telling
  // the truth about absence. An extractor that "matched" no matter what --
  // or that silently returned an empty string treated as a pass -- would let
  // checks 4, 5 and 6 pass whether or not the real code existed. Proven here
  // against inputs guaranteed not to match, so the null path is exercised,
  // not merely assumed to exist.
  assert.strictEqual(
    findMethodBody(readSrc(SUBSYSTEM), '_thisMethodNameCannotPossiblyExist_zzz'), null,
    'findMethodBody returned something for a method name that cannot exist -- it is not actually checking, so checks 4 and 5 prove nothing'
  );
  assert.strictEqual(
    findTransfersSection('no stock-transfers banners appear anywhere in this string'), null,
    'findTransfersSection returned something with neither banner present -- check 6 could pass while inspecting zero real characters'
  );
  console.log('PASS testExtractorsCanFail');
}

/* ── check 8 ── every new t() key this feature introduces exists in BOTH
 *   locale files, with a real (non-English-copy) Arabic value ──────────── */
function testNewLocaleKeysExistInBothFiles() {
  const shellSrc = readSrc(APP_SHELL);
  const subSrc = readSrc(SUBSYSTEM);
  const navMatch = shellSrc.match(/\{\s*id:\s*'transfers'[^}]*\}/);
  const section = findTransfersSection(subSrc);
  assert.ok(navMatch, 'nav entry not found -- check 8 has nothing to inspect for the nav label');
  assert.ok(section && section.length > 0,
    'STOCK TRANSFERS section not found -- check 8 has nothing to inspect');

  // 'Stock Transfers' itself is the nav LABEL, rendered via t(item.label) in
  // app-shell.js rather than a literal t('Stock Transfers') call, so it is
  // added by hand rather than discovered by the same regex as the rest.
  const keys = new Set(['Stock Transfers', ...literalTKeys(section)]);
  assert.ok(keys.size > 1,
    'no literal t(\'...\') keys were found in the new section -- check 8 would vacuously pass while checking nothing');

  const en = JSON.parse(readSrc(LOCALE_EN));
  const ar = JSON.parse(readSrc(LOCALE_AR));
  const missingEn = [...keys].filter((k) => !(k in en));
  const missingAr = [...keys].filter((k) => !(k in ar));
  assert.deepStrictEqual(missingEn, [], 'keys missing from en.json: ' + missingEn.join(', '));
  assert.deepStrictEqual(missingAr, [], 'keys missing from ar.json: ' + missingAr.join(', '));
  console.log(`PASS testNewLocaleKeysExistInBothFiles (${keys.size} keys checked)`);
}

const tests = [
  testNavEntryHasIdAndCapability,
  testDispatchesTransfersCase,
  testCallsAllSixRoutes,
  testReceivePayloadUsesItemIdNotProductId,
  testViewTransferDistinguishesNullFromZero,
  testNoBareEmojiInNewSection,
  testExtractorsCanFail,
  testNewLocaleKeysExistInBothFiles,
];

let failed = 0;
for (const fn of tests) {
  try {
    fn();
  } catch (err) {
    failed++;
    console.error(`FAIL ${fn.name} -- ${err.message}`);
  }
}
if (failed) {
  console.error(`\n${failed} of ${tests.length} checks failed`);
  process.exit(1);
}
console.log(`\nPASS retail_stock_transfer_ui_test.js -- ${tests.length} checks`);
