/**
 * retail_attestation_checkin_test.js -- AUDIT-033 fix round (verifier V
 * finding #1), the shell half.
 *
 * Owner pins this computer's shop-authority id on the first check-in that
 * claims it, and signs it into the lease. An installation that predates this
 * release has a lease that does not name the id (status field
 * `registry_device_attested === false`), so it cannot sign authority changes
 * and the pin is first-come-first-served. Waiting for the 5-minute poll widens
 * that window for nothing, so the shell asks Owner ONCE, right at boot, while
 * attestation is pending.
 *
 * This RUNS SubsystemApp._checkInNowIfUnattested() against a stubbed fetch and
 * counts the POSTs to /api/licensing/check-in, both directions:
 *   - pending attestation + licensing configured + activated  -> exactly one;
 *   - already attested, no Owner configured, not activated    -> none
 *     (an install that never opted in, or already settled, pays nothing);
 *   - asked twice in one page load                            -> still one.
 *
 *     node products/retail/tests/retail_attestation_checkin_test.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const SHELL_SRC = fs.readFileSync(path.join(__dirname, '..', 'frontend', 'app-shell.js'), 'utf8');

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

function bootShell(src) {
  const sandbox = {
    console: { log() {}, warn() {}, error() {} },
    document: {
      readyState: 'complete', body: fakeElement(), documentElement: fakeElement(), head: fakeElement(),
      getElementById() { return null; }, querySelector() { return null; }, querySelectorAll() { return []; },
      createElement() { return fakeElement(); }, addEventListener() {},
    },
    localStorage: { getItem() { return null; }, setItem() {}, removeItem() {} },
    sessionStorage: { getItem() { return null; }, setItem() {}, removeItem() {} },
    setTimeout, clearTimeout, setInterval, clearInterval,
    location: { href: '', hash: '', reload() {} },
    navigator: { language: 'en', userAgent: 'node' },
    matchMedia: () => ({ matches: false, addEventListener() {}, addListener() {} }),
    requestAnimationFrame: (fn) => setTimeout(fn, 0),
    CustomEvent: function () {}, Event: function () {}, BroadcastChannel: undefined,
    t: (s) => s,
    async fetch() { return { ok: true, status: 200, json: async () => ({}) }; },
  };
  sandbox.window = sandbox;
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(src || SHELL_SRC, sandbox, { filename: 'app-shell.js' });
  const app = sandbox.SubsystemApp;
  assert.ok(app && typeof app._checkInNowIfUnattested === 'function',
    'app-shell.js has no SubsystemApp._checkInNowIfUnattested -- this harness has nothing to test');
  return { app, sandbox };
}

/** Runs the method against a status payload and returns how many check-in POSTs it made. */
async function checkInsFor(status, { calls = 1 } = {}) {
  const { app, sandbox } = bootShell();
  let posts = 0;
  sandbox.fetch = async (url, opts) => {
    if (String(url).includes('/api/licensing/check-in')) {
      assert.strictEqual((opts || {}).method, 'POST');
      posts += 1;
      return { ok: true, status: 200, json: async () => ({ current_state: 'ACTIVE_ONLINE' }) };
    }
    return { ok: true, status: 200, json: async () => status };
  };
  for (let i = 0; i < calls; i += 1) await app._checkInNowIfUnattested();
  return posts;
}

const BASE = { owner_configured: true, installation_id: 'inst-1', current_state: 'ACTIVE_ONLINE' };

(async () => {
  assert.strictEqual(await checkInsFor({ ...BASE, registry_device_attested: false }), 1,
    'an activated, unattested install must ask Owner once at boot');
  assert.strictEqual(await checkInsFor({ ...BASE, registry_device_attested: false }, { calls: 3 }), 1,
    'asked several times in one page load it must still call Owner only once');
  assert.strictEqual(await checkInsFor({ ...BASE, registry_device_attested: true }), 0,
    'an attested install must not make the extra call');
  assert.strictEqual(await checkInsFor({ ...BASE }), 0,
    'an old backend (no flag at all) must not make the extra call');
  assert.strictEqual(await checkInsFor({ ...BASE, owner_configured: false, registry_device_attested: false }), 0,
    'an install with no Owner configured must never call');
  assert.strictEqual(await checkInsFor({ ...BASE, installation_id: undefined, registry_device_attested: false }), 0,
    'an install that has not activated has nothing to check in');

  // The wiring: boot must actually ASK (a method nothing calls proves nothing).
  const start = /_startLicenseCheckInPoll\(\) \{[\s\S]*?\n  \},/.exec(SHELL_SRC);
  assert.ok(start && /this\._checkInNowIfUnattested\(\)/.test(start[0]),
    '_startLicenseCheckInPoll must call _checkInNowIfUnattested()');

  // A failing status call must never break boot.
  const { app, sandbox } = bootShell();
  sandbox.fetch = async () => { throw new Error('offline'); };
  await app._checkInNowIfUnattested();

  console.log('retail_attestation_checkin_test: all assertions passed');
})().catch((e) => { console.error(e); process.exit(1); });
