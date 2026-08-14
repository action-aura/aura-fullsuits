/**
 * Regression test for a WCAG AA contrast bug in the Retail dashboard's
 * "Recent Transactions" table.
 *
 * Bug: RetailSystem._renderDashboard() (products/retail/frontend/subsystem-retail.js)
 * set `style="color:var(--text-muted)"` inline on the table's loading state,
 * empty state, Items column, and Time column. `--text-muted` (css/main.css)
 * is a legacy, un-themed token -- it is defined once at :root (#9aa0a6) and,
 * unlike --text-dim/--text-faint, is never redefined inside the
 * `html[data-theme="light"]` block. Since index.html defaults a fresh install
 * to the light theme, and the light theme's --surface-card is pure white,
 * every user saw ~2.64:1 gray-on-white text in these cells out of the box --
 * well under the 4.5:1 WCAG AA minimum for normal text. A non-!important
 * stylesheet rule (`.sub-chart-card * { color:inherit }`, main.css) cannot
 * fix this either, since inline styles always beat non-!important external
 * rules.
 *
 * Fix: swap `var(--text-muted)` for the theme-aware `var(--text-dim)` token
 * in these four spots, matching how this same render already uses themed
 * tokens (--text-dim / --text-faint) elsewhere instead of the legacy
 * --text-muted. --text-dim resolves to #51607a in the light theme, which is
 * ~6.4:1 against a white card -- comfortably above the 4.5:1 AA floor (and
 * still readable against the dark theme's near-black card).
 *
 * This test loads the REAL products/retail/frontend/subsystem-retail.js (via
 * Node's vm module, not a reimplementation) into a minimal sandboxed DOM/fetch,
 * and asserts none of the Recent Transactions markup (loading state, empty
 * state, or a populated row's Items/Time cells) references the un-themed
 * --text-muted token.
 *
 * No test framework is configured for this vanilla-JS, build-step-free frontend
 * (see CLAUDE.md), so this runs standalone with only Node built-ins:
 *
 *   node products/retail/tests/retail_dashboard_transaction_contrast_test.js
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

function loadRetailSystem({ fetchImpl, tbodyStub }) {
  const code = fs.readFileSync(FRONTEND_FILE, 'utf8');

  const sandbox = {
    console,
    t: (s) => s, // stand-in for i18n.js's global `t()` shorthand
    fetch: fetchImpl,
    getComputedStyle: () => ({ getPropertyValue: () => '' }),
    document: {
      // Returning a truthy stub for 'ret-styles' makes _injectStyles() take
      // its early-return path (style already injected) -- irrelevant here.
      getElementById() { return makeElementStub(); },
      createElement() { return makeElementStub(); },
      querySelector(sel) {
        if (sel === '#r-dash-recent tbody') return tbodyStub;
        return makeElementStub();
      },
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

async function testPopulatedTableAvoidsTextMuted() {
  const statsPayload = {
    today_sales: 100, today_transactions: 2, month_sales: 500, low_stock_alerts: 0,
    total_customers: 5, total_products: 10, today_returns: 0, sales_change_pct: 0,
    hourly_labels: [], hourly_data: [], payment_methods: {},
    recent_sales: [
      { id: 1, sale_number: 'S-1', customer_name: 'Walk-in', item_count: 3,
        payment_method: 'cash', total: 42.5, created_at: '2026-08-14T09:05:00' },
    ],
  };
  const fetchImpl = () => Promise.resolve({ ok: true, json: () => Promise.resolve({ data: statsPayload }) });

  const tbodyStub = makeElementStub();
  const RetailSystem = loadRetailSystem({ fetchImpl, tbodyStub });

  const contentEl = makeElementStub();
  await RetailSystem._renderDashboard(contentEl);

  assert.ok(
    !/var\(--text-muted\)/.test(tbodyStub.innerHTML),
    'Recent Transactions row markup still references the un-themed --text-muted token ' +
    '(Items/Time columns) -- this renders at ~2.64:1 contrast on the light theme\'s white ' +
    'card, below the 4.5:1 WCAG AA floor. Got: ' + tbodyStub.innerHTML
  );
  assert.ok(
    /var\(--text-dim\)/.test(tbodyStub.innerHTML),
    'Expected the Items/Time columns to use the theme-aware --text-dim token. Got: ' + tbodyStub.innerHTML
  );

  console.log('PASS: populated Recent Transactions row avoids --text-muted');
}

async function testEmptyStateAvoidsTextMuted() {
  const statsPayload = {
    today_sales: 0, today_transactions: 0, month_sales: 0, low_stock_alerts: 0,
    total_customers: 0, total_products: 0, today_returns: 0, sales_change_pct: 0,
    hourly_labels: [], hourly_data: [], payment_methods: {},
    recent_sales: [],
  };
  const fetchImpl = () => Promise.resolve({ ok: true, json: () => Promise.resolve({ data: statsPayload }) });

  const tbodyStub = makeElementStub();
  const RetailSystem = loadRetailSystem({ fetchImpl, tbodyStub });

  const contentEl = makeElementStub();
  await RetailSystem._renderDashboard(contentEl);

  assert.ok(
    !/var\(--text-muted\)/.test(tbodyStub.innerHTML),
    '"No transactions yet today" empty state still references --text-muted. Got: ' + tbodyStub.innerHTML
  );
  assert.ok(
    /var\(--text-dim\)/.test(tbodyStub.innerHTML),
    'Expected the empty state to use the theme-aware --text-dim token. Got: ' + tbodyStub.innerHTML
  );

  console.log('PASS: empty Recent Transactions state avoids --text-muted');
}

async function testInitialLoadingStateAvoidsTextMuted() {
  // Fetch never has to resolve for this one -- the "Loading…" row is part of
  // the template written to c.innerHTML synchronously, before the await.
  const fetchImpl = () => new Promise(() => {}); // never resolves
  const tbodyStub = makeElementStub();
  const RetailSystem = loadRetailSystem({ fetchImpl, tbodyStub });

  const contentEl = makeElementStub();
  RetailSystem._renderDashboard(contentEl); // do not await -- inspect synchronous initial markup

  assert.ok(
    /id="r-dash-recent"/.test(contentEl.innerHTML),
    'Expected the dashboard template to include the Recent Transactions table.'
  );
  assert.ok(
    !/var\(--text-muted\)/.test(contentEl.innerHTML),
    'Initial "Loading…" row markup still references --text-muted. Got snippet around Loading…: ' +
    (contentEl.innerHTML.match(/.{0,80}Loading….{0,20}/) || [''])[0]
  );
  assert.ok(
    /var\(--text-dim\)[^"]*"[^>]*>Loading…/.test(contentEl.innerHTML),
    'Expected the initial "Loading…" row to use the theme-aware --text-dim token.'
  );

  console.log('PASS: initial Loading… row avoids --text-muted');
}

async function main() {
  await testPopulatedTableAvoidsTextMuted();
  await testEmptyStateAvoidsTextMuted();
  await testInitialLoadingStateAvoidsTextMuted();
  console.log('PASS: retail_dashboard_transaction_contrast_test.js');
}

main().catch((err) => {
  console.error('FAIL: retail_dashboard_transaction_contrast_test.js');
  console.error(err);
  process.exitCode = 1;
});
