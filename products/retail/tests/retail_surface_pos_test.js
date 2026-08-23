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
 *   4. Every product tile is reachable and operable WITHOUT A POINTER, in both
 *      of its states, and every :hover affordance on this screen has a
 *      :focus-visible counterpart that can actually match.
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
  // classList RECORDS rather than swallowing, and `classToggles` keeps the
  // DECISIONS as well as the result.
  //
  // A no-op classList is fine while nothing under test manipulates classes, and
  // worthless the moment something does. RetailSystem._setMoney() fills every
  // POS money sink and marks a negative by toggling .money--negative /
  // .money--accounting on that same element; against a swallowing stub, the
  // marking — the only cue that survives greyscale — is invisible, and so is
  // its ABSENCE. That is why reverting all four POS `_setMoney` calls to
  // `textContent = _fmt()` left this file green: the fixture could not tell the
  // two apart. Recording the toggle, including `toggle(x, false)`, is what
  // makes "the marking decision was taken on this element" observable.
  const classes = [];
  const classToggles = [];
  const el = Object.assign({
    innerHTML: '',
    textContent: '',
    value: '',
    id: '',
    disabled: false,
    style: {},
    classes,
    classToggles,
    classList: {
      add(c) { classToggles.push([c, true]); if (!classes.includes(c)) classes.push(c); },
      remove(c) { classToggles.push([c, false]); const i = classes.indexOf(c); if (i !== -1) classes.splice(i, 1); },
      toggle(c, on) { if (on) this.add(c); else this.remove(c); },
      contains(c) { return classes.includes(c); },
    },
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
  // A SOLD-OUT product, so the grid renders its second tile state for real.
  // Without one, _renderPOSGrid's out-of-stock branch never executes and the
  // keyboard/AT claims below would only ever be checked against the happy
  // tile — which is the branch least likely to be wrong.
  { id: 'p3', name: 'Oat Milk 1L', sku: 'OM1', barcode: '333', sell_price: 2.75,
    tax_rate: 16, total_stock: 0, reorder_level: 6, unit: 'carton', category_name: 'Beverages' },
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

// ─────────────────────────────────────────────────────────────────────────────
// CLAIM 4 — the product tile is reachable and operable without a mouse
// ─────────────────────────────────────────────────────────────────────────────
//
// The tile is the most-used control in the product, and it shipped as
// `<div class="pos-card" onclick="...">`. A <div> is not focusable, so there
// was NO keyboard route to it: Tab never landed on it, Enter and Space could
// never activate it, and an AT user was read a group of text with no control
// in it. Its :hover rule was the only affordance it had — and a touchscreen,
// which is what a large share of these installs are, has no hover. Between the
// two, adding a product to a sale was a mouse-only operation on a machine
// usually driven by a scanner, a keyboard, or a finger.
//
// This asserts the STRUCTURE that makes it operable, not a list of tiles:
// every tile the real _renderPOSGrid emits, in both of its states.
//
// Mutation-proven:
//   * turn the tile back into a <div>                     -> "not a <button>"
//   * add tabindex="0" role="button" to that <div> instead -> still fails, and
//     deliberately: the brief asks for the native element, which brings BOTH
//     activation keys and cannot drift out of sync with its own semantics
//   * mark the sold-out tile `disabled` instead of aria-disabled -> fails, it
//     leaves the tab order and stops announcing why it cannot be sold
//   * empty the tile's contents                           -> "no accessible name"

/** The accessible name a <button> computes from its own contents (accname 2F). */
function nameFromContents(el) {
  let out = '';
  const visit = (n) => {
    if (n.type === 'text') { out += n.text; return; }
    if (n.attrs && n.attrs['aria-hidden'] === 'true') return;   // decorative, contributes nothing
    for (const c of n.children || []) visit(c);
  };
  for (const c of el.children || []) visit(c);
  return out.replace(/\s+/g, ' ').trim();
}

function testProductTilesAreKeyboardOperableAndAnnounced() {
  const { gridRoot } = renderPopulatedCart();

  // Derived from the render, never from a fixture list: a tile is anything
  // carrying the .pos-card class the grid actually emitted.
  const tiles = dom.allElements(gridRoot).filter((el) => el.classes.includes('pos-card'));
  const sellable = tiles.filter((el) => !el.classes.includes('pos-card-outofstock'));
  const soldOut = tiles.filter((el) => el.classes.includes('pos-card-outofstock'));

  // ANTI-VACUITY, both branches. Every assertion below is a loop over these
  // arrays; if the grid stopped rendering, or the fixture lost its sold-out
  // product, the loops would iterate zero times and report success having
  // examined nothing.
  assert.ok(
    sellable.length >= 2,
    'Only ' + sellable.length + ' in-stock product tile(s) rendered, so the checks ' +
    'below are near-vacuous. Grid: ' + gridRoot.children.length + ' top-level node(s).'
  );
  assert.ok(
    soldOut.length >= 1,
    'No OUT-OF-STOCK tile rendered, so the branch that decides whether an ' +
    'unavailable product is still announced was never executed. That branch is ' +
    'the one most likely to be wrong — a `disabled` there silently removes the ' +
    'tile from the tab order.'
  );

  const problems = [];
  for (const tile of tiles) {
    const what = dom.describe(tile);

    // (a) A real button. Not a div, and not a div wearing role/tabindex: the
    //     native element is what supplies focusability, BOTH activation keys
    //     (Space fires on keyup, Enter on keydown) and the button role at once.
    if (tile.tag !== 'button') {
      problems.push(
        `${what} is a <${tile.tag}>, not a <button>. A <${tile.tag}> with a click ` +
        'handler is unfocusable, so there is no keyboard or scanner path to it at all.'
      );
      continue;
    }
    // (b) type="button". Harmless today, load-bearing the moment any of these
    //     screens grows a <form> around the grid — the default is `submit`.
    if (tile.attrs.type !== 'button') {
      problems.push(`${what} has no type="button"; the HTML default is submit.`);
    }
    // (c) An accessible name. A focusable control with no name is announced as
    //     just "button", which is worse than not reaching it: the operator now
    //     has somewhere to land and nothing telling them what it is.
    const name = nameFromContents(tile);
    if (!name) {
      problems.push(`${what} has no accessible name — its contents are empty or entirely aria-hidden.`);
    }
    // (d) ...and the name has to identify the PRODUCT, not just say "$4.25".
    //     The title attribute already carries the untruncated product name.
    const productName = (tile.attrs.title || '') ||
      (dom.allElements(tile).find((e) => e.classes.includes('pos-card-name')) || { attrs: {} }).attrs.title || '';
    if (productName && name && !name.includes(productName)) {
      problems.push(
        `${what} names itself ${JSON.stringify(name)}, which does not contain the ` +
        `product ${JSON.stringify(productName)}. A grid of tiles that all announce ` +
        'their price and none their identity is not navigable.'
      );
    }
    // (e) Sold out must stay reachable. `disabled` would take it out of the tab
    //     order and silence the one thing the cashier needs to know.
    const isSoldOut = tile.classes.includes('pos-card-outofstock');
    if (isSoldOut) {
      if (tile.attrs.disabled !== undefined) {
        problems.push(
          `${what} is HTML-disabled. That removes it from the tab order and from ` +
          'the accessibility tree, so a keyboard operator can neither reach it nor ' +
          'hear why it cannot be sold. Use aria-disabled and keep the toast.'
        );
      }
      if (tile.attrs['aria-disabled'] !== 'true') {
        problems.push(
          `${what} is visually washed out (45% opacity) but carries no ` +
          'aria-disabled, so nothing but the colour says it is unavailable.'
        );
      }
    } else if (tile.attrs['aria-disabled'] === 'true') {
      // Conditionality: if every tile were aria-disabled the marking would
      // carry no information, and (e) above would be trivially satisfiable.
      problems.push(`${what} is in stock but marked aria-disabled="true".`);
    }
  }

  assert.deepStrictEqual(
    problems, [],
    'Product tiles are not fully operable without a pointer:\n  ' + problems.join('\n  ') +
    '\n\nA till is driven by a barcode scanner (which is a keyboard) and by a finger ' +
    'on a screen with no hover state. A tile reachable only by mouse-hover is a tile ' +
    'half these installs cannot use.'
  );

  console.log(
    `PASS: all ${tiles.length} product tiles (${sellable.length} sellable, ` +
    `${soldOut.length} sold out) are focusable buttons with an accessible name`
  );
}

// Every :hover rule on this screen must have a :focus-visible counterpart that
// CAN actually match — the property retail_design_focus_test.js enforces over
// css/main.css, applied here to the stylesheet this file injects at render
// time, which that test cannot see at all.
//
// Mutation-proven: deleting `:focus-visible` from the `.pos-card` rule fails
// here and nowhere else in the suite.
function testEveryPosHoverAffordanceHasAFocusCounterpart() {
  const { root } = renderPosShell();
  const styleEl = dom.allElements(root).find((el) => el.tag === 'style');
  const css = dom.textOf(styleEl).replace(/\/\*[\s\S]*?\*\//g, '');

  const hover = new Set();
  const focusVisible = new Set();
  for (const m of css.matchAll(/([^{}]+)\{/g)) {
    for (const raw of m[1].split(',')) {
      const s = raw.trim();
      if (!s || s.startsWith('@')) continue;
      if (/:hover\b/.test(s)) hover.add(s.replace(/:hover\b/g, '').trim());
      if (/:focus-visible\b/.test(s)) focusVisible.add(s.replace(/:focus-visible\b/g, '').trim());
    }
  }

  assert.ok(
    hover.size >= 5,
    `Parsed only ${hover.size} :hover selector(s) out of the POS <style> block. ` +
    'The parse is broken, so a green result here would mean nothing.'
  );

  // The tiles and the category chips are CONTROLS: things a cashier operates.
  // The rest of the hover rules on this screen sit on ghost buttons that already
  // pair the two states, and this stays a property rather than a list by
  // deriving the control set from the rendered markup — anything the grid or the
  // rail emits as a <button> and also styles on :hover.
  const controlClasses = new Set();
  const { gridRoot } = renderPopulatedCart();
  for (const r of [root, gridRoot]) {
    for (const el of dom.allElements(r)) {
      if (el.tag !== 'button') continue;
      for (const c of el.classes) controlClasses.add(c);
    }
  }
  assert.ok(
    controlClasses.size >= 4,
    'Found only ' + controlClasses.size + ' button class(es) on the POS; the ' +
    'pairing check below would be near-vacuous.'
  );

  const missing = [...hover].filter((sel) => {
    const last = sel.split(/[\s>+~]+/).filter(Boolean).pop() || sel;
    const bare = last.replace(/^\./, '').replace(/[:[].*$/, '');
    if (!controlClasses.has(bare)) return false;         // not a control this screen renders
    return !focusVisible.has(sel);
  });

  assert.deepStrictEqual(
    missing, [],
    'These POS controls style :hover but not :focus-visible:\n  ' + missing.join('\n  ') +
    '\n\nOn a touchscreen they have NO discoverable state, and a keyboard or ' +
    'barcode-scanner operator cannot see where focus is. This stylesheet is ' +
    'injected from subsystem-retail.js, so retail_design_focus_test.js — which ' +
    'reads css/main.css only — never sees these rules.'
  );

  console.log(
    `PASS: every :hover rule on a rendered POS control (${controlClasses.size} control ` +
    `classes, ${hover.size} hover selectors) has a :focus-visible counterpart`
  );
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
// CLAIM 5 — every POS money sink is filled through the marking path
// ─────────────────────────────────────────────────────────────────────────────
//
// #pos-sub, #pos-tax, #pos-total and #pos-change are the four elements on this
// screen that ARE an amount: each already carries `.money` in the POS template,
// and _recalc()/_calcChange() fill them. They shipped as
// `el.textContent = this._fmt(n)`, which emits an ASCII hyphen and cannot touch
// a class, so `.money--negative` (bold + the negative token) and
// `.money--accounting`'s parentheses had no way of reaching the value.
//
// Reverting all four to that line left EVERY suite green, including the one
// named for negative-amount signalling — because that test looks at #r-k-rev on
// the dashboard, and because this file's own element stub swallowed classList.
//
// WHY THIS ASSERTS THE DECISION AND NOT THE OUTCOME. None of the four can be
// negative on the happy path: _recalc() clamps the discount to 0-100 (so total
// >= 0), and _calcChange() only fills #pos-change when `tendered >= total`. An
// outcome-only assertion ("a negative POS total is marked") would therefore be
// unreachable, and a test that cannot reach its own subject is the vacuity this
// programme keeps shipping. What IS observable, on every render, is whether the
// marking DECISION was taken on each sink — `_setMoney` toggles both classes on
// every call, including to false. `textContent = _fmt()` toggles nothing.
//
// Conditionality is proven separately and directly against the helper, so
// "the decision was taken" cannot be satisfied by a helper that always answers
// the same way.
//
// Mutation-proven:
//   * revert any one of the four sinks to `textContent = this._fmt(...)`
//                                              -> that sink is named here
//   * make _setMoney toggle the classes unconditionally on
//                                              -> the positive control fails
//   * make _setMoney never toggle them         -> the negative control fails

const POS_MONEY_SINKS = ['pos-sub', 'pos-tax', 'pos-total', 'pos-change'];

function testEveryPosMoneySinkGoesThroughTheMarkingPath() {
  const ctx = loadRetailSystem();
  const content = makeElementStub();
  ctx.RetailSystem._renderPOS(content);
  ctx.RetailSystem._products = SAMPLE_PRODUCTS;
  ctx.RetailSystem._cart = [];
  ctx.RetailSystem._addToCart('p1');
  ctx.RetailSystem._addToCart('p2');

  // Cash, over-tendered: the only configuration in which _calcChange() fills
  // #pos-change at all. Without it that sink is never written and this sweep
  // would silently check three of four.
  ctx.RetailSystem._paymentMethod = 'cash';
  ctx.els['pos-tendered'].value = '500';
  ctx.RetailSystem._recalc();

  const unmarked = [];
  for (const id of POS_MONEY_SINKS) {
    const el = ctx.els[id];
    assert.ok(el, `The POS never addressed #${id}; this sweep cannot see it.`);
    if (!/\d/.test(String(el.textContent))) {
      unmarked.push(`#${id} was never given a figure at all (textContent: ${JSON.stringify(el.textContent)})`);
      continue;
    }
    const decided = (el.classToggles || []).some(([c]) => c === 'money--negative');
    if (!decided) {
      unmarked.push(
        `#${id} received "${el.textContent}" without any .money--negative decision being ` +
        'taken on it, so it was filled by a plain textContent write rather than through ' +
        'RetailSystem._setMoney(). A negative value there would carry an ASCII hyphen and ' +
        'no class, so css/main.css could give it neither of its non-colour cues.'
      );
    }
  }
  assert.deepStrictEqual(
    unmarked, [],
    `${unmarked.length} of the ${POS_MONEY_SINKS.length} POS money sinks bypass the marking ` +
    'path:\n  ' + unmarked.join('\n  ') +
    '\n\nFill them with RetailSystem._setMoney(el, n), which writes the digits AND ' +
    'decides the marking on the element that IS the amount.'
  );

  // CONDITIONALITY, against the helper the sinks delegate to. Without this,
  // "a decision was taken" is satisfiable by a helper that always says no —
  // and a marking that never fires carries exactly as much information as one
  // that always does.
  const neg = makeElementStub({ id: 'probe-neg' });
  ctx.RetailSystem._setMoney(neg, -65.44);
  assert.ok(
    neg.classList.contains('money--negative') && neg.classList.contains('money--accounting'),
    'RetailSystem._setMoney() did not mark a NEGATIVE amount. Classes: ' + JSON.stringify(neg.classes)
  );
  assert.ok(
    /−/.test(neg.textContent),
    'A negative amount written by _setMoney() carries no U+2212 MINUS SIGN. Strip every ' +
    'colour from this screen — a washed-out shop panel does exactly that — and the figure ' +
    'must still read as negative. Got: ' + JSON.stringify(neg.textContent)
  );
  const pos = makeElementStub({ id: 'probe-pos' });
  ctx.RetailSystem._setMoney(pos, 65.44);
  assert.ok(
    !pos.classList.contains('money--negative') && !pos.classList.contains('money--accounting') &&
    !/−/.test(pos.textContent),
    'A POSITIVE amount is also being marked negative, so the marking says nothing. Got: ' +
    JSON.stringify(pos.textContent) + ' ' + JSON.stringify(pos.classes)
  );

  console.log(
    `PASS: all ${POS_MONEY_SINKS.length} POS money sinks (${POS_MONEY_SINKS.map((i) => '#' + i).join(', ')}) ` +
    'are filled through _setMoney, and the marking is conditional'
  );
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
  testProductTilesAreKeyboardOperableAndAnnounced();
  testEveryPosHoverAffordanceHasAFocusCounterpart();
  testEveryFingerTargetMeetsTheTouchMinimum();
  testNegativeAmountsAreNotColourAlone();
  testEveryPosMoneySinkGoesThroughTheMarkingPath();
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
