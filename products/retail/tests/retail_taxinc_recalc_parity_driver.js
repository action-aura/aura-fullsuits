/**
 * Driver for retail_taxinc_recalc_parity_test.py -- NOT a test itself (named _driver.js so the
 * `*_test.js` sweep does not pick it up).
 *
 * Reads scenarios on stdin, loads the REAL products/retail/frontend/subsystem-retail.js through
 * Node's vm, drives its REAL `_recalc()` (and, for a cart discount, the real `_cartDiscPlan`
 * through `_applyCartDiscToRecalc`) and returns `_currentTotals` for each scenario. The Python
 * side computes the same carts through core/retail/pricing.calculate_line (+ cart_discount) and
 * compares, to the fil.
 *
 * Scenario: { mode, currency_decimals, lines: [{price, qty, rate}], disc_pct,
 *             cart_disc: null | {mode: 'amount'|'target', value} }
 */
'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const FRONTEND_FILE = path.join(__dirname, '..', 'frontend', 'subsystem-retail.js');

function stub(extra) {
  return Object.assign({
    innerHTML: '', textContent: '', value: '', disabled: false, style: {}, dataset: {}, options: [],
    classList: { toggle() {}, add() {}, remove() {}, contains() { return false; } },
    appendChild() {}, getAttribute() { return null; }, setAttribute() {}, remove() {},
    querySelector() { return null; }, querySelectorAll() { return []; }, addEventListener() {},
    focus() {}, closest() { return null; },
  }, extra);
}

function loadRetailSystem() {
  const els = Object.create(null);
  const sandbox = {
    console: { error() {}, warn() {}, log() {} },
    t: (s) => s,
    setTimeout, clearTimeout, setInterval, clearInterval,
    fetch: () => Promise.reject(new Error('no network')),
    getComputedStyle: () => ({ getPropertyValue: () => '' }),
    navigator: { userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)' },
    localStorage: { getItem: () => null, setItem() {} },
    SubsystemApp: { showToast() {}, role: 'admin', hasCapability: () => true },
    document: {
      activeElement: null,
      getElementById(id) { return els[id] || (els[id] = stub({ id })); },
      createElement() { return stub(); },
      querySelector() { return stub(); },
      querySelectorAll() { return []; },
      head: { appendChild() {} }, body: { appendChild() {} },
      documentElement: { getAttribute() { return 'light'; }, style: { setProperty() {} } },
      addEventListener() {}, removeEventListener() {},
    },
  };
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(FRONTEND_FILE, 'utf8'), sandbox, { filename: FRONTEND_FILE });
  if (!sandbox.RetailSystem) throw new Error('subsystem-retail.js did not expose window.RetailSystem');
  return { RS: sandbox.RetailSystem, els };
}

let raw = '';
process.stdin.on('data', (c) => { raw += c; });
process.stdin.on('end', () => {
  const scenarios = JSON.parse(raw);
  const { RS, els } = loadRetailSystem();
  const out = scenarios.map((s) => {
    RS._currencyDecimals = s.currency_decimals == null ? 3 : s.currency_decimals;
    RS._currencySymbol = 'JD';
    RS._taxMode = s.mode;
    RS._promotions = [];
    RS._cartDisc = null;
    RS._cart = s.lines.map((l, k) => ({
      product_id: 'p' + k, name: 'P' + k, quantity: l.qty, unit_price: l.price, tax_rate: l.rate,
      line_total: l.price * l.qty, max_stock: 1e9, category_id: null,
    }));
    const disc = els['pos-disc'] || (els['pos-disc'] = stub({ id: 'pos-disc' }));
    disc.value = String(s.disc_pct || 0);
    disc.disabled = false;
    if (s.cart_disc) {
      RS._cartDisc = { mode: s.cart_disc.mode, value: s.cart_disc.value, sig: RS._cartDiscSig() };
    }
    RS._recalc();
    const c = RS._currentTotals;
    return { subtotal: c.subtotal, discount: c.discount, tax: c.tax, total: c.total,
             cart_disc_cleared: !!(s.cart_disc && !RS._cartDisc) };
  });
  process.stdout.write(JSON.stringify(out));
});
