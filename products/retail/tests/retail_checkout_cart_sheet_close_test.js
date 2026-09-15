/**
 * Regression test for the "after making a sale, the dashboard button
 * doesn't click" bug reported from the shop floor.
 *
 * Bug (retail-hardware-viewports Step 9, docs/design/phone-ui-redesign.md
 * ~S3.1): on a narrow (<=640px) till, tapping the peek-bar "Charge" pill
 * opens the cart as a fixed sheet (`RetailSystem._toggleCartSheet(true)` ->
 * `.pos-wrap` gains `pos-sheet-open`), which renders `.pos-right` as a
 * `position:fixed` panel at z-index 5002, ABOVE the bottom tab bar
 * (`.sub-tabbar`, z-index 500, css/main.css) that carries Dashboard/Stock/
 * Customers. `RetailSystem._checkout()`'s success branch reset the CART's
 * contents (`_clearCart()`) but never reset the SHEET's own layout state --
 * the only other call to `_toggleCartSheet(false)` anywhere in this file is
 * the "tap outside the sheet" handler, which only fires when a tap lands on
 * the empty scrim itself, never on `.pos-right`'s own (now empty) content.
 * So after a completed sale the sheet stayed open, its real content
 * (a `.pos-summary` div) kept covering the tab bar, and every subsequent tap
 * on Dashboard/Stock/Customers/More landed on that leftover sheet and did
 * nothing -- permanently, until the page was reloaded.
 *
 * Measured directly in a real 390x844 Chromium browser: with the sheet left
 * open, `document.elementFromPoint` at the Dashboard tab's on-screen centre
 * resolved to `DIV.pos-summary`, and a genuine unforced Playwright
 * `page.click()` on the Dashboard tab timed out with "<div class=
 * "pos-summary"> ... intercepts pointer events". After the fix below, the
 * same click succeeds and the header updates to "Dashboard".
 *
 * Fix: `_checkout()`'s success branch now also calls
 * `this._toggleCartSheet(false)` (mirroring the open call the peek bar's
 * Charge pill makes), immediately after `this._clearCart()`.
 *
 * WHY THIS TEST CANNOT BE (AND DOES NOT TRY TO BE) A DOM/CSS TEST.
 * This file's sibling harnesses use a hand-rolled element-stub fake DOM with
 * no CSSOM, no z-index stacking, and no elementFromPoint -- structurally
 * unable to see "an element is visually covered by another". This test does
 * NOT attempt that. It tests the one thing that fake DOM CAN see honestly:
 * the STATE TRANSITION `_checkout()` is responsible for -- whether
 * `.pos-wrap`'s `pos-sheet-open` class is actually gone after a successful
 * sale. A real classList stub (add/remove/contains/toggle backed by a Set)
 * is used, pre-seeded WITH `pos-sheet-open` already present, so the
 * assertion only passes if `_checkout()` itself removes it -- a `_checkout()`
 * that silently no-ops (guard clause, thrown exception, wrong branch) leaves
 * the pre-seeded class in place and this test goes red, not green. The
 * visual/hit-testing half of this bug (does the leftover class actually
 * cover the tab bar) was proven separately in a real browser -- see the
 * diagnosis this test's own bug report cites -- and is not re-litigated
 * here; CSS wiring (media query, z-index) is not source this test can
 * exercise, and grepping for it would only prove the string exists, not
 * that it runs on the reachable path.
 *
 * This test loads the REAL products/retail/frontend/subsystem-retail.js
 * (via Node's vm module, not a reimplementation) into a minimal sandboxed
 * DOM -- same technique as retail_checkout_error_toast_test.js -- stubs
 * `RetailSystem._post` to resolve as a real successful sale response
 * (there is no server here, licensed or not; the point is not the server,
 * it is `_checkout()`'s CLIENT-SIDE success-branch cleanup), and calls the
 * REAL, unmodified `_checkout()`.
 *
 * No test framework is configured for this vanilla-JS, build-step-free
 * frontend (see CLAUDE.md), so this runs standalone with only Node
 * built-ins:
 *
 *   node products/retail/tests/retail_checkout_cart_sheet_close_test.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const FRONTEND_FILE = path.join(__dirname, '..', 'frontend', 'subsystem-retail.js');

function makeElementStub(overrides) {
  return Object.assign({
    innerHTML: '',
    textContent: '',
    value: '',
    id: '',
    disabled: false,
    style: {},
    appendChild() {},
    getAttribute() { return null; },
    setAttribute() {},
    querySelectorAll() { return []; },
    addEventListener() {},
  }, overrides);
}

// A real (if minimal) classList: Set-backed add/remove/contains/toggle,
// matching the exact `classList.toggle(name, force)` two-arg contract
// `_toggleCartSheet` calls. Anything less (e.g. a bare object with no
// `toggle`) would make `_toggleCartSheet` silently no-op inside its own
// try/catch ("cosmetic only") -- which would make this test structurally
// unable to fail, the exact vacuous-pass shape ENGINEERING.md warns about.
function makeClassListStub(initialClasses) {
  const set = new Set(initialClasses || []);
  return {
    add(c) { set.add(c); },
    remove(c) { set.delete(c); },
    contains(c) { return set.has(c); },
    toggle(c, force) {
      const shouldHave = force === undefined ? !set.has(c) : !!force;
      if (shouldHave) set.add(c); else set.delete(c);
      return shouldHave;
    },
  };
}

function loadRetailSystem({ fetchImpl, toasts, posWrap }) {
  const code = fs.readFileSync(FRONTEND_FILE, 'utf8');

  const checkoutBtn = makeElementStub({ id: 'pos-checkout-btn', textContent: 'Processing…', disabled: true });
  const els = {
    'pos-checkout-btn': checkoutBtn,
    'pos-tendered': makeElementStub({ value: '' }),
    'pos-customer': makeElementStub({ value: '' }),
  };

  const sandbox = {
    console,
    t: (s) => s, // stand-in for i18n.js's global `t()` shorthand
    fetch: fetchImpl,
    getComputedStyle: () => ({ getPropertyValue: () => '' }),
    // _showReceipt schedules a cosmetic 8s auto-dismiss timer -- a no-op
    // stub keeps this test from either crashing (ReferenceError: setTimeout
    // is not defined inside a fresh vm context, which does not inherit
    // Node's globals) or hanging the process for a real 8 seconds.
    setTimeout() { return 0; },
    clearTimeout() {},
    document: {
      getElementById(id) { return els[id] || makeElementStub({ id }); },
      createElement() { return makeElementStub(); },
      // '.pos-wrap' resolves to the SAME stateful element every time (the
      // real DOM only ever has one), so `_toggleCartSheet`'s
      // `classList.toggle` calls accumulate on one object this test can
      // inspect afterwards. Anything else falls back to a fresh throwaway
      // stub, matching every other unlisted selector in this file's sibling
      // tests.
      querySelector(sel) { return sel === '.pos-wrap' ? posWrap : makeElementStub(); },
      querySelectorAll() { return []; },
      body: { appendChild() {} },
      head: { appendChild() {} },
      documentElement: { getAttribute() { return null; } },
    },
    SubsystemApp: {
      showToast(msg, type) { toasts.push({ msg, type }); },
      checkAuthAndSetup() {},
    },
  };
  sandbox.window = sandbox; // enough for the `window.SubsystemApp` / `window.RetailSystem` refs used here

  vm.createContext(sandbox);
  vm.runInContext(code, sandbox, { filename: FRONTEND_FILE });

  assert.ok(sandbox.RetailSystem, 'subsystem-retail.js did not expose window.RetailSystem');
  return { RetailSystem: sandbox.RetailSystem, checkoutBtn };
}

async function main() {
  const toasts = [];

  // Pre-seeded WITH pos-sheet-open already present -- exactly the state a
  // narrow-viewport till is in when the cashier taps the real in-sheet
  // Charge button (the peek bar's Charge pill already opened it). If
  // _checkout() does not reach (or does not correctly run) the fix, this
  // class is still here at the end and the test fails.
  const posWrap = makeElementStub({ className: 'pos-wrap pos-sheet-open' });
  posWrap.classList = makeClassListStub(['pos-wrap', 'pos-sheet-open']);

  // No real server anywhere in this test -- only /api/sub/retail/sales is
  // ever called on this path (the printer-kick POST is gated on a printer
  // config this sandbox never sets, so it never fires), and it resolves as
  // a real, successfully-shaped sale response. Anything unexpected rejects
  // loudly rather than silently returning something checkout could
  // misinterpret as success.
  const fetchImpl = (url) => {
    if (String(url).includes('/api/sub/retail/sales')) {
      return Promise.resolve({
        status: 200,
        json: async () => ({
          status: 'success',
          data: {
            id: 1,
            sale_number: 'S-TEST-0001',
            amount_paid: 9.99,
            change: 0,
            total: 9.99,
            subtotal: 9.99,
            tax_amount: 0,
            discount_amount: 0,
            points_redeemed_amount: 0,
            lines: [{ product_id: 'p1', name: 'Widget', quantity: 1, line_total: 9.99 }],
          },
        }),
      });
    }
    return Promise.reject(new Error('unexpected fetch in this test: ' + url));
  };

  const { RetailSystem, checkoutBtn } = loadRetailSystem({ fetchImpl, toasts, posWrap });

  RetailSystem._cart = [{
    product_id: 'p1', name: 'Widget', quantity: 1,
    unit_price: 9.99, tax_rate: 0, line_total: 9.99, max_stock: 10,
    category_id: null, parent_product_id: null,
  }];
  RetailSystem._currentTotals = { subtotal: 9.99, discount: 0, tax: 0, total: 9.99 };
  RetailSystem._paymentMethod = 'cash';

  await RetailSystem._checkout();

  // Anti-vacuity: prove the success branch actually ran, not merely that
  // the pre-seeded class happens to still read as absent for some unrelated
  // reason. A silent early return (guard clause, wrong branch, thrown
  // exception before the fix line) would leave the cart non-empty AND the
  // class still present -- either failure surfaces here with a concrete
  // reason rather than a bare "false !== true".
  const errorToasts = toasts.filter(x => x.type === 'error');
  assert.strictEqual(
    errorToasts.length, 0,
    'checkout hit its failure/catch branch instead of the success branch -- toasts: ' + JSON.stringify(toasts)
  );
  assert.strictEqual(
    RetailSystem._cart.length, 0,
    'cart was not cleared -- _checkout() did not reach its success branch at all'
  );

  // THE actual regression check.
  assert.strictEqual(
    posWrap.classList.contains('pos-sheet-open'), false,
    '_checkout() succeeded but left .pos-wrap carrying "pos-sheet-open" -- on a ' +
    '<=640px till this leaves the cart sheet (position:fixed, z-index 5002) covering ' +
    'the bottom tab bar (z-index 500) that carries Dashboard/Stock/Customers, so every ' +
    'tap there is silently swallowed by the leftover sheet. _checkout()\'s success ' +
    'branch must call this._toggleCartSheet(false) (mirroring the open call the peek ' +
    'bar\'s Charge pill makes) so a completed sale actually returns the till to its ' +
    'closed state.'
  );

  console.log('PASS: retail_checkout_cart_sheet_close_test.js');
}

main().catch((err) => {
  console.error('FAIL: retail_checkout_cart_sheet_close_test.js');
  console.error(err);
  process.exitCode = 1;
});
