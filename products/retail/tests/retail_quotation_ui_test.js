/**
 * retail_quotation_ui_test.js -- the Quotations screen actually wires up to
 * the ten routes retail_api.py already ships (Aseel-parity wave A-PAR,
 * schema v33).
 *
 * WHY THIS EXISTS
 * retail_route_reachability_test.py (this directory) asks "does a shipped
 * client call this route" for the whole product, by literal-substring
 * search. `_search_key` collapses `/quotations`, `/quotations/<id>`,
 * `.../send`, `.../accept`, `.../decline` and `.../cancel` to the SAME key,
 * `quotations` -- MEASURED directly against that file's own extractor: six
 * of the eight quotation routes share one key, so the bare nav entry alone
 * marks all six "reachable" with no fetch call anywhere in this file at
 * all. That is the exact shape of miss retail_stock_transfer_ui_test.js's
 * own header already documents for `/stock-transfers/${id}/send` and its
 * siblings -- "both look identical to a substring search that only wants
 * to see 'stock-transfers' appear somewhere." This file asks the questions
 * specific to THIS screen that the generic guard structurally cannot.
 *
 * Harness shape (source discipline, exit code, no framework/dependency)
 * copied from retail_stock_transfer_ui_test.js in this same directory --
 * run standalone with plain Node, no build step, because this frontend has
 * none:
 *
 *     node products/retail/tests/retail_quotation_ui_test.js
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
 * (BRANCHES) -- see subsystem-retail.js: the section is placed right before
 * BRANCHES, mirroring where STOCK TRANSFERS sits relative to its own next
 * section, so the text between the two banners is exactly the new section
 * and nothing else. */
const SECTION_START = '// ── QUOTATIONS AND SALES ORDERS';
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
 * directly by testExtractorsCanFail below.
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

/** The whole QUOTATIONS section, banner to banner. Null if either marker is
 *  missing, so a renamed/moved section fails loudly rather than scanning
 *  zero characters and reporting "all clear". */
function findQuotationsSection(src) {
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
  // BOTH quote styles, and the double-quoted arm is not hypothetical: this
  // extractor originally matched only t('...'), so it silently skipped
  // t("Use Today's Prices") -- double-quoted precisely BECAUSE the string
  // contains an apostrophe. That key was missing from en.json and ar.json
  // and the completeness test built to catch exactly this reported a pass.
  // A key containing an apostrophe is the most likely key to need the other
  // quote style, so the blind spot aligned perfectly with the failure.
  const patterns = [
    /\bt\(\s*'((?:[^'\\]|\\.)*)'/g,
    /\bt\(\s*"((?:[^"\\]|\\.)*)"/g,
  ];
  for (const re of patterns) {
    let m;
    while ((m = re.exec(snippet))) {
      keys.push(m[1].replace(/\\'/g, "'").replace(/\\"/g, '"'));
    }
  }
  return keys;
}

/* ---- emoji-in-markup detection, mirrored from retail_stock_transfer_ui_
 * test.js (itself mirrored from retail_markup_emoji_test.js). Duplicated
 * rather than required in for the identical reason that file states: this
 * check only needs to run over the new section, not the whole frontend. */
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

/* ── check 1 ── nav entry: id AND capability, matching every quotation
 *   route's own @mt_require_capability(CAP_SELL) ───────────────────────── */
function testNavEntryHasIdAndCapability() {
  const shellSrc = readSrc(APP_SHELL);
  const m = shellSrc.match(/\{\s*id:\s*'quotations'[^}]*\}/);
  assert.ok(m && m[0].length > 0,
    'no nav entry with id \'quotations\' found in app-shell.js -- check 1 has nothing to inspect');
  assert.ok(/capability:\s*'retail\.sell'/.test(m[0]),
    'quotations nav entry does not declare capability \'retail.sell\': ' + m[0]);
  console.log('PASS testNavEntryHasIdAndCapability');
}

/* ── check 2 ── the router dispatches to the new screen ──────────────────── */
function testDispatchesQuotationsCase() {
  const subSrc = readSrc(SUBSYSTEM);
  assert.ok(subSrc.length > 0, 'subsystem-retail.js read as empty -- check 2 has nothing to inspect');
  assert.ok(/case\s+'quotations':\s*return\s+this\._renderQuotations\(c\);/.test(subSrc),
    'no "case \'quotations\': return this._renderQuotations(c);" dispatch found in the section switch');
  console.log('PASS testDispatchesQuotationsCase');
}

/* ── check 3 ── every one of the ten routes is actually called, by its own
 *   literal path, not merely the base "quotations" segment ─────────────── */
function testCallsAllTenRoutes() {
  const subSrc = readSrc(SUBSYSTEM);
  assert.ok(subSrc.includes('quotations'),
    'literal "quotations" not found anywhere in subsystem-retail.js -- check 3 has nothing to inspect');

  // 8 routes under /quotations, each asserted by its OWN literal path --
  // this is the per-route proof retail_route_reachability_test.py's coarse
  // key collapse cannot provide (see this file's own header).
  assert.ok(/\/api\/sub\/retail\/quotations['"`]/.test(subSrc) || /'\/api\/sub\/retail\/quotations'/.test(subSrc),
    'no bare GET/POST to /api/sub/retail/quotations found (list/create)');
  assert.ok(/quotations\/\$\{[^}]*\}`/.test(subSrc) || /`\/api\/sub\/retail\/quotations\/\$\{[^}]*\}`/.test(subSrc),
    'no call to .../quotations/${...} found (GET/PUT one quotation)');
  assert.ok(/quotations\/\$\{[^}]*\}\/send/.test(subSrc),
    'no call to .../quotations/${...}/send found (the SEND route)');
  assert.ok(/quotations\/\$\{[^}]*\}\/accept/.test(subSrc),
    'no call to .../quotations/${...}/accept found (the ACCEPT route)');
  assert.ok(/quotations\/\$\{[^}]*\}\/decline/.test(subSrc),
    'no call to .../quotations/${...}/decline found (the DECLINE route)');
  assert.ok(/quotations\/\$\{[^}]*\}\/cancel/.test(subSrc),
    'no call to .../quotations/${...}/cancel found (the CANCEL route)');
  assert.ok(/quotations\/\$\{[^}]*\}\/prepare-conversion/.test(subSrc),
    'no call to .../quotations/${...}/prepare-conversion found (the PREPARE-CONVERSION route)');
  assert.ok(/quotations\/committed-demand/.test(subSrc),
    'no call to .../quotations/committed-demand found (the UNGATED advisory route)');

  // The PUT verb specifically -- update_quotation is PUT, not POST/PATCH
  // (retail_api.py). A call that merely contains the URL but uses the wrong
  // HTTP method would 404/405 against the real route.
  const updateBody = findMethodBody(subSrc, '_saveQuotation');
  assert.ok(updateBody && updateBody.length > 0,
    '_saveQuotation not found (or empty) in subsystem-retail.js -- the PUT-verb check has nothing to inspect');
  assert.ok(/this\._put\(`\/api\/sub\/retail\/quotations\/\$\{[^}]*\}`/.test(updateBody),
    '_saveQuotation does not call this._put(...) against /quotations/${id} -- update_quotation is a PUT route (retail_api.py), and a POST/PATCH here would 404/405 against it');

  console.log('PASS testCallsAllTenRoutes');
}

/* ── check 4 ── conversion goes through the ORDINARY /sales endpoint, never
 *   a second sale writer, carrying quotation_id in the body ─────────────── */
function testConversionPostsToOrdinarySalesEndpointWithQuotationId() {
  const subSrc = readSrc(SUBSYSTEM);
  const body = findMethodBody(subSrc, '_convertQuotation');
  assert.ok(body && body.length > 0,
    '_convertQuotation not found (or empty) in subsystem-retail.js -- check 4 has nothing to inspect');
  assert.ok(/this\._post\(\s*'\/api\/sub\/retail\/sales'/.test(body),
    '_convertQuotation does not POST to the ordinary /api/sub/retail/sales endpoint -- design point (c) requires conversion to go through create_sale, never a second sale writer');
  assert.ok(/quotation_id/.test(body),
    '_convertQuotation\'s POST body does not carry quotation_id -- create_sale has no other way to resolve which quotation is being converted');
  assert.ok(/honour_quoted_prices/.test(body),
    '_convertQuotation does not send honour_quoted_prices -- the two-explicit-buttons choice (design point E7) would have nothing to carry to the server');
  console.log('PASS testConversionPostsToOrdinarySalesEndpointWithQuotationId');
}

/* ── check 5 ── C8: the conversion sheet re-reads the quotation itself for
 *   the variance display, never trusts the /sales response for it ──────── */
function testConvertReReadsQuotationRatherThanTrustingSalesResponse() {
  const subSrc = readSrc(SUBSYSTEM);
  const body = findMethodBody(subSrc, '_convertQuotation');
  assert.ok(body && body.length > 0,
    '_convertQuotation not found (or empty) in subsystem-retail.js -- check 5 has nothing to inspect');
  assert.ok(/this\._viewQuotation\(/.test(body),
    '_convertQuotation does not re-open the quotation (_viewQuotation) after a successful conversion -- C8: create_sale\'s idempotent-replay path returns only {id, sale_number}, so the conversion sheet must read sales_quotations.conversion_variance_json back, not trust this response\'s own shape');
  console.log('PASS testConvertReReadsQuotationRatherThanTrustingSalesResponse');
}

/* ── check 6 ── the expiry override checkbox is wired into the POST body -── */
function testOverrideExpiryCheckboxIsWiredIn() {
  const subSrc = readSrc(SUBSYSTEM);
  const convertBody = findMethodBody(subSrc, '_convertQuotation');
  const openBody = findMethodBody(subSrc, '_openConvertQuotation');
  assert.ok(convertBody && convertBody.length > 0,
    '_convertQuotation not found -- check 6 has nothing to inspect');
  assert.ok(openBody && openBody.length > 0,
    '_openConvertQuotation not found -- check 6 has nothing to inspect');
  assert.ok(openBody.includes('is_expired'),
    '_openConvertQuotation never reads prepare-conversion\'s own is_expired flag -- the override checkbox would show unconditionally instead of only when it means something');
  assert.ok(convertBody.includes('override_expiry'),
    '_convertQuotation does not send override_expiry -- N4\'s CAP_DISCOUNT override path (create_sale) has no way to be reached from this screen at all');
  console.log('PASS testOverrideExpiryCheckboxIsWiredIn');
}

/* ── check 7 ── no bare emoji sits in the new section's markup ──────────── */
function testNoBareEmojiInNewSection() {
  const subSrc = readSrc(SUBSYSTEM);
  const section = findQuotationsSection(subSrc);
  assert.ok(section && section.length > 0,
    'QUOTATIONS AND SALES ORDERS section not found between its own banner and the BRANCHES banner -- check 7 has nothing to inspect');
  const hits = scanForRawEmoji(section);
  assert.deepStrictEqual(hits, [],
    'raw emoji found in the new quotations markup, outside the icon system: '
    + hits.map((h) => h.glyph + ' ' + h.codepoint).join(', '));
  console.log('PASS testNoBareEmojiInNewSection');
}

/* ── check 8 ── ANTI-VACUITY: the extractors above can actually report
 *   absence, proven directly, not assumed ───────────────────────────────── */
function testExtractorsCanFail() {
  assert.strictEqual(
    findMethodBody(readSrc(SUBSYSTEM), '_thisMethodNameCannotPossiblyExist_zzz'), null,
    'findMethodBody returned something for a method name that cannot exist -- it is not actually checking, so checks 3-6 prove nothing'
  );
  assert.strictEqual(
    findQuotationsSection('no quotations banners appear anywhere in this string'), null,
    'findQuotationsSection returned something with neither banner present -- check 7 could pass while inspecting zero real characters'
  );
  console.log('PASS testExtractorsCanFail');
}

/* ── check 9 ── every new t() key this feature introduces exists in BOTH
 *   locale files, with a real (non-English-copy) Arabic value ──────────── */
function testNewLocaleKeysExistInBothFiles() {
  const shellSrc = readSrc(APP_SHELL);
  const subSrc = readSrc(SUBSYSTEM);
  const navMatch = shellSrc.match(/\{\s*id:\s*'quotations'[^}]*\}/);
  const section = findQuotationsSection(subSrc);
  assert.ok(navMatch, 'nav entry not found -- check 9 has nothing to inspect for the nav label');
  assert.ok(section && section.length > 0,
    'QUOTATIONS AND SALES ORDERS section not found -- check 9 has nothing to inspect');

  // 'Quotations' itself is the nav LABEL, rendered via t(item.label) in
  // app-shell.js rather than a literal t('Quotations') call, so it is added
  // by hand rather than discovered by the same regex as the rest -- the
  // identical convention retail_stock_transfer_ui_test.js's own check 8
  // uses for 'Stock Transfers'.
  const keys = new Set(['Quotations', ...literalTKeys(section)]);
  assert.ok(keys.size > 1,
    'no literal t(\'...\') keys were found in the new section -- check 9 would vacuously pass while checking nothing');

  const en = JSON.parse(readSrc(LOCALE_EN));
  const ar = JSON.parse(readSrc(LOCALE_AR));
  const missingEn = [...keys].filter((k) => !(k in en));
  const missingAr = [...keys].filter((k) => !(k in ar));
  assert.deepStrictEqual(missingEn, [], 'keys missing from en.json: ' + missingEn.join(', '));
  assert.deepStrictEqual(missingAr, [], 'keys missing from ar.json: ' + missingAr.join(', '));

  // A copy-pasted English string masquerading as a translation defeats the
  // whole point of this check (this product ships Arabic and is RTL --
  // CLAUDE.md). Sampled rather than exhaustive: a handful of the longer,
  // most distinctive sentences this wave introduces are enough to catch a
  // lazy blanket copy without hardcoding all 47 keys' expected values here.
  const sample = [
    'Once sent, this document is locked -- its lines and prices can never change again, only the whole thing cancelled and re-issued.',
    'Issue a quotation or sales order for a customer -- convert it to a real sale once they accept.',
    'Withdraw this quotation? This cannot be undone.',
  ];
  for (const k of sample) {
    assert.notStrictEqual(ar[k], en[k],
      `ar.json's value for ${JSON.stringify(k)} is byte-identical to en.json's -- looks like an English copy, not a real Arabic translation`);
  }
  console.log(`PASS: testNewLocaleKeysExistInBothFiles (${keys.size} keys checked)`);
}

const tests = [
  testNavEntryHasIdAndCapability,
  testDispatchesQuotationsCase,
  testCallsAllTenRoutes,
  testConversionPostsToOrdinarySalesEndpointWithQuotationId,
  testConvertReReadsQuotationRatherThanTrustingSalesResponse,
  testOverrideExpiryCheckboxIsWiredIn,
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
console.log(`\nPASS retail_quotation_ui_test.js -- ${tests.length} checks`);
