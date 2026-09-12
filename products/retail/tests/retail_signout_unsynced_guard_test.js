/**
 * retail_signout_unsynced_guard_test.js -- signing out must not silently
 * strand completed sales on a till.
 *
 * WHY THIS EXISTS
 * `SubsystemApp.logout()` stopped the sync poll and posted to
 * /api/auth/logout with no idea whether anything was still queued. On a
 * shared till that is how sales get stranded: a cashier signs out at the end
 * of a shift, the device is behind the relay, and N completed sales sit on a
 * machine nobody signs back into. Nothing anywhere said a word.
 *
 * This file RUNS `logout()` rather than grepping for the guard. A static
 * check could confirm the words are present and could never tell whether the
 * sign-out actually stops -- and "the guard exists but never fires" is the
 * failure this is here to prevent, not the one it would catch.
 *
 * Harness: app-shell.js evaluated in a vm against a thin DOM/fetch stub, the
 * same technique the other frontend suites in this directory use for
 * subsystem-retail.js. Run standalone with plain Node, no build step:
 *
 *     node products/retail/tests/retail_signout_unsynced_guard_test.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const APP_SHELL = path.join(__dirname, '..', 'frontend', 'app-shell.js');
const SHELL_SRC = fs.readFileSync(APP_SHELL, 'utf8');

function fakeElement() {
  return {
    style: {}, dataset: {},
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    addEventListener() {}, removeEventListener() {}, remove() {},
    appendChild() {}, setAttribute() {}, removeAttribute() {}, focus() {}, click() {},
    querySelector() { return null; }, querySelectorAll() { return []; },
    innerHTML: '', textContent: '', title: '', hidden: false, value: '',
  };
}

/**
 * Boot the shell with a controllable world.
 *
 * `health` describes what GET /api/sub/retail/sync/health does:
 *   {throws: true}                 -- the request fails (device offline)
 *   {ok: false}                    -- a non-200
 *   {data: {...}}                  -- a 200 carrying this `data` envelope
 * `cachedHealth` is what a previous poll left on RetailSystem._syncHealth.
 * `confirmAnswer` is what the styled confirm dialog resolves to, or null to
 * leave RetailSystem without a _confirm at all (the pre-login shell).
 */
function bootShell({ health, cachedHealth, confirmAnswer, windowConfirm } = {}) {
  const calls = { logoutPosts: 0, healthGets: 0, confirms: 0, stopPoll: 0 };

  const sandbox = {
    console: { log() {}, warn() {}, error() {} },
    document: {
      readyState: 'complete',
      body: fakeElement(),
      documentElement: fakeElement(),
      head: fakeElement(),
      getElementById() { return null; },
      querySelector() { return null; },
      querySelectorAll() { return []; },
      createElement() { return fakeElement(); },
      addEventListener() {},
    },
    localStorage: { getItem() { return null; }, setItem() {}, removeItem() {} },
    sessionStorage: { getItem() { return null; }, setItem() {}, removeItem() {} },
    setTimeout, clearTimeout, setInterval, clearInterval,
    location: { href: '', hash: '', reload() {} },
    navigator: { language: 'en', userAgent: 'node' },
    matchMedia: () => ({ matches: false, addEventListener() {}, addListener() {} }),
    requestAnimationFrame: (fn) => setTimeout(fn, 0),
    CustomEvent: function () {}, Event: function () {},
    BroadcastChannel: undefined,
    t: (s) => s,
    async fetch(url) {
      if (String(url).includes('/sync/health')) {
        calls.healthGets++;
        if (!health || health.throws) throw new Error('offline');
        if (health.ok === false) return { ok: false, status: 503, json: async () => ({}) };
        return { ok: true, status: 200, json: async () => ({ status: 'success', data: health.data }) };
      }
      if (String(url).includes('/api/auth/logout')) {
        calls.logoutPosts++;
        return { ok: true, status: 200, json: async () => ({ status: 'success' }) };
      }
      return { ok: true, status: 200, json: async () => ({}) };
    },
  };
  sandbox.window = sandbox;
  sandbox.globalThis = sandbox;

  sandbox.window.confirm = (msg) => { calls.confirms++; return !!windowConfirm; };

  vm.createContext(sandbox);
  vm.runInContext(SHELL_SRC, sandbox, { filename: 'app-shell.js' });

  const app = sandbox.SubsystemApp;
  assert.ok(app && typeof app.logout === 'function',
    'app-shell.js did not yield a SubsystemApp with a logout() -- this harness has nothing to test');

  const retail = {};
  if (cachedHealth !== undefined) retail._syncHealth = cachedHealth;
  if (confirmAnswer !== null && confirmAnswer !== undefined) {
    retail._confirm = async () => { calls.confirms++; return confirmAnswer; };
  }
  sandbox.RetailSystem = retail;
  sandbox.window.RetailSystem = retail;

  // Neutralise the two things logout() does purely to repaint the shell --
  // they are not what this file is about, and letting them run would test the
  // relogin modal instead of the guard.
  app._stopSyncHealthPoll = () => { calls.stopPoll++; };
  app.showReloginModal = () => {};

  return { app, calls, sandbox };
}

const CONFIGURED = (pending) => ({ configured: true, pending_count: pending });

/* ── 1 ── queued sales + the operator declines: nothing is torn down ─────── */
async function testDecliningKeepsTheSessionIntact() {
  const { app, calls } = bootShell({ health: { data: CONFIGURED(3) }, confirmAnswer: false });
  await app.logout();
  assert.strictEqual(calls.confirms, 1, 'the operator was never asked, with 3 sales queued');
  assert.strictEqual(calls.logoutPosts, 0,
    'declining the prompt still posted /api/auth/logout -- the sign-out was not actually stopped');
  assert.strictEqual(calls.stopPoll, 0,
    'declining still stopped the sync poll -- answering "stay signed in" must leave the session exactly as it was');
  console.log('PASS: queued sales + decline -> session left intact');
}

/* ── 2 ── queued sales + the operator accepts: sign-out proceeds ─────────── */
async function testAcceptingStillSignsOut() {
  const { app, calls } = bootShell({ health: { data: CONFIGURED(3) }, confirmAnswer: true });
  await app.logout();
  assert.strictEqual(calls.confirms, 1, 'the operator was never asked');
  assert.strictEqual(calls.logoutPosts, 1,
    'accepting did not sign out -- this is the allow half, and a guard that refuses everyone is broken too');
  console.log('PASS: queued sales + accept -> signs out');
}

/* ── 3 ── sync was never switched on: no prompt at all ──────────────────── */
async function testUnconfiguredSyncNeverPrompts() {
  const { app, calls } = bootShell({
    health: { data: { configured: false } }, confirmAnswer: false,
  });
  await app.logout();
  assert.strictEqual(calls.confirms, 0,
    'an install with sync switched off was asked about unsynced sales it cannot have');
  assert.strictEqual(calls.logoutPosts, 1, 'sign-out was blocked on an install with no outbox');
  console.log('PASS: sync not configured -> no prompt, signs out');
}

/* ── 4 ── nothing queued: no prompt ─────────────────────────────────────── */
async function testNothingQueuedNeverPrompts() {
  const { app, calls } = bootShell({ health: { data: CONFIGURED(0) }, confirmAnswer: false });
  await app.logout();
  assert.strictEqual(calls.confirms, 0, 'prompted with nothing queued');
  assert.strictEqual(calls.logoutPosts, 1, 'sign-out was blocked with nothing queued');
  console.log('PASS: nothing queued -> no prompt, signs out');
}

/* ── 5 ── THE TRAP: the health check itself fails, and the cache says there
 *   ARE queued sales. Falling back to "allow" here would silence the guard in
 *   exactly the offline case it exists for. ───────────────────────────────── */
async function testOfflineFallsBackToTheCachedCountNotToAllow() {
  const { app, calls } = bootShell({
    health: { throws: true },
    cachedHealth: CONFIGURED(5),
    confirmAnswer: false,
  });
  await app.logout();
  assert.strictEqual(calls.healthGets, 1, 'the live count was never attempted');
  assert.strictEqual(calls.confirms, 1,
    'the health check failed and the guard gave up -- but the cached snapshot said 5 sales were queued, '
    + 'and a failing health check usually MEANS offline, which is when sales are most likely to be queued');
  assert.strictEqual(calls.logoutPosts, 0, 'declining still signed out');
  console.log('PASS: offline + cached backlog -> still prompts');
}

/* ── 6 ── no live count and no cached one: proceed ──────────────────────── */
async function testNoNumberAtAllProceeds() {
  const { app, calls } = bootShell({ health: { throws: true }, confirmAnswer: false });
  await app.logout();
  assert.strictEqual(calls.confirms, 0,
    'held the operator on the screen over a count nobody could produce');
  assert.strictEqual(calls.logoutPosts, 1, 'sign-out was blocked with no evidence of anything queued');
  console.log('PASS: no count obtainable -> signs out');
}

/* ── 7 ── the pre-login shell has no styled dialog; window.confirm stands in ─ */
async function testFallsBackToWindowConfirm() {
  const { app, calls } = bootShell({
    health: { data: CONFIGURED(2) }, confirmAnswer: null, windowConfirm: false,
  });
  await app.logout();
  assert.strictEqual(calls.confirms, 1,
    'with no RetailSystem._confirm loaded the operator was never asked at all');
  assert.strictEqual(calls.logoutPosts, 0, 'declining the fallback prompt still signed out');
  console.log('PASS: no styled dialog -> window.confirm fallback still guards');
}

/* ── 8 ── ANTI-VACUITY: the harness can actually observe both outcomes ───── */
async function testHarnessObservesBothOutcomes() {
  // Every assertion above rests on `calls.logoutPosts` moving. If the stub
  // fetch never saw /api/auth/logout at all, every "0" above would be
  // vacuously true and this suite would pass against a shell that does
  // nothing whatsoever. Prove the counter moves in the plain case.
  const { app, calls } = bootShell({ health: { data: CONFIGURED(0) }, confirmAnswer: false });
  assert.strictEqual(calls.logoutPosts, 0, 'counter dirty before the call');
  await app.logout();
  assert.strictEqual(calls.logoutPosts, 1,
    'the harness never observed a logout POST even in the unguarded case -- every zero above proves nothing');
  console.log('PASS: the harness observes a real logout POST (anti-vacuity)');
}

(async () => {
  const tests = [
    testDecliningKeepsTheSessionIntact,
    testAcceptingStillSignsOut,
    testUnconfiguredSyncNeverPrompts,
    testNothingQueuedNeverPrompts,
    testOfflineFallsBackToTheCachedCountNotToAllow,
    testNoNumberAtAllProceeds,
    testFallsBackToWindowConfirm,
    testHarnessObservesBothOutcomes,
  ];
  let failed = 0;
  for (const fn of tests) {
    try {
      await fn();
    } catch (e) {
      failed++;
      console.log(`FAIL ${fn.name} -- ${e.message}`);
    }
  }
  console.log('');
  if (failed) {
    console.log(`${failed} of ${tests.length} checks failed`);
    process.exit(1);
  }
  console.log(`PASS retail_signout_unsynced_guard_test.js -- ${tests.length} checks`);
})();
