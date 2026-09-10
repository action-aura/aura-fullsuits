/**
 * retail_receipt_localization_test.js -- closes the seam between the i18n
 * test suite (covers the live UI DOM) and the receipt/branding test suite
 * (covers whether the shop's own brand appears) that let an entire printed
 * receipt render in English on a till running in Arabic.
 *
 * THE DEFECT (fixed 2026-09-09 -- see subsystem-retail.js's own comment
 * directly above _printReceipt's `html` template literal):
 *
 *   AuraI18n translates the live app by sweeping the TEXT NODES of
 *   document.body and re-sweeping every DOM mutation through a
 *   MutationObserver (i18n.js). That is why almost every string in this
 *   product comes out Arabic without anyone writing t(...) by hand.
 *   _printReceipt(), however, builds a receipt document and writes it into
 *   a print IFRAME with doc.write() -- a SEPARATE document object. The
 *   sweep never reaches it and the observer was never watching it. Every
 *   label inside that one template has to be wrapped in a translation call
 *   BY HAND, which is not the rule anywhere else in this file, and nothing
 *   enforced it.
 *
 *   Measured 2026-09-09 on a till already running in Arabic (dir="rtl",
 *   sidebar reading "لوحة التحكم"): the printed receipt read "Subtotal /
 *   Tax / Total / Paid / Change / Thank you" -- entirely in English. Only
 *   the currency was Arabic, because that comes from the money formatter,
 *   not the sweep. A Jordanian shop running its whole till in Arabic handed
 *   every customer an English receipt -- the single most customer-visible
 *   thing this product prints.
 *
 *   Two suites already existed and both missed it: the i18n tests cover the
 *   UI surface (document.body); retail_branding_receipt_test.js covers
 *   whether the shop's OWN brand text (name/address/tax number) reaches the
 *   printed page. Neither one reads the STATIC LABELS baked into
 *   _printReceipt's template. This file reads exactly that, so the seam
 *   between the two suites has an owner.
 *
 * ── A MOVING TARGET WHILE THIS FILE WAS BEING WRITTEN ───────────────────
 *
 *   subsystem-retail.js was being edited concurrently by another agent
 *   while this test was authored, TWICE, in ways that changed the actual
 *   call name a correct label has to go through:
 *
 *     1. First it landed a `_receiptTranslator()` returning a function
 *        assigned to a NEW local named `rt`, alongside every label in the
 *        template switching from `t(...)` to `rt(...)`.
 *     2. Then it was reworked again to `const t =
 *        this._receiptTranslator(...)` -- an ordinary JS lexical SHADOW of
 *        the global `t`, local to _printReceipt only -- so every label in
 *        the template reads `t(...)` again, unchanged in the source text,
 *        but now resolving against `branding_receipt_language`'s chosen
 *        catalogue instead of the till's live one.
 *
 *   Both shapes exist for the same real, deliberate feature (2026-09-10,
 *   setting `branding_receipt_language`): a shop can force ONE receipt to
 *   a language independent of the till's own -- the reverse of the bug
 *   above (an English-language till serving an Arabic-speaking customer).
 *   It defaults ('auto') to reproducing the till's own language exactly,
 *   byte for byte, so an install that never touches the new setting is
 *   unaffected either way. Per ENGINEERING.md ("either the code is wrong
 *   or the test is wrong -- fix that one"): the code was not wrong at
 *   either point, this test's hardcoded assumption about the call name was
 *   stale, twice, so the harvesting/violation regexes below recognize
 *   EITHER `rt(...)` or `t(...)` -- the invariant this file enforces
 *   (every visible label goes through a real translation call, resolvable
 *   in both catalogues) is unchanged regardless of which name is live at
 *   any given moment. Nothing about strictness moved: a bare literal still
 *   fails, an uncatalogued label still fails. Because a hard-coded mutation
 *   anchor re-broke on every one of these renames while this file was being
 *   written, the mutation proofs below (findSubtotalRow) locate the
 *   Subtotal row's wrapper name DYNAMICALLY from whatever is actually on
 *   disk at run time, rather than assuming either spelling -- if the
 *   wrapper moves again, or the row itself is restructured, that lookup's
 *   own "found 0/2 times" assertion will say so loudly rather than silently
 *   proving nothing.
 *
 * ── WHAT THIS CHECKS ─────────────────────────────────────────────────────
 *
 *   1. Every piece of text sitting BETWEEN TWO TAGS inside _printReceipt's
 *      `html` template literal (e.g. the text in `<span>Subtotal</span>`)
 *      must resolve, once every `${...}` interpolation is peeled away, to
 *      nothing but whitespace/punctuation -- i.e. no bare English word
 *      survives outside a translation call.
 *   2. Every label actually passed to `rt(...)`/`t(...)` inside that
 *      template must have a real, non-empty entry in BOTH locales/en.json
 *      and locales/ar.json -- a translation call with no catalogue entry
 *      silently renders English, which is the exact same customer-facing
 *      bug wearing a disguise.
 *
 * ── WHAT THIS DELIBERATELY IGNORES (so it does not cry wolf) ────────────
 *
 *   - Interpolated VALUES: sale numbers, totals, dates, product names --
 *     these are data, not labels.
 *   - The `<style>...</style>` block (CSS, never user-visible text).
 *   - `${lines}`, `${identityBlock}`, `${einvoiceBlock}`, `${brandingBlock}`
 *     -- opaque blocks built by OTHER functions (`_receiptIdentityBlock`,
 *     `_einvoiceReceiptBlock`, `_receiptBranding`/`_brandingReceiptBlock`).
 *     Extraction below isolates only the `html` template literal itself, so
 *     it never even sees those functions' own definitions -- they are each
 *     somebody else's function to test (`_receiptIdentityBlock`'s own
 *     Cashier/Customer labels do correctly use `rt(...)` too, confirmed by
 *     eye while reading this file, but proving that is out of scope here).
 *   - Attribute values and class names (only text BETWEEN tags is scanned).
 *   - Bare punctuation/symbols left over once interpolations are stripped
 *     ("#", "x" for multiplication, "-" for a negative amount).
 *   - branding_receipt_footer -- shop-typed text from Settings, which must
 *     NOT be translated. It reaches the template only as
 *     `${this._esc(branding.branding_receipt_footer)}`, so it is already
 *     excluded by the interpolation-stripping rule above; this file adds no
 *     special case for it, and none is needed.
 *
 * ── EXTRACTION METHOD ────────────────────────────────────────────────────
 *
 *   This does NOT scan _printReceipt's whole function body as flat text.
 *   An earlier version of this file did exactly that and produced a false
 *   "bare label" violation spanning several lines of plain JS (the `const
 *   einvoiceBlock = ...` / `const branding = ...` statements between the
 *   `lines` template and the `html` template) -- the naive `>text<` scan
 *   cannot tell "inside a template literal" from "plain JS that happens to
 *   contain a `>` from one small template and a `<` from the next", so it
 *   swallowed real code as a fake text node. Fixed by ISOLATING only the
 *   `html` template literal first, with a small recursive-descent scanner
 *   (findTemplateLiteralEnd/skipExpression/skipQuotedString below) that
 *   understands backticks, `${...}`, nested template literals and quoted
 *   strings well enough to find the ONE matching closing backtick --
 *   required because the Discount/Tax/Change rows are each their own
 *   `${cond ? \`<div>...rt(...)...</div>\` : ''}`: a nested template
 *   literal, with its OWN tags and its OWN translation calls, sitting
 *   inside the outer `${...}`. Once the `html` literal is correctly
 *   isolated, `stripAllInterpolations()` removes every `${...}` from ITS
 *   text the same way: innermost brace-free `${...}` first (the inner
 *   translation/`_fmt()` calls), which turns what used to be the outer
 *   ternary into a brace-free `${...}` of its own, caught on the next pass.
 *   What remains is scanned for `>text<`, and any surviving Latin letters
 *   are the bug.
 *
 * ── MUTATION-PROVEN (see testMutationProofs) ────────────────────────────
 *   - the real, live Subtotal row's translation call turned bare must fail
 *     the "no unwrapped label" check (D1), while the same line left wrapped
 *     must not (A1);
 *   - that same real call repointed at an uncatalogued label must fail the
 *     catalogue check (D2) while STILL PASSING the unwrapped-label check
 *     (D2b) -- proving the two checks are not redundant, which is the
 *     "disguise" the header above warns about;
 *   - renaming _printReceipt so the extraction anchor no longer matches
 *     must fail loudly rather than silently scanning nothing (L1);
 *   - a hand-built near-empty template fixture must be rejected by the
 *     anti-vacuity guard rather than reported as a clean scan (V1).
 *
 * No test framework is configured for this vanilla-JS, build-step-free
 * frontend (see CLAUDE.md), so this runs standalone on Node built-ins:
 *
 *   node products/retail/tests/retail_receipt_localization_test.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');

const FRONTEND_DIR = path.join(__dirname, '..', 'frontend');
const SRC_PATH = path.join(FRONTEND_DIR, 'subsystem-retail.js');
const EN_PATH = path.join(FRONTEND_DIR, 'locales', 'en.json');
const AR_PATH = path.join(FRONTEND_DIR, 'locales', 'ar.json');

const SRC = fs.readFileSync(SRC_PATH, 'utf8');
const EN = JSON.parse(fs.readFileSync(EN_PATH, 'utf8'));
const AR = JSON.parse(fs.readFileSync(AR_PATH, 'utf8'));

// A minimum number of DISTINCT labels the real template must yield. This
// exists solely so the harvesting regex going quietly blind (a reformat, a
// renamed helper, a switch away from template literals) fails LOUDLY as
// "found suspiciously few labels" instead of silently reporting a clean
// bill of health over zero real checks -- the exact "pass condition IS the
// bug signature" trap, and the specific failure mode this repo has already
// been bitten by (see retail_icons_test.js's own ANTI-VACUITY section for
// the sibling case in the icon harvester).
const MIN_EXPECTED_LABELS = 6;

// ─────────────────────────────────────────────────────────────────────────
// STAGE 1 -- locate _printReceipt's function body by anchor slicing (both
// anchors are single LINES; subsystem-retail.js is LF-only, no CRLF,
// confirmed against the live file, so a plain indexOf slice is exact).
// ─────────────────────────────────────────────────────────────────────────

const START_ANCHOR = 'async _printReceipt(saleData) {';
const END_ANCHOR = 'frame.contentWindow.focus(); frame.contentWindow.print(); } catch (e) {} }, 150);';

/** Slice exactly the body of _printReceipt out of a subsystem-retail.js
 *  source string. Asserts each anchor occurs EXACTLY once first -- the same
 *  discipline retail_icons_test.js's mutate() applies to its own anchors --
 *  so a rename/reformat that breaks the slice fails with "re-anchor it"
 *  instead of silently extracting nothing, or the wrong function. */
function extractPrintReceiptBody(src) {
  const startHits = src.split(START_ANCHOR).length - 1;
  assert.strictEqual(startHits, 1,
    `_printReceipt start anchor found ${startHits} time(s), expected exactly 1. ` +
    'subsystem-retail.js was renamed/reformatted around _printReceipt -- re-anchor this test.');
  const endHits = src.split(END_ANCHOR).length - 1;
  assert.strictEqual(endHits, 1,
    `_printReceipt end anchor found ${endHits} time(s), expected exactly 1. ` +
    "re-anchor this test's END_ANCHOR.");

  const startIdx = src.indexOf(START_ANCHOR);
  const endIdx = src.indexOf(END_ANCHOR, startIdx) + END_ANCHOR.length;
  assert.ok(endIdx > startIdx, '_printReceipt end anchor was found before the start anchor.');
  return src.slice(startIdx, endIdx);
}

// ─────────────────────────────────────────────────────────────────────────
// STAGE 2 -- isolate JUST the `html` template literal out of that body,
// with a tiny recursive-descent scanner. Required because a flat `>text<`
// regex over the WHOLE function body treats the plain-JS gaps between
// separate template literals (`lines`, `footerLine`, `html`, ...) as fair
// game too, and can capture real code as a fake "text node" the moment a
// `>` from one small template and a `<` from the next end up on the same
// line-free stretch -- see the EXTRACTION METHOD header comment for the
// concrete false positive this test hit while being written.
// ─────────────────────────────────────────────────────────────────────────

const HTML_TEMPLATE_ANCHOR = 'const html = `';

/** Scans a single-quoted or double-quoted string starting AT its opening
 *  quote character and returns the index just past its closing quote,
 *  honouring backslash escapes. */
function skipQuotedString(str, start) {
  const quote = str[start];
  let i = start + 1;
  while (i < str.length) {
    if (str[i] === '\\') { i += 2; continue; }
    if (str[i] === quote) return i + 1;
    i++;
  }
  throw new Error(`Unterminated ${quote} string while scanning the template literal (index ${start}).`);
}

/** Scans a `${...}` expression body starting right after the `${` and
 *  returns the index just past its matching closing `}` -- tracking plain
 *  `{`/`}` nesting (ternaries, object literals), quoted strings (so a brace
 *  inside a string never perturbs the depth count) and, recursively, any
 *  NESTED template literal the expression itself contains (the Discount/
 *  Tax/Change rows: `${cond ? \`<div>...\` : ''}`). */
function skipExpression(str, start) {
  let i = start;
  let depth = 1;
  while (i < str.length) {
    const c = str[i];
    if (c === '\\') { i += 2; continue; }
    if (c === '`') { i = findTemplateLiteralEnd(str, i + 1) + 1; continue; }
    if (c === "'" || c === '"') { i = skipQuotedString(str, i); continue; }
    if (c === '{') { depth++; i++; continue; }
    if (c === '}') {
      depth--;
      i++;
      if (depth === 0) return i;
      continue;
    }
    i++;
  }
  throw new Error('Unterminated "${...}" expression while scanning the template literal (index ' + start + ').');
}

/** Scans template-literal TEXT starting right after its opening backtick
 *  and returns the index of its matching, unescaped closing backtick --
 *  the mutual recursion with skipExpression is what lets this correctly
 *  walk past a nested template literal inside a `${...}` and keep going in
 *  the OUTER template afterwards. */
function findTemplateLiteralEnd(str, start) {
  let i = start;
  while (i < str.length) {
    const c = str[i];
    if (c === '\\') { i += 2; continue; }
    if (c === '`') return i;
    if (c === '$' && str[i + 1] === '{') { i = skipExpression(str, i + 2); continue; }
    i++;
  }
  throw new Error(`Unterminated template literal (started scanning at index ${start}).`);
}

/** Isolates the exact text of the `html` template literal (the backticks
 *  themselves excluded) out of an extracted _printReceipt function body. */
function extractHtmlTemplateLiteral(body) {
  const hits = body.split(HTML_TEMPLATE_ANCHOR).length - 1;
  assert.strictEqual(hits, 1,
    `Expected exactly one "${HTML_TEMPLATE_ANCHOR}" inside _printReceipt's body, found ${hits}. ` +
    're-anchor HTML_TEMPLATE_ANCHOR.');
  const startIdx = body.indexOf(HTML_TEMPLATE_ANCHOR) + HTML_TEMPLATE_ANCHOR.length;
  const endIdx = findTemplateLiteralEnd(body, startIdx);
  return body.slice(startIdx, endIdx);
}

/** Remove the <style>...</style> block from the isolated html template.
 *  Its CSS carries plain, unpaired-looking `{`/`}` characters (rule bodies)
 *  that are not part of any `${...}` interpolation, so it has to be gone
 *  before stripAllInterpolations() below -- and it is pure CSS, never
 *  user-visible text, which is explicitly out of scope for this file. */
function stripStyleBlock(templateText) {
  const hits = (templateText.match(/<style>/g) || []).length;
  assert.strictEqual(hits, 1,
    `Expected exactly one <style> block inside the html template, found ${hits}.`);
  const stripped = templateText.replace(/<style>[\s\S]*?<\/style>/, '');
  assert.ok(!stripped.includes('<style>'), '<style> block removal did not actually remove it.');
  return stripped;
}

/** Peel every `${...}` interpolation out of `text`, innermost first, until
 *  none remain -- see the header's EXTRACTION METHOD note. Now that `text`
 *  is already isolated to just the html template literal (stage 2 above),
 *  every `{`/`}` remaining in it (after the <style> block is stripped)
 *  belongs to a real `${...}`, so this is safe: a single pass only ever
 *  matches a `${...}` with no braces inside it (e.g. an inner
 *  `${rt('Discount')}`); once those are gone, what used to be the OUTER
 *  ternary becomes brace-free itself and is caught on the next pass.
 *  Capped iteration count so a genuinely unbalanced `${` (an extraction
 *  bug, not a localization bug) fails loudly instead of spinning forever. */
function stripAllInterpolations(text) {
  let cur = text;
  for (let i = 0; i < 50; i++) {
    const next = cur.replace(/\$\{[^{}]*\}/g, '');
    if (next === cur) {
      assert.ok(!cur.includes('${'),
        'stripAllInterpolations() stalled with an unresolved "${" still present -- likely an ' +
        'extraction bug in this test, not necessarily a real localization bug. Investigate ' +
        'before trusting the result.');
      return cur;
    }
    cur = next;
  }
  throw new Error('stripAllInterpolations() did not converge in 50 passes -- runaway/unbalanced "${".');
}

/** Every user-visible label harvested from an `rt(...)` or `t(...)` call
 *  anywhere in `templateText` -- wrapped in `${}` or not. (A bare `rt('X')`
 *  missing its `${}` wrapper is ALSO a real bug: its rendered text is the
 *  literal string `rt('X')`, which contains letters, so
 *  findUnwrappedLabelViolations below independently catches it too.) Both
 *  names are accepted: `rt` is the receipt-scoped translator this template
 *  actually uses today (see "A MOVING TARGET" above); the legacy global
 *  `t` is accepted too so this test does not itself become the thing that
 *  breaks the next time the wrapper name moves, as long as whatever is
 *  used still resolves through a real catalogue lookup. `\b(?:rt|t)\(`
 *  requires a non-word character immediately before the call name, so this
 *  never matches inside `this._fmt(`/`this._esc(`/`_receiptTranslator(`
 *  (each ends in a word character immediately before its own `(`). */
const T_CALL_RE = /\b(?:rt|t)\(\s*['"]([^'"]*)['"]\s*\)/g;

function harvestLabels(templateText) {
  const labels = [];
  let m;
  T_CALL_RE.lastIndex = 0;
  while ((m = T_CALL_RE.exec(templateText)) !== null) labels.push(m[1]);
  return labels;
}

/** Every text node (text sitting between `>` and `<`) that still contains a
 *  Latin letter after every `${...}` interpolation and the <style> block
 *  have been stripped from the ISOLATED html template. A non-empty result
 *  is exactly the bug this file exists to catch: a label that prints in
 *  raw English no matter what language the till (or the receipt-language
 *  override) is set to. */
function findUnwrappedLabelViolations(templateText) {
  const noStyle = stripStyleBlock(templateText);
  const noInterp = stripAllInterpolations(noStyle);
  const violations = [];
  const nodeRe = />([^<>]*)</g;
  let m;
  while ((m = nodeRe.exec(noInterp)) !== null) {
    const text = m[1];
    if (/[A-Za-z]/.test(text)) violations.push(text.trim());
  }
  return violations;
}

// ─────────────────────────────────────────────────────────────────────────
// CHECKS -- each takes subsystem-retail.js SOURCE TEXT (not a loaded/run
// module: this is static analysis of the template text, not a DOM/print
// run -- retail_branding_receipt_test.js already covers driving the real
// function end-to-end through a stubbed print iframe) so the mutation
// proofs below can run the identical check against a broken copy without
// duplicating any assertions.
// ─────────────────────────────────────────────────────────────────────────

function checkNoUnwrappedLabels(src) {
  const body = extractPrintReceiptBody(src);
  const templateText = extractHtmlTemplateLiteral(body);
  const violations = findUnwrappedLabelViolations(templateText);
  assert.deepStrictEqual(violations, [],
    `${violations.length} bare (non-translated) label(s) found between tags in _printReceipt's ` +
    'html template -- these will print in English no matter what language the till (or the ' +
    `receipt-language override) is set to: ${violations.map((v) => JSON.stringify(v)).join(', ')}`);
}

function checkLabelsResolveInBothCatalogues(src, enDict, arDict) {
  const body = extractPrintReceiptBody(src);
  const templateText = extractHtmlTemplateLiteral(body);
  const labels = Array.from(new Set(harvestLabels(templateText)));

  // ANTI-VACUITY -- see MIN_EXPECTED_LABELS above.
  assert.ok(labels.length >= MIN_EXPECTED_LABELS,
    `Only ${labels.length} distinct label(s) harvested from _printReceipt's html template ` +
    `(${JSON.stringify(labels)}) -- expected at least ${MIN_EXPECTED_LABELS}. Treat this as the ` +
    'harvesting regex going blind, not a clean result: the real template carries Receipt, ' +
    'Subtotal, Discount, Tax, Total, Paid, Change due and Thank you.');

  const missingEn = labels.filter((l) => !(Object.prototype.hasOwnProperty.call(enDict, l) && enDict[l]));
  const missingAr = labels.filter((l) => !(Object.prototype.hasOwnProperty.call(arDict, l) && arDict[l]));
  assert.deepStrictEqual(missingEn, [],
    `label(s) used by rt()/t() in _printReceipt have no entry in locales/en.json: ${missingEn.join(', ')}`);
  assert.deepStrictEqual(missingAr, [],
    'label(s) used by rt()/t() in _printReceipt have no entry in locales/ar.json -- these silently ' +
    `render English on an Arabic till, the exact bug this file exists to catch: ${missingAr.join(', ')}`);
}

// ─────────────────────────────────────────────────────────────────────────
// MUTATION HARNESS -- same shape as retail_icons_test.js's own: an anchor
// that no longer occurs exactly once fails LOUDLY ("re-anchor it") instead
// of silently applying to zero occurrences and proving nothing.
// ─────────────────────────────────────────────────────────────────────────

function mutate(src, find, replace) {
  const hits = src.split(find).length - 1;
  assert.strictEqual(hits, 1,
    `Mutation anchor occurs ${hits} time(s), expected exactly 1:\n  ${JSON.stringify(find)}\n\n` +
    'A mutation that no longer applies would let the proof below pass while proving nothing. ' +
    'Re-anchor it.');
  return src.replace(find, replace);
}

async function provesMutation(what, src, find, replace, check) {
  const broken = mutate(src, find, replace);
  let threw = null;
  try {
    await check(broken);
  } catch (err) {
    threw = err;
  }
  assert.ok(threw,
    `MUTATION SURVIVED -- ${what}\n` +
    'The guard for this passed against a build with the behaviour deliberately broken, so it ' +
    'is not actually watching it. Fix the check, not the mutation.');
  return `${what}  [caught: ${String(threw.message || threw).split('\n')[0].slice(0, 120)}]`;
}

// The Subtotal row this file mutates below, LOCATED DYNAMICALLY rather than
// hard-coded as a literal string. While this file was being written, the
// concurrent edit to subsystem-retail.js changed the label wrapper's exact
// call name three separate times within the same working session (global
// `t` -> a new `rt` -> a local shadow reusing the name `t` -> back to `rt`
// again) -- a hard-coded anchor re-broke every time, which is real evidence
// the file is still being actively reshaped, not evidence this test is
// wrong. Rather than keep guessing the current spelling, this reads
// whichever wrapper is ACTUALLY on disk right now straight out of SRC and
// mutates that -- the row itself (rcpt-line / Subtotal / _fmt(saleData.
// subtotal)) is the stable, semantic anchor; the call name is not assumed.
const SUBTOTAL_ROW_RE =
  /<div class="rcpt-line"><span>\$\{(rt|t)\('Subtotal'\)\}<\/span><span>\$\{this\._fmt\(saleData\.subtotal\)\}<\/span><\/div>/;

function findSubtotalRow(src) {
  const hits = (src.match(new RegExp(SUBTOTAL_ROW_RE.source, 'g')) || []).length;
  assert.strictEqual(hits, 1,
    `The Subtotal row (rt('Subtotal') or t('Subtotal'), whichever is currently live) was found ` +
    `${hits} time(s) in subsystem-retail.js, expected exactly 1. Re-anchor SUBTOTAL_ROW_RE -- the ` +
    'row structure itself, not just its wrapper name, has changed.');
  const m = src.match(SUBTOTAL_ROW_RE);
  return { line: m[0], wrapper: m[1] };
}

async function testMutationProofs() {
  const proved = [];
  const { line: SUBTOTAL_LINE, wrapper: W } = findSubtotalRow(SRC);
  const wrappedSubtotal = `\${${W}('Subtotal')}`;

  // ── DENY HALF -- a real label de-wrapped ────────────────────────────
  proved.push(await provesMutation(
    `D1. a real ${wrappedSubtotal} call is de-wrapped to bare "Subtotal"`,
    SRC,
    SUBTOTAL_LINE,
    SUBTOTAL_LINE.replace(wrappedSubtotal, 'Subtotal'),
    async (broken) => checkNoUnwrappedLabels(broken)));

  // ── ALLOW HALF -- the same real line, left wrapped, must not trip it ─
  checkNoUnwrappedLabels(SRC);
  proved.push(`A1. the real, unmutated Subtotal line (still wrapped in ${W}()) is left alone  [no throw]`);

  // ── DENY HALF -- "the same bug wearing a disguise" ──────────────────
  // Still wrapped, so D1's check is blind to this one ON PURPOSE (proved by
  // D2b below) -- only the catalogue check can see a label with no
  // translation, which is exactly as silently-English as an unwrapped
  // literal.
  const uncatalogued = SUBTOTAL_LINE.replace(wrappedSubtotal, `\${${W}('Something Not In The Catalogue')}`);
  proved.push(await provesMutation(
    `D2. a real \${${W}(...)} call is repointed at a label no catalogue has ` +
    '("the same bug wearing a disguise")',
    SRC,
    SUBTOTAL_LINE,
    uncatalogued,
    async (broken) => checkLabelsResolveInBothCatalogues(broken, EN, AR)));

  // The other half of D2's own claim: prove the two checks are genuinely
  // independent, not that one subsumes the other. If THIS threw, D2's
  // premise above ("still wrapped, D1 is blind to it") would be false.
  checkNoUnwrappedLabels(mutate(SRC, SUBTOTAL_LINE, uncatalogued));
  proved.push('D2b. the SAME mutant still passes the unwrapped-label check -- confirms the two ' +
    'checks are not redundant  [no throw]');

  // ── LOUD-FAILURE HALF -- extraction anchor drift ────────────────────
  // If _printReceipt is ever renamed, a best-effort extractor could easily
  // fall back to "found nothing" and let every check above pass vacuously.
  // This anchor-based extractor refuses to guess: it must fail loudly.
  proved.push(await provesMutation(
    'L1. _printReceipt is renamed -- extraction must fail loudly, not silently scan nothing',
    SRC,
    START_ANCHOR,
    'async _printReceiptRenamed(saleData) {',
    async (broken) => extractPrintReceiptBody(broken)));

  // ── ANTI-VACUITY HALF -- a near-empty template must not report clean ──
  // Not a mutation of the real 9000+-line file (too fragile an anchor to
  // reformat a whole template safely while another agent is concurrently
  // editing this exact file) -- a synthetic minimal fixture standing in for
  // "the harvesting regex went blind after a reformat", proving
  // MIN_EXPECTED_LABELS actually rejects a too-small result instead of
  // reporting a false clean bill of health on it.
  const nearEmptyBody = 'async _printReceipt(saleData) { const html = `<div class="rcpt-center">' +
    "${rt('Thank you')}</div>`; frame.contentWindow.focus(); frame.contentWindow.print(); } catch (e) {} }, 150);";
  let threwOnNearEmpty = null;
  try {
    checkLabelsResolveInBothCatalogues(nearEmptyBody, EN, AR);
  } catch (err) {
    threwOnNearEmpty = err;
  }
  assert.ok(threwOnNearEmpty,
    'MUTATION SURVIVED -- V1. a near-empty template (1 label) was not rejected by the ' +
    'anti-vacuity guard.');
  proved.push('V1. a synthetic near-empty template (1 label) is rejected by the anti-vacuity ' +
    `guard, not reported as a clean scan  [caught: ${String(threwOnNearEmpty.message).slice(0, 90)}]`);

  console.log(`PASS: ${proved.length} guards proved by breaking the behaviour they watch (A1/D2b ` +
    'prove the inverse: confirming they do NOT break what they should not):');
  for (const p of proved) console.log('      ' + p);
}

// ─────────────────────────────────────────────────────────────────────────
// RUNNER -- every check runs, every failure is collected. Same shape as
// retail_payment_grid_test.js's own CASES/main().
// ─────────────────────────────────────────────────────────────────────────

function testRealSourceHasNoUnwrappedLabels() {
  checkNoUnwrappedLabels(SRC);
  console.log('PASS: every label between tags in the real _printReceipt html template goes through ' +
    'a translation call');
}

function testRealSourceLabelsResolveInBothCatalogues() {
  checkLabelsResolveInBothCatalogues(SRC, EN, AR);
  console.log('PASS: every rt()/t() label in the real _printReceipt html template resolves in both ' +
    'locales/en.json and locales/ar.json');
}

const CASES = [
  testRealSourceHasNoUnwrappedLabels,
  testRealSourceLabelsResolveInBothCatalogues,
  testMutationProofs,
];

async function main() {
  let failed = 0;
  for (const fn of CASES) {
    try {
      await fn();
    } catch (err) {
      failed += 1;
      console.error('  FAIL ' + fn.name);
      console.error('       ' + (err && err.message ? err.message : err));
    }
  }
  if (failed) {
    console.error(`FAIL: retail_receipt_localization_test.js — ${failed} of ${CASES.length} case(s) failed`);
    process.exitCode = 1;
  } else {
    console.log(`PASS: retail_receipt_localization_test.js — ${CASES.length} case(s)`);
  }
}

main();
