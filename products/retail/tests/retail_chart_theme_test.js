/**
 * retail_chart_theme_test.js — P1a item #9 ("Charts in the product's own
 * colours").
 *
 * Covers products/retail/frontend/chart-theme.js's `window.AuraChartTheme`
 * (loaded via plain CommonJS `require`, same guarded-export shape
 * share.js uses):
 *
 *   1. jdAxisLabel: tabular, always 3 decimals.
 *   2. shouldShowLegend / legendOptions: "no legend when there is only one
 *      series" -- exact boundary at 1 vs 2 datasets.
 *   3. cssToken / moneyIn / moneyOut / accent: reads a stubbed
 *      getComputedStyle, falls back correctly when the token is absent.
 *   4. apply(): actually mutates a stubbed Chart.defaults (grid colour,
 *      tick colour, legend label colour) -- proving this is wired to
 *      Chart.js's real extension point, not just a set of unused helpers.
 *   5. translate(): uses a global t() when present, passes the raw label
 *      through unchanged otherwise (never throws).
 *
 * No test framework is configured for this vanilla-JS, build-step-free
 * frontend (see CLAUDE.md) -- this runs standalone on Node built-ins.
 *
 * Run: node products/retail/tests/retail_chart_theme_test.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');

const AuraChartTheme = require(path.join(__dirname, '..', 'frontend', 'chart-theme.js'));
const SUBSYSTEM_SRC = fs.readFileSync(
  path.join(__dirname, '..', 'frontend', 'subsystem-retail.js'), 'utf8');

const cases = [];
function test(name, fn) { cases.push([name, fn]); }

// ── jdAxisLabel ──────────────────────────────────────────────────────────

test('jdAxisLabel: tabular, always 3 decimals', () => {
  assert.strictEqual(AuraChartTheme.jdAxisLabel(0), '0.000');
  assert.strictEqual(AuraChartTheme.jdAxisLabel(1200), '1200.000');
  assert.strictEqual(AuraChartTheme.jdAxisLabel(0.4), '0.400');
  assert.strictEqual(AuraChartTheme.jdAxisLabel(12.3456), '12.346'); // rounds, not truncates
});

test('jdAxisLabel: a non-numeric value is returned as its own string rather than throwing', () => {
  assert.strictEqual(AuraChartTheme.jdAxisLabel('n/a'), 'n/a');
});

// ── shouldShowLegend / legendOptions ────────────────────────────────────

test('shouldShowLegend: exactly one dataset hides the legend, two or more show it', () => {
  assert.strictEqual(AuraChartTheme.shouldShowLegend(0), false);
  assert.strictEqual(AuraChartTheme.shouldShowLegend(1), false);
  assert.strictEqual(AuraChartTheme.shouldShowLegend(2), true);
  assert.strictEqual(AuraChartTheme.shouldShowLegend(6), true);
});

test('legendOptions: display flag matches shouldShowLegend and labels carry a colour', () => {
  const one = AuraChartTheme.legendOptions(1);
  const two = AuraChartTheme.legendOptions(2);
  assert.strictEqual(one.display, false);
  assert.strictEqual(two.display, true);
  assert.ok(two.labels && typeof two.labels.color === 'string' && two.labels.color.length > 0,
    'expected a non-empty legend label colour');
});

test('legendOptions: an `extra` block (e.g. a custom generateLabels) is merged in, not dropped', () => {
  let called = false;
  const opts = AuraChartTheme.legendOptions(2, { labels: { generateLabels: () => { called = true; return []; } } });
  opts.labels.generateLabels();
  assert.strictEqual(called, true, 'expected the caller-supplied generateLabels to survive the merge');
});

// ── cssToken / money helpers (no DOM at all -- must fall back cleanly) ─────

test('cssToken and money helpers fall back cleanly with no DOM present', () => {
  assert.strictEqual(AuraChartTheme.cssToken('--nope', 'fallback'), 'fallback');
  assert.strictEqual(typeof AuraChartTheme.moneyIn(), 'string');
  assert.strictEqual(typeof AuraChartTheme.moneyOut(), 'string');
  assert.strictEqual(typeof AuraChartTheme.accent(), 'string');
  assert.ok(AuraChartTheme.moneyIn().length > 0);
});

test('cssToken reads a stubbed getComputedStyle when a window/document exist', () => {
  const originalWindow = global.window;
  const originalGCS = global.getComputedStyle;
  global.window = {
    document: {
      documentElement: {},
    },
  };
  global.getComputedStyle = () => ({
    getPropertyValue: (name) => (name === '--text-money-positive' ? '  #0c8b61  ' : ''),
  });
  try {
    assert.strictEqual(AuraChartTheme.cssToken('--text-money-positive', 'fallback'), '#0c8b61');
    assert.strictEqual(AuraChartTheme.cssToken('--not-set', 'fallback'), 'fallback');
  } finally {
    global.window = originalWindow;
    global.getComputedStyle = originalGCS;
  }
});

// ── apply(): must actually mutate Chart.defaults ────────────────────────

test('apply(): mutates a stubbed Chart.defaults with the resolved tokens', () => {
  const originalWindow = global.window;
  const originalGCS = global.getComputedStyle;
  const chartDefaults = {};
  global.window = {
    document: { documentElement: {} },
    Chart: { defaults: chartDefaults },
  };
  global.getComputedStyle = () => ({
    getPropertyValue: (name) => {
      if (name === '--text-secondary') return '#51607a';
      if (name === '--border-hairline') return '#e3e8ef';
      return '';
    },
  });
  try {
    AuraChartTheme.apply();
    assert.strictEqual(chartDefaults.color, '#51607a');
    assert.strictEqual(chartDefaults.borderColor, '#e3e8ef');
    assert.ok(chartDefaults.plugins && chartDefaults.plugins.legend && chartDefaults.plugins.legend.labels,
      'expected Chart.defaults.plugins.legend.labels to be set');
    assert.strictEqual(chartDefaults.plugins.legend.labels.color, '#51607a');
    assert.ok(chartDefaults.scale && chartDefaults.scale.grid, 'expected Chart.defaults.scale.grid to be set (hairline grid)');
    assert.strictEqual(chartDefaults.scale.grid.color, '#e3e8ef');
    assert.strictEqual(chartDefaults.scale.grid.lineWidth, 1, 'expected a hairline (1px) grid line width');
  } finally {
    global.window = originalWindow;
    global.getComputedStyle = originalGCS;
  }
});

test('apply(): does nothing (never throws) when window.Chart is absent', () => {
  const originalWindow = global.window;
  global.window = { document: { documentElement: {} } }; // no .Chart
  try {
    assert.doesNotThrow(() => AuraChartTheme.apply());
  } finally {
    global.window = originalWindow;
  }
});

// ── translate() ──────────────────────────────────────────────────────────

test('translate: uses a global t() when present', () => {
  global.t = (label) => `AR:${label}`;
  try {
    assert.strictEqual(AuraChartTheme.translate('Revenue'), 'AR:Revenue');
  } finally {
    delete global.t;
  }
});

test('translate: passes the raw label through unchanged with no t() available', () => {
  assert.strictEqual(AuraChartTheme.translate('Revenue'), 'Revenue');
});

// FIX ROUND F4: "replace retail_chart_theme_test.js's helper-only checks
// with, or add, checks that the helpers are wired into the real charts."
// The tests above prove the FUNCTIONS work in isolation; these read the
// REAL subsystem-retail.js source and prove each one is actually CALLED
// from the dashboard/Reports chart-building code, not merely defined and
// forgotten. A source-anchor check, not a live-DOM one (that evidence is
// retail_p1a_till_e2e.py's job, in a real browser) -- but a strong enough
// one that removing any of these calls fails a check immediately, without
// needing to boot Chromium first.
test('AuraChartTheme.apply() is called from both the dashboard and Reports chart setup', () => {
  const hits = (SUBSYSTEM_SRC.match(/AuraChartTheme\.apply\(\)/g) || []).length;
  assert.ok(hits >= 2, `expected AuraChartTheme.apply() called at least twice (dashboard + Reports), found ${hits}`);
});

test('AuraChartTheme.jdAxisLabel is used by the shared axisMoney() formatter', () => {
  assert.ok(SUBSYSTEM_SRC.includes('AuraChartTheme.jdAxisLabel('),
    'expected axisMoney() to call AuraChartTheme.jdAxisLabel(), not only its own toFixed()');
});

test('AuraChartTheme.shouldShowLegend is used by a real single-series chart', () => {
  assert.ok(SUBSYSTEM_SRC.includes('AuraChartTheme.shouldShowLegend('),
    'expected at least one real chart to decide its legend via AuraChartTheme.shouldShowLegend()');
});

test('AuraChartTheme.legendOptions is used by real multi-series charts', () => {
  const hits = (SUBSYSTEM_SRC.match(/AuraChartTheme\.legendOptions\(/g) || []).length;
  assert.ok(hits >= 3, `expected AuraChartTheme.legendOptions() used by all three multi-series Reports charts (rep-trend/rep-top/rep-branch-chart), found ${hits}`);
});

// ── FIX ROUND 3 (LOW-4): axis decimals follow the currency; doughnut mirrors ──
// These run the REAL subsystem-retail.js in a vm sandbox (as
// retail_currency_surface_test.js does) and call the real axisMoney() and
// retailPaymentChartConfig() -- not a source-text anchor.
const vm = require('vm');

function stubEl() {
  const el = {
    id: '', textContent: '', value: '', disabled: false, style: {}, options: [],
    classList: { toggle() {}, add() {}, remove() {} }, appendChild() {},
    getAttribute() { return null; }, setAttribute() {}, querySelectorAll() { return []; },
    addEventListener() {}, focus() {},
  };
  let html = '';
  Object.defineProperty(el, 'innerHTML', { get() { return html; }, set(v) { html = String(v); } });
  return el;
}

function loadSubsystem(opts) {
  const o = opts || {};
  const sandbox = {
    console: { error() {}, warn() {}, log() {} },
    t: (s) => s, setTimeout, clearTimeout,
    fetch: () => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ status: 'success', data: [] }) }),
    getComputedStyle: () => ({ getPropertyValue: () => '' }),
    SubsystemApp: { showToast() {}, role: 'admin', hasCapability: () => true },
    AuraChartTheme,
    document: {
      getElementById() { return stubEl(); }, createElement() { return stubEl(); },
      querySelector() { return stubEl(); }, querySelectorAll() { return []; },
      head: { appendChild() {} }, body: { appendChild() {} },
      documentElement: { getAttribute() { return null; } }, addEventListener() {},
    },
  };
  if (o.rtl) sandbox.AuraI18n = { isRTL: () => true };
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(SUBSYSTEM_SRC, sandbox, { filename: 'subsystem-retail.js' });
  return sandbox;
}

test('jdAxisLabel: decimals argument follows the currency; a bad value falls back to 3, never throws', () => {
  assert.strictEqual(AuraChartTheme.jdAxisLabel(12, 2), '12.00');
  assert.strictEqual(AuraChartTheme.jdAxisLabel(12.345, 0), '12');
  assert.strictEqual(AuraChartTheme.jdAxisLabel(12, 3), '12.000');
  // allow-half AND deny-half: an out-of-range / non-integer count must not
  // reach toFixed() (RangeError above 100) nor silently pick another count.
  assert.strictEqual(AuraChartTheme.jdAxisLabel(12, 101), '12.000');
  assert.strictEqual(AuraChartTheme.jdAxisLabel(12, -1), '12.000');
  assert.strictEqual(AuraChartTheme.jdAxisLabel(12, 2.5), '12.000');
  assert.strictEqual(AuraChartTheme.jdAxisLabel(12, 'x'), '12.000');
  assert.strictEqual(AuraChartTheme.jdAxisLabel(12, null), '12.000');
});

test('axisMoney: EVERY tick uses the currency decimals (JOD 3, dollar 2) -- no whole-number branch', () => {
  const sb = loadSubsystem();
  sb.RetailSystem._currencySymbol = 'JD';
  sb.RetailSystem._currencyDecimals = 3;
  // whole ticks 0/400/1200 used to print 'JD 400' and 'JD 1,200'.
  assert.strictEqual(sb.axisMoney(400, [0, 400, 800, 1200]), 'JD 400.000');
  assert.strictEqual(sb.axisMoney(1200, [0, 400, 800, 1200]), 'JD 1200.000');
  assert.strictEqual(sb.axisMoney(0.4), 'JD 0.400');
  sb.RetailSystem._currencySymbol = '$';
  sb.RetailSystem._currencyDecimals = 2;
  assert.strictEqual(sb.axisMoney(400, [0, 400]), '$400.00', 'a dollar shop must not be forced to 3 decimals');
});

test('doughnut legend mirrors in Arabic (rtl) and is untouched in English', () => {
  const en = loadSubsystem();
  const cfgEn = en.retailPaymentChartConfig(['cash', 'card'], [5, 3], '#000');
  assert.strictEqual(cfgEn.type, 'doughnut');
  assert.strictEqual(cfgEn.options.plugins.legend.rtl, false);
  assert.strictEqual(cfgEn.options.plugins.legend.textDirection, 'ltr');
  const ar = loadSubsystem({ rtl: true });
  const cfgAr = ar.retailPaymentChartConfig(['cash', 'card'], [5, 3], '#000');
  assert.strictEqual(cfgAr.options.plugins.legend.rtl, true);
  assert.strictEqual(cfgAr.options.plugins.legend.textDirection, 'rtl');
});

let failed = 0;
for (const [name, fn] of cases) {
  try {
    fn();
    console.log(`PASS: ${name}`);
  } catch (err) {
    failed += 1;
    console.error(`FAIL: ${name}\n      ${err && err.stack || err}`);
  }
}
console.log(`\n=== ${cases.length - failed} passed, ${failed} failed, ${cases.length} total ===`);
process.exit(failed ? 1 : 0);
