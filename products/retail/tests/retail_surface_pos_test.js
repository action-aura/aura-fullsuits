/**
 * retail_surface_pos_test.js — structural guards for the POS/till redesign
 * ("Operational Calm").
 *
 * These are not screenshot tests and not substring assertions. Each one states
 * a claim the DESIGN makes, and checks it against the tree and cascade that
 * products/retail/frontend/subsystem-retail.js actually produces, loaded for
 * real through Node's vm module (never reimplemented here):
 *
 *   1. The sale total is the largest money element in the POS DOM.
 *   2. The scan input regains focus after an interaction that should not steal
 *      it — AND keeps its hands off a field the cashier deliberately focused.
 *   3. Destructive controls are not adjacent to high-frequency ones.
 *
 * EACH CLAIM IS MUTATION-PROVEN. A layout test that still passes when you
 * delete the layout is worth nothing, so every claim below was verified to
 * FAIL against a deliberately broken version of the source before being kept;
 * the specific mutation is recorded next to each test. Two further habits are
 * followed throughout, because this programme keeps shipping tests that lack
 * them:
 *
 *   * Nothing is asserted vacuously. Every test first asserts that it FOUND
 *     the things it is about to reason over (a money element set, a set of
 *     destructive controls, a set of frequent controls). A guard whose pass
 *     condition is "I found nothing to check" is the bug signature, not a pass.
 *
 *   * The fixtures do not manufacture the state under test. The cart, product
 *     list and totals are pushed through the REAL _addToCart/_renderCart/
 *     _renderPOSGrid code paths; the markup examined is whatever those emit.
 *
 * Run: node products/retail/tests/retail_surface_pos_test.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const dom = require('./retail_surface_domlite.js');

const FRONTEND_DIR = path.join(__dirname, '..', 'frontend');
const FRONTEND_FILE = path.join(FRONTEND_DIR, 'subsystem-retail.js');
const CSS_FILE = path.join(FRONTEND_DIR, 'css', 'main.css');

// ─────────────────────────────────────────────────────────────────────────────
// Harness
// ─────────────────────────────────────────────────────────────────────────────

function makeElementStub(overrides) {
  const el = Object.assign({
    innerHTML: '',
    textContent: '',
    value: '',
    id: '',
    disabled: false,
    style: {},
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    appendChild() {},
    getAttribute() { return null; },
    setAttribute() {},
    querySelectorAll() { return []; },
    addEventListener() {},
  }, overrides);
  return el;
}

/**
 * Load the real subsystem-retail.js into a sandbox.
 *
 * `focusLog` records every focus() call by element id, and `activeId` is a
 * mutable box for document.activeElement — those two are what make the
 * focus-retention claim observable without a browser.
 */
function loadRetailSystem(opts) {
  const options = opts || {};
  const code = fs.readFileSync(FRONTEND_FILE, 'utf8');
  const focusLog = [];
  const els = Object.create(null);
  const state = { activeId: options.activeId || null };

  const getEl = (id) => {
    if (!els[id]) {
      els[id] = makeElementStub({
        id,
        focus() { focusLog.push(id); state.activeId = id; },
      });
    }
    return els[id];
  };
  // Pre-create the ids the POS addresses, so a test can read their innerHTML.
  ['pos-cart', 'pos-product-grid', 'pos-search', 'pos-total', 'pos-sub', 'pos-tax',
   'pos-change', 'pos-change-row', 'pos-checkout-btn', 'pos-disc', 'pos-tendered',
   'pos-customer', 'pos-cats', 'pos-held-count'].forEach(getEl);

  const sandbox = {
    console,
    t: (s) => s,                                   // i18n.js's global shorthand
    fetch: () => Promise.reject(new Error('no network in this test')),
    getComputedStyle: () => ({ getPropertyValue: () => '' }),
    navigator: { userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)' },
    localStorage: { getItem: () => null, setItem: () => {} },
    document: {
      get activeElement() { return state.activeId ? getEl(state.activeId) : null; },
      getElementById(id) {
        if (id === 'ret-styles') return options.stylesInjected ? makeElementStub() : null;
        return getEl(id);
      },
      createElement() { return makeElementStub(); },
      querySelector() { return makeElementStub(); },
      querySelectorAll() { return []; },
      head: { appendChild() {} },
      documentElement: { getAttribute: () => 'light', style: { setProperty() {} } },
      addEventListener() {},
    },
    SubsystemApp: { active: 'retail', showToast() {}, hasCapability: () => true, _navigate() {} },
  };
  sandbox.window = sandbox;

  vm.createContext(sandbox);
  vm.runInContext(code, sandbox, { filename: FRONTEND_FILE });
  assert.ok(sandbox.RetailSystem, 'subsystem-retail.js did not expose window.RetailSystem');

  return { RetailSystem: sandbox.RetailSystem, focusLog, els, state, getEl };
}

const SAMPLE_PRODUCTS = [
  { id: 'p1', name: 'Espresso Beans 1kg', sku: 'EB1', barcode: '111', sell_price: 18.5,
    tax_rate: 16, total_stock: 40, reorder_level: 5, unit: 'bag', category_name: 'Beverages' },
  { id: 'p2', name: 'Paper Cups (50)', sku: 'PC50', barcode: '222', sell_price: 4.25,
    tax_rate: 16, total_stock: 3, reorder_level: 10, unit: 'pack', category_name: 'Groceries' },
];

/** Render the POS shell and return its markup + parsed tree + parsed stylesheet. */
function renderPosShell() {
  const ctx = loadRetailSystem();
  const content = makeElementStub();
  ctx.RetailSystem._renderPOS(content);

  const root = dom.parseFragment(content.innerHTML);
  const styleEl = dom.allElements(root).find((el) => el.tag === 'style');
  assert.ok(styleEl, 'The POS render emitted no <style> block to reason about.');
  const rules = dom.parseCss(dom.textOf(styleEl));
  const tokens = dom.parseTokens(fs.readFileSync(CSS_FILE, 'utf8'));

  return { ctx, html: content.innerHTML, root, rules, tokens };
}

/** Drive the REAL cart + grid code paths and return their rendered trees. */
function renderPopulatedCart(ctx) {
  const rs = ctx || loadRetailSystem();
  rs.RetailSystem._products = SAMPLE_PRODUCTS;
  rs.RetailSystem._cart = [];
  rs.RetailSystem._addToCart('p1');
  rs.RetailSystem._addToCart('p1');   // quantity 2, so the stepper is exercised
  rs.RetailSystem._addToCart('p2');
  rs.RetailSystem._renderPOSGrid();

  const cartRoot = dom.parseFragment(rs.els['pos-cart'].innerHTML);
  const gridRoot = dom.parseFragment(rs.els['pos-product-grid'].innerHTML);
  return { rs, cartRoot, gridRoot };
}

const MONEY_TEXT = /[$][\s]*\d/;

/**
 * Every element on a screen that DISPLAYS AN AMOUNT.
 *
 * Derived from the rendered output, never from a hand-written id list: an
 * element qualifies if it carries the `.money` class the money helper emits,
 * or if its own text (not a descendant's) contains a currency figure. A
 * hand-written list would be the classic "fixture that manufactures the state
 * that hides the bug" -- it would keep passing after someone added a new,
 * larger amount somewhere the list had never heard of.
 */
function moneyElements(root) {
  return dom.allElements(root).filter((el) => {
    if (el.classes.includes('money')) return true;
    return MONEY_TEXT.test(dom.ownText(el));
  });
}

// ─────────────────────────────────────────────────────────────────────────────
// CLAIM 1 — the sale total is the largest money element in the POS DOM
// ─────────────────────────────────────────────────────────────────────────────
//
// Mutation-proven: setting `.pos-grand-value { font-size: 15px }` makes this
// fail with the checkout button and the line totals named as larger. Deleting
// the `.pos-grand-value` font-size rule entirely also fails (unresolvable size
// is an error here, never a skip).

function testTotalIsLargestMoneyElement() {
  const { root, rules, tokens } = renderPosShell();
  const { cartRoot, gridRoot } = renderPopulatedCart();

  // The comparison covers the whole till, not just the summary panel: the
  // shell (subtotal, tax, total, change, Charge button), the cart lines, and
  // the product tiles all put amounts in front of the same pair of eyes.
  const candidates = [
    ...moneyElements(root),
    ...moneyElements(cartRoot),
    ...moneyElements(gridRoot),
  ];

  assert.ok(
    candidates.length >= 6,
    'Found only ' + candidates.length + ' money elements across the POS. This test ' +
    'cannot be meaningful without several to compare, so treat this as a harness ' +
    'failure, not a pass: ' + candidates.map(dom.describe).join(' | ')
  );

  const total = candidates.find((el) => el.attrs.id === 'pos-total');
  assert.ok(
    total,
    'No #pos-total element was found among the POS money elements. The running ' +
    'total is the single most important number on this screen; if it is not in ' +
    'the DOM as a money element, nothing below is checkable. Found: ' +
    candidates.map(dom.describe).join(' | ')
  );

  const totalRange = dom.fontSizeRange(rules, total, tokens);
  assert.ok(
    totalRange,
    'Could not resolve a font-size for #pos-total from the POS stylesheet. ' +
    'An unresolvable size is a failure, not a skip -- otherwise deleting the ' +
    'rule would make this test pass.'
  );

  // An amount whose size is set by no rule in this screen's stylesheet inherits
  // the app shell's base type. Rather than treat that as "0px and therefore
  // fine" -- which would let a genuinely large amount hide in the gap -- it is
  // bounded by the token layer's largest non-total step, and the total is
  // required to clear that bound too.
  const displayStep = dom.lengthRange(tokens['--text-size-display'] || '30px', tokens);
  const UNRESOLVED_CEILING = displayStep ? displayStep.max : 30;
  assert.ok(
    totalRange.min > UNRESOLVED_CEILING,
    `#pos-total can render as small as ${totalRange.min}px, which does not clear the ` +
    `${UNRESOLVED_CEILING}px ceiling used for amounts that inherit their size. Until it ` +
    'does, this comparison cannot be trusted for inherited-size amounts.'
  );

  const bigger = [];
  for (const el of candidates) {
    if (el === total) continue;
    const range = dom.effectiveFontSizeRange(rules, el, tokens);
    const max = range ? range.max : UNRESOLVED_CEILING;
    const why = range ? range.sources.join(', ') : `inherits shell type (bounded at ${UNRESOLVED_CEILING}px)`;
    // Strongest available form of the claim: the total at its SMALLEST
    // permitted size still beats this element at its LARGEST.
    if (max >= totalRange.min) {
      bigger.push(`${dom.describe(el)} -> ${why} (up to ${max}px)`);
    }
  }

  assert.deepStrictEqual(
    bigger, [],
    'The running total is not unmistakably the largest money element on the POS.\n' +
    `#pos-total renders at ${totalRange.min}-${totalRange.max}px ` +
    `(${totalRange.sources.join(', ')}), but these amounts can render at least as large:\n  ` +
    bigger.join('\n  ') +
    '\n\nA cashier reads the total aloud to a customer and a customer checks it from ' +
    'the far side of the counter. It has to win on size, not by a hair.'
  );

  console.log(
    `PASS: #pos-total (${totalRange.min}-${totalRange.max}px) is the largest of ` +
    `${candidates.length} money elements in the POS DOM`
  );
}

// A separate, narrower claim: the total must also carry tabular figures and
// must not be allowed to wrap its currency mark away from its digits.
function testTotalIsTabularAndUnbreakable() {
  const { root, rules } = renderPosShell();
  const total = dom.allElements(root).find((el) => el.attrs.id === 'pos-total');
  assert.ok(total, 'No #pos-total element in the POS markup.');

  const variant = dom.declaredValues(rules, total, 'font-variant-numeric');
  assert.ok(
    variant.some((d) => /tabular-nums/.test(d.value)),
    'The running total does not set font-variant-numeric: tabular-nums. Without it ' +
    'a 1 is narrower than a 7, so the figure visibly reflows as the sale grows and ' +
    'a column of amounts never lines up. Got: ' + JSON.stringify(variant)
  );

  const wrap = dom.declaredValues(rules, total, 'white-space');
  assert.ok(
    wrap.some((d) => /nowrap/.test(d.value)),
    'The running total does not set white-space: nowrap, so "$" can wrap away from ' +
    'its digits on a narrow till. Got: ' + JSON.stringify(wrap)
  );

  console.log('PASS: #pos-total is tabular-figured and cannot wrap away from its currency mark');
}

// ─────────────────────────────────────────────────────────────────────────────
// CLAIM 2 — the scan input regains focus after an interaction that should not
//           steal it, and does NOT steal it back from a deliberate one
// ─────────────────────────────────────────────────────────────────────────────
//
// Mutation-proven, both directions:
//   * delete the `this._refocusScan()` call at the end of _renderCart()   -> (a) fails
//   * delete the _POS_FOCUS_KEEPERS guard inside _refocusScan()           -> (b) fails
// The second half is the important one. Without it, "refocus the scan field"
// could be satisfied by an unconditional focus() on every render, which would
// make the discount and cash-tendered boxes literally untypeable -- a worse bug
// than the one being fixed, and one that an outcome-only assertion would wave
// straight through.

function testScanFieldRegainsFocusAfterProductTap() {
  const ctx = loadRetailSystem({ activeId: null });
  ctx.RetailSystem._products = SAMPLE_PRODUCTS;
  ctx.RetailSystem._cart = [];

  ctx.focusLog.length = 0;
  ctx.RetailSystem._addToCart('p1');   // the real "cashier taps a product tile" path

  assert.ok(
    ctx.focusLog.includes('pos-search'),
    'Tapping a product did not return focus to the scan field (#pos-search).\n' +
    'This is not cosmetic: _onScannerKey() refuses to auto-detect a scan while ' +
    'focus sits in a non-search text field, and a tap moves focus off the scan ' +
    'box, so scanning silently stops working until the cashier notices and clicks ' +
    'back. Focus calls seen: ' + JSON.stringify(ctx.focusLog)
  );

  console.log('PASS: a product tap hands focus back to the scan field');
}

function testScanFieldDoesNotStealFocusFromADeliberateEdit() {
  // The cashier has deliberately put the caret in the discount box and is
  // typing. Every keystroke there fires oninput -> _recalc(), and a cart
  // re-render can happen underneath. Focus must stay put.
  const ctx = loadRetailSystem({ activeId: 'pos-disc' });
  ctx.RetailSystem._products = SAMPLE_PRODUCTS;
  ctx.RetailSystem._cart = [];
  ctx.RetailSystem._addToCart('p1');

  ctx.state.activeId = 'pos-disc';
  ctx.focusLog.length = 0;
  ctx.RetailSystem._renderCart();

  assert.deepStrictEqual(
    ctx.focusLog, [],
    'The scan field grabbed focus away from #pos-disc while the cashier was ' +
    'typing in it. An unconditional refocus makes the discount and cash-tendered ' +
    'boxes impossible to type into, because each keystroke re-renders and yanks ' +
    'the caret. The refocus must be guarded, not blanket. Focus calls: ' +
    JSON.stringify(ctx.focusLog)
  );

  // ...and prove the guard is a GUARD, not a dead code path: the very same
  // call refocuses once the caret is no longer in a deliberate typing target.
  ctx.state.activeId = null;
  ctx.RetailSystem._renderCart();
  assert.ok(
    ctx.focusLog.includes('pos-search'),
    'With focus outside every text field, _renderCart() still did not return ' +
    'focus to the scan field -- so the previous assertion passed only because ' +
    'nothing ever refocuses, which is the bug this pair exists to catch.'
  );

  console.log('PASS: the refocus is guarded — it declines mid-edit and fires otherwise');
}

function testStrayPointerPressDoesNotBlurTheScanField() {
  const ctx = loadRetailSystem({ activeId: 'pos-search' });

  // A press on a plain region (a product tile, the pane background). The
  // browser would move focus to it before any click handler ran, so the
  // default must be prevented outright.
  let prevented = false;
  ctx.focusLog.length = 0;
  ctx.RetailSystem._keepScanFocus({
    target: { tagName: 'DIV' },
    preventDefault() { prevented = true; },
  });
  assert.ok(
    prevented,
    '_keepScanFocus() did not preventDefault() on a press over a non-text region. ' +
    'Refocusing after the fact is not equivalent: the field still blurs first, ' +
    'which on a touchscreen dismisses the on-screen keyboard and flickers the caret.'
  );

  // ...but a press on a real text control must be left alone, or the control
  // becomes unusable. A <select> in particular would never open its dropdown.
  for (const tagName of ['INPUT', 'SELECT', 'TEXTAREA']) {
    let blocked = false;
    ctx.RetailSystem._keepScanFocus({
      target: { tagName },
      preventDefault() { blocked = true; },
    });
    assert.strictEqual(
      blocked, false,
      `_keepScanFocus() swallowed the press on a <${tagName.toLowerCase()}>. That ` +
      'stops the cashier focusing it at all -- and for <select> it stops the ' +
      'dropdown opening. The guard must exempt real text controls.'
    );
  }

  console.log('PASS: stray presses are absorbed; presses on real text controls are not');
}

// ─────────────────────────────────────────────────────────────────────────────
// CLAIM 3 — destructive controls are not adjacent to high-frequency ones
// ─────────────────────────────────────────────────────────────────────────────
//
// Mutation-proven: moving the remove button back inside `.pos-line-freq`
// (where it shipped, immediately after the "+" button) fails this immediately,
// naming _updateQty as the adjacent frequent control.

// Classified by what the control ACTUALLY DOES — read off its wired handler,
// not off a class name. A class can be renamed without changing the hazard;
// the handler is the hazard.
const DESTRUCTIVE = [/_clearCart\b/, /_removeLine\b/, /_voidSale\b/];
const HIGH_FREQUENCY = [/_updateQty\b/, /_addToCart\b/, /_checkout\b/, /_setPayment\b/, /_holdSale\b/];

function classify(el) {
  const handler = (el.attrs.onclick || '') + ' ' + (el.attrs.onmousedown || '');
  if (DESTRUCTIVE.some((re) => re.test(handler))) return 'destructive';
  if (HIGH_FREQUENCY.some((re) => re.test(handler))) return 'frequent';
  return null;
}

function collectControls(roots) {
  const destructive = [];
  const frequent = [];
  for (const root of roots) {
    for (const el of dom.allElements(root)) {
      const kind = classify(el);
      if (kind === 'destructive') destructive.push(el);
      else if (kind === 'frequent') frequent.push(el);
    }
  }
  return { destructive, frequent };
}

function testDestructiveControlsAreNotAdjacentToFrequentOnes() {
  const { root } = renderPosShell();
  const { cartRoot, gridRoot } = renderPopulatedCart();
  const roots = [root, cartRoot, gridRoot];
  const { destructive, frequent } = collectControls(roots);

  // Vacuity guards. If either set is empty this test proves nothing, and
  // "proves nothing" must never read as green.
  assert.ok(
    destructive.length >= 2,
    'Expected to find at least the two destructive controls this screen has ' +
    '(void the sale, remove a line), found ' + destructive.length + '. Either the ' +
    'markup changed or the classifier is stale -- either way this test is not ' +
    'checking what it claims to.'
  );
  assert.ok(
    frequent.length >= 4,
    'Expected several high-frequency controls (quantity steppers, add-to-cart, ' +
    'pay methods, charge), found ' + frequent.length + '. Without them the ' +
    'adjacency check below is vacuous.'
  );

  const violations = [];
  for (const d of destructive) {
    // (i) Nothing frequent immediately beside it.
    for (const sib of dom.adjacentSiblings(d)) {
      if (classify(sib) === 'frequent') {
        violations.push(
          `${dom.describe(d)} is an immediate sibling of ${dom.describe(sib)}`
        );
      }
    }
    // (ii) Nothing frequent sharing its container either. A spacer between two
    //      buttons in one flex row is still one mis-tap away at till speed.
    if (d.parent) {
      for (const el of dom.allElements(d.parent)) {
        if (el === d) continue;
        if (classify(el) === 'frequent') {
          violations.push(
            `${dom.describe(d)} shares a container (${dom.describe(d.parent)}) with ` +
            `${dom.describe(el)}`
          );
        }
      }
    }
  }

  assert.deepStrictEqual(
    violations, [],
    'A destructive control sits next to a high-frequency one on the till:\n  ' +
    violations.join('\n  ') +
    '\n\nRemoving a line or voiding a sale by mis-tap, mid-queue, in front of the ' +
    'customer, costs a real re-ring. Destructive controls get their own container ' +
    'and real separation.'
  );

  console.log(
    `PASS: ${destructive.length} destructive control(s) are neither adjacent to nor ` +
    `co-located with any of the ${frequent.length} high-frequency control(s)`
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Supporting direction checks: touch targets and non-colour negatives
// ─────────────────────────────────────────────────────────────────────────────

function testEveryFingerTargetMeetsTheTouchMinimum() {
  const { root, rules, tokens } = renderPosShell();
  const { cartRoot } = renderPopulatedCart();
  const controls = [];
  for (const r of [root, cartRoot]) {
    for (const el of dom.allElements(r)) {
      if (classify(el)) controls.push(el);
    }
  }
  assert.ok(controls.length >= 6, 'Found too few POS controls to check touch sizing.');

  const tooSmall = [];
  for (const el of controls) {
    const block = dom.declaredValues(rules, el, 'min-block-size');
    if (!block.length) { tooSmall.push(`${dom.describe(el)} declares no min-block-size`); continue; }
    const ok = block.some((d) => {
      const r = dom.lengthRange(d.value, tokens);
      return r && r.min >= 44;
    });
    if (!ok) tooSmall.push(`${dom.describe(el)} -> ${block.map((d) => d.value).join(', ')}`);
  }

  assert.deepStrictEqual(
    tooSmall, [],
    'These POS controls are below the 44px touch minimum:\n  ' + tooSmall.join('\n  ') +
    '\n\nThis is a till. Half the installs are touchscreens and the person using it ' +
    'is being watched by a queue.'
  );

  console.log(`PASS: all ${controls.length} POS finger targets are >= 44px`);
}

function testNegativeAmountsAreNotColourAlone() {
  const ctx = loadRetailSystem();
  const negative = ctx.RetailSystem._money(-65.44);
  const positive = ctx.RetailSystem._money(65.44);

  const negRoot = dom.parseFragment(negative);
  const negEl = dom.allElements(negRoot)[0];
  assert.ok(negEl, 'RetailSystem._money() emitted no element for a negative amount.');

  assert.ok(
    negEl.classes.includes('money--negative'),
    'A negative amount does not carry .money--negative, so css/main.css cannot ' +
    'style it at all. Got: ' + negative
  );
  // The class alone is colour. The text itself has to say "negative" too.
  assert.ok(
    /−/.test(dom.textOf(negEl)),
    'A negative amount does not contain a U+2212 MINUS SIGN in its own text. ' +
    'Strip every colour from this screen -- a washed-out shop monitor does exactly ' +
    'that -- and the amount must still read as negative. Got: ' + JSON.stringify(negative)
  );
  assert.ok(
    negEl.classes.includes('money--accounting'),
    'A negative amount does not opt into .money--accounting, the stylesheet\'s ' +
    'parenthesis pair. Parentheses are the convention that survives greyscale and ' +
    'photocopying. Got: ' + negative
  );
  // And prove the marking is CONDITIONAL, not stamped on every amount --
  // otherwise "negatives are marked" would be trivially true and meaningless.
  const posEl = dom.allElements(dom.parseFragment(positive))[0];
  assert.ok(
    !posEl.classes.includes('money--negative') && !/−/.test(dom.textOf(posEl)),
    'A POSITIVE amount is also being marked as negative, so the negative marking ' +
    'carries no information. Got: ' + positive
  );

  console.log('PASS: negatives carry a minus glyph + accounting parentheses, positives do not');
}

// ─────────────────────────────────────────────────────────────────────────────
// RTL by construction
// ─────────────────────────────────────────────────────────────────────────────
//
// Mutation-proven: changing `.pos-line-total { text-align:end }` to
// `text-align:right`, or `padding-inline-start` to `padding-left`, fails.

function testPosLayoutIsMirrorSafeByConstruction() {
  const { html, root } = renderPosShell();
  const { cartRoot, gridRoot } = renderPopulatedCart();

  // Both surfaces a rule can live on: the screen's <style> block, and every
  // inline style attribute in the markup it emits.
  const surfaces = [];
  const styleEl = dom.allElements(root).find((el) => el.tag === 'style');
  surfaces.push({ where: 'POS <style> block', css: dom.textOf(styleEl) });
  for (const r of [root, cartRoot, gridRoot]) {
    for (const el of dom.allElements(r)) {
      if (el.tag === 'style') continue;
      if (el.attrs.style) surfaces.push({ where: dom.describe(el), css: el.attrs.style });
    }
  }
  assert.ok(
    surfaces.length >= 5 && html.length > 1000,
    'Too few styling surfaces to check; treat as a harness failure rather than a pass.'
  );

  const violations = [];
  for (const s of surfaces) {
    for (const hit of dom.physicalDirectionHits(s.css)) {
      violations.push(`${s.where}: "${hit.snippet}" — ${hit.why}`);
    }
  }

  assert.deepStrictEqual(
    violations, [],
    'The POS pins layout to physical edges, so it will not mirror correctly in ' +
    'Arabic:\n  ' + violations.join('\n  ') +
    '\n\nThis product ships RTL. A physical property here means rtl.css has to grow ' +
    'another rule to chase it, which is how the mirroring drifts out of sync with ' +
    'the layout in the first place.'
  );

  console.log(`PASS: all ${surfaces.length} POS styling surfaces use logical properties only`);
}

// ─────────────────────────────────────────────────────────────────────────────

function main() {
  testTotalIsLargestMoneyElement();
  testTotalIsTabularAndUnbreakable();
  testScanFieldRegainsFocusAfterProductTap();
  testScanFieldDoesNotStealFocusFromADeliberateEdit();
  testStrayPointerPressDoesNotBlurTheScanField();
  testDestructiveControlsAreNotAdjacentToFrequentOnes();
  testEveryFingerTargetMeetsTheTouchMinimum();
  testNegativeAmountsAreNotColourAlone();
  testPosLayoutIsMirrorSafeByConstruction();
  console.log('PASS: retail_surface_pos_test.js');
}

try {
  main();
} catch (err) {
  console.error('FAIL: retail_surface_pos_test.js');
  console.error(err && err.message ? err.message : err);
  process.exitCode = 1;
}
