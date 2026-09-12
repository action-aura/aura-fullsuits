/**
 * retail_einvoicing_status_test.js — the e-invoicing status card's "which
 * provider is actually running" fix (commercial_runtime/einvoicing/routes.py
 * + products/retail/frontend/einvoicing.js).
 *
 * The /status endpoint used to report the provider by reading the SETTING
 * (settings.get_setting(conn, cid, 'provider'), default string 'mock'),
 * never the object actually passed into make_einvoicing_blueprint(...) by
 * each product's app.py. The two can disagree -- and with e-invoicing
 * becoming ON by default in Jordan, the common case for a fresh shop is
 * about to be: enabled, documents queueing, nothing submitting, because
 * nobody has registered on the JoFotara portal yet. Showing "Enabled,
 * Provider: mock, Queued: 47" in that state tells a shopkeeper nothing they
 * can act on -- one of those three facts is also outright wrong.
 *
 * This file drives the REAL einvoicing.js (never a reimplementation) against
 * a stub /api/einvoicing/status response and inspects the rendered
 * #status-content tree, proving:
 *
 *   1. provider_configured: false -> the not-connected notice renders and
 *      "Submit queue now" is disabled with an explanatory title.
 *   2. provider_configured: true -> neither of those (allow-half of 1).
 *   3. provider_in_use: 'unconfigured' -> the provider row reads
 *      "Not connected" (PROVIDER_LABELS lookup).
 *   4. A payload with NEITHER new key (an older backend) still renders the
 *      card and still shows the old `provider` value -- backward compatible.
 *   5. [MUTATION-PROVED] a build of renderStatus() that ignores
 *      provider_configured is made to fail check 1, then the real file is
 *      restored and re-proved to pass it.
 *
 * WHY THIS DOM IS SIMPLER THAN retail_join_shop_modal_test.js's
 *
 * einvoicing.js never calls querySelector/querySelectorAll on its own
 * rendered output and never re-reads a node by id after building it (each
 * card's content is torn down with clearChildren() and rebuilt from
 * scratch every refresh) -- so plain object nodes with appendChild/
 * removeChild/textContent, walked by a small recursive `findAll`, are
 * enough to prove everything above; no live-DOM selector engine needed.
 *
 * einvoicing.js is a self-invoking IIFE with no window export (unlike
 * app-shell.js's window.SubsystemApp) -- it kicks off
 * Promise.all([refreshStatus(), refreshSettings(), refreshCredentials(),
 * refreshOutbox()]) itself at load time. So each test loads a fresh vm
 * context, lets those promises settle against a stubbed fetch, then reads
 * back the #status-content tree the script itself built.
 *
 * Run: node products/retail/tests/retail_einvoicing_status_test.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const FRONTEND_DIR = path.join(__dirname, '..', 'frontend');
const SCRIPT_FILE = path.join(FRONTEND_DIR, 'einvoicing.js');
const SCRIPT_SRC = fs.readFileSync(SCRIPT_FILE, 'utf8');

// ─────────────────────────────────────────────────────────────────────────────
// Mutation harness (same shape as retail_join_shop_modal_test.js /
// retail_branches_test.js)
// ─────────────────────────────────────────────────────────────────────────────

function eolOf(src) { return src.indexOf('\r\n') !== -1 ? '\r\n' : '\n'; }
function nlFor(src) { const eol = eolOf(src); return (s) => s.replace(/\n/g, eol); }

function mutate(src, pairs) {
  const nl = nlFor(src);
  let out = src;
  for (const [rawFind, rawReplace] of pairs) {
    const find = nl(rawFind);
    const replace = nl(rawReplace);
    const hits = out.split(find).length - 1;
    assert.strictEqual(
      hits, 1,
      `Mutation anchor occurs ${hits} time(s), expected exactly 1:\n  ${JSON.stringify(find)}\n\n` +
      'A mutation that no longer applies would let the proof below pass while proving nothing. Re-anchor it.'
    );
    out = out.replace(find, replace);
  }
  return out;
}

async function provesMutation(what, check) {
  let threw = null;
  try {
    await check();
  } catch (err) {
    threw = err;
  }
  assert.ok(
    threw,
    `MUTATION SURVIVED — ${what}\n` +
    'The guard for this passed against a build with the behaviour deliberately broken, so it is ' +
    'not actually watching it. Fix the check, not the mutation.'
  );
  return { message: `${what}  [caught: ${String(threw.message || threw).split('\n')[0].slice(0, 140)}]` };
}

// ─────────────────────────────────────────────────────────────────────────────
// Minimal node tree — enough for appendChild/removeChild/textContent/
// disabled/title/className to agree with each other and be walked
// afterward. Not a selector engine: einvoicing.js never needs one (see file
// header).
// ─────────────────────────────────────────────────────────────────────────────

function makeNode(tag) {
  const node = {
    tag: (tag || 'div').toLowerCase(),
    id: '',
    className: '',
    disabled: false,
    title: '',
    type: '',
    value: '',
    placeholder: '',
    checked: false,
    htmlFor: '',
    children: [],
    _text: '',
    _listeners: {},
    get firstChild() { return this.children.length ? this.children[0] : null; },
    get textContent() {
      if (!this.children.length) return this._text;
      return this.children.map((c) => c.textContent).join('');
    },
    set textContent(v) { this._text = v == null ? '' : String(v); this.children = []; },
    appendChild(child) { this.children.push(child); return child; },
    removeChild(child) {
      const i = this.children.indexOf(child);
      if (i !== -1) this.children.splice(i, 1);
      return child;
    },
    addEventListener(type, fn) { this._listeners[type] = fn; },
  };
  return node;
}

function findAll(root, predicate) {
  const out = [];
  const walk = (n) => {
    if (predicate(n)) out.push(n);
    for (const c of n.children) walk(c);
  };
  walk(root);
  return out;
}

// ─────────────────────────────────────────────────────────────────────────────
// fetch fixture — routes keyed "METHOD /path" (query string stripped, same
// as retail_join_shop_modal_test.js), handler(callCount) -> body
// ─────────────────────────────────────────────────────────────────────────────

function makeFetch(routes) {
  const fn = (url, init) => {
    const method = ((init && init.method) || 'GET').toUpperCase();
    const u = String(url).split('?')[0];
    const key = method + ' ' + u;
    const handler = routes[key];
    if (!handler) {
      return Promise.resolve({ status: 404, json: () => Promise.resolve({}) });
    }
    const body = typeof handler === 'function' ? handler() : handler;
    return Promise.resolve({ status: 200, json: () => Promise.resolve(body) });
  };
  return fn;
}

// ─────────────────────────────────────────────────────────────────────────────
// Sandbox loader — the REAL einvoicing.js. It has no window export: it
// drives itself via Promise.all([...]) at load time, so we load it, let the
// microtask queue settle, then read back #status-content.
// ─────────────────────────────────────────────────────────────────────────────

async function settle() {
  for (let i = 0; i < 8; i++) await Promise.resolve();
  await new Promise((resolve) => setImmediate(resolve));
}

async function loadAndRenderStatus(statusData, opts) {
  const o = opts || {};
  const scriptSrc = o.scriptSrc || SCRIPT_SRC;

  const ids = ['message-area', 'status-content', 'settings-content', 'credentials-content', 'outbox-content'];
  const elementsById = {};
  for (const id of ids) {
    const n = makeNode('div');
    n.id = id;
    elementsById[id] = n;
  }

  const doc = {
    getElementById: (id) => elementsById[id] || null,
    createElement: (tag) => makeNode(tag),
  };

  const fetchFn = makeFetch({
    'GET /api/einvoicing/status': () => ({ status: 'success', data: statusData }),
    ...(o.routes || {}),
  });

  const sandbox = {
    console,
    document: doc,
    fetch: fetchFn,
    window: {},
  };
  sandbox.window = sandbox;

  vm.createContext(sandbox);
  vm.runInContext(scriptSrc, sandbox, { filename: SCRIPT_FILE });
  await settle();

  return { statusContent: elementsById['status-content'], doc };
}

// ─────────────────────────────────────────────────────────────────────────────
// Fixtures — real payload shapes per routes.py's /status handler.
// ─────────────────────────────────────────────────────────────────────────────

const BASE_STATUS = {
  enabled: true,
  killswitch: { disabled: false, reason: null },
  counts_by_state: { QUEUED: 3, CLEARED: 12 },
  oldest_pending_created_at: null,
};

function findRunBtn(statusContent) {
  return findAll(statusContent, (n) => n.tag === 'button' && n.textContent === 'Submit queue now')[0];
}

function findNotice(statusContent) {
  // The badge helper always prefixes className with 'state-badge ' (see
  // stateBadge() in einvoicing.js) -- an exact 'state-warning' className
  // (no 'state-badge' prefix) can only be the standalone notice div, never
  // the killswitch-paused badge, which is also styled with state-warning.
  return findAll(statusContent, (n) => n.className === 'state-warning')[0];
}

function findProviderRow(statusContent) {
  const dl = findAll(statusContent, (n) => n.tag === 'dl')[0];
  assert.ok(dl, 'No <dl> rendered in the status card.');
  const dts = dl.children.filter((c) => c.tag === 'dt');
  const dds = dl.children.filter((c) => c.tag === 'dd');
  const idx = dts.findIndex((dt) => dt.textContent === 'Provider');
  assert.notStrictEqual(idx, -1, 'No "Provider" row rendered in the status card.');
  return dds[idx];
}

// ─────────────────────────────────────────────────────────────────────────────
// (1) provider_configured: false -> notice renders, submit button disabled
//     with an explanatory title [MUTATION-PROVED below]
// ─────────────────────────────────────────────────────────────────────────────

async function notConnectedShowsNoticeAndDisablesButton(scriptSrc) {
  const { statusContent } = await loadAndRenderStatus({
    ...BASE_STATUS,
    provider: 'jofotara',
    provider_in_use: 'jofotara',
    provider_configured: false,
  }, { scriptSrc });

  const notice = findNotice(statusContent);
  assert.ok(notice, 'No not-connected notice rendered when provider_configured is false.');
  assert.ok(/not connected to JoFotara/i.test(notice.textContent),
    `Notice text did not mention being disconnected from JoFotara: ${JSON.stringify(notice.textContent)}`);
  assert.ok(/JoFotara portal/i.test(notice.textContent) && /Client-ID/i.test(notice.textContent),
    'Notice text did not tell the shop owner how to connect (portal + Client-ID/Secret-Key/activity number).');

  const runBtn = findRunBtn(statusContent);
  assert.ok(runBtn, 'No "Submit queue now" button rendered.');
  assert.strictEqual(runBtn.disabled, true, 'The "Submit queue now" button was not disabled while not connected.');
  assert.ok(runBtn.title && runBtn.title.length > 0,
    'The disabled "Submit queue now" button carries no title explaining why it is disabled.');
}

async function testNotConnectedShowsNoticeAndDisablesButton() {
  await notConnectedShowsNoticeAndDisablesButton();
  console.log('PASS: provider_configured=false shows the not-connected notice and disables "Submit queue now"');
}

async function testMutationProvedNoticeGuard() {
  // Neutralise the guard's condition so the notice/disable branch can never
  // fire, regardless of provider_configured -- the exact defect this test
  // exists to catch.
  const mutated = mutate(SCRIPT_SRC, [[
    `    const notConnected = data.provider_configured === false;`,
    `    const notConnected = false; /* mutant: ignore provider_configured */`,
  ]]);
  const { message } = await provesMutation(
    'not-connected notice/button-disable check survived a mutant that ignores provider_configured',
    () => notConnectedShowsNoticeAndDisablesButton(mutated)
  );
  // Re-prove against the REAL file to show the guard passes once restored --
  // required so this file cannot end on the mutated (broken) copy.
  await notConnectedShowsNoticeAndDisablesButton();
  console.log('PASS: ' + message);
  console.log('PASS: guard passes again against the restored, unmutated einvoicing.js');
}

// ─────────────────────────────────────────────────────────────────────────────
// (2) provider_configured: true -> neither the notice nor the disabled
//     button appear (allow-half of 1)
// ─────────────────────────────────────────────────────────────────────────────

async function testConfiguredShowsNoNoticeAndEnabledButton() {
  const { statusContent } = await loadAndRenderStatus({
    ...BASE_STATUS,
    provider: 'jofotara',
    provider_in_use: 'jofotara',
    provider_configured: true,
  });

  assert.strictEqual(findNotice(statusContent), undefined,
    'The not-connected notice rendered even though provider_configured is true.');
  const runBtn = findRunBtn(statusContent);
  assert.ok(runBtn, 'No "Submit queue now" button rendered.');
  assert.strictEqual(runBtn.disabled, false,
    'The "Submit queue now" button was disabled even though provider_configured is true.');
  console.log('PASS: provider_configured=true shows neither the notice nor a disabled submit button');
}

// ─────────────────────────────────────────────────────────────────────────────
// (3) provider_in_use: 'unconfigured' -> the provider row reads
//     "Not connected"
// ─────────────────────────────────────────────────────────────────────────────

async function testUnconfiguredProviderInUseLabel() {
  const { statusContent } = await loadAndRenderStatus({
    ...BASE_STATUS,
    provider: 'jofotara',
    provider_in_use: 'unconfigured',
    provider_configured: false,
  });

  const providerRow = findProviderRow(statusContent);
  assert.strictEqual(providerRow.textContent, 'Not connected',
    `Expected the provider row to read "Not connected", got: ${JSON.stringify(providerRow.textContent)}`);
  console.log('PASS: provider_in_use="unconfigured" renders the provider row as "Not connected"');
}

// ─────────────────────────────────────────────────────────────────────────────
// (4) Backward compatibility — a payload with NEITHER new key (an older
//     backend) still renders the card and still shows the old `provider`
//     value
// ─────────────────────────────────────────────────────────────────────────────

async function testBackwardCompatibleWithOlderBackend() {
  const oldPayload = { ...BASE_STATUS, provider: 'mock' };
  // Prove the fixture itself carries neither new key -- otherwise this
  // "older backend" case would silently exercise the new-field path instead.
  assert.ok(!('provider_in_use' in oldPayload) && !('provider_configured' in oldPayload),
    'Test fixture bug: the "older backend" payload accidentally carries a new key.');

  const { statusContent } = await loadAndRenderStatus(oldPayload);

  assert.strictEqual(findNotice(statusContent), undefined,
    'The not-connected notice rendered against a payload with no provider_configured key at all.');
  const runBtn = findRunBtn(statusContent);
  assert.ok(runBtn, 'No "Submit queue now" button rendered against an older backend payload.');
  assert.strictEqual(runBtn.disabled, false,
    'The "Submit queue now" button was disabled against an older backend payload with no provider_configured key.');
  const providerRow = findProviderRow(statusContent);
  assert.strictEqual(providerRow.textContent, 'Test mode — no live submissions',
    `Expected the old 'provider' value ("mock") to still render via PROVIDER_LABELS, got: ${JSON.stringify(providerRow.textContent)}`);
  console.log('PASS: a payload with neither new key still renders the card using the old provider value');
}

// ─────────────────────────────────────────────────────────────────────────────

const CHECKS = [
  ['provider_configured=false shows the notice and disables the submit button', testNotConnectedShowsNoticeAndDisablesButton],
  ['provider_configured=true shows neither', testConfiguredShowsNoNoticeAndEnabledButton],
  ['provider_in_use="unconfigured" renders "Not connected"', testUnconfiguredProviderInUseLabel],
  ['backward compatible with an older backend payload', testBackwardCompatibleWithOlderBackend],
  ['not-connected guard is mutation-proved', testMutationProvedNoticeGuard],
];

async function main() {
  const failures = [];
  for (const [name, fn] of CHECKS) {
    try {
      await fn();
    } catch (err) {
      failures.push(name);
      console.error(`FAIL: ${name}`);
      console.error('      ' + String((err && err.stack) || err).replace(/\n/g, '\n      '));
    }
  }
  if (failures.length) {
    console.error(`\nFAIL: retail_einvoicing_status_test.js — ${failures.length} of ${CHECKS.length} checks failed:`);
    for (const name of failures) console.error(`  - ${name}`);
    process.exitCode = 1;
    return;
  }
  console.log(`PASS: retail_einvoicing_status_test.js — ${CHECKS.length} checks`);
}

main().catch((err) => {
  console.error('FAIL: retail_einvoicing_status_test.js (runner)');
  console.error(err);
  process.exitCode = 1;
});
