/**
 * Regression test for a missing client-side guard on Credit-method sales.
 *
 * Bug: RetailSystem._checkout() (products/retail/frontend/subsystem-retail.js)
 * never checked the selected payment method against customer selection
 * before POSTing. Clicking the "Credit" payment button only calls
 * _setPayment('credit', this), which sets `_paymentMethod = 'credit'` with
 * no validation of its own. So a walk-in (no customer selected) could hit
 * Charge with Credit selected, and the Charge button would flip to
 * "Processing…", send the request, and only then get rejected by the
 * server -- retail_api.py's POST /api/sub/retail/sales returns 400
 * "Credit sales require a customer (walk-in not allowed)." -- at which
 * point the button reverted. The round trip was needed to discover
 * something the client already had enough information to reject locally.
 *
 * Fix: _checkout() now checks `this._paymentMethod === 'credit' && !customerId`
 * immediately after reading the customer selector and before touching the
 * Charge button or calling _post(), showing the same rule as an error toast
 * and returning without ever starting the network request.
 *
 * This test loads the REAL products/retail/frontend/subsystem-retail.js
 * (via Node's vm module, not a reimplementation) into a minimal sandboxed
 * DOM -- same harness as retail_checkout_error_toast_test.js -- selects
 * Credit with no customer, calls _checkout(), and asserts: no network call
 * was made, an error toast fired, and the Charge button was never disabled
 * / never left on "Processing…".
 *
 * No test framework is configured for this vanilla-JS, build-step-free
 * frontend (see CLAUDE.md), so this runs standalone with only Node
 * built-ins:
 *
 *   node products/retail/tests/retail_checkout_credit_walkin_guard_test.js
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

function loadRetailSystem({ fetchImpl, toasts, customerValue }) {
  const code = fs.readFileSync(FRONTEND_FILE, 'utf8');

  const checkoutBtn = makeElementStub({ id: 'pos-checkout-btn', textContent: 'Charge — $9.99', disabled: false });
  const els = {
    'pos-checkout-btn': checkoutBtn,
    'pos-tendered': makeElementStub({ value: '' }),
    // '' mirrors the real <select id="pos-customer"> default option value
    // for "no customer selected" (a walk-in) -- see _checkout()'s
    // `document.getElementById('pos-customer')?.value || null` read.
    'pos-customer': makeElementStub({ value: customerValue }),
  };

  const sandbox = {
    console,
    t: (s) => s, // stand-in for i18n.js's global `t()` shorthand
    fetch: fetchImpl,
    getComputedStyle: () => ({ getPropertyValue: () => '' }),
    document: {
      getElementById(id) { return els[id] || makeElementStub({ id }); },
      createElement() { return makeElementStub(); },
      querySelector() { return makeElementStub(); },
      querySelectorAll() { return []; },
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
  let fetchCalled = false;
  // If this ever reaches the network, the guard has regressed -- fail loudly
  // rather than let a real POST stand in for the assertion.
  const fetchImpl = () => { fetchCalled = true; return Promise.reject(new Error('_checkout() must not call the network for an invalid credit sale')); };

  const { RetailSystem, checkoutBtn } = loadRetailSystem({ fetchImpl, toasts, customerValue: '' });

  RetailSystem._cart = [{
    product_id: 'p1', name: 'Widget', quantity: 1,
    unit_price: 9.99, tax_rate: 0, line_total: 9.99, max_stock: 10,
  }];
  RetailSystem._currentTotals = { subtotal: 9.99, discount: 0, tax: 0, total: 9.99 };
  RetailSystem._paymentMethod = 'credit'; // Credit selected via the pos-pay-btn, no customer picked

  await RetailSystem._checkout();

  assert.strictEqual(
    fetchCalled,
    false,
    'RetailSystem._checkout() must not POST /api/sub/retail/sales for a Credit sale with no ' +
    'customer selected -- the server always rejects this (retail_api.py: "Credit sales require ' +
    'a customer (walk-in not allowed)."), so the client should catch it before the network call.'
  );

  const errorToasts = toasts.filter(x => x.type === 'error');
  assert.ok(
    errorToasts.length >= 1,
    'RetailSystem._checkout() must show an error toast when Credit is selected with no customer. ' +
    'Toasts seen: ' + JSON.stringify(toasts)
  );

  assert.strictEqual(
    checkoutBtn.disabled,
    false,
    'Charge button must never be disabled/enter "Processing…" for a checkout that was rejected ' +
    'client-side before any network call was made.'
  );
  assert.ok(
    /Charge/.test(checkoutBtn.textContent),
    'Charge button text must remain untouched ("Charge — …") since _checkout() should return ' +
    'before touching the button at all. Got: ' + checkoutBtn.textContent
  );

  console.log('PASS: retail_checkout_credit_walkin_guard_test.js');
}

main().catch((err) => {
  console.error('FAIL: retail_checkout_credit_walkin_guard_test.js');
  console.error(err);
  process.exitCode = 1;
});
