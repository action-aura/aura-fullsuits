/**
 * Regression test for a stored-XSS bug on the Retail Products & Inventory
 * screen (products/retail/frontend/subsystem-retail.js).
 *
 * Bug: RetailSystem._renderProductTable()'s Stock/Delete action buttons
 * built their `onclick="..."` attribute from the raw product name with only
 * apostrophes backslash-escaped:
 *
 *   onclick="RetailSystem._openStockAdjust('${this._esc(p.id)}',
 *            '${p.name.replace(/'/g,"\\'")}',${p.total_stock})"
 *
 * -- never routing the name through this file's own `this._esc(...)`
 * helper, even though the very next `<td>` over (the name cell itself)
 * already does exactly that (`this._esc(p.name)`). A double quote in the
 * product name is not neutralized by an apostrophe-only replace, so it
 * terminates the double-quoted `onclick="..."` attribute early; the
 * trailing text becomes a brand new attribute on the `<button>` (e.g.
 * `onmouseover="..."`), which the browser then executes.
 *
 * A product name is not guaranteed to be trusted, admin-typed text: it can
 * arrive via the CSV Import Wizard (no sanitization there) or be typed by
 * any logged-in user -- CLAUDE.md is explicit that this app has "no real
 * RBAC -- only a bare role string, no permission matrix." So a low-trust
 * user can plant a name like `Widget" onmouseover="alert(document.cookie)`
 * and get arbitrary JS execution -- with full cookie access, since this
 * file's fetches use `credentials:'include'` -- the moment any other staff
 * member views or hovers the Products & Inventory screen.
 *
 * The same unescaped `name` is then threaded straight into the Stock-adjust
 * modal's title (`<h3>... Adjust Stock — ${name}</h3>`, built via
 * innerHTML), which is a second, independent injection point once the
 * Stock button is clicked.
 *
 * Fix: route the name through `this._esc(...)` at the onclick-attribute
 * call sites (matching the identical convention already used one `<td>`
 * over, and already used for suppliers at _renderSuppliers), and escape it
 * again at the modal-title interpolation site in _openStockAdjust.
 *
 * This test loads the REAL products/retail/frontend/subsystem-retail.js
 * (via Node's vm module, not a reimplementation) into a minimal sandboxed
 * DOM, seeds it with a product whose name is an attribute-breaking payload,
 * renders the Products table and opens the Stock-adjust modal, and asserts
 * the raw payload never appears unescaped in the resulting markup.
 *
 * No test framework is configured for this vanilla-JS, build-step-free
 * frontend (see CLAUDE.md), so this runs standalone with only Node
 * built-ins:
 *
 *   node products/retail/tests/retail_products_table_name_xss_test.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const FRONTEND_FILE = path.join(__dirname, '..', 'frontend', 'subsystem-retail.js');

// Breaks out of a double-quoted `onclick="..."` attribute by way of a raw
// `"`, then plants a new event-handler attribute -- exactly the scenario
// the finding describes.
const MALICIOUS_NAME = `Widget" onmouseover="alert(document.cookie)`;

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

function loadRetailSystem() {
  const code = fs.readFileSync(FRONTEND_FILE, 'utf8');

  const tbodyEl = makeElementStub();

  const sandbox = {
    console,
    t: (s) => s, // stand-in for i18n.js's global `t()` shorthand
    fetch: () => Promise.reject(new Error('network not used by this test')),
    getComputedStyle: () => ({ getPropertyValue: () => '' }),
    document: {
      getElementById() { return makeElementStub(); },
      createElement() { return makeElementStub(); },
      querySelector(sel) {
        if (sel === '#prod-table tbody') return tbodyEl;
        return makeElementStub();
      },
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
  return { RetailSystem: sandbox.RetailSystem, tbodyEl };
}

function testProductTableButtonsEscapeName() {
  const { RetailSystem, tbodyEl } = loadRetailSystem();

  RetailSystem._renderProductTable([{
    id: 'p1', name: MALICIOUS_NAME, sku: 'SKU-1', barcode: '',
    category_id: null, category_name: null,
    total_stock: 10, reorder_level: 2, cost_price: 5, sell_price: 9.99, unit: 'pc',
  }]);

  const html = tbodyEl.innerHTML;
  assert.ok(
    !html.includes(MALICIOUS_NAME),
    `Products table: the raw, unescaped malicious product name leaked into the ` +
    `Stock/Delete buttons' onclick attribute -- this breaks out of the ` +
    `double-quoted attribute and is a stored-XSS injection point. Got: ${html}`
  );
  assert.ok(
    html.includes('&quot;'),
    `Products table: expected the product name's " to be HTML-escaped (via ` +
    `this._esc) inside the onclick attribute, but no escaped form was found. Got: ${html}`
  );

  console.log('PASS: Products table Stock/Delete buttons escape a malicious product name');
}

function testStockAdjustModalEscapesName() {
  // _openStockAdjust receives whatever the onclick attribute's JS argument
  // decodes to at runtime -- by the time an inline `onclick="..."` attribute
  // is parsed and its JS executed, the browser has already HTML-entity-
  // decoded the attribute value once, so the function sees the plain
  // (un-entity-encoded) name here. A fresh sandbox is used so the
  // `document.createElement('div')` call that builds the modal overlay can
  // be captured and inspected.
  const code = fs.readFileSync(FRONTEND_FILE, 'utf8');
  let capturedOverlay = null;

  const sandbox = {
    console,
    t: (s) => s,
    fetch: () => Promise.reject(new Error('network not used by this test')),
    getComputedStyle: () => ({ getPropertyValue: () => '' }),
    document: {
      getElementById() { return makeElementStub(); },
      createElement() {
        capturedOverlay = makeElementStub();
        return capturedOverlay;
      },
      querySelector() { return makeElementStub(); },
      querySelectorAll() { return []; },
      head: { appendChild() {} },
      body: { appendChild() {} },
      documentElement: { getAttribute() { return null; } },
    },
  };
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(code, sandbox, { filename: FRONTEND_FILE });

  sandbox.RetailSystem._openStockAdjust('p1', MALICIOUS_NAME, 10);

  assert.ok(capturedOverlay, 'Stock-adjust modal overlay was never created');
  const html = capturedOverlay.innerHTML;
  assert.ok(
    !html.includes(MALICIOUS_NAME),
    `Stock-adjust modal: the raw, unescaped malicious product name leaked into ` +
    `the modal title's innerHTML -- a second stored-XSS injection point. Got: ${html}`
  );
  assert.ok(
    html.includes('&quot;'),
    `Stock-adjust modal: expected the product name's " to be HTML-escaped ` +
    `(via this._esc) in the title, but no escaped form was found. Got: ${html}`
  );

  console.log('PASS: Stock-adjust modal title escapes a malicious product name');
}

function main() {
  testProductTableButtonsEscapeName();
  testStockAdjustModalEscapesName();
  console.log('PASS: retail_products_table_name_xss_test.js');
}

try {
  main();
} catch (err) {
  console.error('FAIL: retail_products_table_name_xss_test.js');
  console.error(err);
  process.exitCode = 1;
}
