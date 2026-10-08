/**
 * retail_update_ui_test.js -- the update channel's two front-end halves, run
 * straight against the real source in a node vm.
 *
 *   1. app-shell.js "Update ready" pill: the decision (_updatePillModel), the
 *      polling rules (a cashier never polls; "no Owner" stops polling for good)
 *      and the paint/dismiss behaviour.
 *   2. settings-hub.js: the Updates section is LISTED only when
 *      GET /api/updates/status says enabled, only for the owner, and sits
 *      right after Data & backup.
 *
 * Every guard is MUTATION-PROVED at the bottom: the source is broken on
 * purpose and the test must go red, else the file fails with MUTATION SURVIVED.
 *
 *   node products/retail/tests/retail_update_ui_test.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const FRONT = path.join(__dirname, '..', 'frontend');
const SHELL_SRC = fs.readFileSync(path.join(FRONT, 'app-shell.js'), 'utf8');
const HUB_SRC = fs.readFileSync(path.join(FRONT, 'settings-hub.js'), 'utf8');

function mutate(src, find, replace) {
  const hits = src.split(find).length - 1;
  assert.strictEqual(hits, 1, `Mutation anchor occurs ${hits} time(s), expected 1:\n  ${JSON.stringify(find)}`);
  return src.split(find).join(replace);
}

function stub() {
  const el = {
    innerHTML: '', textContent: '', value: '', id: '', className: '', style: {}, dataset: {}, children: [],
    classList: { toggle() {}, add() {}, remove() {} },
    appendChild() {}, addEventListener() {}, removeEventListener() {},
    remove() { el.removed = true; },
    insertBefore(node) { el.children.unshift(node); },
    get firstChild() { return el.children[0] || null; },
    getAttribute() { return null; }, setAttribute() {},
    querySelector() { return null; }, querySelectorAll() { return []; },
  };
  return el;
}

// ── shell ────────────────────────────────────────────────────────────────────
function loadShell(src, { fetchImpl, role = 'admin' } = {}) {
  const byId = {};
  const header = stub();
  const sandbox = {
    console, t: (s) => s, setTimeout, clearTimeout,
    setInterval: () => 1, clearInterval() {},
    navigator: { userAgent: 'x' }, location: { hash: '', href: 'http://localhost/' },
    localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
    AuraI18n: { current: 'en' },
    document: {
      readyState: 'complete', body: stub(), head: { appendChild() {} },
      documentElement: { getAttribute: () => null, setAttribute() {}, style: { setProperty() {} } },
      getElementById: (id) => (byId[id] && !byId[id].removed ? byId[id] : null),
      createElement() { const e = stub(); return new Proxy(e, { set(o, k, v) { o[k] = v; if (k === 'id') byId[v] = o; return true; } }); },
      querySelector: (sel) => (sel === '.sub-header-right' ? header : null),
      querySelectorAll() { return []; }, addEventListener() {},
    },
  };
  sandbox.fetch = fetchImpl;
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(src, sandbox, { filename: 'app-shell.js' });
  const App = sandbox.SubsystemApp;
  App.role = role;
  return { sb: sandbox, App, header };
}

const ENABLED = (over) => Object.assign({
  enabled: true, reason_disabled: null, current_version: '1.0.0-rc.8', phase: 'idle',
  available: { version: '1.0.0-rc.9', size_bytes: 10, required: false, below_min_supported: false },
}, over || {});

function testPillDecision(src) {
  const { App } = loadShell(src, { fetchImpl: async () => ({ ok: false }) });
  assert.strictEqual(App._updatePillModel(ENABLED()).show, true, 'an administrator with an offer must see the pill');
  assert.strictEqual(App._updatePillModel(ENABLED()).version, '1.0.0-rc.9');
  App.role = 'cashier';
  assert.strictEqual(App._updatePillModel(ENABLED()).show, false, 'a cashier must never see it (actions are admin-only)');
  App.role = 'manager';
  assert.strictEqual(App._updatePillModel(ENABLED()).show, false);
  App.role = 'admin';
  assert.strictEqual(App._updatePillModel(ENABLED({ enabled: false })).show, false);
  assert.strictEqual(App._updatePillModel(ENABLED({ available: null })).show, false);
  assert.strictEqual(App._updatePillModel(null).show, false);
  assert.strictEqual(App._updatePillModel(ENABLED({ phase: 'downloading' })).show, false);
  assert.strictEqual(App._updatePillModel(ENABLED({ phase: 'installing' })).show, false);
  assert.strictEqual(App._updatePillModel(ENABLED({ phase: 'ready' })).show, true);
  App._updatePillDismissed = '1.0.0-rc.9';
  assert.strictEqual(App._updatePillModel(ENABLED()).show, false, 'a dismissed version stays hidden');
  const newer = ENABLED({ available: { version: '1.0.0-rc.10', size_bytes: 1 } });
  assert.strictEqual(App._updatePillModel(newer).show, true, 'a NEWER version shows again after a dismissal');
  console.log('PASS: pill decision -- deny and allow halves');
}

async function testPillPollAndPaint(src) {
  const calls = [];
  const body = { value: ENABLED() };
  const fetchImpl = async (url) => { calls.push(url); return { ok: true, json: async () => body.value }; };
  const { App, header, sb } = loadShell(src, { fetchImpl });
  await App._pollUpdates();
  assert.deepStrictEqual(calls, ['/api/updates/status']);   // the poll RAN
  const wrap = sb.document.getElementById('aura-update-pill-wrap');
  assert.ok(wrap, 'the pill wrapper was not created');
  assert.ok(wrap.innerHTML.includes('Update ready') && wrap.innerHTML.includes('SubsystemApp._openUpdates()'));
  assert.strictEqual(header.children[0].id, 'aura-update-pill-wrap', 'the pill goes first in the header');
  App._dismissUpdatePill();
  assert.strictEqual(App._updatePillDismissed, '1.0.0-rc.9');
  await App._pollUpdates();
  assert.strictEqual(sb.document.getElementById('aura-update-pill-wrap'), null, 'dismissed pill came back on the next poll');

  // A cashier never polls at all.
  const c = loadShell(src, { fetchImpl, role: 'cashier' });
  const before = calls.length;
  c.App._startUpdatePoll();
  assert.strictEqual(calls.length, before, 'a cashier session made an update request');

  // "No Owner configured" stops polling for good.
  body.value = { enabled: false, reason_disabled: 'OWNER_NOT_CONFIGURED' };
  const d = loadShell(src, { fetchImpl });
  await d.App._pollUpdates();
  assert.strictEqual(d.App._updatePollDisabled, true);
  const n = calls.length;
  d.App._startUpdatePoll();
  assert.strictEqual(calls.length, n, 'polling continued after OWNER_NOT_CONFIGURED');
  // ...but NOT_ACTIVATED keeps polling (it flips when the shop activates).
  body.value = { enabled: false, reason_disabled: 'NOT_ACTIVATED' };
  const e = loadShell(src, { fetchImpl });
  await e.App._pollUpdates();
  assert.strictEqual(e.App._updatePollDisabled, false);
  console.log('PASS: pill poll, paint, dismiss; cashier and no-Owner never poll');
}

// ── hub ──────────────────────────────────────────────────────────────────────
async function hubSections(src, { statusBody, role = 'admin' }) {
  const gets = [];
  const sandbox = {
    console, t: (s) => s, setTimeout, clearTimeout,
    document: { getElementById: () => null, querySelectorAll: () => [], addEventListener() {}, documentElement: { getAttribute: () => null } },
    PlanDevices: { render() {} },
    SubsystemApp: { role, hasCapability: () => true },
    RetailSystem: { _esc: (s) => String(s), _get: async (u) => { gets.push(u); return statusBody; } },
    AuraI18n: { current: 'en' },
  };
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(src, sandbox, { filename: 'settings-hub.js' });
  await sandbox.SettingsHub.refreshUpdatesStatus();
  return { ids: Array.from(sandbox.SettingsHub.sections()).map((s) => s.id), gets };
}

async function testHubSections(src) {
  const LEGACY = ['shop-receipt', 'tax', 'credit', 'tills', 'extras', 'hardware', 'display', 'team', 'data-backup', 'plan-devices'];
  let r = await hubSections(src, { statusBody: { enabled: false, reason_disabled: 'OWNER_NOT_CONFIGURED' } });
  assert.deepStrictEqual(r.gets, ['/api/updates/status']);       // the status was asked for
  assert.deepStrictEqual(r.ids, LEGACY, 'a disabled channel must leave the section list exactly as it was');
  r = await hubSections(src, { statusBody: ENABLED() });
  assert.deepStrictEqual(r.ids, ['shop-receipt', 'tax', 'credit', 'tills', 'extras', 'hardware', 'display', 'team', 'data-backup', 'updates', 'plan-devices']);
  r = await hubSections(src, { statusBody: ENABLED(), role: 'manager' });
  assert.ok(!r.ids.includes('updates'), 'a non-owner must not be offered Updates');
  console.log('PASS: Updates is listed only when enabled, only for the owner, right after Data & backup');
}

function testHubCallsTheRealRoutes(src) {
  for (const url of ['/api/updates/check', '/api/updates/download', '/api/updates/install']) {
    assert.ok(src.includes(url), `settings-hub.js never calls ${url}`);
  }
  assert.ok(/quit_app/.test(src), 'the hub must close the window after a successful install request');
  console.log('PASS: the hub calls check / download / install');
}

(async () => {
  await testPillDecision(SHELL_SRC);
  await testPillPollAndPaint(SHELL_SRC);
  await testHubSections(HUB_SRC);
  testHubCallsTheRealRoutes(HUB_SRC);

  // ── mutation proofs ──
  const proofs = [
    ['pill shown to non-admins', SHELL_SRC, "if (this.role !== 'admin') return { show: false };\n    if (!status", "if (!status", async (s) => testPillDecision(s)],
    ['dismissal ignored', SHELL_SRC, "version === this._updatePillDismissed", "false", async (s) => testPillDecision(s)],
    ['cashier polls', SHELL_SRC, "if (this.role !== 'admin' || this._updatePollDisabled) return;", "if (this._updatePollDisabled) return;", async (s) => testPillPollAndPaint(s)],
    ['no-Owner never stops polling', SHELL_SRC, "this._updatePollDisabled = true;", "this._updatePollDisabled = false;", async (s) => testPillPollAndPaint(s)],
    ['disabled channel still lists Updates', HUB_SRC, ".filter((s) => s.id !== 'updates' || s.visible)", "", async (s) => testHubSections(s)],
    ['non-owner sees Updates', HUB_SRC, "visible: owner && !!S.updates }", "visible: !!S.updates }", async (s) => testHubSections(s)],
  ];
  for (const [name, src, find, repl, run] of proofs) {
    let survived = false;
    try { await run(mutate(src, find, repl)); survived = true; } catch (e) { /* red, as required */ }
    assert.ok(!survived, `MUTATION SURVIVED: ${name}`);
    console.log(`PROOF: ${name} -> test goes red`);
  }
})().catch((e) => { console.error(e); process.exit(1); });
