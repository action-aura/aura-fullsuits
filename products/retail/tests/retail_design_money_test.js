/**
 * OPERATIONAL CALM — money must be unambiguous.
 *
 * WHY THIS TEST EXISTS
 * Before this redesign the retail stylesheet contained ZERO occurrences of
 * `font-variant-numeric`. Every amount in the product rendered with the
 * bundled font's default PROPORTIONAL figures, where a "1" is visibly
 * narrower than a "7". Consequences at a real till:
 *
 *   - A column of amounts does not align on the decimal point, so a cashier
 *     cannot scan it or spot an order-of-magnitude typo (12.00 vs 120.0).
 *   - Two totals of the same digit count render different widths, which
 *     defeats the "does this look like the number I expected" check that
 *     people actually use when money is moving.
 *
 * The fix is `font-variant-numeric: tabular-nums`, and this test asserts it
 * on every class the app uses to render a comparable number.
 *
 * WHAT IT ASSERTS, AND WHY THAT SHAPE
 * The list of money surfaces below is NOT "the classes that happen to be
 * styled" -- deriving the expectation from the stylesheet would make the
 * test circular, passing no matter what the stylesheet did. It is instead a
 * list of the surfaces this product actually prints money into, grounded in
 * the markup: the POS totals, the dashboard/report KPI values, and the data
 * tables that carry amount columns. The test then requires the stylesheet to
 * cover each of them.
 *
 * KNOWN GAP, STATED RATHER THAN HIDDEN
 * RetailSystem._fmt() (products/retail/frontend/subsystem-retail.js, owned by
 * the other half of this change) returns a BARE STRING -- "-$120.00" with no
 * wrapper element. So amounts are styled by whatever container they land in,
 * which is why the rule targets containers (table cells, KPI values) as well
 * as the .money/.num contract classes that css/rtl.css already assumes exist.
 * Wrapping _fmt() output in <span class="money"> would let this tighten from
 * container-level to value-level; that is a handoff note, not something this
 * half can do.
 *
 * No test framework is configured for this vanilla-JS, build-step-free
 * frontend (see CLAUDE.md), so this runs standalone on Node built-ins:
 *
 *   node products/retail/tests/retail_design_money_test.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');

const CSS_DIR = path.join(__dirname, '..', 'frontend', 'css');
const MAIN_CSS = path.join(CSS_DIR, 'main.css');
const RTL_CSS = path.join(CSS_DIR, 'rtl.css');

/* Surfaces this product prints a comparable number into. Grounded in the
   markup, not in the stylesheet -- see the header note on circularity. */
const MONEY_SURFACES = [
  { sel: '.money', why: 'Generic money span; css/rtl.css already forces it LTR, so the contract class exists.' },
  { sel: '.num', why: 'Generic numeric span; same LTR contract in css/rtl.css.' },
  { sel: '.kpi-val', why: 'Dashboard KPI value — revenue, takings, average basket.' },
  { sel: '.sub-kpi-value', why: 'Subsystem shell KPI value; the dashboard\'s headline figures.' },
  { sel: '.cl-kpi-value', why: 'List-screen KPI value (customer/supplier balances).' },
  { sel: '.crm-card-value', why: 'CRM deal value — a monetary amount.' },
  { sel: '.sub-table td', why: 'Subsystem data tables carry amount columns (sales, payments, AR/AP).' },
  { sel: '.cl-table td', why: 'List tables carry balance and total columns.' },
  { sel: 'table.aura-table td', why: 'The shared data table used across report screens.' },
  { sel: '.report-kpi-row', why: 'Report KPI rows are columns of figures meant to be summed by eye.' },
];

function readCss() {
  return fs.readFileSync(MAIN_CSS, 'utf8');
}

/* Collect every selector that is granted tabular figures, so the check is
   "is this surface covered", not "does this exact rule exist". */
function selectorsWithTabularNums(css) {
  const covered = new Set();
  const ruleRe = /([^{}]+)\{([^{}]*)\}/g;
  let m;
  while ((m = ruleRe.exec(css)) !== null) {
    const [, rawSel, body] = m;
    if (!/font-variant-numeric\s*:[^;]*tabular-nums/.test(body)) continue;
    // Strip comments from the selector list BEFORE splitting on commas: a
    // comment containing a comma would otherwise glue itself to the first
    // selector and that selector would never register.
    rawSel.replace(/\/\*[\s\S]*?\*\//g, '')
      .split(',')
      .map((s) => s.trim())
      .filter(Boolean)
      .forEach((s) => covered.add(s));
  }
  return covered;
}

function testEveryMoneySurfaceHasTabularFigures() {
  const css = readCss();
  const covered = selectorsWithTabularNums(css);

  // ANTI-VACUITY: if the rule parser broke, `covered` would be empty and the
  // loop below would report every surface as missing (loud), or -- if the
  // assertion were written the other way round -- silently pass. Assert the
  // parse found a real rule set first.
  assert.ok(
    covered.size >= 8,
    `Expected the stylesheet to grant tabular figures to at least 8 selectors, ` +
    `parsed only ${covered.size}. The rule parser is probably broken.`
  );

  const missing = MONEY_SURFACES.filter((s) => !covered.has(s.sel));
  assert.deepStrictEqual(
    missing.map((s) => s.sel), [],
    'Money surface(s) render with proportional figures:\n' +
    missing.map((s) => `  ${s.sel}\n      ${s.why}`).join('\n') +
    '\n\nWithout font-variant-numeric:tabular-nums a "1" is narrower than a "7", ' +
    'so a column of amounts will not align and a mis-keyed order of magnitude ' +
    'stops being visible at a glance.'
  );
  console.log(`PASS: all ${MONEY_SURFACES.length} money surfaces carry tabular figures (${covered.size} selectors covered in total)`);
}

function testCurrencyNeverWrapsAwayFromItsAmount() {
  const css = readCss();
  const nowrap = new Set();
  const ruleRe = /([^{}]+)\{([^{}]*)\}/g;
  let m;
  while ((m = ruleRe.exec(css)) !== null) {
    const [, rawSel, body] = m;
    if (!/white-space\s*:\s*nowrap/.test(body)) continue;
    rawSel.replace(/\/\*[\s\S]*?\*\//g, '').split(',').map((s) => s.trim()).filter(Boolean)
      .forEach((s) => nowrap.add(s));
  }

  // The value-level classes are the ones that must not break: a "$" left
  // alone at the end of a line, with "120.00" on the next, is a misread
  // waiting to happen at a till.
  const mustNotWrap = ['.money', '.num', '.kpi-val', '.sub-kpi-value', '.cl-kpi-value'];
  const wrapping = mustNotWrap.filter((s) => !nowrap.has(s));
  assert.deepStrictEqual(
    wrapping, [],
    `Money value class(es) may wrap: ${wrapping.join(', ')}. ` +
    'Currency must stay glued to its amount.'
  );
  console.log(`PASS: all ${mustNotWrap.length} money value classes are wrap-protected`);
}

function testNegativeAmountsSurviveGreyscale() {
  /* Direction requirement: a negative amount must be distinguishable WITHOUT
     colour. Roughly 1 in 12 men has a red/green deficiency, and these installs
     run on cheap, often badly-calibrated monitors under fluorescent light --
     a red minus is exactly the cue that disappears first.

     Assert the negative rule differs from the positive rule by something a
     greyscale screenshot would still show. */
  const css = readCss();
  const neg = /\.money--negative\s*\{([^}]*)\}/.exec(css);
  const pos = /\.money--positive\s*\{([^}]*)\}/.exec(css);
  assert.ok(neg, 'Expected a .money--negative rule');
  assert.ok(pos, 'Expected a .money--positive rule');

  const nonColour = /(font-weight|text-decoration|font-style|content|border|outline)\s*:/g;
  const negCues = (neg[1].match(nonColour) || []).length;
  assert.ok(
    negCues >= 1,
    '.money--negative differs from .money--positive by COLOUR ALONE:\n' +
    `  positive: ${pos[1].trim()}\n  negative: ${neg[1].trim()}\n\n` +
    'Add a cue that survives greyscale — weight, decoration, or accounting ' +
    'parentheses via ::before/::after.'
  );

  // And the accounting-parenthesis variant must be opt-in, never automatic:
  // RetailSystem._fmt() already emits a leading "-", so an unconditional
  // parenthesis would render "(-$120.00)" and read as a double negative.
  /* Must anchor at a SELECTOR BOUNDARY. A bare substring test also matches
     `.money--accounting.money--negative::before`, i.e. the correctly gated
     opt-in rule, and reports the very thing it is checking for as a failure.
     Requiring a boundary char (start of line, comma, brace or whitespace)
     before `.money--negative` distinguishes "this rule stands alone" from
     "this rule is qualified by another class". */
  const autoParen = /(^|[,{}\s])\.money--negative::before\s*\{[^}]*content/m.test(css);
  assert.ok(
    !autoParen,
    'Accounting parentheses are applied to .money--negative unconditionally. ' +
    'RetailSystem._fmt() already emits a leading minus, so this renders ' +
    '"(-$120.00)" — a double negative. Gate it behind an explicit opt-in class.'
  );
  console.log(`PASS: negative amounts carry ${negCues} non-colour cue(s), and parentheses are opt-in (no double negative)`);
}

function testRtlKeepsFiguresLtr() {
  /* Arabic is a shipped locale. Numerals must stay left-to-right inside an
     RTL paragraph or "120.00" reverses into nonsense, so rtl.css forces
     direction:ltr on the numeric contract classes. If main.css ever stops
     agreeing with rtl.css about which classes those are, money silently
     mirrors in one locale only. */
  const rtl = fs.readFileSync(RTL_CSS, 'utf8');
  const main = readCss();

  const ltrForced = [...rtl.matchAll(/body\.rtl\s+\.([a-z0-9-]+)/gi)].map((m) => m[1]);
  const numeric = ltrForced.filter((c) => ['money', 'num', 'iw-num'].includes(c));
  assert.ok(
    numeric.length >= 2,
    `rtl.css should force direction:ltr on the numeric classes; found ${numeric.length}.`
  );

  const uncovered = numeric.filter((c) => !new RegExp(`\\.${c}\\b`).test(main));
  assert.deepStrictEqual(
    uncovered, [],
    `rtl.css forces LTR on .${uncovered.join(', .')} but main.css never styles ` +
    'them, so they carry no tabular figures. The two stylesheets disagree about ' +
    'which classes render money.'
  );
  console.log(`PASS: the ${numeric.length} numeric classes rtl.css forces LTR are all styled in main.css`);
}

function main() {
  testEveryMoneySurfaceHasTabularFigures();
  testCurrencyNeverWrapsAwayFromItsAmount();
  testNegativeAmountsSurviveGreyscale();
  testRtlKeepsFiguresLtr();
  console.log('PASS: retail_design_money_test.js');
}

try {
  main();
} catch (err) {
  console.error('FAIL: retail_design_money_test.js');
  console.error(err.message || err);
  process.exitCode = 1;
}
