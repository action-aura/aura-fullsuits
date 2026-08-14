/**
 * Regression test for a broken-UI bug on the Retail Suppliers screen
 * (products/retail/frontend/subsystem-retail.js).
 *
 * Bug: the Supplier Edit/Delete/+PO buttons in _loadSuppliers, and the
 * supplier-contact Delete button in _renderSupplierContactsList, built their
 * `onclick="..."` JS-argument for `name` like this:
 *
 *   '${this._esc(s.name).replace(/'/g,"\\'")}'
 *
 * `this._esc()` already turns every `'` into the HTML entity `&#39;` before
 * `.replace(/'/g,...)` ever runs -- so by the time `.replace` looks for a
 * literal `'` character to backslash-escape, none remain (they're already
 * multi-character `&#39;` entities). The `.replace` call is a dead no-op.
 *
 * The onclick attribute is HTML source text sitting inside a *double*-quoted
 * attribute (`onclick="..."`), with the JS argument itself delimited by
 * *single* quotes. When the browser parses that double-quoted attribute
 * value, it HTML-entity-decodes it (turning `&#39;` back into a literal `'`)
 * to produce the actual JS source text the inline handler compiles from.
 * Without the backslash that `.replace` was supposed to add, a supplier (or
 * supplier-contact) name containing an apostrophe -- e.g. "O'Brien
 * Distributors", a very plausible real business name -- decodes to a bare
 * `'` that terminates the JS string literal early:
 *
 *   RetailSystem._openEditSupplier('id','O'Brien Distributors', ...)
 *                                          ^ closes the string here
 *
 * This is a JS SyntaxError, so the inline handler silently fails to run.
 * Edit / Delete / +PO on that supplier row (and Delete on that supplier
 * contact) becomes permanently inert the moment its name contains an
 * apostrophe, with no workaround short of editing the database directly.
 *
 * Fix: apply the backslash-escape to the raw name *first*, then HTML-escape
 * the result (`this._esc(s.name.replace(/'/g,"\\'"))`) so the final decoded
 * JS source contains a backslash-escaped quote (`\'`) instead of a bare one.
 *
 * This test loads the REAL products/retail/frontend/subsystem-retail.js (via
 * Node's vm module, not a reimplementation), renders the Suppliers table and
 * the supplier-contacts list for a name containing an apostrophe, extracts
 * the generated `onclick="..."` attribute text, HTML-entity-decodes it
 * exactly as a browser would when parsing a double-quoted attribute value,
 * and then compiles+executes the decoded text as the inline handler's JS
 * source -- asserting it is syntactically valid AND that the argument the
 * handler receives round-trips back to the original name.
 *
 *   node products/retail/tests/retail_supplier_name_apostrophe_onclick_test.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const FRONTEND_FILE = path.join(__dirname, '..', 'frontend', 'subsystem-retail.js');

// A plausible real-world business name containing an apostrophe -- the
// scenario the finding describes.
const SUPPLIER_NAME = `O'Brien Distributors`;
const CONTACT_NAME = `Sam's Wholesale Contact`;

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

  const supTbodyEl = makeElementStub();
  const contactsListEl = makeElementStub();

  const sandbox = {
    console,
    t: (s) => s, // stand-in for i18n.js's global `t()` shorthand
    fetch: async () => ({
      status: 200,
      json: async () => ({
        status: 'success',
        data: [{ id: 's1', name: SUPPLIER_NAME, phone: '', email: '', address: '', order_count: 0 }],
      }),
    }),
    getComputedStyle: () => ({ getPropertyValue: () => '' }),
    document: {
      getElementById(id) {
        if (id === 'sup-contacts-list') return contactsListEl;
        return makeElementStub();
      },
      createElement() { return makeElementStub(); },
      querySelector(sel) {
        if (sel === '#sup-table tbody') return supTbodyEl;
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
  return { RetailSystem: sandbox.RetailSystem, supTbodyEl, contactsListEl };
}

// Mirrors what a browser does when it parses a double-quoted HTML attribute
// value: entity references are decoded to their literal characters. This is
// the step that turns the `&#39;` the fix relies on back into a real `'`
// before the attribute text becomes the inline handler's JS source.
function decodeHtmlAttributeEntities(s) {
  return s
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&amp;/g, '&');
}

// Extracts every onclick="..." attribute's raw (still HTML-entity-encoded)
// text from a chunk of generated HTML.
function extractOnclickAttrs(html) {
  const attrs = [];
  const re = /onclick="([^"]*)"/g;
  let m;
  while ((m = re.exec(html))) attrs.push(m[1]);
  return attrs;
}

// Simulates the browser compiling+running an inline onclick handler: decode
// HTML entities in the attribute text, then compile that decoded text as a
// function body. A name with an unescaped apostrophe makes this throw a
// SyntaxError -- exactly the bug this test guards against.
function runOnclickAsHandler(rawAttrText, stubRetailSystem) {
  const decoded = decodeHtmlAttributeEntities(rawAttrText);
  const fn = new Function('RetailSystem', 't', decoded); // eslint-disable-line no-new-func
  fn(stubRetailSystem, (s) => s);
  return decoded;
}

async function testSupplierRowButtonsSurviveApostropheInName() {
  const { RetailSystem, supTbodyEl } = loadRetailSystem();
  await RetailSystem._loadSuppliers();

  const onclicks = extractOnclickAttrs(supTbodyEl.innerHTML);
  assert.strictEqual(onclicks.length, 3, `expected 3 onclick attrs (Edit/Delete/+PO), found ${onclicks.length}`);

  const calls = { edit: null, del: null, po: null };
  const stub = {
    _openEditSupplier: (...args) => { calls.edit = args; },
    _deleteSupplier: (...args) => { calls.del = args; },
    _openCreatePO: (...args) => { calls.po = args; },
  };

  for (const attrText of onclicks) {
    assert.doesNotThrow(
      () => runOnclickAsHandler(attrText, stub),
      (err) => {
        throw new Error(
          `Supplier row onclick handler is not valid JS once the browser decodes its ` +
          `HTML entities -- the apostrophe in "${SUPPLIER_NAME}" broke out of the JS ` +
          `string literal. Raw attr: ${attrText}\nDecoded: ${decodeHtmlAttributeEntities(attrText)}\n${err}`
        );
      }
    );
  }

  assert.deepStrictEqual(calls.edit, ['s1', SUPPLIER_NAME, '', '', ''], '_openEditSupplier did not receive the original name back');
  assert.deepStrictEqual(calls.del, ['s1', SUPPLIER_NAME], '_deleteSupplier did not receive the original name back');
  assert.deepStrictEqual(calls.po, ['s1', SUPPLIER_NAME], '_openCreatePO did not receive the original name back');

  console.log('PASS: Supplier Edit/Delete/+PO onclick handlers survive an apostrophe in the name');
}

function testSupplierContactDeleteButtonSurvivesApostropheInName() {
  const { RetailSystem, contactsListEl } = loadRetailSystem();
  RetailSystem._supplierContacts = [
    { id: 'c1', name: CONTACT_NAME, role: 'orders', channel_preference: 'phone', phone: '', email: '', whatsapp: '', is_primary: false, status: 'active' },
  ];
  RetailSystem._renderSupplierContactsList('s1');

  const onclicks = extractOnclickAttrs(contactsListEl.innerHTML);
  // Edit contact (2 args) + Delete contact (3 args, the one this bug affects).
  assert.strictEqual(onclicks.length, 2, `expected 2 onclick attrs (Edit/Delete), found ${onclicks.length}`);

  let deleteArgs = null;
  const stub = { _deleteSupplierContact: (...args) => { deleteArgs = args; }, _openEditSupplierContact() {} };

  for (const attrText of onclicks) {
    assert.doesNotThrow(
      () => runOnclickAsHandler(attrText, stub),
      (err) => {
        throw new Error(
          `Supplier-contact onclick handler is not valid JS once the browser decodes ` +
          `its HTML entities -- the apostrophe in "${CONTACT_NAME}" broke out of the JS ` +
          `string literal. Raw attr: ${attrText}\nDecoded: ${decodeHtmlAttributeEntities(attrText)}\n${err}`
        );
      }
    );
  }

  assert.deepStrictEqual(deleteArgs, ['s1', 'c1', CONTACT_NAME], '_deleteSupplierContact did not receive the original name back');

  console.log('PASS: Supplier-contact Delete onclick handler survives an apostrophe in the name');
}

async function main() {
  await testSupplierRowButtonsSurviveApostropheInName();
  testSupplierContactDeleteButtonSurvivesApostropheInName();
  console.log('PASS: retail_supplier_name_apostrophe_onclick_test.js');
}

main().catch((err) => {
  console.error('FAIL: retail_supplier_name_apostrophe_onclick_test.js');
  console.error(err);
  process.exitCode = 1;
});
