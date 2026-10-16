/**
 * TAXINC: the quotation view/print must not put a TAXABLE line total above an INCLUSIVE total.
 * Loads the REAL subsystem-retail.js, drives the real _viewQuotation with a stubbed _get and
 * reads the HTML it builds.
 * Run: node products/retail/tests/retail_taxinc_quotation_test.js
 */
'use strict';
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const FILE = path.join(__dirname, '..', 'frontend', 'subsystem-retail.js');

function stub(extra) {
  return Object.assign({
    innerHTML: '', textContent: '', value: '', style: {}, dataset: {}, id: '',
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    appendChild() {}, getAttribute() { return null; }, setAttribute() {}, remove() {},
    querySelector() { return null; }, querySelectorAll() { return []; }, addEventListener() {}, focus() {},
  }, extra);
}

async function viewHtml(quotation, lines) {
  let built = null;
  const sandbox = {
    console: { error() {}, warn() {}, log() {} }, t: (s) => s, setTimeout, clearTimeout, setInterval, clearInterval,
    fetch: () => Promise.reject(new Error('no network')),
    getComputedStyle: () => ({ getPropertyValue: () => '' }),
    navigator: { userAgent: 'x' }, localStorage: { getItem: () => null, setItem() {} },
    SubsystemApp: { showToast() {}, hasCapability: () => true },
    document: {
      activeElement: null, getElementById: () => stub(), querySelector: () => stub(), querySelectorAll: () => [],
      createElement() { const e = stub(); built = e; return e; },
      head: { appendChild() {} }, body: { appendChild() {} },
      documentElement: { getAttribute: () => 'light', style: { setProperty() {} } },
      addEventListener() {}, removeEventListener() {},
    },
  };
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(FILE, 'utf8'), sandbox, { filename: FILE });
  const RS = sandbox.RetailSystem;
  RS._currencyDecimals = 3; RS._currencySymbol = 'JD';
  RS._get = async () => ({ data: { quotation, lines } });
  await RS._viewQuotation(1);
  for (let i = 0; i < 6; i++) await Promise.resolve();
  assert.ok(built && built.innerHTML, 'the view built no HTML');
  return built.innerHTML;
}

const LINE = { product_name_snapshot: 'Shelf Item', quantity: 3, unit_price: 0.25, discount_pct: 0, line_total: 0.647 };

(async () => {
  const inc = await viewHtml({ doc_number: 'Q-1', tax_mode: 'inclusive', subtotal: 0.75, discount_amount: 0,
    tax_amount: 0.103, total: 0.75, status: 'draft' }, [LINE]);
  assert.ok(inc.includes('Tax (included)'), 'inclusive quote must say the tax is included');
  assert.ok(inc.includes('0.750'), 'inclusive line must show what the customer pays (0.750)');
  assert.ok(!inc.includes('0.647'), 'the taxable 0.647 must not appear as the line total');

  const exc = await viewHtml({ doc_number: 'Q-2', tax_mode: 'after_discount', subtotal: 0.647, discount_amount: 0,
    tax_amount: 0.103, total: 0.75, status: 'draft' }, [LINE]);
  assert.ok(!exc.includes('Tax (included)'), 'an exclusive quote must not claim the tax is included');
  assert.ok(exc.includes('0.647'), 'exclusive quote keeps its line total exactly as before');
  console.log('PASS: retail_taxinc_quotation_test (inclusive and exclusive quote views)');
})().catch((e) => { console.error(e); process.exit(1); });
