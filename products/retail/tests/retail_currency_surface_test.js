/**
 * retail_currency_surface_test.js — every money surface must speak the SHOP's
 * currency, including the ones no test was looking at.
 *
 * WHY THIS EXISTS
 *
 * The currency work was verified by unit-testing the formatter. The formatter
 * was correct. The product still showed dollars, in two places, and all 43 JS
 * suites passed the entire time:
 *
 *   1. The POS totals. `#pos-sub`, `#pos-tax` and `#pos-total` were seeded with
 *      the LITERAL string "$0.00" in the POS template, and only ever corrected
 *      by `_recalc()`, which runs on cart mutations. An empty cart never
 *      mutates, so the resting state of the till — what a cashier looks at all
 *      day between sales — read "$0.00" beside a button reading
 *      "Charge — JD 0.000". Measured on the running app, not inferred.
 *
 *   2. Every chart axis. All four wrote `v => '$' + v`, so the dashboard's
 *      Revenue-Today axis read $1 / $0.8 directly under a JD 0.000 headline.
 *
 * Both were found by screenshotting the running product, because neither is
 * reachable by a formatter test: one is an initial-HTML literal that the
 * formatter never touches, and the other is a callback Chart.js owns.
 *
 * The lesson this file encodes: a money surface is not "covered" because the
 * money FUNCTION is covered. Assert on what the screen renders.
 *
 *   node products/retail/tests/retail_currency_surface_test.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const FRONTEND_FILE = path.join(__dirname, '..', 'frontend', 'subsystem-retail.js');
const SOURCE = fs.readFileSync(FRONTEND_FILE, 'utf8');

function makeElementStub(id) {
  const el = {
    id: id || '', _innerHTML: '', textContent: '', value: '', disabled: false,
    style: {}, options: [], classList: { toggle() {}, add() {}, remove() {} },
    appendChild() {}, getAttribute() { return null; }, setAttribute() {},
    querySelectorAll() { return []; }, addEventListener() {}, focus() {},
  };
  Object.defineProperty(el, 'innerHTML', {
    get() { return el._innerHTML; },
    set(v) { el._innerHTML = String(v); },
  });
  return el;
}

function load() {
  const els = new Map();
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
      getElementById(id) {
        if (!els.has(id)) els.set(id, makeElementStub(id));
        return els.get(id);
      },
      createElement() { return makeElementStub(); },
      querySelector() { return makeElementStub(); },
      querySelectorAll() { return []; },
      head: { appendChild() {} }, body: { appendChild() {} },
      documentElement: { getAttribute() { return null; } },
      addEventListener() {},
    },
  };
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(SOURCE, sandbox, { filename: FRONTEND_FILE });
  return { RetailSystem: sandbox.RetailSystem, sandbox, els };
}

/** Render the POS with a given currency and return its markup. */
function renderPos(symbol, decimals) {
  const { RetailSystem } = load();
  RetailSystem._currencySymbol = symbol;
  RetailSystem._currencyDecimals = decimals;
  const c = makeElementStub('content');
  RetailSystem._renderPOS(c);
  return c.innerHTML;
}

function testPosRestingStateUsesTheShopCurrency() {
  const html = renderPos('JD', 3);

  // The three summary spans, as the cashier first sees them.
  for (const id of ['pos-sub', 'pos-tax', 'pos-total']) {
    const m = html.match(new RegExp('id="' + id + '"[^>]*>([^<]*)<'));
    assert.ok(m, 'the POS template must still carry #' + id);
    const shown = m[1].trim();
    assert.ok(!shown.includes('$'),
      '#' + id + ' renders "' + shown + '" before any cart activity. A hard ' +
      'dollar here is what a Jordanian cashier stares at all day between ' +
      'sales, beside a Charge button that correctly says JD.');
    assert.ok(shown.includes('JD'),
      '#' + id + ' must carry the configured mark from the FIRST paint, not ' +
      'only after _recalc() runs on a cart change. Got: ' + shown);
    assert.ok(/0\.000/.test(shown),
      '#' + id + ' must use the currency\'s own precision (JOD has three ' +
      'decimal places — fils). Got: ' + shown);
  }
}

function testPosRestingStateFollowsADifferentCurrency() {
  // The allow-half: this must not be "always print JD" either.
  const html = renderPos('$', 2);
  const m = html.match(/id="pos-total"[^>]*>([^<]*)</);
  assert.ok(m, '#pos-total must exist');
  const shown = m[1].trim();
  assert.ok(shown.includes('$') && /0\.00(?!0)/.test(shown),
    'a shop configured in dollars must see $0.00 — the fix is "use the ' +
    'configured currency", not "hardcode a different one". Got: ' + shown);
}

function testNoChartAxisHardcodesACurrencyMark() {
  // Chart.js owns these callbacks, so no rendering test can observe them.
  // The source is the only place this is checkable, which is exactly why it
  // went unnoticed through four separate axes.
  const offenders = SOURCE.match(/callback\s*:\s*v\s*=>\s*'\$'\s*\+\s*v/g) || [];
  assert.strictEqual(offenders.length, 0,
    'a chart axis hardcodes "$". All four axes did, so the dashboard\'s ' +
    'Revenue-Today axis read $1 / $0.8 directly under a JD headline. Use ' +
    'axisMoney(v).');
}

function testAxisMoneyUsesTheConfiguredMarkAndDegradesSafely() {
  const { RetailSystem, sandbox } = load();
  assert.strictEqual(typeof sandbox.axisMoney, 'function',
    'axisMoney must exist for the chart builders to share');

  RetailSystem._currencySymbol = 'JD';
  RetailSystem._currencyDecimals = 3;
  assert.ok(String(sandbox.axisMoney(5)).includes('JD'),
    'the axis must carry the configured mark');

  RetailSystem._currencySymbol = '$';
  RetailSystem._currencyDecimals = 2;
  assert.ok(String(sandbox.axisMoney(5)).includes('$'),
    'and must follow a different currency rather than pinning JD');

  // A chart can be built before the first /settings/tax response. An unmarked
  // axis is ambiguous; an axis marked with the WRONG currency is a misread, so
  // the bare number is the correct degradation.
  const broken = Object.create(null);
  const saved = RetailSystem._currencyPrefix;
  RetailSystem._currencyPrefix = () => { throw new Error('not ready'); };
  assert.strictEqual(String(sandbox.axisMoney(7)), '7',
    'axisMoney must fall back to the bare number, never throw and never ' +
    'guess a mark');
  RetailSystem._currencyPrefix = saved;
  void broken;
}

function testCashTenderedAcceptsTheCurrencysSmallestUnit() {
  // step="0.01" on a THREE-decimal currency is not cosmetic: it makes a fils
  // amount a step mismatch, so the till cannot accept 3.755 JOD cleanly and
  // the arrow keys move in the wrong unit.
  const jod = renderPos('JD', 3);
  const m = jod.match(/id="pos-tendered"[^>]*step="([^"]+)"/);
  assert.ok(m, 'the cash-tendered input must declare a step');
  assert.strictEqual(m[1], '0.001',
    'a JOD till must step in fils, not in cents. Got step=' + m[1]);

  const ph = jod.match(/id="pos-tendered"[^>]*placeholder="([^"]+)"/);
  assert.strictEqual(ph && ph[1], '0.000',
    'the placeholder must show the currency precision the cashier types in');

  // Allow-half: a 2-decimal currency must still get cents.
  const usd = renderPos('$', 2);
  const m2 = usd.match(/id="pos-tendered"[^>]*step="([^"]+)"/);
  assert.strictEqual(m2 && m2[1], '0.01',
    'a dollar till must still step in cents -- the fix is currency-driven, ' +
    'not a second hardcoded value');
}

function main() {
  testPosRestingStateUsesTheShopCurrency();
  testPosRestingStateFollowsADifferentCurrency();
  testNoChartAxisHardcodesACurrencyMark();
  testAxisMoneyUsesTheConfiguredMarkAndDegradesSafely();
  testCashTenderedAcceptsTheCurrencysSmallestUnit();
  console.log('PASS: retail_currency_surface_test.js (5 cases)');
}

try {
  main();
} catch (err) {
  console.error('FAIL: retail_currency_surface_test.js');
  console.error(err);
  process.exitCode = 1;
}
