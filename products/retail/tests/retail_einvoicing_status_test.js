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
 * P0 #18 ADDS A SECOND, LARGER DEFECT THIS FILE NOW ALSO PROVES CLOSED: the
 * not-connected notice above fired on `provider_configured === false` ALONE,
 * with no regard for `data.enabled` -- so on a FRESH install (tax_regime
 * defaults to 'none', so `enabled` is already false) the card said
 * "E-invoicing is on and every sale is being recorded, but nothing has been
 * sent..." for a shop that had never touched the feature. Pause/Submit
 * rendered too, controlling a queue that could not be running. Fixed by
 * gating the whole rest of the card on `data.enabled`, with one neutral line
 * ("E-invoicing is off for this shop...") in its place, and by re-wording the
 * not-connected notice itself to never say "waiting" (no queue is
 * progressing toward delivery in this version) or "compliant" (an
 * unconnected install files nothing, so calling it compliant would be false).
 *
 * This file drives the REAL einvoicing.js (never a reimplementation) against
 * stub /api/einvoicing/status and /api/einvoicing/settings responses and
 * inspects the rendered #status-content / #settings-content trees, proving:
 *
 *   1. provider_configured: false (with enabled: true) -> the not-connected
 *      notice renders with the exact required wording, and "Submit queue
 *      now" is disabled with an explanatory title.
 *   2. provider_configured: true -> neither of those (allow-half of 1).
 *   3. provider_in_use: 'unconfigured' -> the provider row reads
 *      "Not connected" (PROVIDER_LABELS lookup).
 *   4. A payload with NEITHER new key (an older backend) still renders the
 *      card and still shows the old `provider` value -- backward compatible.
 *   5. [MUTATION-PROVED] a build of renderStatus() that ignores
 *      provider_configured is made to fail check 1, then the real file is
 *      restored and re-proved to pass it.
 *   6. enabled: false -> ONLY the Disabled pill and the neutral off-line
 *      render; no "is on" text, no Submit button, no Pause/Resume button.
 *      [MUTATION-PROVED] deny-half.
 *   7. Neither "waiting" nor "compliant" appears anywhere in #status-content
 *      OR #settings-content, across every reachable state (not-connected,
 *      connected, disabled, paused). [MUTATION-PROVED, P0 review-fix -- the
 *      original version of this check only ever read the not-connected
 *      notice's own textContent, which could not see a forbidden word
 *      injected anywhere else on the card.]
 *   8. Settings: the enable checkbox is disabled (with a reason) while
 *      tax_regime is not 'jordan', and enabled (allow-half) once it is.
 *      [MUTATION-PROVED]
 *   9. Settings: the enable checkbox reads UNCHECKED against the REAL
 *      fresh-install defaults (tax_regime:'none', enabled:'1' --
 *      commercial_runtime/einvoicing/settings.py's own DEFAULTS), not just
 *      against this file's zeroed BASE_SETTINGS fixture. [MUTATION-PROVED,
 *      P0 review-fix]
 *  10. einvoicing.html's static subtitle (rendered before any fetch
 *      resolves) never claims a fixed on/off default. [MUTATION-PROVED,
 *      P0 review-fix]
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
    // P0 #18 -- exercising renderSettings() for real (the enable-checkbox
    // tests below) reaches `toggleLabel.style.margin = '0'`, real DOM's
    // ordinary style-object assignment. A plain object is enough: nothing
    // here reads style back, it only needs to not throw.
    style: {},
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

  return {
    statusContent: elementsById['status-content'],
    settingsContent: elementsById['settings-content'],
    doc,
  };
}

// Settings-card variant of the loader above: same sandbox/settle machinery,
// but stubs GET /api/einvoicing/settings (settings-content's own source)
// instead of GET /api/einvoicing/status. Kept separate rather than
// overloading loadAndRenderStatus's signature further -- the two cards read
// different routes and the settings tests below never care about status.
async function loadAndRenderSettings(settings, opts) {
  const o = opts || {};
  return loadAndRenderStatus(BASE_STATUS, {
    ...o,
    routes: {
      'GET /api/einvoicing/settings': () => ({ status: 'success', data: { settings, credentials: { configured: false } } }),
      ...(o.routes || {}),
    },
  });
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
  assert.ok(notice, 'No not-connected notice rendered when enabled is true and provider_configured is false.');
  // P0 #18 -- exact required wording, never "waiting" (implies a queue is
  // progressing toward delivery -- it is not, in this version) and never
  // "compliant" (an unconnected install files nothing with ISTD).
  assert.ok(/Not being reported to JoFotara\. Sending is not available in this version\./.test(notice.textContent),
    `Notice text did not carry the required sentence: ${JSON.stringify(notice.textContent)}`);
  assert.ok(!/waiting/i.test(notice.textContent), `Notice text says "waiting": ${JSON.stringify(notice.textContent)}`);
  assert.ok(!/compliant/i.test(notice.textContent), `Notice text says "compliant": ${JSON.stringify(notice.textContent)}`);
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
  console.log('PASS: provider_configured=false (enabled=true) shows the not-connected notice, with the required wording, and disables "Submit queue now"');
}

async function testMutationProvedNoticeGuard() {
  // Neutralise the guard's condition so the notice/disable branch can never
  // fire, regardless of provider_configured -- the exact defect this test
  // exists to catch.
  //
  // P0 review-fix (2026-09-27) -- this anchor used to quote
  // `data.enabled === true && data.provider_configured === false`. A
  // reviewer traced the `data.enabled === true &&` half and found it was
  // dead code: by the line it appears on, `data.enabled` can only ever be
  // truthy already (the `if (!data.enabled) return;` a few lines above has
  // left the function otherwise), so einvoicing.js simplified the condition
  // to the one clause that can actually vary here. That correctly left this
  // guard's OWN check red (a mutation anchor that no longer occurs anywhere
  // throws loudly -- see mutate() above -- rather than silently matching 0
  // times and reporting a false pass), so re-anchoring it on the simplified
  // line is what actually restores the proof rather than papering over it.
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
// (6) enabled: false -> ONLY the Disabled pill and the neutral off-line;
//     no "is on" text, no Submit button, no Pause/Resume button
//     [MUTATION-PROVED below -- deny-half of the enabled/disabled split]
// ─────────────────────────────────────────────────────────────────────────────

function findBadgeText(statusContent, text) {
  return findAll(statusContent, (n) => n.className && n.className.indexOf('state-badge') === 0 && n.textContent === text)[0];
}

async function disabledShowsOnlyNeutralLine(scriptSrc) {
  const { statusContent } = await loadAndRenderStatus({
    ...BASE_STATUS,
    enabled: false,
    provider: 'jofotara',
    provider_in_use: 'unconfigured',
    provider_configured: false,
  }, { scriptSrc });

  assert.ok(findBadgeText(statusContent, 'Disabled'), 'No "Disabled" pill rendered when enabled is false.');
  assert.ok(!/is on/i.test(statusContent.textContent),
    `Status card said the feature "is on" while enabled is false: ${JSON.stringify(statusContent.textContent)}`);
  assert.ok(/E-invoicing is off for this shop\. Nothing is recorded or sent\./.test(statusContent.textContent),
    `The required neutral off-line did not render: ${JSON.stringify(statusContent.textContent)}`);
  assert.strictEqual(findRunBtn(statusContent), undefined,
    '"Submit queue now" rendered while e-invoicing is disabled -- there is no queue to submit.');
  assert.strictEqual(findAll(statusContent, (n) => n.tag === 'button').length, 0,
    'A button rendered on the disabled card -- Pause/Resume/Submit must all be hidden while disabled.');
}

async function testDisabledShowsOnlyNeutralLine() {
  await disabledShowsOnlyNeutralLine();
  console.log('PASS: enabled=false renders only the Disabled pill and the neutral off-line, with no buttons and no "is on" text');
}

async function testMutationProvedDisabledGuard() {
  // Neutralise the early return so the rest of the card renders regardless
  // of data.enabled -- exactly the pre-fix defect (Pause/Submit controlling
  // a queue that cannot be running, on every fresh install).
  const mutated = mutate(SCRIPT_SRC, [[
    `    if (!data.enabled) {\n      statusContent.appendChild(el('p', {\n        className: 'hint',\n        text: t('E-invoicing is off for this shop. Nothing is recorded or sent.'),\n      }));\n      return;\n    }`,
    `    if (false) { /* mutant: never treat e-invoicing as disabled */ }`,
  ]]);
  const { message } = await provesMutation(
    'disabled-state neutral-line guard survived a mutant that never short-circuits on data.enabled=false',
    () => disabledShowsOnlyNeutralLine(mutated)
  );
  await disabledShowsOnlyNeutralLine();
  console.log('PASS: ' + message);
  console.log('PASS: guard passes again against the restored, unmutated einvoicing.js');
}

// ─────────────────────────────────────────────────────────────────────────────
// (8) Settings: the enable checkbox is disabled, with a reason, while
//     tax_regime is not 'jordan'; enabled once it is (allow-half)
//     [MUTATION-PROVED below]
// ─────────────────────────────────────────────────────────────────────────────

const BASE_SETTINGS = {
  tax_regime: 'none',
  enabled: '0',
  invoice_family: 'income',
  seller_name: '',
  seller_tin: '',
  currency: 'JOD',
};

function findEnableToggle(settingsContent) {
  return findAll(settingsContent, (n) => n.id === 'einv-enabled')[0];
}

async function enableBoxDisabledWhileRegimeNotJordan(scriptSrc) {
  const { settingsContent } = await loadAndRenderSettings({ ...BASE_SETTINGS, tax_regime: 'none' }, { scriptSrc });
  const toggle = findEnableToggle(settingsContent);
  assert.ok(toggle, 'No #einv-enabled checkbox rendered.');
  assert.strictEqual(toggle.disabled, true, 'The enable checkbox was not disabled while tax_regime is "none".');
  assert.ok(toggle.title && toggle.title.length > 0,
    'The disabled enable checkbox carries no title/reason explaining why.');
  assert.ok(/tax regime.*Jordan/i.test(settingsContent.textContent),
    `Settings card did not surface the reason as visible text: ${JSON.stringify(settingsContent.textContent)}`);
}

async function testEnableBoxDisabledWhileRegimeNotJordan() {
  await enableBoxDisabledWhileRegimeNotJordan();
  console.log('PASS: tax_regime="none" disables the enable checkbox with a visible reason');
}

async function testEnableBoxEnabledWhenRegimeIsJordan() {
  const { settingsContent } = await loadAndRenderSettings({ ...BASE_SETTINGS, tax_regime: 'jordan' });
  const toggle = findEnableToggle(settingsContent);
  assert.ok(toggle, 'No #einv-enabled checkbox rendered.');
  assert.strictEqual(toggle.disabled, false, 'The enable checkbox was disabled even though tax_regime is "jordan".');
  console.log('PASS: tax_regime="jordan" leaves the enable checkbox enabled (allow-half)');
}

async function testMutationProvedEnableBoxGuard() {
  const mutated = mutate(SCRIPT_SRC, [[
    `    const regimeIsJordan = settings.tax_regime === 'jordan';`,
    `    const regimeIsJordan = true; /* mutant: always treat the regime as Jordan */`,
  ]]);
  const { message } = await provesMutation(
    'enable-checkbox disable guard survived a mutant that always treats the regime as Jordan',
    () => enableBoxDisabledWhileRegimeNotJordan(mutated)
  );
  await enableBoxDisabledWhileRegimeNotJordan();
  console.log('PASS: ' + message);
  console.log('PASS: guard passes again against the restored, unmutated einvoicing.js');
}

// ─────────────────────────────────────────────────────────────────────────────
// (9) [P0 review-fix] Settings: the enable checkbox reads UNCHECKED against
//     the REAL fresh-install defaults, not just against this file's own
//     zeroed BASE_SETTINGS fixture. commercial_runtime/einvoicing/
//     settings.py's own DEFAULTS is {tax_regime:'none', enabled:'1'} -- the
//     raw toggle bit defaults ON even though the regime defaults OFF -- but
//     BASE_SETTINGS above hardcodes enabled:'0', a combination a real fresh
//     install never has. That is exactly ENGINEERING.md's failure shape #2
//     (the fixture manufactures away the one state that would show the
//     bug): every check above this one could stay green while a fresh
//     install rendered a TICKED, greyed-out box sitting right next to the
//     "Disabled" status pill -- two true-on-their-own facts that read as a
//     contradiction to whoever opens this page, measured live 2026-09-27
//     (see the item's own evidence). [MUTATION-PROVED below]
// ─────────────────────────────────────────────────────────────────────────────

const FRESH_INSTALL_SETTINGS = { ...BASE_SETTINGS, tax_regime: 'none', enabled: '1' };

async function enableBoxUncheckedOnFreshInstallDefaults(scriptSrc) {
  // Prove the fixture itself actually IS the real defaults, not a second
  // hand-typed guess that could silently drift from settings.py -- otherwise
  // this test would only be checking itself.
  assert.strictEqual(FRESH_INSTALL_SETTINGS.tax_regime, 'none');
  assert.strictEqual(FRESH_INSTALL_SETTINGS.enabled, '1');

  const { settingsContent } = await loadAndRenderSettings(FRESH_INSTALL_SETTINGS, { scriptSrc });
  const toggle = findEnableToggle(settingsContent);
  assert.ok(toggle, 'No #einv-enabled checkbox rendered.');
  assert.strictEqual(toggle.checked, false,
    'The enable checkbox was TICKED against the real fresh-install defaults (tax_regime="none", enabled="1") -- ' +
    'a shop that never touched e-invoicing would see a ticked, greyed-out box next to the "Disabled" status pill.');
}

async function testEnableBoxUncheckedOnFreshInstallDefaults() {
  await enableBoxUncheckedOnFreshInstallDefaults();
  console.log('PASS: real fresh-install defaults (tax_regime="none", enabled="1") render the enable checkbox UNCHECKED, matching the "Disabled" status pill');
}

async function testMutationProvedFreshInstallCheckboxGuard() {
  // Revert to the pre-fix behaviour: tick the box straight off the raw
  // setting, ignoring the regime -- the exact defect this check exists to
  // catch (see its own header comment and the item's evidence).
  const mutated = mutate(SCRIPT_SRC, [[
    `    toggle.checked = regimeIsJordan && settings.enabled === '1';`,
    `    toggle.checked = settings.enabled === '1'; /* mutant: tick straight off the raw setting, ignoring the regime */`,
  ]]);
  const { message } = await provesMutation(
    'fresh-install checkbox guard survived a mutant that ticks the box straight off the raw enabled setting, ignoring the regime',
    () => enableBoxUncheckedOnFreshInstallDefaults(mutated)
  );
  await enableBoxUncheckedOnFreshInstallDefaults();
  console.log('PASS: ' + message);
  console.log('PASS: guard passes again against the restored, unmutated einvoicing.js');
}

// ─────────────────────────────────────────────────────────────────────────────
// (7) [P0 review-fix] Neither "waiting" nor "compliant" appears ANYWHERE in
//     the rendered card -- #status-content AND #settings-content together --
//     across every reachable state, not merely inside the not-connected
//     notice's own textContent. The item's own pin list (see this file's
//     header, point 7) promises this for "ANY of the states above"; the
//     original check only ever read notice.textContent (see
//     notConnectedShowsNoticeAndDisablesButton above), so a mutant that
//     appended either word to the neutral disabled line, the provider
//     label or the Pause/Resume row would have passed silently.
//     [MUTATION-PROVED below]
// ─────────────────────────────────────────────────────────────────────────────

const FORBIDDEN_WORDS_STATES = [
  // provider_configured=false, enabled=true -- the not-connected notice itself.
  { ...BASE_STATUS, provider: 'jofotara', provider_in_use: 'jofotara', provider_configured: false },
  // provider_configured=true, enabled=true -- Pause/Resume + Submit rendered.
  { ...BASE_STATUS, provider: 'jofotara', provider_in_use: 'jofotara', provider_configured: true },
  // enabled=false -- the neutral off-line, the one place the notice-only
  // check above could never reach.
  { ...BASE_STATUS, enabled: false, provider: 'jofotara', provider_in_use: 'unconfigured', provider_configured: false },
  // killswitch paused, still connected -- the Paused badge + Resume button.
  { ...BASE_STATUS, provider: 'jofotara', provider_in_use: 'jofotara', provider_configured: true, killswitch: { disabled: true, reason: 'manual' } },
];

async function forbiddenWordsNeverAppearInWholeCard(scriptSrc) {
  for (const statusData of FORBIDDEN_WORDS_STATES) {
    const { statusContent, settingsContent } = await loadAndRenderStatus(statusData, {
      scriptSrc,
      routes: {
        'GET /api/einvoicing/settings': () => ({
          status: 'success',
          data: { settings: BASE_SETTINGS, credentials: { configured: false } },
        }),
      },
    });
    const wholeCardText = statusContent.textContent + ' ' + settingsContent.textContent;
    const stateLabel = `enabled=${statusData.enabled}, provider_configured=${statusData.provider_configured}, killswitch=${!!(statusData.killswitch && statusData.killswitch.disabled)}`;
    assert.ok(!/waiting/i.test(wholeCardText),
      `"waiting" appeared somewhere in the card (${stateLabel}): ${JSON.stringify(wholeCardText)}`);
    assert.ok(!/compliant/i.test(wholeCardText),
      `"compliant" appeared somewhere in the card (${stateLabel}): ${JSON.stringify(wholeCardText)}`);
  }
}

async function testForbiddenWordsNeverAppearInWholeCard() {
  await forbiddenWordsNeverAppearInWholeCard();
  console.log('PASS: neither "waiting" nor "compliant" appears anywhere in #status-content or #settings-content, across ' + FORBIDDEN_WORDS_STATES.length + ' rendered states');
}

async function testMutationProvedForbiddenWordsGuard() {
  // Append "compliant" to the DISABLED-state neutral off-line -- a location
  // outside the not-connected notice, so this proves the check reads the
  // whole card rather than just re-checking the same notice the older,
  // narrower check already covered.
  const mutated = mutate(SCRIPT_SRC, [[
    `        text: t('E-invoicing is off for this shop. Nothing is recorded or sent.'),`,
    `        text: t('E-invoicing is off for this shop. You are compliant. Nothing is recorded or sent.'), /* mutant */`,
  ]]);
  const { message } = await provesMutation(
    'whole-card forbidden-word guard survived a mutant that injects "compliant" into the disabled neutral off-line, outside the not-connected notice',
    () => forbiddenWordsNeverAppearInWholeCard(mutated)
  );
  await forbiddenWordsNeverAppearInWholeCard();
  console.log('PASS: ' + message);
  console.log('PASS: guard passes again against the restored, unmutated einvoicing.js');
}

// ─────────────────────────────────────────────────────────────────────────────
// (10) [P0 review-fix] einvoicing.html's static subtitle (rendered before
//      any fetch resolves, and never re-rendered afterward) must not claim
//      either default state -- a reviewer flagged this line as changed with
//      no guard anywhere (see this file's own header for why #status-content
//      itself needs no equivalent check: it re-fetches is_enabled() and is
//      already covered by the checks above). Reads the real HTML file's
//      source text directly; nothing here re-implements a template engine.
// ─────────────────────────────────────────────────────────────────────────────

const HTML_FILE = path.join(FRONTEND_DIR, 'einvoicing.html');

function subtitleTextFrom(htmlSrc) {
  const m = htmlSrc.match(/<div class="subtitle"[^>]*>([\s\S]*?)<\/div>/);
  assert.ok(m, 'No <div class="subtitle"> found in einvoicing.html -- re-anchor this check.');
  return m[1];
}

async function subtitleNamesNoDefaultState(htmlSrc) {
  const subtitle = subtitleTextFrom(htmlSrc);
  assert.ok(!/on by default/i.test(subtitle),
    `Static subtitle claims a fixed "on" default, which tax_regime's own default ('none') contradicts: ${JSON.stringify(subtitle)}`);
  assert.ok(!/off by default/i.test(subtitle),
    `Static subtitle claims a fixed "off" default, which an operator-set tax_regime='jordan' contradicts: ${JSON.stringify(subtitle)}`);
  assert.ok(!/enabled by default/i.test(subtitle),
    `Static subtitle claims e-invoicing is "enabled by default": ${JSON.stringify(subtitle)}`);
}

async function testSubtitleNamesNoDefaultState() {
  const htmlSrc = fs.readFileSync(HTML_FILE, 'utf8');
  await subtitleNamesNoDefaultState(htmlSrc);
  console.log('PASS: einvoicing.html\'s static subtitle names neither state as the default (it cannot re-fetch is_enabled() the way #status-content does)');
}

async function testMutationProvedSubtitleGuard() {
  const realHtml = fs.readFileSync(HTML_FILE, 'utf8');
  const find = 'Government e-invoicing status and settings for this installation. Recording and submission depend on the tax regime and connection status below.';
  const hits = realHtml.split(find).length - 1;
  assert.strictEqual(hits, 2,
    `Mutation anchor occurs ${hits} time(s) in einvoicing.html, expected exactly 2 (data-i18n attribute + element text). Re-anchor it.`);
  const mutated = realHtml.split(find).join(
    'On by default. Government e-invoicing status and settings for this installation.'
  );
  const { message } = await provesMutation(
    'static subtitle guard survived a mutant that reintroduces an "On by default" claim',
    () => subtitleNamesNoDefaultState(mutated)
  );
  await subtitleNamesNoDefaultState(realHtml);
  console.log('PASS: ' + message);
  console.log('PASS: guard passes again against the restored, unmutated einvoicing.html');
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
  ['enabled=false shows only the Disabled pill and the neutral off-line', testDisabledShowsOnlyNeutralLine],
  ['disabled-state guard is mutation-proved', testMutationProvedDisabledGuard],
  ['settings: enable checkbox disabled while regime is not Jordan', testEnableBoxDisabledWhileRegimeNotJordan],
  ['settings: enable checkbox enabled once regime is Jordan', testEnableBoxEnabledWhenRegimeIsJordan],
  ['settings: enable-checkbox guard is mutation-proved', testMutationProvedEnableBoxGuard],
  ['settings: enable checkbox unchecked against real fresh-install defaults', testEnableBoxUncheckedOnFreshInstallDefaults],
  ['fresh-install checkbox guard is mutation-proved', testMutationProvedFreshInstallCheckboxGuard],
  ['"waiting"/"compliant" never appear anywhere in the whole rendered card', testForbiddenWordsNeverAppearInWholeCard],
  ['whole-card forbidden-word guard is mutation-proved', testMutationProvedForbiddenWordsGuard],
  ['einvoicing.html static subtitle names no default state', testSubtitleNamesNoDefaultState],
  ['static subtitle guard is mutation-proved', testMutationProvedSubtitleGuard],
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
