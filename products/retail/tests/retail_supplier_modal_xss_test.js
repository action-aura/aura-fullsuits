/**
 * Regression test for a stored-XSS bug on the Retail Suppliers screen
 * (products/retail/frontend/subsystem-retail.js).
 *
 * Bug: RetailSystem._showSupplierModal() (the Add/Edit Supplier form)
 * interpolated `s.name` / `s.phone` / `s.email` / `s.address` directly into
 * `innerHTML`'s `value="..."` attributes with zero escaping -- even though
 * the Suppliers table listing right above it (_loadSuppliers) already
 * escapes this exact data via `this._esc(...)`, and its own comment
 * explains why: a supplier record "can arrive from ANOTHER DEVICE over the
 * sync relay", which is a genuinely new trust boundary (same reasoning as
 * Fix 7 for Categories, and the identical gap already fixed once for the
 * Customer modal -- see retail_customer_modal_xss_test.js).
 *
 * A supplier name/phone/email/address is not guaranteed to be trusted,
 * admin-typed text: create_supplier in retail_api.py does no sanitization,
 * and CLAUDE.md is explicit that this app has "no real RBAC -- only a bare
 * role string, no permission matrix." A supplier created with a name like
 * `Acme" onmouseover="alert(1)"><img src=x onerror=alert(2)>` renders
 * safely in the table (it uses this._esc), but the payload executes
 * immediately the moment anyone -- including the user who created the
 * record -- clicks "Edit Supplier" (_showSupplierModal). Full cookie/
 * session access follows, since this file's fetches use
 * `credentials:'include'`.
 *
 * Fix: route s.name/s.phone/s.email/s.address through this._esc(...) at
 * every interpolation site in _showSupplierModal's Details panel, matching
 * the convention already used in the table listing and in the Customer
 * modal fix.
 *
 * This test loads the REAL products/retail/frontend/subsystem-retail.js
 * (via Node's vm module, not a reimplementation) into a minimal sandboxed
 * DOM, seeds it with a supplier whose fields are HTML/attribute-injection
 * payloads, opens the Edit-Supplier modal, and asserts the raw payload
 * never appears unescaped in the resulting markup.
 *
 * No test framework is configured for this vanilla-JS, build-step-free
 * frontend (see CLAUDE.md), so this runs standalone with only Node
 * built-ins:
 *
 *   node products/retail/tests/retail_supplier_modal_xss_test.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const FRONTEND_FILE = path.join(__dirname, '..', 'frontend', 'subsystem-retail.js');

// Breaks out of a double-quoted attribute (`value="..."`) -- exercises the
// injection context the finding describes (modal form inputs).
const MALICIOUS_NAME = `Acme" onmouseover="alert(document.cookie)"><img src=x onerror=alert(1)>`;
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

function testShowSupplierModalEscapesFields() {
  let capturedOverlay = null;
  const RetailSystem = loadRetailSystem((el) => { capturedOverlay = el; });

  RetailSystem._showSupplierModal({
    id: 's1',
    name: MALICIOUS_NAME,
    phone: MALICIOUS_PHONE,
    email: MALICIOUS_EMAIL,
    address: MALICIOUS_ADDRESS,
  });

  assert.ok(capturedOverlay, 'Edit Supplier modal overlay was never created');
  const html = capturedOverlay.innerHTML;

  for (const [field, payload] of [
    ['name', MALICIOUS_NAME], ['phone', MALICIOUS_PHONE],
    ['email', MALICIOUS_EMAIL], ['address', MALICIOUS_ADDRESS],
  ]) {
    assert.ok(
      !html.includes(payload),
      `Edit Supplier modal: the raw, unescaped malicious ${field} leaked into ` +
      `the form's innerHTML -- this breaks out of the input's value="..." ` +
      `attribute (or injects a tag) and is a stored-XSS injection point. Got: ${html}`
    );
  }
  assert.ok(
    html.includes('&quot;'),
    `Edit Supplier modal: expected the payloads' " to be HTML-escaped (via ` +
    `this._esc) but no escaped form was found. Got: ${html}`
  );

  console.log('PASS: Edit Supplier modal escapes name/phone/email/address');
}

function main() {
  testShowSupplierModalEscapesFields();
  console.log('PASS: retail_supplier_modal_xss_test.js');
}

try {
  main();
} catch (err) {
  console.error('FAIL: retail_supplier_modal_xss_test.js');
  console.error(err);
  process.exitCode = 1;
}
