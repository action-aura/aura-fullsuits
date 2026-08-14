/**
 * Regression test for a stored-XSS bug on the Retail POS screen.
 *
 * Bug: RetailSystem._renderPOSGrid() and RetailSystem._renderCart()
 * (products/retail/frontend/subsystem-retail.js) interpolated a product's
 * `name` field directly into innerHTML (and, for the grid, into a
 * double-quoted `title="..."` attribute too) with no escaping -- even
 * though this exact file already has an established `this._esc(...)`
 * convention for the identical field (_renderProductTable already does
 * `this._esc(p.name)` on the Products table).
 *
 * A product name is not necessarily admin-hand-typed, trusted text: it can
 * arrive via the CSV Import Wizard (no sanitization there) or be typed by
 * any logged-in user -- CLAUDE.md is explicit that this app has "no real
 * RBAC -- only a bare role string, no permission matrix." A name containing
 * `"><img src=x onerror=...>` would break out of the `title` attribute (or
 * the surrounding element) and execute with full DOM/cookie access the
 * moment any cashier viewed the POS grid or added the product to their
 * cart -- a real stored-XSS-to-session-hijack path, since this file's
 * fetches use `credentials:'include'`.
 *
 * Fix: both call sites now route `p.name` / `item.name` through the same
 * `this._esc(...)` helper _renderProductTable already uses.
 *
 * This test loads the REAL products/retail/frontend/subsystem-retail.js
 * (via Node's vm module, not a reimplementation) into a minimal sandboxed
 * DOM, seeds it with a product whose name is an HTML/attribute-injection
 * payload, renders the POS grid and the cart, and asserts the raw payload
 * never appears unescaped in the resulting innerHTML.
 *
 * No test framework is configured for this vanilla-JS, build-step-free
 * frontend (see CLAUDE.md), so this runs standalone with only Node
 * built-ins:
 *
 *   node products/retail/tests/retail_pos_name_xss_test.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const FRONTEND_FILE = path.join(__dirname, '..', 'frontend', 'subsystem-retail.js');

// A payload that is dangerous in TWO different contexts at once: it breaks
// out of a double-quoted HTML attribute (the `title="${p.name}"` site) AND
// contains a classic script-injection vector (the plain-text `${p.name}`
// site), matching how the finding describes the real-world exploit.
const MALICIOUS_NAME = `"><img src=x onerror=alert(document.cookie)>`;

function makeElementStub() {
  return {
    innerHTML: '',
    textContent: '',
    id: '',
    style: {},
    appendChild() {},
    getAttribute() { return null; },
    setAttribute() {},
    querySelectorAll() { return []; },
  };
}

function loadRetailSystem() {
  const code = fs.readFileSync(FRONTEND_FILE, 'utf8');

  const gridEl = makeElementStub();
  const cartEl = makeElementStub();

  const sandbox = {
    console,
    t: (s) => s, // stand-in for i18n.js's global `t()` shorthand
    fetch: () => Promise.reject(new Error('network not used by this test')),
    getComputedStyle: () => ({ getPropertyValue: () => '' }),
    document: {
      getElementById(id) {
        if (id === 'pos-product-grid') return gridEl;
        if (id === 'pos-cart') return cartEl;
        return makeElementStub();
      },
      createElement() { return makeElementStub(); },
      querySelector() { return makeElementStub(); },
      querySelectorAll() { return []; },
      head: { appendChild() {} },
      documentElement: { getAttribute() { return null; } },
    },
  };
  sandbox.window = sandbox; // enough for the `window.Chart` / `window.RetailSystem` refs used here

  vm.createContext(sandbox);
  vm.runInContext(code, sandbox, { filename: FRONTEND_FILE });

  assert.ok(sandbox.RetailSystem, 'subsystem-retail.js did not expose window.RetailSystem');
  return { RetailSystem: sandbox.RetailSystem, gridEl, cartEl };
}

function assertPayloadEscaped(html, label) {
  assert.ok(
    !html.includes(MALICIOUS_NAME),
    `${label}: the raw, unescaped malicious product name leaked into innerHTML -- ` +
    `this is a stored-XSS injection point. Got: ${html}`
  );
  assert.ok(
    html.includes('&lt;img') && html.includes('&quot;&gt;'),
    `${label}: expected the product name to be HTML-escaped (via this._esc), ` +
    `but no escaped form was found. Got: ${html}`
  );
}

function testPOSGridEscapesProductName() {
  const { RetailSystem, gridEl } = loadRetailSystem();

  RetailSystem._products = [{
    id: 'p1', name: MALICIOUS_NAME, sku: 'SKU-1', barcode: '',
    category_id: null, category_name: null,
    total_stock: 10, reorder_level: 2, sell_price: 9.99, unit: 'pc',
  }];
  RetailSystem._categories = [];
  RetailSystem._activeCat = null;

  RetailSystem._renderPOSGrid();

  assertPayloadEscaped(gridEl.innerHTML, 'POS product grid');

  console.log('PASS: POS product grid escapes a malicious product name');
}

function testCartEscapesItemName() {
  const { RetailSystem, cartEl } = loadRetailSystem();

  RetailSystem._cart = [{
    product_id: 'p1', name: MALICIOUS_NAME, quantity: 1,
    unit_price: 9.99, tax_rate: 0, line_total: 9.99, max_stock: 10,
  }];

  RetailSystem._renderCart();

  assertPayloadEscaped(cartEl.innerHTML, 'POS cart row');

  console.log('PASS: POS cart row escapes a malicious product name');
}

function main() {
  testPOSGridEscapesProductName();
  testCartEscapesItemName();
  console.log('PASS: retail_pos_name_xss_test.js');
}

try {
  main();
} catch (err) {
  console.error('FAIL: retail_pos_name_xss_test.js');
  console.error(err);
  process.exitCode = 1;
}
