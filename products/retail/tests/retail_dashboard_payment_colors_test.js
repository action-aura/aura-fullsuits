/**
 * Regression test for the Retail dashboard "Payment Methods" donut chart
 * running out of colors on a day that uses every payment method.
 *
 * Bug: RetailSystem._renderDashboard() (products/retail/frontend/subsystem-retail.js)
 * built the donut's `backgroundColor` array with only 5 hex colors, but the
 * POS checkout screen (pos-pay-btns buttons, same file) offers 6 distinct
 * payment methods: cash, card, mobile, transfer, credit, voucher. The
 * backend's dashboard/stats endpoint groups today's sales by payment_method
 * with no cap (GROUP BY payment_method, retail_api.py), so `pmLabels` /
 * `pmData` can legitimately have 6 entries. Chart.js indexes into
 * `backgroundColor` by slice position, so on any day where all 6 methods
 * were used, the 6th slice had no color at that index and silently fell
 * back to Chart.js's undefined-color default instead of a deliberate,
 * distinguishable color -- breaking the color language used everywhere
 * else on this screen.
 *
 * Fix: extended the backgroundColor array to 6 entries so every payment
 * method the POS supports gets a real, distinguishable color.
 *
 * This test loads the REAL products/retail/frontend/subsystem-retail.js (via
 * Node's vm module, not a reimplementation) into a minimal sandboxed DOM/fetch
 * with a stub Chart.js that records the config it was constructed with, feeds
 * a dashboard-stats payload with all 6 payment methods present, and asserts
 * the donut's backgroundColor array has at least as many colors as there are
 * payment-method slices -- and that every color is a real, non-empty value
 * (not undefined).
 *
 * No test framework is configured for this vanilla-JS, build-step-free frontend
 * (see CLAUDE.md), so this runs standalone with only Node built-ins:
 *
 *   node products/retail/tests/retail_dashboard_payment_colors_test.js
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
    getContext() { return {}; },
    parentElement: null,
  };
}

function loadRetailSystem({ fetchImpl, chartConfigs }) {
  const code = fs.readFileSync(FRONTEND_FILE, 'utf8');

  // Minimal stand-in for Chart.js that just records every config it was
  // constructed with, so the test can inspect the donut's backgroundColor
  // array without needing a real canvas/rendering environment.
  function ChartStub(ctx, config) {
    chartConfigs.push(config);
  }

  const payDonutEl = makeElementStub();
  payDonutEl.parentElement = makeElementStub();
  const hourlyEl = makeElementStub();
  hourlyEl.parentElement = makeElementStub();

  const sandbox = {
    console,
    t: (s) => s, // stand-in for i18n.js's global `t()` shorthand
    fetch: fetchImpl,
    getComputedStyle: () => ({ getPropertyValue: () => '' }),
    document: {
      // Returning a truthy stub for 'ret-styles' makes _injectStyles() take
      // its early-return path (style already injected) -- irrelevant here.
      getElementById(id) {
        if (id === 'r-dash-pay') return payDonutEl;
        if (id === 'r-dash-hourly') return hourlyEl;
        return makeElementStub();
      },
      createElement() { return makeElementStub(); },
      querySelector() { return makeElementStub(); },
      head: { appendChild() {} },
      documentElement: { getAttribute() { return null; } },
    },
  };
  sandbox.window = sandbox; // enough for the `window.Chart` / `window.RetailSystem` refs used here
  sandbox.window.Chart = ChartStub;

  vm.createContext(sandbox);
  vm.runInContext(code, sandbox, { filename: FRONTEND_FILE });

  assert.ok(sandbox.RetailSystem, 'subsystem-retail.js did not expose window.RetailSystem');
  return sandbox.RetailSystem;
}

async function testAllSixPaymentMethodsGetDistinctColors() {
  // Every payment method the POS checkout screen actually offers
  // (pos-pay-btns: cash, card, mobile, transfer, credit, voucher).
  const paymentMethods = {
    cash:     { count: 3, revenue: 120.5 },
    card:     { count: 2, revenue: 80.0 },
    mobile:   { count: 1, revenue: 40.0 },
    transfer: { count: 1, revenue: 25.0 },
    credit:   { count: 1, revenue: 15.0 },
    voucher:  { count: 1, revenue: 10.0 },
  };
  const statsPayload = {
    today_sales: 290.5, today_transactions: 9, month_sales: 1000, low_stock_alerts: 0,
    total_customers: 5, total_products: 10, today_returns: 0, sales_change_pct: 0,
    hourly_labels: [], hourly_data: [],
    payment_methods: paymentMethods,
    recent_sales: [],
  };
  const fetchImpl = () => Promise.resolve({ ok: true, json: () => Promise.resolve({ data: statsPayload }) });

  const chartConfigs = [];
  const RetailSystem = loadRetailSystem({ fetchImpl, chartConfigs });

  const contentEl = makeElementStub();
  await RetailSystem._renderDashboard(contentEl);

  const donutConfig = chartConfigs.find((c) => c.type === 'doughnut');
  assert.ok(donutConfig, 'Expected the Payment Methods donut chart to be constructed');

  const labels = donutConfig.data.labels;
  const colors = donutConfig.data.datasets[0].backgroundColor;

  assert.strictEqual(labels.length, 6, 'Expected all 6 payment methods to produce 6 chart slices. Got: ' + JSON.stringify(labels));
  assert.ok(
    Array.isArray(colors) && colors.length >= labels.length,
    `Payment Methods donut backgroundColor array has ${Array.isArray(colors) ? colors.length : 0} colors ` +
    `but there are ${labels.length} payment-method slices (cash/card/mobile/transfer/credit/voucher). ` +
    'Chart.js falls back to an undefined/default color for any slice past the end of this array.'
  );
  colors.slice(0, labels.length).forEach((c, i) => {
    assert.ok(
      typeof c === 'string' && /^#[0-9a-fA-F]{3,8}$/.test(c),
      `Slice ${i} ("${labels[i]}") does not have a real hex color assigned. Got: ${c}`
    );
  });

  console.log('PASS: all 6 payment methods get a real, distinguishable donut color');
}

async function main() {
  await testAllSixPaymentMethodsGetDistinctColors();
  console.log('PASS: retail_dashboard_payment_colors_test.js');
}

main().catch((err) => {
  console.error('FAIL: retail_dashboard_payment_colors_test.js');
  console.error(err);
  process.exitCode = 1;
});
