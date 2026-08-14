/**
 * Regression test for a silent-failure bug on the Retail POS Charge button.
 *
 * Bug: RetailSystem._checkout() (products/retail/frontend/subsystem-retail.js)
 * wrapped its POST /api/sub/retail/sales call in a try/catch whose catch
 * block only reset the Charge button's text/disabled state -- it never
 * called SubsystemApp.showToast(...). _fetch() (same file) only throws for
 * HTTP 401 (which shows its own relogin modal); every OTHER failure mode --
 * the server unreachable, a 500 with a non-JSON body making _post's
 * res.json() throw a SyntaxError, a rejected fetch() promise -- propagated
 * to this catch block with zero visible feedback. The cashier watched
 * "Processing…" flash back to "Charge" with no indication of whether the
 * sale went through, whether to retry, or whether the customer's cash was
 * actually recorded -- on the single most consequential action on the
 * screen.
 *
 * Fix: the catch block now logs the error and calls
 * SubsystemApp.showToast(..., 'error') before resetting the button, the
 * same pattern this file already uses in _deleteCategory / _deleteCustomer.
 *
 * This test loads the REAL products/retail/frontend/subsystem-retail.js
 * (via Node's vm module, not a reimplementation) into a minimal sandboxed
 * DOM, forces the sales POST to reject (simulating a network failure --
 * non-401, so _fetch's own auth handling never kicks in), calls
 * _checkout(), and asserts SubsystemApp.showToast was called with an
 * 'error' toast AND that the Charge button was re-enabled.
 *
 * No test framework is configured for this vanilla-JS, build-step-free
 * frontend (see CLAUDE.md), so this runs standalone with only Node
 * built-ins:
 *
 *   node products/retail/tests/retail_checkout_error_toast_test.js
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

function loadRetailSystem({ fetchImpl, toasts }) {
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
  // Simulates a network-level failure (server unreachable / connection
  // reset) -- NOT an HTTP 401, so this exercises the generic catch path
  // in _checkout() rather than the already-handled auth-expiry path.
  const failingFetch = () => Promise.reject(new Error('simulated network failure'));

  const { RetailSystem, checkoutBtn } = loadRetailSystem({ fetchImpl: failingFetch, toasts });

  RetailSystem._cart = [{
    product_id: 'p1', name: 'Widget', quantity: 1,
    unit_price: 9.99, tax_rate: 0, line_total: 9.99, max_stock: 10,
  }];
  RetailSystem._currentTotals = { subtotal: 9.99, discount: 0, tax: 0, total: 9.99 };
  RetailSystem._paymentMethod = 'cash';

  await RetailSystem._checkout();

  const errorToasts = toasts.filter(x => x.type === 'error');
  assert.ok(
    errorToasts.length >= 1,
    'RetailSystem._checkout() must call SubsystemApp.showToast(..., "error") when the ' +
    'sales POST fails for a non-401 reason (network error, 500, malformed JSON) -- ' +
    'it silently reset the button with zero cashier-visible feedback instead. ' +
    'Toasts seen: ' + JSON.stringify(toasts)
  );

  assert.strictEqual(
    checkoutBtn.disabled,
    false,
    'Charge button must be re-enabled after a failed checkout so the cashier can retry.'
  );
  assert.ok(
    /Charge/.test(checkoutBtn.textContent),
    'Charge button text must be restored (not left on "Processing…") after a failed checkout. ' +
    'Got: ' + checkoutBtn.textContent
  );

  console.log('PASS: retail_checkout_error_toast_test.js');
}

main().catch((err) => {
  console.error('FAIL: retail_checkout_error_toast_test.js');
  console.error(err);
  process.exitCode = 1;
});
