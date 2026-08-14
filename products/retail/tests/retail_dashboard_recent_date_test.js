/**
 * Regression test for a date-ambiguity bug in the Retail dashboard's
 * "Recent Transactions" table.
 *
 * Bug: the `recent` query in products/retail/backend/api/retail_api.py
 * (RetailDashboardStats) has no date filter -- it's just
 * `ORDER BY s.created_at DESC LIMIT 8`, scoped only by company_id. On any
 * day with fewer than 8 sales so far, the remaining rows are genuinely from
 * earlier days. RetailSystem._renderDashboard() (subsystem-retail.js) used
 * to render only `(s.created_at||'').slice(11,16)` -- i.e. just "HH:MM" --
 * under a plain "Time" column header, with no date. A cashier or owner
 * glancing at the dashboard mid-morning, before 8 sales have happened
 * today, would see an older transaction time-stamped like "18:42" with
 * nothing indicating it's from yesterday, not today -- easy to mistake for
 * today's activity.
 *
 * Fix: render the full "YYYY-MM-DD HH:MM" (`.slice(0,16)`) in that column,
 * matching the "Date" column convention already used by the Sales History
 * table (_loadSalesHistory) and the customer Purchase History table
 * (_showCustomerDetail) elsewhere in this same file -- both of which can
 * also span multiple days and already show the full date+time.
 *
 * This test loads the REAL products/retail/frontend/subsystem-retail.js
 * (via Node's vm module, not a reimplementation) into a minimal sandboxed
 * DOM/fetch, feeds it a `recent_sales` row dated two days before "today",
 * and asserts the rendered cell shows a full date (not just a bare HH:MM
 * that would be indistinguishable from a same-time entry made today).
 *
 * No test framework is configured for this vanilla-JS, build-step-free
 * frontend (see CLAUDE.md), so this runs standalone with only Node
 * built-ins:
 *
 *   node products/retail/tests/retail_dashboard_recent_date_test.js
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
  sandbox.window = sandbox;

  vm.createContext(sandbox);
  vm.runInContext(code, sandbox, { filename: FRONTEND_FILE });

  assert.ok(sandbox.RetailSystem, 'subsystem-retail.js did not expose window.RetailSystem');
  return sandbox.RetailSystem;
}

async function testOlderRecentSaleShowsItsDate() {
  // Simulate the real bug condition: the backend's `recent` query has no
  // date filter, so a slow morning (only 1 sale so far today) surfaces a
  // sale from two days ago at the SAME clock time as today's, in the same
  // 8-row list.
  const staleCreatedAt = '2026-08-12T18:42:00'; // "two days before today" for this test
  const statsPayload = {
    today_sales: 42.5, today_transactions: 1, month_sales: 500, low_stock_alerts: 0,
    total_customers: 5, total_products: 10, today_returns: 0, sales_change_pct: 0,
    hourly_labels: [], hourly_data: [], payment_methods: {},
    recent_sales: [
      { id: 1, sale_number: 'S-1', customer_name: 'Walk-in', item_count: 3,
        payment_method: 'cash', total: 42.5, created_at: staleCreatedAt },
    ],
  };
  const fetchImpl = () => Promise.resolve({ ok: true, json: () => Promise.resolve({ data: statsPayload }) });

  const tbodyStub = makeElementStub();
  const RetailSystem = loadRetailSystem({ fetchImpl, tbodyStub });

  const contentEl = makeElementStub();
  await RetailSystem._renderDashboard(contentEl);

  // The bare "HH:MM" the old code rendered ("18:42") gives no signal this
  // row is from a different day than today -- assert the full date portion
  // ("2026-08-12") made it into the cell, not just the time.
  assert.ok(
    tbodyStub.innerHTML.includes('2026-08-12'),
    'Recent Transactions row for an older sale does not show its date -- only the ' +
    'time is rendered, so an old sale is indistinguishable from a sale made today ' +
    'at the same clock time. Got: ' + tbodyStub.innerHTML
  );
  assert.ok(
    tbodyStub.innerHTML.includes('18:42'),
    'Expected the row to still include the time. Got: ' + tbodyStub.innerHTML
  );

  console.log('PASS: an older Recent Transactions row renders its full date, not just HH:MM');
}

async function main() {
  await testOlderRecentSaleShowsItsDate();
  console.log('PASS: retail_dashboard_recent_date_test.js');
}

main().catch((err) => {
  console.error('FAIL: retail_dashboard_recent_date_test.js');
  console.error(err);
  process.exitCode = 1;
});
