/**
 * Regression test: a NET-NEGATIVE payment-method bucket must never be drawn
 * as a doughnut slice.
 *
 * Bug introduced by the report/metrics consolidation. Both payment-method
 * charts -- the dashboard's #r-dash-pay and the Reports page's #rep-pay --
 * used to be fed gross SUM(sales.total) per tender, which can never be
 * negative. They are now fed core/retail/metrics.py's NET revenue per
 * tender, keyed by the tender the refund was PAID BACK IN, and that module
 * states explicitly (decision #1) that a bucket which saw only refunds
 * reports a negative number -- on purpose, because that is what makes the
 * buckets sum back to the Revenue KPI printed beside them.
 *
 * The real-world case is ordinary: a card sale yesterday, refunded to card
 * today, with no card sale today. `card` is then -57.50.
 *
 * Chart.js sizes a doughnut arc by |value|, so -57.50 renders as a perfectly
 * ordinary 57.50-sized wedge. The chart would read "card took 57.50 today" --
 * the exact opposite of the truth -- and the slices would no longer sum to
 * the whole. Clamping or dropping the bucket was rejected: it deletes real
 * money from a financial screen and re-breaks the sum-to-KPI identity the
 * consolidation exists to establish. See retailPaymentChartConfig() in
 * products/retail/frontend/subsystem-retail.js for the full reasoning.
 *
 * Contract asserted here, for BOTH charts:
 *   1. All buckets >= 0  -> still a doughnut (the ordinary trading day is
 *      untouched; retail_dashboard_payment_colors_test.js covers it too).
 *   2. Any bucket < 0    -> NOT a doughnut/pie. Rendered as a bar chart with
 *      a zero baseline.
 *   3. The negative value is passed through EXACTLY -- never clamped to 0,
 *      never made positive, never dropped from the labels.
 *   4. Colors stay the named per-tender lookup in both shapes, so a tender
 *      keeps its identity across the switch.
 *
 * No test framework is configured for this vanilla-JS, build-step-free
 * frontend (see CLAUDE.md), so this runs standalone on Node built-ins:
 *
 *   node products/retail/tests/retail_payment_chart_negative_bucket_test.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const FRONTEND_FILE = path.join(__dirname, '..', 'frontend', 'subsystem-retail.js');

function makeElementStub(id) {
  const el = {
    id: id || '',
    innerHTML: '',
    textContent: '',
    value: '30',
    style: {},
    appendChild() {},
    getAttribute() { return null; },
    setAttribute() {},
    querySelectorAll() { return []; },
    getContext() { return {}; },
    parentElement: null,
  };
  el.parentElement = {
    innerHTML: '', style: {}, appendChild() {}, getContext() { return {}; },
  };
  return el;
}

function loadRetailSystem({ fetchImpl, chartConfigs }) {
  const code = fs.readFileSync(FRONTEND_FILE, 'utf8');

  function ChartStub(ctx, config) { chartConfigs.push(config); }
  ChartStub.getChart = () => null;

  const elements = {};
  const sandbox = {
    console,
    t: (s) => s,
    fetch: fetchImpl,
    getComputedStyle: () => ({ getPropertyValue: () => '' }),
    document: {
      getElementById(id) {
        if (!elements[id]) elements[id] = makeElementStub(id);
        return elements[id];
      },
      createElement() { return makeElementStub(); },
      querySelector() { return makeElementStub(); },
      head: { appendChild() {} },
      documentElement: { getAttribute() { return null; } },
    },
  };
  sandbox.window = sandbox;
  sandbox.window.Chart = ChartStub;

  vm.createContext(sandbox);
  vm.runInContext(code, sandbox, { filename: FRONTEND_FILE });

  assert.ok(sandbox.RetailSystem, 'subsystem-retail.js did not expose window.RetailSystem');
  return { RetailSystem: sandbox.RetailSystem, elements };
}

// The tender buckets a refund-only day actually produces: cash still took
// money, card only gave money back.
const NET_NEGATIVE_BUCKETS = {
  cash: { count: 4, revenue: 230.0 },
  card: { count: 0, revenue: -57.5 },
};

function dashboardPayload(paymentMethods) {
  return {
    today_sales: 172.5, today_transactions: 4, month_sales: 1000, low_stock_alerts: 0,
    total_customers: 5, total_products: 10, today_returns: 57.5, sales_change_pct: 0,
    hourly_labels: [], hourly_data: [],
    payment_methods: paymentMethods,
    recent_sales: [],
  };
}

function jsonResponse(body) {
  return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
}

function assertHonestNegativeChart(cfg, where) {
  assert.ok(cfg, `${where}: expected a payment-method chart to be constructed`);

  assert.notStrictEqual(
    cfg.type, 'doughnut',
    `${where}: a net-negative tender bucket was rendered as a DOUGHNUT. Chart.js sizes an arc ` +
    'by |value|, so the -57.50 card bucket draws as an ordinary 57.50-sized wedge -- it reads as ' +
    'money taken when it is money refunded, and the slices no longer sum to the Revenue KPI.'
  );
  assert.notStrictEqual(cfg.type, 'pie', `${where}: pie has the identical |value| problem as doughnut`);
  assert.strictEqual(cfg.type, 'bar', `${where}: expected the signed-safe bar fallback, got ${cfg.type}`);

  // Array.from(): these arrays were created inside the vm realm, so their
  // prototype is the sandbox's Array.prototype and deepStrictEqual's
  // prototype check would fail on otherwise-identical contents.
  const labels = Array.from(cfg.data.labels);
  const values = Array.from(cfg.data.datasets[0].data);
  assert.deepStrictEqual(
    labels, ['cash', 'card'],
    `${where}: the negative tender must stay in the chart, not be dropped from the legend`
  );
  assert.deepStrictEqual(
    values, [230.0, -57.5],
    `${where}: the negative bucket must be passed through EXACTLY -- not clamped to 0, not made ` +
    `positive, not reordered. Got ${JSON.stringify(values)}.`
  );

  // A zero baseline is what makes "below zero" visible rather than merely "shorter".
  const valueAxis = (cfg.options.scales || {}).x || {};
  assert.strictEqual(
    valueAxis.beginAtZero, true,
    `${where}: the value axis must begin at zero so a negative bar sits on the other side of the ` +
    'baseline instead of just rendering as a short positive-looking bar'
  );

  // Named per-tender colors survive the switch to bars.
  assert.deepStrictEqual(
    Array.from(cfg.data.datasets[0].backgroundColor), ['#10b981', '#3b82f6'],
    `${where}: cash/card must keep their usual colors in the bar fallback`
  );
}

async function testDashboardChartDoesNotDrawNegativeSlices() {
  const chartConfigs = [];
  const fetchImpl = () => jsonResponse({ data: dashboardPayload(NET_NEGATIVE_BUCKETS) });
  const { RetailSystem } = loadRetailSystem({ fetchImpl, chartConfigs });

  await RetailSystem._renderDashboard(makeElementStub());

  // The hourly chart is also a 'bar'; pick the payment chart by its labels.
  const cfg = chartConfigs.find(c => Array.isArray(c.data.labels) && c.data.labels.includes('card'));
  assertHonestNegativeChart(cfg, 'dashboard #r-dash-pay');
  console.log('PASS: dashboard payment chart renders a net-negative tender honestly');
}

async function testDashboardStillUsesADoughnutOnAnOrdinaryDay() {
  const chartConfigs = [];
  const positive = { cash: { count: 4, revenue: 230.0 }, card: { count: 2, revenue: 115.0 } };
  const fetchImpl = () => jsonResponse({ data: dashboardPayload(positive) });
  const { RetailSystem } = loadRetailSystem({ fetchImpl, chartConfigs });

  await RetailSystem._renderDashboard(makeElementStub());

  const cfg = chartConfigs.find(c => Array.isArray(c.data.labels) && c.data.labels.includes('card'));
  assert.ok(cfg, 'Expected the payment-method chart to be constructed');
  assert.strictEqual(
    cfg.type, 'doughnut',
    'An all-positive day must still render the familiar share-of-total doughnut -- the bar ' +
    'fallback is for signed data only, not a wholesale replacement of the chart.'
  );
  assert.deepStrictEqual(Array.from(cfg.data.datasets[0].data), [230.0, 115.0]);
  console.log('PASS: an ordinary all-positive day still renders the doughnut');
}

async function testReportsPageChartDoesNotDrawNegativeSlices() {
  const chartConfigs = [];
  const fetchImpl = (url) => {
    if (url.includes('/reports/payment-methods')) {
      return jsonResponse({ success: true, data: [
        { payment_method: 'cash', count: 4, revenue: 230.0 },
        { payment_method: 'card', count: 0, revenue: -57.5 },
      ] });
    }
    if (url.includes('/reports/sales-trend')) {
      return jsonResponse({ success: true, labels: ['2026-01-01'], data: [172.5], transactions: [4], avg_ticket: [43.13] });
    }
    if (url.includes('/reports/top-products')) {
      return jsonResponse({ success: true, labels: ['Item'], data: [4], revenue: [172.5], profit: [132.5] });
    }
    if (url.includes('/reports/by-branch')) {
      return jsonResponse({ success: true, labels: ['Main'], data: [172.5], transactions: [4], avg_ticket: [43.13], branch_ids: [1] });
    }
    if (url.includes('/reports/summary')) {
      return jsonResponse({ success: true, data: { revenue: 172.5, transactions: 4, gross_profit: 132.5, margin_pct: 76.8, avg_ticket: 43.13 } });
    }
    return jsonResponse({ data: [] });
  };

  const { RetailSystem } = loadRetailSystem({ fetchImpl, chartConfigs });
  await RetailSystem._loadReports();

  const cfg = chartConfigs.find(c => Array.isArray(c.data.labels) && c.data.labels.includes('card'));
  assertHonestNegativeChart(cfg, 'reports #rep-pay');
  console.log('PASS: reports payment chart renders a net-negative tender honestly');
}

async function main() {
  await testDashboardChartDoesNotDrawNegativeSlices();
  await testDashboardStillUsesADoughnutOnAnOrdinaryDay();
  await testReportsPageChartDoesNotDrawNegativeSlices();
  console.log('PASS: retail_payment_chart_negative_bucket_test.js');
}

main().catch((err) => {
  console.error('FAIL: retail_payment_chart_negative_bucket_test.js');
  console.error(err);
  process.exitCode = 1;
});
