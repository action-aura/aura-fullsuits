/**
 * Driver for retail_pricing_parity_test.py — NOT a test itself.
 *
 * Named `_driver.js` rather than `_test.js` on purpose: the JS sweep runs
 * `products/retail/tests/*_test.js`, and this file is a subprocess helper,
 * not a suite.
 *
 * Reads a JSON array of scenarios on stdin, loads the REAL
 * products/retail/frontend/subsystem-retail.js through Node's vm, and for
 * each scenario returns the effective per-line discount percentage the
 * BROWSER would apply. The Python side computes the same thing through
 * core/retail/promotions.resolve_line_discount_pct and compares.
 *
 * Scenario shape:
 *   { promotions: [...], item: {product_id, parent_product_id, category_id},
 *     manual_pct: Number }
 *
 * Output: [{ effective_pct: Number, matched: String|null }, ...]
 */
'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const FRONTEND_FILE = path.join(__dirname, '..', 'frontend', 'subsystem-retail.js');

function stub() {
  const el = {
    innerHTML: '', textContent: '', value: '', disabled: false, style: {},
    options: [], classList: { toggle() {}, add() {}, remove() {} },
    appendChild() {}, getAttribute() { return null; }, setAttribute() {},
    querySelectorAll() { return []; }, addEventListener() {}, focus() {},
  };
  return el;
}

function loadRetailSystem() {
  const sandbox = {
    console: { error() {}, warn() {}, log() {} },
    t: (s) => s,
    setTimeout, clearTimeout,
    fetch: () => Promise.resolve({
      ok: true, status: 200,
      json: () => Promise.resolve({ status: 'success', data: [] }),
    }),
    getComputedStyle: () => ({ getPropertyValue: () => '' }),
    SubsystemApp: { showToast() {}, role: 'admin', hasCapability: () => true },
    document: {
      getElementById() { return stub(); },
      createElement() { return stub(); },
      querySelector() { return stub(); },
      querySelectorAll() { return []; },
      head: { appendChild() {} }, body: { appendChild() {} },
      documentElement: { getAttribute() { return null; } },
      addEventListener() {},
    },
  };
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(FRONTEND_FILE, 'utf8'), sandbox,
                  { filename: FRONTEND_FILE });
  if (!sandbox.RetailSystem) {
    throw new Error('subsystem-retail.js did not expose window.RetailSystem');
  }
  return sandbox.RetailSystem;
}

let raw = '';
process.stdin.on('data', (c) => { raw += c; });
process.stdin.on('end', () => {
  const scenarios = JSON.parse(raw);
  const RetailSystem = loadRetailSystem();
  const out = scenarios.map((s) => {
    RetailSystem._promotions = s.promotions || [];
    const promo = RetailSystem._bestPromoFor(s.item);

    // Exactly the rule _recalc() applies per line:
    //     const lineFrac = Math.max(manualFrac, promoFrac)
    // expressed as a percentage so the two languages compare like for like.
    const promoPct = promo ? (promo.discount_pct || 0) : 0;
    const manualPct = s.manual_pct || 0;
    return {
      effective_pct: Math.max(promoPct, manualPct),
      matched: promo ? String(promo.id) : null,
    };
  });
  process.stdout.write(JSON.stringify(out));
});
