/**
 * retail_activation_handoff_test.js -- proves the post-activation session
 * hand-off fix.
 *
 * THE BUG: POST /api/licensing/activate rebinds this install's company_id to
 * the licence's identity (commercial_runtime/identity/company_rebind.py), and
 * that rebind deliberately invalidates every session issued before it -- the
 * same session_version mechanism mt_auth.py uses for a password reset or a
 * role change. So the admin's own request that JUST activated the device is
 * followed by a 401 on the very next one, carrying the backend's real,
 * correctly-worded "Session expired or revoked. Please log in again." --
 * true, and exactly the wrong thing to show, because nothing is wrong: the
 * till did this to itself, on purpose, to adopt the licence's identity.
 *
 * THE FIX: app-shell.js's ACTIVATION_HANDOFF_KEY marker (see its own comment
 * there for the full mechanism). Written by whichever activation call site
 * just succeeded (licensing.js's onActivateClicked/pollActivationApproval,
 * this file's own _setupSubmit()/_pollSetupApproval()/_setupKeyRetrySubmit()),
 * read-and-cleared by the next 401/unauthenticated arrival
 * (checkAuthAndSetup(), init()'s own auth gate), which then shows a friendly,
 * localized hand-off message with the email pre-filled instead of the raw
 * backend text.
 *
 * Plain `node` script, no framework -- matches every other *_test.js in this
 * directory. The mutation harness (mutate()/provesMutation()) and the base
 * shell-loading sandbox are the same shape as retail_shell_chrome_test.js's
 * (that file already proves this exact sandbox shape is sufficient to run
 * SubsystemApp.showReloginModal() for real -- see its own
 * testIntroNeverShownOnSignInPath).
 *
 * COVERS
 *   1. A successful activation's marker makes checkAuthAndSetup() show the
 *      friendly, localized hand-off message and pre-fill the email -- never
 *      the raw "Session expired or revoked" text.
 *   2. A 401 with NO live marker (nothing just activated) still shows the
 *      raw, real error -- the control case for the scoping mutation below.
 *   3. The hand-off marker is single-use (consumed exactly once) and ages
 *      out on its own TTL.
 *   4. licensing.js (a separate document, separate script scope, no module
 *      system -- see its own header) WRITES a marker that app-shell.js can
 *      really read: the exact cross-file-drift risk CLAUDE.md already flags
 *      for _needsActivation()'s three mirrored copies.
 *   5. Every user-facing string this fix adds is a real locale lookup,
 *      present in both catalogs with real Arabic.
 *
 * SCOPE NOTE: init()'s own auth-gate branch carries the identical marker
 * read/prefill/message logic as checkAuthAndSetup() (by design -- see
 * ACTIVATION_HANDOFF_KEY's comment) and is covered directly below by driving
 * the real init() through its full boot sequence. checkAuthAndSetup() is
 * still exercised on its own too, since it is the path a live shell actually
 * takes (subsystem-retail.js's _fetch on a 401), and it is what the scoping
 * mutation proof is anchored to.
 *
 * Run: node products/retail/tests/retail_activation_handoff_test.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const FRONTEND_DIR = path.join(__dirname, '..', 'frontend');
const SHELL_FILE = path.join(FRONTEND_DIR, 'app-shell.js');
const ICONS_FILE = path.join(FRONTEND_DIR, 'icons.js');
const LICENSING_FILE = path.join(FRONTEND_DIR, 'licensing.js');
const SHELL_SRC = fs.readFileSync(SHELL_FILE, 'utf8');
const ICONS_SRC = fs.readFileSync(ICONS_FILE, 'utf8');
const LICENSING_SRC = fs.readFileSync(LICENSING_FILE, 'utf8');
const EN = JSON.parse(fs.readFileSync(path.join(FRONTEND_DIR, 'locales', 'en.json'), 'utf8'));
const AR = JSON.parse(fs.readFileSync(path.join(FRONTEND_DIR, 'locales', 'ar.json'), 'utf8'));

const HANDOFF_MESSAGE = 'Your license is now active. Please sign in again to continue.';
const RAW_BACKEND_MESSAGE = 'Session expired or revoked. Please log in again.';

// Cheap drift guard: if app-shell.js's literal ever changes without this
// test being updated, fail loudly here instead of the rest of the file
// quietly asserting against a string the source no longer contains.
assert.ok(
  SHELL_SRC.includes(`t('${HANDOFF_MESSAGE}')`),
  'HANDOFF_MESSAGE in this test no longer matches the literal t(...) call in app-shell.js -- keep them in step.'
);

// ─────────────────────────────────────────────────────────────────────────────
// Mutation harness -- byte-for-byte the same shape as retail_shell_chrome_
// test.js: re-anchor to exact source text, require the anchor to occur
// exactly once so a stale mutation cannot silently no-op.
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
  return `${what}  [caught: ${String(threw.message || threw).split('\n')[0].slice(0, 140)}]`;
}

// ─────────────────────────────────────────────────────────────────────────────
// A real-enough element stub: a genuine parent/child tree (so
// getElementById-by-walk, clearChildren's firstChild/removeChild, and
// remove()/replaceWith() all operate on real structure instead of a guess),
// plus the handful of DOM APIs app-shell.js and licensing.js actually call.
// ─────────────────────────────────────────────────────────────────────────────

function makeElementStub(tag) {
  const node = {
    tagName: String(tag || 'div').toUpperCase(),
    children: [],
    parentNode: null,
    value: '',
    id: '',
    className: '',
    textContent: '',
    innerHTML: '',
    style: {},
    dataset: {},
    _listeners: {},
    classList: { toggle() {}, add() {}, remove() {}, contains() { return false; } },
    appendChild(child) {
      node.children.push(child);
      child.parentNode = node;
      return child;
    },
    removeChild(child) {
      const i = node.children.indexOf(child);
      if (i !== -1) node.children.splice(i, 1);
      child.parentNode = null;
      return child;
    },
    remove() {
      if (node.parentNode) node.parentNode.removeChild(node);
    },
    replaceWith(repl) {
      if (node.parentNode) {
        const p = node.parentNode;
        const i = p.children.indexOf(node);
        if (i !== -1) p.children[i] = repl;
        repl.parentNode = p;
      }
    },
    addEventListener(type, handler) {
      (node._listeners[type] = node._listeners[type] || []).push(handler);
    },
    removeEventListener() {},
    focus() {},
    blur() {},
    click() {
      (node._listeners.click || []).forEach((h) => h());
    },
    getAttribute() { return null; },
    setAttribute() {},
    querySelector() { return null; },
    querySelectorAll() { return []; },
  };
  Object.defineProperty(node, 'firstChild', { get() { return node.children[0] || null; } });
  return node;
}

// ─────────────────────────────────────────────────────────────────────────────
// app-shell.js sandbox -- same base shape retail_shell_chrome_test.js already
// proves sufficient to run showReloginModal() for real, extended with a
// working sessionStorage (the marker's real storage) and a getElementById
// that resolves BOTH real-property ids (overlay.id = '...') and the
// template-literal ids that only exist inside an innerHTML STRING
// (showReloginModal builds its card with one `overlay.innerHTML = \`...\`
// assignment, not createElement/appendChild) -- exactly the shape this
// fix's own prefill code needs to be observable from outside.
// ─────────────────────────────────────────────────────────────────────────────

function loadShell(shellSrc, opts) {
  const o = opts || {};
  const localStore = o.localStore || {};
  const sessionStore = o.sessionStore || {};
  const fetchStub = o.fetch || (() => Promise.resolve({ ok: true, json: () => Promise.resolve({}) }));

  const sandbox = {
    console,
    t: o.t || ((s) => s),
    setTimeout, clearTimeout, setInterval, clearInterval,
    navigator: { userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)' },
    location: { hash: '', href: 'http://localhost/', search: '' },
    fetch: fetchStub,
    matchMedia: () => ({ matches: false }),
    MutationObserver: class { observe() {} disconnect() {} },
    localStorage: {
      getItem: (k) => (k in localStore ? localStore[k] : null),
      setItem: (k, v) => { localStore[k] = String(v); },
      removeItem: (k) => { delete localStore[k]; },
    },
    sessionStorage: {
      getItem: (k) => (k in sessionStore ? sessionStore[k] : null),
      setItem: (k, v) => { sessionStore[k] = String(v); },
      removeItem: (k) => { delete sessionStore[k]; },
    },
    document: {
      readyState: 'complete',
      head: { appendChild() {} },
      documentElement: {
        getAttribute() { return null; },
        setAttribute() {},
        style: { setProperty() {} },
      },
      createElement(tag) { return makeElementStub(tag); },
      createTextNode(text) { return { nodeValue: String(text) }; },
      querySelector() { return null; },
      querySelectorAll() { return []; },
      addEventListener() {},
    },
  };
  sandbox.document.body = makeElementStub('body');
  // Real-id-first, innerHTML-substring-fallback -- see the section comment
  // above for why both are needed.
  sandbox.document.getElementById = (id) => {
    const stack = [...sandbox.document.body.children];
    while (stack.length) {
      const el = stack.shift();
      if (el.id === id) return el;
      if (el.children && el.children.length) stack.push(...el.children);
    }
    for (const el of sandbox.document.body.children) {
      if (typeof el.innerHTML === 'string' && el.innerHTML.includes(`id="${id}"`)) {
        el._innerIds = el._innerIds || {};
        if (!el._innerIds[id]) el._innerIds[id] = makeElementStub('input');
        return el._innerIds[id];
      }
    }
    return null;
  };
  // A real browser window has addEventListener; app-shell _installAuthGuard
  // registers a `pagehide` listener on it since DAY-UI U7 (2d3481f9). The stub
  // only records nothing -- no test here depends on page unload.
  sandbox.addEventListener = () => {};
  sandbox.removeEventListener = () => {};
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(ICONS_SRC, sandbox, { filename: ICONS_FILE });
  assert.ok(sandbox.AuraIcons, 'icons.js did not expose window.AuraIcons');
  vm.runInContext(shellSrc || SHELL_SRC, sandbox, { filename: SHELL_FILE });
  assert.ok(sandbox.SubsystemApp, 'app-shell.js did not expose window.SubsystemApp');
  return { sandbox, App: sandbox.SubsystemApp, sessionStore, localStore };
}

function findRelogin(sandbox) {
  return sandbox.document.body.children.find((el) => el.id === 'aura-relogin-modal') || null;
}

// A tiny delay -- enough for the microtask queue (and one or two chained
// promises) to settle without hand-rolling a flush loop. Same convention
// this codebase's own comments describe for real, short waits in a Node
// test with no fake-timer shim (see app-shell.js's _waitForShopAccount
// comment).
function tick(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms == null ? 20 : ms));
}

// ═════════════════════════════════════════════════════════════════════════════
// 1 — a live hand-off marker: checkAuthAndSetup() shows the friendly message,
//     pre-fills the email, and consumes the marker (single-use)
//     [MUTATION-PROVED below: the scoping condition itself]
// ═════════════════════════════════════════════════════════════════════════════

function fetchStatusNeedsSetupFalse(url) {
  if (String(url).includes('/api/onboarding/status')) {
    return Promise.resolve({ ok: true, json: () => Promise.resolve({ needs_setup: false }) });
  }
  return Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
}

async function assertLiveHandoffShowsFriendlyMessage(shellSrc) {
  const tCalls = [];
  const { sandbox, App } = loadShell(shellSrc, {
    t: (s) => { tCalls.push(s); return s; },
    fetch: fetchStatusNeedsSetupFalse,
  });
  App._markActivationHandoff('owner@example.com');
  await App.checkAuthAndSetup(RAW_BACKEND_MESSAGE);

  const overlay = findRelogin(sandbox);
  assert.ok(overlay, 'checkAuthAndSetup() with a live hand-off marker did not render the relogin modal at all.');
  assert.ok(
    overlay.innerHTML.includes(HANDOFF_MESSAGE),
    'the friendly hand-off message was not rendered on the relogin modal.'
  );
  assert.ok(
    !overlay.innerHTML.includes(RAW_BACKEND_MESSAGE),
    'the raw backend "Session expired or revoked" text leaked onto a post-activation hand-off screen -- ' +
    'this is exactly the friction the fix exists to remove.'
  );
  assert.ok(
    tCalls.includes(HANDOFF_MESSAGE),
    't() was never called with the hand-off message -- it is not a locale lookup.'
  );

  const emailInput = sandbox.document.getElementById('rl-email');
  assert.ok(emailInput, 'no #rl-email input was found on the rendered relogin modal.');
  assert.strictEqual(
    emailInput.value, 'owner@example.com',
    'the email captured moments ago at activation was not carried onto the sign-in form.'
  );

  assert.strictEqual(
    sandbox.sessionStorage.getItem(App.ACTIVATION_HANDOFF_KEY), null,
    'the hand-off marker was still present after checkAuthAndSetup() used it -- it must be single-use.'
  );
}

async function testLiveHandoffShowsFriendlyMessageAndPrefillsEmail() {
  await assertLiveHandoffShowsFriendlyMessage();
  console.log('PASS: a live post-activation marker shows the localized hand-off message and pre-fills the email');
}

// ═════════════════════════════════════════════════════════════════════════════
// 2 — no marker: a genuine 401 still reads as a real error [the control case]
// ═════════════════════════════════════════════════════════════════════════════

async function assertUnrelated401StillShowsRealError(shellSrc) {
  const { sandbox, App } = loadShell(shellSrc, { fetch: fetchStatusNeedsSetupFalse });
  // Deliberately NO App._markActivationHandoff(...) call -- nothing just
  // activated, so this must be treated as a genuine session failure.
  await App.checkAuthAndSetup(RAW_BACKEND_MESSAGE);

  const overlay = findRelogin(sandbox);
  assert.ok(overlay, 'checkAuthAndSetup() with no marker did not render the relogin modal at all.');
  assert.ok(
    overlay.innerHTML.includes(RAW_BACKEND_MESSAGE),
    'a genuine session failure with no live hand-off marker was swallowed -- the friendly path must be ' +
    'narrowly scoped to a just-completed activation, and this is not one.'
  );
  assert.ok(
    !overlay.innerHTML.includes(HANDOFF_MESSAGE),
    'the post-activation hand-off message was shown for a 401 that has nothing to do with activation.'
  );
}

async function testUnrelated401StillShowsRealError() {
  await assertUnrelated401StillShowsRealError();
  console.log('PASS: a 401 with no live hand-off marker still shows the real, raw error');
}

// ═════════════════════════════════════════════════════════════════════════════
// 2b — [MUTATION-PROVED] the scoping condition is load-bearing: widen it to
//      swallow ANY 401 and watch case 2's assertion go red.
// ═════════════════════════════════════════════════════════════════════════════

async function testScopingConditionMutationIsCaught() {
  // Anchored on checkAuthAndSetup()'s OWN comment immediately above its
  // `const handoff = ...` line -- init()'s auth gate declares the identical
  // variable name one function away, so the bare assignment line alone is
  // not a unique anchor (mutate() requires exactly one match). Mutating to
  // `|| { email: '' }` rather than to a bare `true` deliberately avoids a
  // null-dereference crash on `handoff.email` a couple of lines down: a
  // mutant that instead THROWS would be caught by checkAuthAndSetup()'s own
  // catch-all, which happens to re-show the same raw errorMsg this test
  // checks for -- proving nothing about the scoping condition itself. This
  // is the real shape of "every 401 is now treated as post-activation".
  const mutated = mutate(SHELL_SRC, [[
    `    // call to this function.
    const handoff = this._consumeActivationHandoff();`,
    `    // call to this function.
    const handoff = this._consumeActivationHandoff() || { email: '' };  // MUTATED: never null -- every 401 now treated as post-activation`,
  ]]);
  const msg = await provesMutation(
    'the "unrelated 401 still shows a real error" check survived a mutant that treats EVERY 401 as ' +
    'post-activation',
    () => assertUnrelated401StillShowsRealError(mutated)
  );
  console.log('PASS: ' + msg);
}

// ═════════════════════════════════════════════════════════════════════════════
// 3 — the marker is single-use [MUTATION-PROVED]
// ═════════════════════════════════════════════════════════════════════════════

function assertMarkerIsSingleUse(shellSrc) {
  const { App } = loadShell(shellSrc);
  App._markActivationHandoff('once@example.com');
  const first = App._consumeActivationHandoff();
  assert.ok(first && first.email === 'once@example.com', 'the first consume did not return the marker.');
  const second = App._consumeActivationHandoff();
  assert.strictEqual(second, null, 'a SECOND consume still returned the marker -- it is not single-use.');
}

function testMarkerIsSingleUse() {
  assertMarkerIsSingleUse();
  console.log('PASS: _consumeActivationHandoff() is single-use -- a second read finds nothing');
}

async function testSingleUseMutationIsCaught() {
  const mutated = mutate(SHELL_SRC, [[
    `      raw = sessionStorage.getItem(this.ACTIVATION_HANDOFF_KEY);
      sessionStorage.removeItem(this.ACTIVATION_HANDOFF_KEY);`,
    `      raw = sessionStorage.getItem(this.ACTIVATION_HANDOFF_KEY);
      // MUTATED: removeItem deleted -- the marker is never actually consumed`,
  ]]);
  const msg = await provesMutation(
    'the "marker is single-use" check survived a mutant that stops deleting it on read',
    () => assertMarkerIsSingleUse(mutated)
  );
  console.log('PASS: ' + msg);
}

// ═════════════════════════════════════════════════════════════════════════════
// 4 — the marker ages out on its own TTL [MUTATION-PROVED]
// ═════════════════════════════════════════════════════════════════════════════

function assertExpiredMarkerIsIgnored(shellSrc) {
  const sessionStore = {};
  const { App } = loadShell(shellSrc, { sessionStore });
  // Written directly (not via _markActivationHandoff, which always stamps
  // "now") to simulate a marker that has genuinely gone stale -- a crashed
  // tab, a redirect that never completed.
  sessionStore[App.ACTIVATION_HANDOFF_KEY] = JSON.stringify({
    v: 1, at: Date.now() - (App.ACTIVATION_HANDOFF_MAX_AGE_MS + 5000), email: 'stale@example.com',
  });
  const rec = App._consumeActivationHandoff();
  assert.strictEqual(rec, null, 'an EXPIRED marker was still honoured -- the TTL is not being enforced.');
}

function testExpiredMarkerIsIgnored() {
  assertExpiredMarkerIsIgnored();
  console.log('PASS: a marker older than ACTIVATION_HANDOFF_MAX_AGE_MS is ignored');
}

async function testTTLMutationIsCaught() {
  const mutated = mutate(SHELL_SRC, [[
    `    if (typeof rec.at !== 'number' || (Date.now() - rec.at) > this.ACTIVATION_HANDOFF_MAX_AGE_MS) return null;`,
    `    if (typeof rec.at !== 'number' || false) return null;  // MUTATED: age check deleted`,
  ]]);
  const msg = await provesMutation(
    'the "expired marker is ignored" check survived a mutant that deletes the TTL comparison',
    () => assertExpiredMarkerIsIgnored(mutated)
  );
  console.log('PASS: ' + msg);
}

// ═════════════════════════════════════════════════════════════════════════════
// 5 — init()'s own auth-gate branch carries the identical hand-off logic
// ═════════════════════════════════════════════════════════════════════════════

function fetchForInitBoot(opts) {
  const o = opts || {};
  return (url) => {
    const u = String(url);
    if (u.includes('/api/onboarding/status')) {
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ needs_setup: false }) });
    }
    if (u.includes('/api/auth/active-modules')) {
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ modules: [] }) });
    }
    if (u.includes('/api/auth/session')) {
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ authenticated: false }) });
    }
    return Promise.resolve({ ok: o.defaultOk !== false, json: () => Promise.resolve({}) });
  };
}

async function testInitAuthGateShowsHandoffWhenMarkerPresent() {
  const { sandbox, App } = loadShell(undefined, { fetch: fetchForInitBoot() });
  App._markActivationHandoff('owner2@example.com');
  await App.init();

  const overlay = findRelogin(sandbox);
  assert.ok(overlay, 'init() with an unauthenticated session and a live marker did not render the relogin modal.');
  assert.ok(
    overlay.innerHTML.includes(HANDOFF_MESSAGE),
    'init()\'s own auth gate did not show the friendly hand-off message for a live marker.'
  );
  const emailInput = sandbox.document.getElementById('rl-email');
  assert.ok(emailInput && emailInput.value === 'owner2@example.com',
    'init()\'s own auth gate did not pre-fill the email from the hand-off marker.');
  console.log('PASS: init()\'s own auth gate shows the same hand-off message and prefill as checkAuthAndSetup()');
}

async function testInitAuthGateShowsGenericSignInWhenNoMarker() {
  const { sandbox, App } = loadShell(undefined, { fetch: fetchForInitBoot() });
  await App.init();

  const overlay = findRelogin(sandbox);
  assert.ok(overlay, 'init() with an unauthenticated session did not render the relogin modal.');
  assert.ok(
    overlay.innerHTML.includes('Sign in to your store'),
    'init()\'s ordinary "no admin session yet" case stopped showing its own generic message.'
  );
  assert.ok(!overlay.innerHTML.includes(HANDOFF_MESSAGE),
    'the hand-off message appeared on an ordinary returning-user login with no marker.');
  console.log('PASS: init()\'s auth gate still shows its ordinary "Sign in to your store" with no marker');
}

// ═════════════════════════════════════════════════════════════════════════════
// 6 — licensing.js (separate document, separate script scope) WRITES a
//     marker app-shell.js can really read
// ═════════════════════════════════════════════════════════════════════════════

function loadLicensingPage(opts) {
  const o = opts || {};
  const localStore = {};
  const sessionStore = o.sessionStore || {};
  const messageAreaEl = makeElementStub('div');
  const contentEl = makeElementStub('div');
  const created = [];

  const sandbox = {
    console,
    setTimeout, clearTimeout, setInterval, clearInterval,
    location: { search: o.search || '', href: 'http://localhost/static/licensing.html' },
    fetch: o.fetch,
    localStorage: {
      getItem: (k) => (k in localStore ? localStore[k] : null),
      setItem: (k, v) => { localStore[k] = String(v); },
      removeItem: (k) => { delete localStore[k]; },
    },
    sessionStorage: {
      getItem: (k) => (k in sessionStore ? sessionStore[k] : null),
      setItem: (k, v) => { sessionStore[k] = String(v); },
      removeItem: (k) => { delete sessionStore[k]; },
    },
    document: {
      activeElement: null,
      getElementById(id) {
        if (id === 'message-area') return messageAreaEl;
        if (id === 'content') return contentEl;
        return null;
      },
      createElement(tag) { const el = makeElementStub(tag); created.push(el); return el; },
      createTextNode(text) { return { nodeValue: String(text) }; },
      addEventListener() {},
    },
  };
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(LICENSING_SRC, sandbox, { filename: LICENSING_FILE });
  return { sandbox, created, messageAreaEl, contentEl, sessionStore };
}

async function testLicensingPageWritesAMarkerAppShellCanConsume() {
  const sessionStore = {};
  const fetchLog = [];
  const licensingFetch = (url, fetchOpts) => {
    const u = String(url);
    fetchLog.push(u);
    if (u.includes('/api/auth/session')) {
      return Promise.resolve({
        status: 200, json: () => Promise.resolve({ authenticated: true, user: { email: 'shopowner@example.com' } }),
      });
    }
    if (u.includes('/api/licensing/activate') && fetchOpts && fetchOpts.method === 'POST') {
      return Promise.resolve({ status: 200, json: () => Promise.resolve({ result: 'SUCCESS' }) });
    }
    if (u.includes('/api/licensing/status')) {
      return Promise.resolve({ status: 200, json: () => Promise.resolve({ current_state: 'ACTIVATION_REQUIRED' }) });
    }
    return Promise.resolve({ status: 200, json: () => Promise.resolve({}) });
  };

  const { created, sessionStore: writtenStore } = loadLicensingPage({ fetch: licensingFetch, sessionStore });
  // Let the page's own boot-time refresh() (fired at IIFE load, unawaited)
  // settle and render the activation form before interacting with it.
  await tick(30);

  const input = created.find((el) => el.id === 'license-key-input');
  const activateBtn = created.find((el) => el.id === 'activate-btn');
  assert.ok(input && activateBtn, 'licensing.js did not render its key-entry form (input/#activate-btn missing).');

  input.value = 'AURA-RETAIL-TEST-KEY';
  const clickHandlers = activateBtn._listeners.click || [];
  assert.strictEqual(clickHandlers.length, 1, 'the Activate button has no click handler wired.');
  await clickHandlers[0]();
  await tick(30);

  assert.ok(
    fetchLog.some((u) => u.includes('/api/auth/session')),
    'onActivateClicked() never asked who was signed in before activating -- there is no email to hand off.'
  );

  // Cross-file compatibility: read the marker back with app-shell.js's REAL
  // _consumeActivationHandoff(), sharing the SAME sessionStorage a real
  // navigation would carry across. This is the actual regression this test
  // exists to catch -- CLAUDE.md already documents _needsActivation() as a
  // three-way-duplicated predicate that once silently drifted; this marker
  // is duplicated the same way, between the same two files.
  const { App } = loadShell(undefined, { sessionStore: writtenStore });
  const rec = App._consumeActivationHandoff();
  assert.ok(rec, 'app-shell.js could not read back the marker licensing.js wrote -- the two copies have drifted.');
  assert.strictEqual(
    rec.email, 'shopowner@example.com',
    `licensing.js wrote the wrong email into the hand-off marker (got ${JSON.stringify(rec.email)}).`
  );
  console.log('PASS: licensing.js writes a hand-off marker app-shell.js\'s real _consumeActivationHandoff() can read, with the right email');
}

// ═════════════════════════════════════════════════════════════════════════════
// 7 — every user-facing string this fix adds is a real locale lookup
// ═════════════════════════════════════════════════════════════════════════════

function testHandoffMessageIsARealLocaleLookup() {
  assert.ok(HANDOFF_MESSAGE in EN, `'${HANDOFF_MESSAGE}' is missing from en.json.`);
  assert.ok(HANDOFF_MESSAGE in AR, `'${HANDOFF_MESSAGE}' is missing from ar.json.`);
  const arValue = AR[HANDOFF_MESSAGE];
  assert.ok(arValue && arValue.trim(), `'${HANDOFF_MESSAGE}' has an empty Arabic translation.`);
  assert.notStrictEqual(
    arValue, EN[HANDOFF_MESSAGE],
    `'${HANDOFF_MESSAGE}' is not translated -- Arabic is byte-identical to English.`
  );
  assert.ok(
    /[؀-ۿ]/.test(arValue),
    `'${HANDOFF_MESSAGE}' has no Arabic script in its translation: ${JSON.stringify(arValue)}`
  );
  console.log('PASS: the hand-off message is a real, translated locale key in both catalogs');
}

// Guards against the exact near-miss this codebase has been bitten by
// before (CLAUDE.md: "this catalogue translates 'Credit' as 'آجل' meaning
// the on-credit payment method, and reusing keys across senses has already
// bitten this project"). The hand-off message is a brand-new key, not a
// reuse of an existing one -- pin that explicitly rather than assuming it.
function testHandoffMessageDoesNotReuseAnExistingKey() {
  const priorEnKeys = Object.keys(EN).filter((k) => k !== HANDOFF_MESSAGE);
  assert.ok(
    !priorEnKeys.includes(HANDOFF_MESSAGE),
    'sanity check failed: HANDOFF_MESSAGE filtering did not work.'
  );
  // The one existing key close enough in shape to be a real reuse risk --
  // 'Activation successful.' (licensing.js) says something already happened;
  // this message additionally has to say what to do next. Confirms they are
  // deliberately two different catalog entries, not one being stretched.
  assert.notStrictEqual(HANDOFF_MESSAGE, 'Activation successful.');
  console.log('PASS: the hand-off message is its own catalog key, not a reuse of an existing one');
}

// ═════════════════════════════════════════════════════════════════════════════

const CASES = [
  testLiveHandoffShowsFriendlyMessageAndPrefillsEmail,
  testUnrelated401StillShowsRealError,
  testScopingConditionMutationIsCaught,
  testMarkerIsSingleUse,
  testSingleUseMutationIsCaught,
  testExpiredMarkerIsIgnored,
  testTTLMutationIsCaught,
  testInitAuthGateShowsHandoffWhenMarkerPresent,
  testInitAuthGateShowsGenericSignInWhenNoMarker,
  testLicensingPageWritesAMarkerAppShellCanConsume,
  testHandoffMessageIsARealLocaleLookup,
  testHandoffMessageDoesNotReuseAnExistingKey,
];

async function main() {
  let failed = 0;
  for (const fn of CASES) {
    try {
      await fn();
    } catch (err) {
      failed += 1;
      console.error('  FAIL ' + fn.name);
      console.error('       ' + (err && err.stack ? err.stack : String(err)));
    }
  }
  if (failed) {
    console.error(`FAIL: retail_activation_handoff_test.js — ${failed} of ${CASES.length} case(s) failed`);
    process.exitCode = 1;
  } else {
    console.log(`PASS: retail_activation_handoff_test.js — ${CASES.length} case(s)`);
  }
}

main();
