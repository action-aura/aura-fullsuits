/**
 * Regression test for a stored-XSS bug on the Retail Customers screen
 * (products/retail/frontend/subsystem-retail.js).
 *
 * Bug: RetailSystem._showCustomerModal() (the Add/Edit Customer form) and
 * RetailSystem._viewCustomer() (the customer detail modal) interpolated
 * `cu.name` / `cu.phone` / `cu.email` / `cu.address` directly into
 * `innerHTML` with zero escaping -- even though the Customers table listing
 * right above them (_loadCustomers) already escapes this exact data via
 * `this._esc(...)`, and its own comment explains why: a customer record
 * "can arrive from ANOTHER DEVICE over the sync relay", which is a
 * genuinely new trust boundary (same reasoning as Fix 7 for Categories).
 *
 * A customer name/phone/email/address is not guaranteed to be trusted,
 * admin-typed text: `create_customer` in retail_api.py does no
 * sanitization, and CLAUDE.md is explicit that this app has "no real RBAC
 * -- only a bare role string, no permission matrix." A customer created
 * with a name like `Jane" onmouseover="alert(1)"><img src=x onerror=alert(2)>`
 * renders safely in the table (it uses this._esc), but the payload executes
 * immediately the moment anyone -- including the user who created the
 * record -- clicks "Edit" on that customer (_showCustomerModal) or clicks
 * the row to open the detail view (_viewCustomer). Full cookie/session
 * access follows, since this file's fetches use `credentials:'include'`.
 *
 * Fix: route cu.name/cu.phone/cu.email/cu.address through this._esc(...)
 * at every interpolation site in both functions, matching the convention
 * already used one line over in the table listing.
 *
 * This test loads the REAL products/retail/frontend/subsystem-retail.js
 * (via Node's vm module, not a reimplementation) into a minimal sandboxed
 * DOM, seeds it with a customer whose fields are HTML/attribute-injection
 * payloads, opens both the Edit-Customer modal and the customer detail
 * view, and asserts the raw payload never appears unescaped in the
 * resulting markup.
 *
 * No test framework is configured for this vanilla-JS, build-step-free
 * frontend (see CLAUDE.md), so this runs standalone with only Node
 * built-ins:
 *
 *   node products/retail/tests/retail_customer_modal_xss_test.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const FRONTEND_FILE = path.join(__dirname, '..', 'frontend', 'subsystem-retail.js');

// Breaks out of a double-quoted attribute (`value="..."`) AND injects a new
// tag into a plain innerHTML text node -- exercises both injection contexts
// the finding describes (modal form inputs vs. the detail view's <h3>).
const MALICIOUS_NAME = `Jane" onmouseover="alert(document.cookie)"><img src=x onerror=alert(1)>`;
const MALICIOUS_PHONE = `+1" onmouseover="alert(2)`;
const MALICIOUS_EMAIL = `a@b.com" onmouseover="alert(3)`;
const MALICIOUS_ADDRESS = `123 Main St" onmouseover="alert(4)`;

function makeElementStub() {
  return {
    innerHTML: '',
    textContent: '',
    id: '',
    className: '',
    style: {},
    appendChild() {},
    addEventListener() {},
    focus() {},
    getAttribute() { return null; },
    setAttribute() {},
    querySelectorAll() { return []; },
  };
}

function loadRetailSystem(onCreateElement) {
  const code = fs.readFileSync(FRONTEND_FILE, 'utf8');

  const sandbox = {
    console,
    t: (s) => s, // stand-in for i18n.js's global `t()` shorthand
    fetch: () => Promise.reject(new Error('network not used by this test')),
    getComputedStyle: () => ({ getPropertyValue: () => '' }),
    document: {
      getElementById() { return makeElementStub(); },
      createElement() {
        const el = makeElementStub();
        if (onCreateElement) onCreateElement(el);
        return el;
      },
      querySelector() { return makeElementStub(); },
      querySelectorAll() { return []; },
      head: { appendChild() {} },
      body: { appendChild() {} },
      documentElement: { getAttribute() { return null; } },
    },
  };
  sandbox.window = sandbox; // enough for the `window.Chart` / `window.RetailSystem` refs used here

  vm.createContext(sandbox);
  vm.runInContext(code, sandbox, { filename: FRONTEND_FILE });

  assert.ok(sandbox.RetailSystem, 'subsystem-retail.js did not expose window.RetailSystem');
  return sandbox.RetailSystem;
}

function testShowCustomerModalEscapesFields() {
  let capturedOverlay = null;
  const RetailSystem = loadRetailSystem((el) => { capturedOverlay = el; });

  RetailSystem._showCustomerModal({
    id: 'c1',
    name: MALICIOUS_NAME,
    phone: MALICIOUS_PHONE,
    email: MALICIOUS_EMAIL,
    address: MALICIOUS_ADDRESS,
  });

  assert.ok(capturedOverlay, 'Edit Customer modal overlay was never created');
  const html = capturedOverlay.innerHTML;

  for (const [field, payload] of [
    ['name', MALICIOUS_NAME], ['phone', MALICIOUS_PHONE],
    ['email', MALICIOUS_EMAIL], ['address', MALICIOUS_ADDRESS],
  ]) {
    assert.ok(
      !html.includes(payload),
      `Edit Customer modal: the raw, unescaped malicious ${field} leaked into ` +
      `the form's innerHTML -- this breaks out of the input's value="..." ` +
      `attribute (or injects a tag) and is a stored-XSS injection point. Got: ${html}`
    );
  }
  assert.ok(
    html.includes('&quot;'),
    `Edit Customer modal: expected the payloads' " to be HTML-escaped (via ` +
    `this._esc) but no escaped form was found. Got: ${html}`
  );

  console.log('PASS: Edit Customer modal escapes name/phone/email/address');
}

function testViewCustomerEscapesNameAndPhone() {
  let capturedOverlay = null;
  const RetailSystem = loadRetailSystem((el) => { capturedOverlay = el; });

  RetailSystem._customers = [{
    id: 'c1', name: MALICIOUS_NAME, phone: MALICIOUS_PHONE,
    total_spent: 42, order_count: 3, loyalty_points: 10,
  }];

  // _viewCustomer is async but builds+sets overlay.innerHTML synchronously
  // before its first `await` (the purchase-history fetch), so the markup is
  // already in place by the time this call returns control.
  RetailSystem._viewCustomer('c1');

  assert.ok(capturedOverlay, 'Customer detail modal overlay was never created');
  const html = capturedOverlay.innerHTML;

  assert.ok(
    !html.includes(MALICIOUS_NAME),
    `Customer detail modal: the raw, unescaped malicious name leaked into the ` +
    `<h3> title's innerHTML -- a stored-XSS injection point. Got: ${html}`
  );
  assert.ok(
    !html.includes(MALICIOUS_PHONE),
    `Customer detail modal: the raw, unescaped malicious phone leaked into the ` +
    `stat tile's innerHTML -- a stored-XSS injection point. Got: ${html}`
  );
  assert.ok(
    html.includes('&quot;'),
    `Customer detail modal: expected the payloads' " to be HTML-escaped (via ` +
    `this._esc) but no escaped form was found. Got: ${html}`
  );

  console.log('PASS: Customer detail modal escapes name and phone');
}

function main() {
  testShowCustomerModalEscapesFields();
  testViewCustomerEscapesNameAndPhone();
  console.log('PASS: retail_customer_modal_xss_test.js');
}

try {
  main();
} catch (err) {
  console.error('FAIL: retail_customer_modal_xss_test.js');
  console.error(err);
  process.exitCode = 1;
}
