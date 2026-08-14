/**
 * Regression test for the Retail dashboard "silent failure + fake LIVE badge" bug.
 *
 * Bug: RetailSystem._renderDashboard() (products/retail/frontend/subsystem-retail.js)
 * wrapped its fetch + DOM-population block in a try/catch that only did
 * `console.error(...)` on failure and never rethrew. Because _renderDashboard is
 * async, that meant a failed GET /api/sub/retail/dashboard/stats resolved the
 * function's promise successfully instead of rejecting it. app-shell.js's
 * _navigate() awaits renderer.render(sectionId) and, seeing no rejection, went on
 * to call this._updateLiveBadge(true) -- showing a green "● LIVE" badge over a
 * dashboard whose KPI tiles were still frozen on their "—" placeholders and whose
 * recent-transactions table was stuck on "Loading…". The 60s auto-refresh timer
 * would then repeat this silently forever.
 *
 * Fix: the catch block now rethrows after logging, so the promise returned by
 * _renderDashboard (and therefore by RetailSystem.render()) rejects on a failed
 * fetch. That lets _navigate's own try/catch do what it already does for every
 * other section: turn the LIVE badge off and show the "Failed to load" + Retry
 * panel instead of a false "everything is fine" indicator.
 *
 * This test loads the REAL products/retail/frontend/subsystem-retail.js (via
 * Node's vm module, not a reimplementation) into a minimal sandboxed DOM/fetch,
 * forces the dashboard-stats fetch to fail, and asserts _renderDashboard's
 * returned promise rejects -- which is exactly the signal app-shell.js relies on
 * to avoid showing a fake LIVE badge.
 *
 * No test framework is configured for this vanilla-JS, build-step-free frontend
 * (see CLAUDE.md), so this runs standalone with only Node built-ins:
 *
 *   node products/retail/tests/retail_dashboard_error_propagation_test.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const FRONTEND_FILE = path.join(__dirname, '..', 'frontend', 'subsystem-retail.js');

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

function loadRetailSystem({ fetchImpl }) {
  const code = fs.readFileSync(FRONTEND_FILE, 'utf8');

  const sandbox = {
    console,
    t: (s) => s, // stand-in for i18n.js's global `t()` shorthand
    fetch: fetchImpl,
    getComputedStyle: () => ({ getPropertyValue: () => '' }),
    document: {
      // Returning a truthy stub for 'ret-styles' makes _injectStyles() take
      // its early-return path (style already injected) -- irrelevant to the
      // bug under test.
      getElementById() { return makeElementStub(); },
      createElement() { return makeElementStub(); },
      querySelector() { return makeElementStub(); },
      head: { appendChild() {} },
      documentElement: { getAttribute() { return null; } },
    },
  };
  sandbox.window = sandbox; // enough for the `window.Chart` / `window.RetailSystem` refs used here

  vm.createContext(sandbox);
  vm.runInContext(code, sandbox, { filename: FRONTEND_FILE });

  assert.ok(sandbox.RetailSystem, 'subsystem-retail.js did not expose window.RetailSystem');
  return sandbox.RetailSystem;
}

async function main() {
  const failingFetch = () => Promise.reject(new Error('simulated dashboard-stats fetch failure'));
  const RetailSystem = loadRetailSystem({ fetchImpl: failingFetch });

  const contentEl = makeElementStub();
  let rejected = false;
  let rejectionReason = null;
  try {
    await RetailSystem._renderDashboard(contentEl);
  } catch (e) {
    rejected = true;
    rejectionReason = e;
  }

  assert.strictEqual(
    rejected,
    true,
    'RetailSystem._renderDashboard() must reject when the dashboard-stats fetch fails, ' +
    'so app-shell.js\'s _navigate() sees the failure and skips lighting the "LIVE" badge. ' +
    'It resolved instead -- the fetch error was swallowed silently.'
  );
  assert.ok(
    rejectionReason && /simulated dashboard-stats fetch failure/.test(rejectionReason.message || ''),
    'Expected the original fetch error to propagate, got: ' + rejectionReason
  );

  console.log('PASS: retail_dashboard_error_propagation_test.js');
}

main().catch((err) => {
  console.error('FAIL: retail_dashboard_error_propagation_test.js');
  console.error(err);
  process.exitCode = 1;
});
