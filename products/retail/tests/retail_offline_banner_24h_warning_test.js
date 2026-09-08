/**
 * Phase 7 stage 7c-i — the 24-hour soft warning
 * (docs/launch-readiness/phase7-offline-ux.md, "PART 2 -- the 24-hour soft
 * warning").
 *
 * Stage 7b (retail_offline_banner_stale_stock_test.js) built the "behind"
 * banner tier once a device passes RetailSystem.SYNC_STALE_THRESHOLD_SECONDS
 * (30 minutes). This is the escalation on top of that SAME banner: once a
 * device has been behind for more than RetailSystem.
 * SYNC_STALE_WARNING_THRESHOLD_SECONDS (24 hours), app-shell.js's
 * _renderSyncBanner() must render a visibly STRONGER banner instead --
 * still carrying the same two facts (last-sync clock time, unsynced count),
 * still purely informational (no capability, nothing blocked -- Decision 2
 * in the design doc).
 *
 * This file duplicates retail_offline_banner_stale_stock_test.js's small
 * harness (loadApp/healthFixture) rather than importing it -- the same
 * established per-file-duplication convention this frontend already uses
 * everywhere else (see subsystem-retail.js's _mostRecentSyncSuccess comment:
 * "this file has no module system").
 *
 * No test framework is configured for this vanilla-JS, build-step-free
 * frontend (see CLAUDE.md), so this runs standalone with only Node
 * built-ins:
 *
 *   node products/retail/tests/retail_offline_banner_24h_warning_test.js
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
// Harness (mirrors retail_offline_banner_stale_stock_test.js's own)
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
 * shared vm context -- matching index.html's own load order, so app-shell.js's
 * reads of RetailSystem.SYNC_STALE_WARNING_THRESHOLD_SECONDS /
 * RetailSystem._formatClockTime resolve against the real values, not a
 * stand-in.
 */
function loadApp() {
  const retailCode = fs.readFileSync(RETAIL_FILE, 'utf8');
  const shellCode = fs.readFileSync(SHELL_FILE, 'utf8');

  const els = Object.create(null);
  const getEl = (id) => {
    if (!els[id]) els[id] = makeElementStub({ id });
    return els[id];
  };

  const sandbox = {
    console,
    t: (s) => s,                                   // i18n.js's global shorthand: identity in English
    fetch: () => Promise.reject(new Error('no network in this test')),
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

  return { RetailSystem: sandbox.RetailSystem, SubsystemApp: sandbox.SubsystemApp };
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

// ─────────────────────────────────────────────────────────────────────────────
// 6 — behind by more than 24h: the STRONGER banner, still carrying both facts
// ─────────────────────────────────────────────────────────────────────────────

function twentyFiveHoursBehindFixture() {
  // 25 hours ago -- comfortably past the 24-hour warning threshold, and
  // naturally also past the plain 30-minute "behind" threshold.
  const iso = new Date(Date.now() - 25 * 3600 * 1000).toISOString();
  return healthFixture({
    never_synced: false,
    seconds_since_last_success: 25 * 3600,
    pending_count: 11,
    push: { last_success_at: iso, consecutive_failures: 0 },
    pull: { last_success_at: iso, consecutive_failures: 0 },
  });
}

function testBehindByOver24hShowsTheEscalatedBanner() {
  const ctx = loadApp();
  const data = twentyFiveHoursBehindFixture();

  ctx.SubsystemApp._renderSyncBanner(data);
  const bannerHtml = ctx.SubsystemApp._syncBannerEl.innerHTML;

  // Still carries both facts the ordinary "behind" banner carries.
  assert.ok(
    /\d{1,2}:\d{2}/.test(bannerHtml),
    'The 24h-warning banner does not carry a clock-time "last synced" fact. Got: ' + bannerHtml
  );
  assert.ok(
    bannerHtml.includes('11'),
    'The 24h-warning banner does not carry the unsent-event count (pending_count: 11). Got: ' + bannerHtml
  );

  // But it must be the ESCALATED tier, not the ordinary "behind" one: its
  // own distinct headline word, its own explanatory detail sentence, and
  // its own stronger colour -- none of which the ordinary tier renders.
  assert.ok(
    bannerHtml.includes('Still offline since'),
    'A device behind by 25 hours did not render the escalated 24h-warning headline. Got: ' + bannerHtml
  );
  assert.ok(
    /over 24 hours/i.test(bannerHtml),
    'The escalated banner is missing its explanatory detail sentence about being behind ' +
    'over 24 hours. Got: ' + bannerHtml
  );
  // Re-anchored 2026-09-08 from the literal '#ef4444' to the DANGER TOKEN
  // FAMILY, because the banner tiers were moved off hardcoded hex onto
  // --state-* tokens (DESIGN.md §4.4: no colour literal outside the token
  // block). What this can no longer catch: the exact hex value. What it still
  // catches -- which is all it ever actually asserted -- is that the escalated
  // tier paints itself from a DIFFERENT, stronger state family than the plain
  // "behind" tier, which now resolves through --state-info-*. The negative
  // half at the bottom of this file pins the other direction.
  assert.ok(
    bannerHtml.includes('--state-danger-'),
    'The escalated banner did not use its own visibly-stronger colour family. Got: ' + bannerHtml
  );
  assert.ok(
    !bannerHtml.includes('Offline since'),
    'The escalated banner also rendered the PLAIN "behind" tier\'s headline -- these are ' +
    'meant to be mutually exclusive tiers. Got: ' + bannerHtml
  );

  console.log('PASS: a device behind by more than 24 hours shows the escalated banner with both facts');
}

// ─────────────────────────────────────────────────────────────────────────────
// 7 — behind by LESS than 24h: still the ordinary 7b banner (allow-half)
// ─────────────────────────────────────────────────────────────────────────────
//
// Without this test, an implementation that escalates at ANY staleness (not
// just past 24h) would pass test 6 while wrongly alarming every till that is
// merely a couple of hours behind.

function twoHoursBehindFixture() {
  // 2 hours ago -- past the 30-minute "behind" threshold, comfortably short
  // of the 24-hour warning threshold.
  const iso = new Date(Date.now() - 2 * 3600 * 1000).toISOString();
  return healthFixture({
    never_synced: false,
    seconds_since_last_success: 2 * 3600,
    pending_count: 3,
    push: { last_success_at: iso, consecutive_failures: 0 },
    pull: { last_success_at: iso, consecutive_failures: 0 },
  });
}

function testBehindByUnder24hStillShowsTheOrdinaryBanner() {
  const ctx = loadApp();
  const data = twoHoursBehindFixture();

  ctx.SubsystemApp._renderSyncBanner(data);
  const bannerHtml = ctx.SubsystemApp._syncBannerEl.innerHTML;

  assert.ok(
    bannerHtml.includes('Offline since'),
    'A device behind by only 2 hours did not render the ordinary "behind" banner. Got: ' + bannerHtml
  );
  assert.ok(
    bannerHtml.includes('3'),
    'The ordinary "behind" banner does not carry the unsent-event count (pending_count: 3). Got: ' + bannerHtml
  );
  assert.ok(
    !bannerHtml.includes('Still offline since'),
    'A device behind by only 2 hours incorrectly rendered the escalated 24h-warning headline. Got: ' + bannerHtml
  );
  assert.ok(
    !/over 24 hours/i.test(bannerHtml),
    'A device behind by only 2 hours incorrectly rendered the 24h-warning detail sentence. Got: ' + bannerHtml
  );
  // Same re-anchor as the escalated half above, and the same trade: the exact
  // hex is no longer pinned, the SEPARATION between the two tiers still is.
  assert.ok(
    !bannerHtml.includes('--state-danger-'),
    'A device behind by only 2 hours incorrectly used the escalated tier\'s colour family. Got: ' + bannerHtml
  );

  console.log('PASS: a device behind by less than 24 hours still shows the ordinary 7b banner (allow-half)');
}

// ─────────────────────────────────────────────────────────────────────────────

const CHECKS = [
  ['behind by more than 24h shows the escalated banner with both facts', testBehindByOver24hShowsTheEscalatedBanner],
  ['behind by less than 24h still shows the ordinary banner', testBehindByUnder24hStillShowsTheOrdinaryBanner],
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
    console.error(`\nFAIL: retail_offline_banner_24h_warning_test.js — ${failures.length} of ${CHECKS.length} checks failed:`);
    for (const name of failures) console.error(`  - ${name}`);
    process.exitCode = 1;
    return;
  }
  console.log(`PASS: retail_offline_banner_24h_warning_test.js — ${CHECKS.length} checks`);
}

main().catch((err) => {
  console.error('FAIL: retail_offline_banner_24h_warning_test.js (runner)');
  console.error(err);
  process.exitCode = 1;
});
