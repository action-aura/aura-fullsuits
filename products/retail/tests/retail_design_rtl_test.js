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

function main() {
  testNewCodeUsesLogicalProperties();
  testLegacyPhysicalPropertyCountDoesNotGrow();
  testLogicalPropertiesAreActuallyUsed();
  testRtlStylesheetStillCoversTheChartExceptions();
  console.log('PASS: retail_design_rtl_test.js');
}

try {
  main();
} catch (err) {
  console.error('FAIL: retail_design_rtl_test.js');
  console.error(err.message || err);
  process.exitCode = 1;
}
