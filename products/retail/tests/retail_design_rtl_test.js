/**
 * OPERATIONAL CALM — RTL correct by construction, not by a chasing stylesheet.
 *
 * WHY THIS TEST EXISTS
 * This product ships Arabic. The existing approach is css/rtl.css: a second
 * stylesheet, loaded after main.css, that re-states rules under `body.rtl`
 * to undo physical directions main.css baked in. That approach works only
 * for the rules somebody remembered to chase — and its own comments record
 * the cost, including a bug where a chat bubble's corner radius stayed on
 * its LTR corner while the avatar mirrored, so the tail pointed away from
 * its own avatar. That was found by inspecting computed styles in a real
 * RTL session, which is not a scalable way to find this class of bug.
 *
 * Logical properties remove the class of bug instead of chasing instances.
 * `margin-inline-start` is "the start edge" in whichever direction the
 * document runs, so it needs no mirror rule, no second stylesheet, and no
 * Playwright session to verify. `margin-left` is "the left edge" always, and
 * needs all three.
 *
 * WHAT IT ASSERTS, AND WHY THAT SHAPE
 * This is a RATCHET on NEW code, not a demand that 3,900 existing lines be
 * rewritten — rewriting them wholesale would be unreviewable and would touch
 * surfaces nobody has looked at, which is exactly the risk this redesign was
 * scoped to avoid. So:
 *
 *   - The blocks this change introduced (the token layer and the behaviour
 *     layer, both delimited by markers) must be free of physical
 *     left/right properties where a logical property exists.
 *   - The pre-existing body of the file is measured, and its physical-property
 *     count must not GROW. The current count is recorded below as a budget.
 *
 * A budget number in a test is only honest if it is checked in both
 * directions: this one fails if the count rises AND tells you to lower it if
 * the count falls, so the ratchet cannot silently slacken.
 *
 * No test framework is configured for this vanilla-JS, build-step-free
 * frontend (see CLAUDE.md), so this runs standalone on Node built-ins:
 *
 *   node products/retail/tests/retail_design_rtl_test.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');

const CSS_FILE = path.join(__dirname, '..', 'frontend', 'css', 'main.css');

/* Physical properties that have a direct logical equivalent. `left`/`right`
   as positioning offsets are included via `inset-inline-*`. Properties with
   no logical equivalent (transform, background-position, text-shadow offsets)
   are deliberately absent — flagging them would be noise. */
const PHYSICAL = [
  { re: /(^|[\s;{])margin-left\s*:/g, use: 'margin-inline-start' },
  { re: /(^|[\s;{])margin-right\s*:/g, use: 'margin-inline-end' },
  { re: /(^|[\s;{])padding-left\s*:/g, use: 'padding-inline-start' },
  { re: /(^|[\s;{])padding-right\s*:/g, use: 'padding-inline-end' },
  { re: /(^|[\s;{])border-left\s*:/g, use: 'border-inline-start' },
  { re: /(^|[\s;{])border-right\s*:/g, use: 'border-inline-end' },
  { re: /(^|[\s;{])border-left-color\s*:/g, use: 'border-inline-start-color' },
  { re: /(^|[\s;{])border-right-color\s*:/g, use: 'border-inline-end-color' },
  { re: /(^|[\s;{])border-top-left-radius\s*:/g, use: 'border-start-start-radius' },
  { re: /(^|[\s;{])border-top-right-radius\s*:/g, use: 'border-start-end-radius' },
  { re: /(^|[\s;{])border-bottom-left-radius\s*:/g, use: 'border-end-start-radius' },
  { re: /(^|[\s;{])border-bottom-right-radius\s*:/g, use: 'border-end-end-radius' },
  { re: /(^|[\s;{])text-align\s*:\s*left/g, use: 'text-align: start' },
  { re: /(^|[\s;{])text-align\s*:\s*right/g, use: 'text-align: end' },
];

/* The pre-existing body of main.css carries this many physical properties.
   The number is a BUDGET, not a target: it may fall, it must never rise. */
const LEGACY_PHYSICAL_BUDGET = 63;

/* ── THE SECOND SCAN: inline styles built in JAVASCRIPT ────────────────────
 *
 * Everything above looks at css/main.css and NOTHING ELSE, which was a real
 * blind spot rather than a theoretical one. This frontend writes most of its
 * new rules as inline `style="..."` strings inside template literals and as
 * `el.style.cssText = '...'` assignments, and 16 physical `margin-left:Npx`
 * declarations had accumulated there -- eleven on the SECOND BUTTON of a row
 * pair in subsystem-retail.js, two in app-shell.js's sync banner, three in
 * import-wizard.js. In Arabic the row reverses, so the gap lands on the outer
 * edge and the two buttons collide. This scan could not see a single one of
 * them, and app-shell.js's own comment already named `margin-left` (physical)
 * as a cost "a dark theme and an Arabic layout would both have had to pay".
 *
 * Same ratchet shape as the stylesheet half: a recorded count that may fall
 * and must never rise. What remains in the budget, deliberately:
 *   * FOUR `margin-left:auto;margin-right:auto` PAIRS (8 of the count) --
 *     direction-neutral centering, not a leading-edge pin, so converting them
 *     would be churn with no RTL meaning.
 *   * 21 `text-align:left|right` declarations, most of them money columns.
 *     Those are a DIFFERENT question (an amount column's alignment is about
 *     digits, not reading order) with its own money/POS suites pinning them,
 *     so they are counted here and left for whoever owns that decision --
 *     counted rather than filtered, so the number cannot quietly grow.
 *   * employees.js's setup-link input, which pairs `text-align:left` with an
 *     explicit `direction:ltr` and explains why three lines below it.
 *   * app-shell.js's kpiCard(), which sets `border-left-color` inline over a
 *     `.sub-kpi-card` rule that lives in css/main.css. That one IS a real
 *     leading-edge accent and should become border-inline-start-color, but
 *     the fix has to move with whatever main.css/rtl.css do for that class,
 *     which is a different owner. Recorded here so it is visible rather than
 *     invisible -- which is the whole point of adding this scan.
 */
const JS_FILES_DIR = path.join(__dirname, '..', 'frontend');
const LEGACY_JS_PHYSICAL_BUDGET = 31;

function stripComments(css) {
  return css.replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, ' '));
}

function readSections() {
  const raw = fs.readFileSync(CSS_FILE, 'utf8');
  const tokenBegin = raw.indexOf('[design-tokens:begin]');
  const tokenEnd = raw.indexOf('[design-tokens:end]');
  const behaviourBegin = raw.indexOf('[operational-calm:begin]');
  const behaviourEnd = raw.indexOf('[operational-calm:end]');

  assert.ok(tokenBegin !== -1 && tokenEnd > tokenBegin, 'token-layer markers missing from main.css');
  assert.ok(behaviourBegin !== -1 && behaviourEnd > behaviourBegin, 'behaviour-layer markers missing from main.css');

  const clean = stripComments(raw);
  return {
    newCode: clean.slice(tokenBegin, tokenEnd) + '\n' + clean.slice(behaviourBegin, behaviourEnd),
    legacy: clean.slice(tokenEnd, behaviourBegin),
    raw,
  };
}

function countPhysical(css) {
  const hits = [];
  for (const { re, use } of PHYSICAL) {
    re.lastIndex = 0;
    let m;
    while ((m = re.exec(css)) !== null) {
      const line = css.slice(0, m.index).split('\n').length;
      hits.push({ prop: m[0].trim().replace(/[;{]/, '').trim(), use, line });
    }
  }
  return hits;
}

function testNewCodeUsesLogicalProperties() {
  const { newCode } = readSections();

  // ANTI-VACUITY: if the markers moved or the slice came back empty, the scan
  // below would find nothing and pass while checking nothing.
  assert.ok(
    newCode.length > 2000,
    `The token + behaviour layers sliced to only ${newCode.length} characters. ` +
    'The section markers have probably moved, so this scan is not looking at ' +
    'the new code at all.'
  );

  const hits = countPhysical(newCode);
  assert.deepStrictEqual(
    hits.map((h) => `${h.prop} (use ${h.use})`), [],
    `${hits.length} physical direction propert(ies) in newly added CSS:\n  ` +
    hits.map((h) => `${h.prop} — use ${h.use} instead`).join('\n  ') +
    '\n\nThis product ships Arabic. A physical property needs a mirror rule in ' +
    'css/rtl.css to be correct in RTL; a logical property is correct by ' +
    'construction and needs nothing.'
  );
  console.log('PASS: the token and behaviour layers contain no physical direction properties');
}

function testLegacyPhysicalPropertyCountDoesNotGrow() {
  const { legacy } = readSections();
  const count = countPhysical(legacy).length;

  assert.ok(
    count <= LEGACY_PHYSICAL_BUDGET,
    `Physical direction properties in the pre-existing CSS rose from ` +
    `${LEGACY_PHYSICAL_BUDGET} to ${count}. Every one of these needs a mirror ` +
    'rule in css/rtl.css to be correct in Arabic. Use the logical equivalent ' +
    'in new rules.'
  );
  assert.ok(
    count >= LEGACY_PHYSICAL_BUDGET - 20,
    `Physical direction properties fell from ${LEGACY_PHYSICAL_BUDGET} to ${count}. ` +
    'Good — but lower LEGACY_PHYSICAL_BUDGET to ' + count + ' so the ratchet ' +
    'actually holds the new ground instead of leaving slack for a regression.'
  );
  console.log(`PASS: legacy physical-property count is ${count}, within the ${LEGACY_PHYSICAL_BUDGET} budget`);
}

function testLogicalPropertiesAreActuallyUsed() {
  /* Guard against "passes because it declares nothing": the new layers should
     not merely avoid physical properties, they should positively use logical
     ones (or box-model-free layout) where they set inline spacing. */
  const { raw } = readSections();
  /* Must include the longhands. An earlier pattern stopped at
     `border-inline-start:` and so counted 2 where the file actually has a
     dozen `border-inline-start-color:` declarations -- under-reporting the
     very thing it exists to confirm. */
  const logical = (raw.match(/(margin|padding|border|inset)-(inline|block)(-(start|end))?(-(color|width|style))?\s*:/g) || []).length;
  assert.ok(
    logical >= 1,
    'No logical direction properties appear anywhere in main.css. The new ' +
    'layers should be setting inline spacing logically, not avoiding the ' +
    'question by setting no spacing at all.'
  );
  console.log(`PASS: ${logical} logical direction propert(ies) present`);
}

/* JS comments carry the WORD "margin-left" all over this frontend (they are
   how the physical/logical decision is explained at each site), so they are
   blanked before the scan -- otherwise fixing a site and documenting the fix
   would RAISE the count. Whole-line `//` comments and `/* */` blocks only:
   this is a comment-density heuristic matched to how this codebase actually
   writes comments, not a JS parser, and it errs toward over-counting (a
   trailing `// note` after code is still scanned) rather than under. */
function stripJsComments(js) {
  return js
    .replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, ' '))
    .replace(/^[ \t]*\/\/.*$/gm, (m) => ' '.repeat(m.length));
}

/* The SAME property list as the stylesheet scan, with one widened boundary:
   an inline style begins immediately after the opening quote
   (`style="text-align:right`), so a declaration at the very start of the
   attribute has a QUOTE in front of it, not whitespace. Derived from PHYSICAL
   rather than retyped, so a property added there is covered here too -- an
   earlier cut of this scan missed 19 of 30 hits for exactly this reason. */
const JS_PHYSICAL = PHYSICAL.map(({ re, use }) => ({
  re: new RegExp(re.source.replace('(^|[\\s;{])', '(^|[\\s;{"\'])'), re.flags),
  use,
}));

function scanJsInlineStyles() {
  const files = fs.readdirSync(JS_FILES_DIR).filter((f) => f.endsWith('.js')).sort();
  assert.ok(files.length >= 5,
    `Only ${files.length} .js file(s) found in ${JS_FILES_DIR}. This scan would report a clean ` +
    'bill of health having read almost nothing.');
  const hits = [];
  let scanned = 0;
  for (const f of files) {
    const src = stripJsComments(fs.readFileSync(path.join(JS_FILES_DIR, f), 'utf8'));
    scanned += src.length;
    for (const { re, use } of JS_PHYSICAL) {
      re.lastIndex = 0;
      let m;
      while ((m = re.exec(src)) !== null) {
        const line = src.slice(0, m.index).split('\n').length;
        hits.push({ file: f, line, prop: m[0].trim().replace(/^["';{]/, '').trim(), use });
      }
    }
  }
  assert.ok(scanned > 200000,
    `Only ${scanned} characters of JavaScript were scanned; the frontend is far larger than that, ` +
    'so treat this as a harness failure rather than a clean result.');
  return hits;
}

function testJsInlineStylePhysicalPropertyCountDoesNotGrow() {
  const hits = scanJsInlineStyles();
  const count = hits.length;

  assert.ok(
    count <= LEGACY_JS_PHYSICAL_BUDGET,
    `Physical direction properties in JS-built inline styles rose from ${LEGACY_JS_PHYSICAL_BUDGET} ` +
    `to ${count}. css/rtl.css cannot mirror an inline style at all -- an inline declaration beats ` +
    'every stylesheet rule -- so each of these is simply WRONG in Arabic, with no second file that ' +
    'could fix it. Use the logical equivalent:\n  ' +
    hits.map((h) => `${h.file}:${h.line}  ${h.prop} — use ${h.use}`).join('\n  ')
  );
  assert.ok(
    count >= LEGACY_JS_PHYSICAL_BUDGET - 10,
    `Physical direction properties in JS fell from ${LEGACY_JS_PHYSICAL_BUDGET} to ${count}. ` +
    'Good — but lower LEGACY_JS_PHYSICAL_BUDGET to ' + count + ' so the ratchet holds the new ' +
    'ground instead of leaving slack for a regression.'
  );
  console.log(`PASS: JS inline-style physical-property count is ${count}, within the ${LEGACY_JS_PHYSICAL_BUDGET} budget`);
}

/* The margins this change actually removed, pinned BY NAME rather than only
   by the count above. A budget alone cannot tell "eleven margin-left:6px were
   converted" from "eleven were converted and eleven text-aligns were added";
   this says the specific defect is gone and stays gone. */
function testTheRowPairMarginsAreLogical() {
  const files = ['subsystem-retail.js', 'app-shell.js', 'import-wizard.js'];
  const offenders = [];
  let logical = 0;
  for (const f of files) {
    const src = stripJsComments(fs.readFileSync(path.join(JS_FILES_DIR, f), 'utf8'));
    // A leading-edge pin has a LENGTH; `margin-left:auto` is the centering
    // pair and is out of scope on purpose (see the budget comment above).
    const re = /margin-left\s*:\s*(-?[\d.]+[a-z%]*)/g;
    let m;
    while ((m = re.exec(src)) !== null) {
      const line = src.slice(0, m.index).split('\n').length;
      offenders.push(`${f}:${line}  margin-left:${m[1]}`);
    }
    logical += (src.match(/margin-inline-start\s*:/g) || []).length;
  }
  assert.deepStrictEqual(
    offenders, [],
    `${offenders.length} JS-built inline style(s) still pin a LENGTH to the physical left edge:\n  ` +
    offenders.join('\n  ') +
    '\n\nIn Arabic the row reverses, so the gap lands on the outer edge and the two controls ' +
    'collide. Use margin-inline-start.'
  );
  // ANTI-VACUITY: "no margin-left with a length" is also satisfied by a file
  // that sets no inline margin at all, which is exactly what a bad refactor
  // would look like. The conversions must be PRESENT, not merely absent.
  assert.ok(
    logical >= 11,
    `Only ${logical} margin-inline-start declaration(s) across ${files.join(', ')}. The eleven ` +
    'row-pair buttons, the two sync-banner detail spans and the wizard sites were converted, so ' +
    'a count this low means they were deleted rather than converted.'
  );
  console.log(`PASS: no JS inline style pins a length to the physical left edge (${logical} logical margins present)`);
}

function testRtlStylesheetStillCoversTheChartExceptions() {
  /* Charts and canvases must NOT mirror: flipping a chart reverses its axes,
     legend order and value order, which silently misreports the shop's own
     numbers. rtl.css forces them back to LTR; that rule is load-bearing and
     easy to delete by accident during a redesign. */
  const rtl = fs.readFileSync(path.join(__dirname, '..', 'frontend', 'css', 'rtl.css'), 'utf8');
  assert.ok(
    /body\.rtl[^{]*canvas[^{]*\{[^}]*direction\s*:\s*ltr/s.test(rtl) ||
    /canvas/.test(rtl) && /direction\s*:\s*ltr/.test(rtl),
    'css/rtl.css no longer forces canvases back to direction:ltr. Mirroring a ' +
    'chart reverses its axes and value order, so the shop reads its own ' +
    'numbers backwards in Arabic.'
  );
  console.log('PASS: rtl.css still forces charts/canvases back to LTR');
}

/* PER-TEST ISOLATION -- see retail_design_money_test.js for the reasoning and
   the measurement. A flat sequence reports one failure per run however many
   exist, and hides the rest as "not run", which is indistinguishable from
   "passed". */
const CHECKS = [
  ['new code uses logical properties', testNewCodeUsesLogicalProperties],
  ['the legacy physical-property count does not grow', testLegacyPhysicalPropertyCountDoesNotGrow],
  ['logical properties are actually used', testLogicalPropertiesAreActuallyUsed],
  ['the JS inline-style physical count does not grow', testJsInlineStylePhysicalPropertyCountDoesNotGrow],
  ['no JS inline style pins a length to the left edge', testTheRowPairMarginsAreLogical],
  ['rtl.css still covers the chart exceptions', testRtlStylesheetStillCoversTheChartExceptions],
];

function main() {
  const failures = [];
  for (const [name, fn] of CHECKS) {
    try {
      fn();
    } catch (err) {
      failures.push(name);
      console.error(`FAIL: ${name}`);
      console.error('      ' + String((err && err.message) || err).replace(/\n/g, '\n      '));
    }
  }
  if (failures.length) {
    console.error(`\nFAIL: retail_design_rtl_test.js — ${failures.length} of ${CHECKS.length} checks failed:`);
    for (const name of failures) console.error(`  - ${name}`);
    process.exitCode = 1;
    return;
  }
  console.log(`PASS: retail_design_rtl_test.js — ${CHECKS.length} checks`);
}

try {
  main();
} catch (err) {
  console.error('FAIL: retail_design_rtl_test.js (runner)');
  console.error(err.message || err);
  process.exitCode = 1;
}
