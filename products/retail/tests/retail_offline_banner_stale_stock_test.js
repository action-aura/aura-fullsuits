/**
 * Phase 7 stage 7b — "tell the truth about being offline"
 * (docs/launch-readiness/phase7-offline-ux.md).
 *
 * Stage 7a made the offline clock durable: GET /api/sub/retail/sync/health
 * (sync_service.py's get_health()) now returns `configured`, `never_synced`,
 * `seconds_since_last_success`, `pending_count`, and per-half
 * `last_success_at`. This is the display built on those facts:
 *
 *   - the persistent offline banner (app-shell.js's _renderSyncBanner() and
 *     its new _renderSyncBehindState()/_isSyncBehindThreshold()), and
 *   - the POS product tile's stale-stock treatment (subsystem-retail.js's
 *     _isStockStale()/_staleStockLabel(), consumed from _renderPOSGrid()).
 *
 * CHANGE NO ENFORCEMENT is this stage's own scope line (see the doc's
 * "Correction to Decision 1"): create_sale's server-side "Insufficient
 * stock" check and _updateQty's max_stock cart cap both read the SAME local
 * balance, so this file never asserts anything about what a sale will or
 * will not be allowed to do -- only about what the till DISPLAYS.
 *
 * Both files are loaded for REAL through Node's vm module (never
 * reimplemented here), in the same order index.html loads them
 * (subsystem-retail.js, then app-shell.js, into one shared global), so
 * app-shell.js's new cross-file reads of `RetailSystem.
 * SYNC_STALE_THRESHOLD_SECONDS` / `RetailSystem._formatClockTime` exercise
 * the real functions, not a stand-in.
 *
 * No test framework is configured for this vanilla-JS, build-step-free
 * frontend (see CLAUDE.md), so this runs standalone with only Node
 * built-ins:
 *
 *   node products/retail/tests/retail_offline_banner_stale_stock_test.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const FRONTEND_DIR = path.join(__dirname, '..', 'frontend');
const RETAIL_FILE = path.join(FRONTEND_DIR, 'subsystem-retail.js');
const SHELL_FILE = path.join(FRONTEND_DIR, 'app-shell.js');

// ─────────────────────────────────────────────────────────────────────────────
// Harness
// ─────────────────────────────────────────────────────────────────────────────

function makeElementStub(overrides) {
  const classes = [];
  const el = Object.assign({
    innerHTML: '',
    textContent: '',
    id: '',
    title: '',
    style: {},
    classes,
    classList: {
      add(c) { if (!classes.includes(c)) classes.push(c); },
      remove(c) { const i = classes.indexOf(c); if (i !== -1) classes.splice(i, 1); },
      toggle(c, on) { if (on) this.add(c); else this.remove(c); },
      contains(c) { return classes.includes(c); },
    },
    appendChild() {},
    remove() {},
    getAttribute() { return null; },
    setAttribute() {},
    querySelectorAll() { return []; },
    addEventListener() {},
  }, overrides);
  return el;
}

/**
 * Load the REAL subsystem-retail.js, then the REAL app-shell.js, into ONE
 * shared vm context -- matching index.html's own load order, and giving
 * app-shell.js's new `RetailSystem.SYNC_STALE_THRESHOLD_SECONDS` /
 * `RetailSystem._formatClockTime` reads a real RetailSystem to resolve
 * against instead of a stand-in.
 */
function loadApp(fetchImpl) {
  const retailCode = fs.readFileSync(RETAIL_FILE, 'utf8');
  const shellCode = fs.readFileSync(SHELL_FILE, 'utf8');

  const els = Object.create(null);
  const getEl = (id) => {
    if (!els[id]) els[id] = makeElementStub({ id });
    return els[id];
  };
  getEl('pos-product-grid');

  const sandbox = {
    console,
    t: (s) => s,                                   // i18n.js's global shorthand: identity in English
    fetch: fetchImpl || (() => Promise.reject(new Error('no network in this test'))),
    getComputedStyle: () => ({ getPropertyValue: () => '' }),
    navigator: { userAgent: 'Mozilla/5.0 (test)' },
    localStorage: { getItem: () => null, setItem: () => {} },
    document: {
      getElementById(id) { return getEl(id); },
      createElement() { return makeElementStub(); },
      querySelector() { return makeElementStub(); },
      querySelectorAll() { return []; },
      head: { appendChild() {} },
      body: { appendChild() {} },
      documentElement: { getAttribute: () => 'light', style: { setProperty() {} } },
      addEventListener() {},
      readyState: 'complete',
    },
  };
  sandbox.window = sandbox;

  vm.createContext(sandbox);
  vm.runInContext(retailCode, sandbox, { filename: RETAIL_FILE });
  assert.ok(sandbox.RetailSystem, 'subsystem-retail.js did not expose window.RetailSystem');
  vm.runInContext(shellCode, sandbox, { filename: SHELL_FILE });
  assert.ok(sandbox.SubsystemApp, 'app-shell.js did not expose window.SubsystemApp');

  return { RetailSystem: sandbox.RetailSystem, SubsystemApp: sandbox.SubsystemApp, getEl };
}

/** A realistic /sync/health payload, with per-test overrides. */
function healthFixture(overrides) {
  const o = overrides || {};
  const base = {
    configured: true,
    running: true,
    healthy: true,
    interval_seconds: 10,
    retry_interval_seconds: 10,
    pending_count: 0,
    never_synced: false,
    seconds_since_last_success: 0,
    push: { last_success_at: null, last_failure_at: null, last_failure_reason: null, consecutive_failures: 0, healthy: true },
    pull: { last_success_at: null, last_failure_at: null, last_failure_reason: null, consecutive_failures: 0, healthy: true },
  };
  return Object.assign({}, base, o, {
    push: Object.assign({}, base.push, o.push),
    pull: Object.assign({}, base.pull, o.pull),
  });
}

// A product whose stock figure (37) cannot be mistaken for any other number
// on the tile (reorder_level 5, price 12.5) -- so a substring check for "37"
// unambiguously means the QUANTITY, not something else.
const CANDIDATE_PRODUCT = {
  id: 'p1', name: 'Espresso Beans 1kg', sku: 'EB1', barcode: '111',
  category_id: null, category_name: null,
  total_stock: 37, reorder_level: 5, sell_price: 12.5, unit: 'bag',
};

const FRESH_STOCK_TEXT = `Stock: ${CANDIDATE_PRODUCT.total_stock} ${CANDIDATE_PRODUCT.unit}`;

function renderTile(ctx, health, product) {
  ctx.RetailSystem._syncHealth = health;
  ctx.RetailSystem._products = [product || CANDIDATE_PRODUCT];
  ctx.RetailSystem._activeCat = null;
  ctx.RetailSystem._categories = [];
  ctx.RetailSystem._renderPOSGrid();
  return ctx.getEl('pos-product-grid').innerHTML;
}

// ─────────────────────────────────────────────────────────────────────────────
// 1 — unconfigured install: NO banner, NO staleness treatment
// ─────────────────────────────────────────────────────────────────────────────
//
// Goes through the REAL _pollSyncHealth(), not a direct _renderSyncBanner()
// call: in production, _renderSyncBanner is never even CALLED for an
// unconfigured install (the gate lives in _pollSyncHealth, before the
// banner render), and this is the one test that has to prove that gate is
// real. Also proves the stash-for-the-tile wiring (RetailSystem._syncHealth)
// is real, since the tile assertion below depends on it having run.

async function testUnconfiguredInstallIsCompletelySilent() {
  const fetchImpl = () => Promise.resolve({
    ok: true,
    json: () => Promise.resolve({ status: 'success', data: { configured: false } }),
  });
  const ctx = loadApp(fetchImpl);

  await ctx.SubsystemApp._pollSyncHealth();

  assert.ok(
    !ctx.SubsystemApp._syncBannerEl,
    'A banner element was created for an unconfigured install. sync_health\'s own ' +
    'docstring requires this to stay completely silent -- {"configured": false} is the ' +
    'honest, non-error answer on most installs (SYNC_RELAY_BASE_URL unset) and on ' +
    'Android, and a permanent banner about a feature the shop never turned on trains ' +
    'people to ignore banners.'
  );
  assert.deepStrictEqual(
    ctx.RetailSystem._syncHealth, { configured: false },
    '_pollSyncHealth() did not stash the health payload on RetailSystem, so the POS ' +
    'tile has no way to read it without polling a second time.'
  );

  // Even with an absurd elapsed figure AND a completed prior sync (so ONLY
  // the `configured` guard, not the separate `never_synced` guard, is what
  // could be suppressing staleness here), an unconfigured install's own
  // local balance is not a stale copy of anything -- it is the only ledger
  // there is.
  const html = renderTile(ctx, { configured: false, never_synced: false, seconds_since_last_success: 999999 });
  assert.ok(
    html.includes(FRESH_STOCK_TEXT),
    'The POS tile did not show the ordinary stock figure on an unconfigured install. Got: ' + html
  );
  assert.ok(
    !html.includes('is-stale'),
    'The POS tile applied stale-stock styling on an unconfigured install, where the ' +
    'local balance cannot be a stale copy of anything. Got: ' + html
  );

  console.log('PASS: an unconfigured install renders no banner and no staleness treatment');
}

// ─────────────────────────────────────────────────────────────────────────────
// 2 — never synced (but configured): own wording, not a timestamp, no staleness
// ─────────────────────────────────────────────────────────────────────────────

function testNeverSyncedGetsOwnWordingAndNoStaleness() {
  const ctx = loadApp();
  const data = healthFixture({ never_synced: true, seconds_since_last_success: null, pending_count: 4 });

  ctx.SubsystemApp._renderSyncBanner(data);
  const bannerHtml = ctx.SubsystemApp._syncBannerEl.innerHTML;
  assert.ok(
    !/\d{1,2}:\d{2}/.test(bannerHtml),
    'The never-synced banner rendered something clock-time-shaped. There is no "last ' +
    'synced" instant to report on an install that has never completed a first sync. Got: ' + bannerHtml
  );
  assert.ok(
    /first sync/i.test(bannerHtml),
    'The never-synced banner did not use its own distinct wording ("Waiting for first ' +
    'sync"). Got: ' + bannerHtml
  );

  const html = renderTile(ctx, data);
  assert.ok(
    html.includes(FRESH_STOCK_TEXT),
    'The POS tile applied the dated form on a never-synced install, which has no basis ' +
    'for claiming the figure has drifted -- there is nothing to have drifted FROM yet. Got: ' + html
  );
  assert.ok(!html.includes('is-stale'), 'Got: ' + html);

  console.log('PASS: a never-synced install gets its own wording and no staleness treatment');
}

// ─────────────────────────────────────────────────────────────────────────────
// 3 — behind threshold: the real banner (both facts) AND the dated tile
// ─────────────────────────────────────────────────────────────────────────────

function behindThresholdFixture() {
  // 1 hour ago -- comfortably past the 30-minute threshold.
  const iso = new Date(Date.now() - 3600 * 1000).toISOString();
  return healthFixture({
    never_synced: false,
    seconds_since_last_success: 3600,
    pending_count: 7,
    push: { last_success_at: iso, consecutive_failures: 0 },
    pull: { last_success_at: iso, consecutive_failures: 0 },
  });
}

function testBehindThresholdShowsTheRealBannerAndDatedTile() {
  const ctx = loadApp();
  const data = behindThresholdFixture();

  ctx.SubsystemApp._renderSyncBanner(data);
  const bannerHtml = ctx.SubsystemApp._syncBannerEl.innerHTML;
  assert.ok(
    /\d{1,2}:\d{2}/.test(bannerHtml),
    'The "behind" banner does not carry a clock-time "last synced" fact. Got: ' + bannerHtml
  );
  assert.ok(
    bannerHtml.includes('7'),
    'The "behind" banner does not carry the unsent-event count (pending_count: 7). Got: ' + bannerHtml
  );

  const html = renderTile(ctx, data);
  assert.ok(
    !html.includes(FRESH_STOCK_TEXT),
    'The POS tile still shows the LIVE-looking label ("Stock: 37 bag") once behind the ' +
    'threshold -- it must present a DATED fact instead. Got: ' + html
  );
  assert.ok(
    /\d{1,2}:\d{2}/.test(html),
    'The dated tile has no clock-time in it. Got: ' + html
  );
  assert.ok(html.includes('is-stale'), 'The dated tile is missing its de-emphasis class. Got: ' + html);

  console.log('PASS: a configured install behind the threshold shows the real banner and the dated tile');
}

// ─────────────────────────────────────────────────────────────────────────────
// 4 — fresh and healthy: ordinary number, no banner (allow-half)
// ─────────────────────────────────────────────────────────────────────────────
//
// Without this test, an implementation that hides/dates everything ALWAYS
// would pass every other test here while making the POS useless.

function testFreshAndHealthyShowsOrdinaryNumberAndNoBanner() {
  const ctx = loadApp();
  const iso = new Date(Date.now() - 30 * 1000).toISOString(); // 30s ago
  const data = healthFixture({
    never_synced: false,
    seconds_since_last_success: 30,
    pending_count: 0,
    push: { last_success_at: iso, consecutive_failures: 0 },
    pull: { last_success_at: iso, consecutive_failures: 0 },
  });

  ctx.SubsystemApp._renderSyncBanner(data);
  const bannerHtml = ctx.SubsystemApp._syncBannerEl.innerHTML;
  assert.ok(
    !bannerHtml.includes('Offline since'),
    'A fresh, healthy install rendered the "behind" banner. Got: ' + bannerHtml
  );
  assert.ok(
    bannerHtml.includes('Synced'),
    'A fresh, healthy install lost its existing calm-state indicator. Got: ' + bannerHtml
  );

  const html = renderTile(ctx, data);
  assert.ok(
    html.includes(FRESH_STOCK_TEXT),
    'A fresh sync lost the ordinary stock number. Got: ' + html
  );
  assert.ok(!html.includes('is-stale'), 'A fresh sync incorrectly applied the stale treatment. Got: ' + html);

  console.log('PASS: a fresh, healthy install shows the ordinary number and no banner (allow-half)');
}

// ─────────────────────────────────────────────────────────────────────────────
// 5 — the dated form still contains the actual figure (never silently blank)
// ─────────────────────────────────────────────────────────────────────────────

function testDatedFormStillContainsTheFigure() {
  const ctx = loadApp();
  const html = renderTile(ctx, behindThresholdFixture());
  assert.ok(
    html.includes(String(CANDIDATE_PRODUCT.total_stock)),
    'The dated stock label dropped the number entirely -- "dated" must never silently ' +
    'become "blank"; the figure is the only information the cashier had. Got: ' + html
  );
  console.log('PASS: the dated tile still contains the actual stock figure');
}

// ─────────────────────────────────────────────────────────────────────────────
// 6 — anything interpolated is escaped (stored-XSS regression, same family as
//     retail_pos_name_xss_test.js / retail_customer_modal_xss_test.js)
// ─────────────────────────────────────────────────────────────────────────────
//
// The dated branch is a NEW template-literal call site this stage adds, so it
// is exactly the kind of place that family of bugs re-appears: p.unit is
// catalog data (CSV import / any logged-in user, per CLAUDE.md's "no real
// RBAC"), not this file's own hand-typed strings.

const MALICIOUS_NAME = `"><img src=x onerror=alert(document.cookie)>`;
const MALICIOUS_UNIT = `"><script>alert(1)</script>`;

function testDatedTileEscapesProductNameAndUnit() {
  const ctx = loadApp();
  const product = {
    id: 'p1', name: MALICIOUS_NAME, sku: 'SKU-1', barcode: '',
    category_id: null, category_name: null,
    total_stock: 9, reorder_level: 2, sell_price: 9.99, unit: MALICIOUS_UNIT,
  };
  const html = renderTile(ctx, behindThresholdFixture(), product);

  assert.ok(
    !html.includes(MALICIOUS_NAME),
    'The raw, unescaped malicious product name leaked into the dated tile\'s innerHTML. Got: ' + html
  );
  assert.ok(
    !html.includes(MALICIOUS_UNIT),
    'The raw, unescaped malicious unit leaked into the dated tile\'s innerHTML. Got: ' + html
  );
  assert.ok(
    html.includes('&lt;img') && html.includes('&lt;script'),
    'Expected both the product name and the unit to be HTML-escaped. Got: ' + html
  );

  console.log('PASS: the dated tile escapes both product name and unit');
}

// ─────────────────────────────────────────────────────────────────────────────

const CHECKS = [
  ['an unconfigured install is completely silent', testUnconfiguredInstallIsCompletelySilent],
  ['a never-synced install gets its own wording and no staleness', testNeverSyncedGetsOwnWordingAndNoStaleness],
  ['behind the threshold shows the real banner and the dated tile', testBehindThresholdShowsTheRealBannerAndDatedTile],
  ['fresh and healthy shows the ordinary number and no banner', testFreshAndHealthyShowsOrdinaryNumberAndNoBanner],
  ['the dated form still contains the figure', testDatedFormStillContainsTheFigure],
  ['the dated tile escapes product name and unit', testDatedTileEscapesProductNameAndUnit],
];

async function main() {
  const failures = [];
  for (const [name, fn] of CHECKS) {
    try {
      await fn();
    } catch (err) {
      failures.push(name);
      console.error(`FAIL: ${name}`);
      console.error('      ' + String((err && err.message) || err).replace(/\n/g, '\n      '));
    }
  }
  if (failures.length) {
    console.error(`\nFAIL: retail_offline_banner_stale_stock_test.js — ${failures.length} of ${CHECKS.length} checks failed:`);
    for (const name of failures) console.error(`  - ${name}`);
    process.exitCode = 1;
    return;
  }
  console.log(`PASS: retail_offline_banner_stale_stock_test.js — ${CHECKS.length} checks`);
}

main().catch((err) => {
  console.error('FAIL: retail_offline_banner_stale_stock_test.js (runner)');
  console.error(err);
  process.exitCode = 1;
});
