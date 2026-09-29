/**
 * retail_p1a_till_landing_test.js -- FINAL polish F12.
 *
 * `_renderCashierLanding` used to print "Sales totals and reports are limited
 * to managers and the store owner..." at a working cashier whenever she landed
 * on the dashboard by any route except the shell's own first-screen choice
 * (a stale #dashboard hash, _navigate('dashboard'), the 60 s refresh tick).
 * A person who can SELL must never be shown a refusal: she is handed to the
 * till through the shell's own _navigate('pos') instead.
 *
 * Both directions are asserted, because the deny half is the obvious one:
 *   ALLOW  retail.sell, no retail.reports -> no refusal sentence, and the
 *          shell is asked for the till (once).
 *   KEEP   neither sell nor reports       -> the honest panel is unchanged and
 *          NO navigation happens (there is no till for them to be sent to).
 *   KEEP   retail.reports (manager)       -> the real dashboard, no landing,
 *          no navigation.
 *   GUARD  already on the till            -> no navigation (no loop).
 *
 * Run: node products/retail/tests/retail_p1a_till_landing_test.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const SRC = fs.readFileSync(path.join(__dirname, '..', 'frontend', 'subsystem-retail.js'), 'utf8');
const REFUSAL = 'limited to managers and the store owner';

function stubEl() {
  return {
    innerHTML: '', textContent: '', value: '', style: {}, dataset: {},
    appendChild() {}, getAttribute() { return null; }, setAttribute() {},
    querySelector() { return stubEl(); }, querySelectorAll() { return []; },
    getContext() { return {}; }, addEventListener() {},
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    parentElement: { innerHTML: '', style: {}, appendChild() {} },
  };
}

function load(caps, currentSection) {
  const navigations = [];
  const fetches = [];
  const sandbox = {
    console: { error() {}, warn() {}, log() {} },
    t: (s) => s, setTimeout, clearTimeout,
    fetch: (u) => { fetches.push(u); return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ status: 'success', data: {} }) }); },
    getComputedStyle: () => ({ getPropertyValue: () => '' }),
    SubsystemApp: {
      active: 'retail', currentSection: currentSection || 'dashboard', showToast() {},
      _navigate(id) { navigations.push(id); },
      hasCapability(code) { return caps.includes(code); },
    },
    document: {
      getElementById() { return stubEl(); }, createElement() { return stubEl(); },
      querySelector() { return stubEl(); }, querySelectorAll() { return []; },
      head: { appendChild() {} }, body: { appendChild() {} },
      documentElement: { getAttribute() { return null; } }, addEventListener() {},
    },
  };
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(SRC, sandbox, { filename: 'subsystem-retail.js' });
  return { RS: sandbox.RetailSystem, navigations, fetches };
}

const cases = [];
function test(name, fn) { cases.push([name, fn]); }

test('a cashier (sell, no reports) is handed to the till and is never shown the refusal', () => {
  const { RS, navigations } = load(['retail.sell', 'retail.refund', 'retail.cash.close']);
  const c = stubEl();
  RS._renderDashboard(c);
  assert.ok(!c.innerHTML.includes(REFUSAL), 'a person who can sell was shown the refusal sentence');
  assert.ok(c.innerHTML.length > 200, 'positive control: the panel must still render something (fallback for shell-less hosts)');
  assert.deepStrictEqual(navigations, ['pos'], 'the shell was not asked for the till exactly once');
});

test('someone who can neither sell nor see reports keeps the honest panel and is NOT navigated', () => {
  const { RS, navigations } = load(['retail.cash.close']);
  const c = stubEl();
  RS._renderDashboard(c);
  assert.ok(c.innerHTML.includes(REFUSAL), 'the refusal for a person with no till was lost');
  assert.deepStrictEqual(navigations, [], 'a person with no sell grant was sent to the till');
});

test('a manager (reports) gets the real dashboard: no landing, no navigation', () => {
  const { RS, navigations } = load(['retail.sell', 'retail.reports']);
  const c = stubEl();
  RS._renderDashboard(c);
  assert.ok(!c.innerHTML.includes(REFUSAL));
  assert.ok(!c.innerHTML.includes('Ready to sell'), 'a manager was shown the cashier landing');
  assert.deepStrictEqual(navigations, [], 'a manager was navigated away from the dashboard');
});

test('already on the till: the landing does not navigate again (no loop)', () => {
  const { RS, navigations } = load(['retail.sell'], 'pos');
  RS._renderCashierLanding(stubEl());
  assert.deepStrictEqual(navigations, []);
});

let failed = 0;
for (const [name, fn] of cases) {
  try { fn(); console.log(`PASS: ${name}`); }
  catch (err) { failed += 1; console.error(`FAIL: ${name}\n      ${err && err.stack || err}`); }
}
console.log(`\n=== ${cases.length - failed} passed, ${failed} failed, ${cases.length} total ===`);
process.exit(failed ? 1 : 0);
