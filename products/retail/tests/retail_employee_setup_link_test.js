/**
 * retail_employee_setup_link_test.js — the employee-invite "#setup/<token>"
 * landing screen (app-shell.js's _showEmployeeSetupScreen/_employeeSetupSubmit).
 *
 * Measured 2026-09-06 20:15 on the demo laptop, in Chromium: the Employees
 * screen's "+ Add Employee" dialog issues an invite link shaped
 * http://127.0.0.1:5010/#setup/<token> ("This link works once and expires in
 * 7 days"). Opening it in a fresh browser showed the ORDINARY sign-in modal
 * -- the new employee has no password and nowhere to set one, because
 * SubsystemApp.init() handled #verify-email/<token> and
 * #reset-password/<token> at boot but not #setup/<token>. The backend route
 * (POST /api/auth/employee/setup, commercial_runtime/identity/
 * onboarding_routes.py) already existed -- its own comment called the invite
 * link "currently frontend-unwired". This file exercises the frontend door
 * that now closes that gap:
 *
 *   (a) init() on a #setup/<token> hash renders the setup screen and returns
 *       BEFORE the onboarding-status check -- no /api/onboarding/status call.
 *   (b) _employeeSetupSubmit() posts exactly one POST to
 *       /api/auth/employee/setup with {token, password}, and a successful
 *       reply swaps the card to the "Your password is set" state.
 *   (c) both halves of client-side validation (< 6 chars; mismatched
 *       confirmation) reject BEFORE any network call.
 *   (d) a server-reported failure (e.g. an expired token) renders the
 *       server's own sentence and leaves the form in place.
 *   (e) mutation proof: check (b)'s "posts to the setup endpoint" assertion
 *       is re-run against a deliberately broken copy of app-shell.js that
 *       posts to /api/auth/reset-password instead, and is required to fail
 *       there -- otherwise the check would pass whether or not this screen
 *       ever talks to the right route.
 *
 * WHY A HAND-ROLLED LIVE DOM
 *
 * Same reasoning as retail_join_shop_modal_test.js (see its header): the
 * screen mutates and re-queries the SAME live elements by id
 * (getElementById('es-pass')/('es-error')/('es-btn'), then
 * overlay.querySelector('.auth-card')), so a real tree has to exist for that
 * round trip to mean anything. The minimal DOM/mutation helpers below are
 * copied from that file's pattern (not imported -- this frontend has no
 * bundler, no jsdom, no package.json; see CLAUDE.md) and trimmed to only
 * what _showEmployeeSetupScreen/_employeeSetupSubmit actually touch.
 *
 * Run: node products/retail/tests/retail_employee_setup_link_test.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const FRONTEND_DIR = path.join(__dirname, '..', 'frontend');
const SHELL_FILE = path.join(FRONTEND_DIR, 'app-shell.js');
const SHELL_SRC = fs.readFileSync(SHELL_FILE, 'utf8');

// ─────────────────────────────────────────────────────────────────────────────
// Mutation harness (same shape as retail_join_shop_modal_test.js)
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
  return `${what}  [caught: ${String(threw.message || threw).split('\n')[0].slice(0, 100)}]`;
}

// ─────────────────────────────────────────────────────────────────────────────
// A minimal LIVE DOM -- real enough that innerHTML parses into a tree that
// createElement/appendChild/querySelector/getElementById/getAttribute all
// agree on. Trimmed subset of retail_join_shop_modal_test.js's DOM (see this
// file's header for why a real tree is needed here too).
// ─────────────────────────────────────────────────────────────────────────────

const VOID_TAGS = new Set(['area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input',
  'link', 'meta', 'param', 'source', 'track', 'wbr']);

function parseAttrs(raw) {
  const attrs = {};
  const re = /([a-zA-Z_:@][-a-zA-Z0-9_:.]*)\s*(?:=\s*("([^"]*)"|'([^']*)'|([^\s"'>]+)))?/g;
  let m;
  while ((m = re.exec(raw)) !== null) {
    const name = m[1].toLowerCase();
    const value = m[3] !== undefined ? m[3] : m[4] !== undefined ? m[4] : m[5] !== undefined ? m[5] : '';
    attrs[name] = value;
  }
  return attrs;
}

function compoundMatches(compound, el) {
  let ok = true;
  const attrRe = /\[([-a-zA-Z0-9_:.]+)(?:=("([^"]*)"|'([^']*)'|[^\]]*))?\]/g;
  let m;
  while ((m = attrRe.exec(compound)) !== null) {
    const name = m[1].toLowerCase();
    let val;
    if (m[2] !== undefined) val = m[3] !== undefined ? m[3] : (m[4] !== undefined ? m[4] : m[2]);
    const actual = el.getAttribute(name);
    if (val === undefined) { if (actual === null) ok = false; }
    else if (actual !== val) ok = false;
  }
  const rest = compound.replace(attrRe, '');
  const tokens = rest.match(/[#.]?[-a-zA-Z0-9_]+/g) || [];
  for (const tok of tokens) {
    if (tok.startsWith('#')) { if (el.id !== tok.slice(1)) ok = false; }
    else if (tok.startsWith('.')) { if (!el.classList.contains(tok.slice(1))) ok = false; }
    else if (tok.toLowerCase() !== el.tag) ok = false;
  }
  return ok;
}

function elMatches(el, selector) {
  return selector.split(',').some((raw) => {
    const parts = raw.trim().split(/\s+/).filter(Boolean);
    const compound = parts[parts.length - 1];
    return compound ? compoundMatches(compound, el) : false;
  });
}

function findById(root, id) {
  if (!root) return null;
  if (root.type === 'element' && root.id === id) return root;
  for (const c of (root.children || [])) {
    const found = findById(c, id);
    if (found) return found;
  }
  return null;
}

function queryAll(root, sel) {
  const out = [];
  const walk = (node) => {
    for (const c of (node.children || [])) {
      if (c.type === 'element') {
        if (elMatches(c, sel)) out.push(c);
        walk(c);
      }
    }
  };
  walk(root);
  return out;
}

function parseChildren(html, parentNode) {
  const rootChildren = [];
  const stack = [{ node: parentNode, children: rootChildren }];
  let i = 0;
  const top = () => stack[stack.length - 1];
  const pushText = (text) => { if (text) top().children.push({ type: 'text', text, parent: top().node }); };

  while (i < html.length) {
    const lt = html.indexOf('<', i);
    if (lt === -1) { pushText(html.slice(i)); break; }
    pushText(html.slice(i, lt));

    if (html.startsWith('<!--', lt)) { const end = html.indexOf('-->', lt + 4); i = end === -1 ? html.length : end + 3; continue; }
    if (html.startsWith('<!', lt)) { const end = html.indexOf('>', lt); i = end === -1 ? html.length : end + 1; continue; }

    if (html[lt + 1] === '/') {
      const end = html.indexOf('>', lt);
      if (end === -1) { i = html.length; break; }
      if (stack.length > 1) stack.pop();
      i = end + 1;
      continue;
    }

    const end = html.indexOf('>', lt);
    if (end === -1) { pushText(html.slice(lt)); break; }
    let inner = html.slice(lt + 1, end);
    const selfClosing = inner.endsWith('/');
    if (selfClosing) inner = inner.slice(0, -1);
    const sp = inner.search(/\s/);
    const tag = (sp === -1 ? inner : inner.slice(0, sp)).toLowerCase();
    const attrs = parseAttrs(sp === -1 ? '' : inner.slice(sp));
    const node = makeElNode(tag);
    node.attrs = attrs;
    node.parent = top().node;
    top().children.push(node);
    if (!selfClosing && !VOID_TAGS.has(tag)) stack.push({ node, children: node.children });
    i = end + 1;
  }
  return rootChildren;
}

function makeElNode(tag) {
  const el = {
    type: 'element',
    tag: (tag || 'div').toLowerCase(),
    attrs: {},
    children: [],
    parent: null,
    style: {},
    disabled: false,
    _value: undefined,
    _textOverride: null,
    get id() { return this.attrs.id || ''; },
    set id(v) { this.attrs.id = v; },
    get className() { return this.attrs.class || ''; },
    set className(v) { this.attrs.class = v; },
    getAttribute(name) { return Object.prototype.hasOwnProperty.call(this.attrs, name) ? this.attrs[name] : null; },
    setAttribute(name, value) { this.attrs[name] = String(value); },
    hasAttribute(name) { return Object.prototype.hasOwnProperty.call(this.attrs, name); },
    removeAttribute(name) { delete this.attrs[name]; },
    get value() { return this._value !== undefined ? this._value : (this.attrs.value || ''); },
    set value(v) { this._value = v; },
    get textContent() {
      if (this._textOverride !== null) return this._textOverride;
      return this.children.map((c) => (c.type === 'text' ? c.text : c.textContent)).join('');
    },
    set textContent(v) { this._textOverride = v; this.children = []; },
    get innerHTML() { return this._textOverride !== null ? this._textOverride : ''; },
    set innerHTML(html) { this._textOverride = null; this.children = parseChildren(html, this); },
    appendChild(child) { child.parent = this; this.children.push(child); return child; },
    remove() {
      if (this.parent) {
        const i = this.parent.children.indexOf(this);
        if (i !== -1) this.parent.children.splice(i, 1);
      }
    },
    closest(sel) {
      let n = this;
      while (n) { if (n.type === 'element' && compoundMatches(sel, n)) return n; n = n.parent; }
      return null;
    },
    querySelector(sel) { return queryAll(this, sel)[0] || null; },
    querySelectorAll(sel) { return queryAll(this, sel); },
    addEventListener() {},
    removeEventListener() {},
    focus() {},
    blur() {},
    get classList() {
      const self = this;
      const list = () => (self.attrs.class || '').split(/\s+/).filter(Boolean);
      return {
        add(c) { const l = list(); if (!l.includes(c)) { l.push(c); self.attrs.class = l.join(' '); } },
        remove(c) { self.attrs.class = list().filter((x) => x !== c).join(' '); },
        toggle(c, on) { const has = list().includes(c); const want = on === undefined ? !has : on; if (want) this.add(c); else this.remove(c); },
        contains(c) { return list().includes(c); },
      };
    },
  };
  return el;
}

// ─────────────────────────────────────────────────────────────────────────────
// fetch fixture — routes keyed "METHOD /path", handler(callCountForThisRoute, body)
// ─────────────────────────────────────────────────────────────────────────────

function makeFetch(routes) {
  const calls = [];
  const counts = {};
  const fn = (url, init) => {
    const method = ((init && init.method) || 'GET').toUpperCase();
    let body = null;
    if (init && typeof init.body === 'string') { try { body = JSON.parse(init.body); } catch (e) { body = init.body; } }
    const u = String(url).split('?')[0];
    calls.push({ method, url: u, body });
    const key = method + ' ' + u;
    counts[key] = (counts[key] || 0) + 1;
    const handler = routes[key];
    const result = typeof handler === 'function' ? handler(counts[key], body) : (handler || {});
    if (result instanceof Error) return Promise.reject(result);
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(result) });
  };
  fn.calls = calls;
  return fn;
}

// ─────────────────────────────────────────────────────────────────────────────
// Sandbox loader — the REAL app-shell.js, never a reimplementation.
// ─────────────────────────────────────────────────────────────────────────────

function loadApp(opts) {
  const o = opts || {};
  const shellCode = o.shellSrc || SHELL_SRC;
  const bodyEl = makeElNode('body');

  const doc = {
    body: bodyEl,
    head: makeElNode('head'),
    documentElement: { getAttribute: () => 'light', setAttribute() {}, style: { setProperty() {} } },
    readyState: 'complete',
    createElement: (tag) => makeElNode(tag || 'div'),
    getElementById: (id) => findById(bodyEl, id),
    querySelector: (sel) => queryAll(bodyEl, sel)[0] || null,
    querySelectorAll: (sel) => queryAll(bodyEl, sel),
    addEventListener() {},
  };

  const fetchFn = o.fetchFn || makeFetch(o.routes || {});

  const sandbox = {
    console,
    t: (s) => s, // identity stub -- catalog coverage is retail_localization_test.py's job
    AuraIcons: { render: () => '' },
    fetch: fetchFn,
    getComputedStyle: () => ({ getPropertyValue: () => '' }),
    navigator: { userAgent: 'Mozilla/5.0 (test)' },
    localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
    sessionStorage: { getItem: () => null, setItem() {} },
    isDemoMode: false,
    setTimeout, clearTimeout, setInterval, clearInterval,
    location: { hash: o.hash || '', href: '' }, // init() reads location.hash before anything else
    document: doc,
  };
  sandbox.window = sandbox;

  vm.createContext(sandbox);
  vm.runInContext(shellCode, sandbox, { filename: SHELL_FILE });
  assert.ok(sandbox.SubsystemApp, 'app-shell.js did not expose window.SubsystemApp');
  return { SubsystemApp: sandbox.SubsystemApp, doc, calls: fetchFn.calls };
}

// ─────────────────────────────────────────────────────────────────────────────
// (a) init() on a #setup/<token> hash renders the setup screen and returns
//     BEFORE the onboarding-status check.
// ─────────────────────────────────────────────────────────────────────────────

async function testInitRendersSetupScreenAndSkipsOnboardingCheck() {
  const ctx = loadApp({
    hash: '#setup/tok-123',
    routes: { 'GET /api/onboarding/status': () => ({ needs_setup: true }) },
  });
  await ctx.SubsystemApp.init();

  const passField = ctx.doc.getElementById('es-pass');
  const btn = ctx.doc.getElementById('es-btn');
  assert.ok(passField, 'init() on a #setup/<token> hash did not render the #es-pass field.');
  assert.ok(btn, 'init() on a #setup/<token> hash did not render the #es-btn button.');
  const title = ctx.doc.querySelector('.auth-title');
  assert.ok(title && title.textContent === 'Set your password',
    `Expected the "Set your password" title, got: ${title && title.textContent}`);

  const onboardingCalls = ctx.calls.filter((c) => c.url === '/api/onboarding/status');
  assert.strictEqual(onboardingCalls.length, 0,
    `init() called /api/onboarding/status (${onboardingCalls.length} time(s)) before returning from the ` +
    '#setup/ branch -- the setup screen must short-circuit before the onboarding check, same as ' +
    '#verify-email/ and #reset-password/ do.');
  console.log('PASS: init() on a #setup/<token> hash renders the setup screen and skips the onboarding check');
}

// ─────────────────────────────────────────────────────────────────────────────
// (b) _employeeSetupSubmit() success — posts ONLY to
//     /api/auth/employee/setup with {token, password}, and swaps the card to
//     the "Your password is set" state. [MUTATION-PROVED]
// ─────────────────────────────────────────────────────────────────────────────

async function employeeSetupSuccessFlow(shellSrc) {
  const ctx = loadApp({
    shellSrc,
    routes: { 'POST /api/auth/employee/setup': () => ({ success: true }) },
  });
  ctx.SubsystemApp._showEmployeeSetupScreen('tok-123');
  ctx.doc.getElementById('es-pass').value = 'Demo1234!';
  ctx.doc.getElementById('es-pass2').value = 'Demo1234!';
  await ctx.SubsystemApp._employeeSetupSubmit('tok-123');

  const setupCalls = ctx.calls.filter((c) => c.method === 'POST' && c.url === '/api/auth/employee/setup');
  assert.strictEqual(setupCalls.length, 1,
    `Expected exactly 1 POST to /api/auth/employee/setup, saw ${setupCalls.length}. Calls:\n` +
    JSON.stringify(ctx.calls, null, 2));
  assert.deepStrictEqual(setupCalls[0].body, { token: 'tok-123', password: 'Demo1234!' },
    'The setup POST body did not match {token, password}.');

  const overlay = ctx.doc.getElementById('aura-employee-setup-screen');
  assert.ok(overlay, 'The overlay disappeared after a successful setup.');
  const card = overlay.querySelector('.auth-card');
  assert.ok(card, 'No .auth-card left in the overlay after a successful setup.');
  const title = card.querySelector('.auth-title');
  assert.ok(title && title.textContent === 'Your password is set',
    `Expected the "Your password is set" state, got: ${title && title.textContent}`);
}

async function testEmployeeSetupSubmitSuccessPostsSetupAndRendersDone() {
  await employeeSetupSuccessFlow();
  console.log('PASS: _employeeSetupSubmit() on SUCCESS posts {token, password} to /api/auth/employee/setup and renders "Your password is set"');
}

async function testEmployeeSetupSubmitPostsCorrectRouteIsMutationProved() {
  // Insert exactly the wrong-endpoint mistake this must never make: post to
  // the password-RESET endpoint instead of the employee-SETUP endpoint.
  // Anchored to the fetch call inside _employeeSetupSubmit specifically (not
  // _resetPasswordSubmit's identically-shaped call) via the surrounding
  // showErr fallback text, which is unique to this function.
  const mutated = mutate(SHELL_SRC, [[
    `      const res = await fetch('/api/auth/employee/setup', {
        method: 'POST', credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token, password: pass }),
      });
      const data = await res.json();
      if (!data.success) return showErr(data.error || t('This setup link is invalid or has expired. Ask your admin for a new one.'));`,
    `      const res = await fetch('/api/auth/reset-password', {
        method: 'POST', credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token, password: pass }),
      });
      const data = await res.json();
      if (!data.success) return showErr(data.error || t('This setup link is invalid or has expired. Ask your admin for a new one.'));`,
  ]]);
  const msg = await provesMutation(
    'employeeSetupSubmit "posts to /api/auth/employee/setup" check survived a mutant pointing it at /api/auth/reset-password',
    () => employeeSetupSuccessFlow(mutated)
  );
  console.log('PASS: ' + msg);
}

// ─────────────────────────────────────────────────────────────────────────────
// (c) Client-side validation rejects BEFORE any network call, both halves:
//     too-short password, and mismatched confirmation.
// ─────────────────────────────────────────────────────────────────────────────

async function testShortPasswordRejectsWithoutNetworkCall() {
  const ctx = loadApp({ routes: {} }); // no route registered -- any fetch throws (undefined handler -> {} -> not an Error but would still count as a call)
  ctx.SubsystemApp._showEmployeeSetupScreen('tok-123');
  ctx.doc.getElementById('es-pass').value = 'abc';
  ctx.doc.getElementById('es-pass2').value = 'abc';
  await ctx.SubsystemApp._employeeSetupSubmit('tok-123');

  assert.strictEqual(ctx.calls.length, 0,
    `A 3-character password should be rejected client-side with no network call at all, but saw ${ctx.calls.length} call(s).`);
  const errEl = ctx.doc.getElementById('es-error');
  assert.strictEqual(errEl.textContent, 'Password must be at least 6 characters.');
  assert.strictEqual(errEl.style.display, 'block');
  console.log('PASS: a too-short password is rejected with no network call and the server\'s own sentence');
}

async function testMismatchedPasswordsRejectWithoutNetworkCall() {
  const ctx = loadApp({ routes: {} });
  ctx.SubsystemApp._showEmployeeSetupScreen('tok-123');
  ctx.doc.getElementById('es-pass').value = 'Demo1234!';
  ctx.doc.getElementById('es-pass2').value = 'Demo1234?';
  await ctx.SubsystemApp._employeeSetupSubmit('tok-123');

  assert.strictEqual(ctx.calls.length, 0,
    `Mismatched passwords should be rejected client-side with no network call at all, but saw ${ctx.calls.length} call(s).`);
  const errEl = ctx.doc.getElementById('es-error');
  assert.strictEqual(errEl.textContent, 'Passwords do not match.');
  console.log('PASS: mismatched passwords are rejected with no network call');
}

// ─────────────────────────────────────────────────────────────────────────────
// (d) Server-reported failure (e.g. an expired token) renders the server's
//     own sentence and leaves the form in place (allow-half of (b)).
// ─────────────────────────────────────────────────────────────────────────────

async function testServerFailureRendersServerReasonAndKeepsForm() {
  const ctx = loadApp({
    routes: {
      'POST /api/auth/employee/setup': () => ({ success: false, error: 'Invalid or expired setup token.' }),
    },
  });
  ctx.SubsystemApp._showEmployeeSetupScreen('tok-123');
  ctx.doc.getElementById('es-pass').value = 'Demo1234!';
  ctx.doc.getElementById('es-pass2').value = 'Demo1234!';
  await ctx.SubsystemApp._employeeSetupSubmit('tok-123');

  const errEl = ctx.doc.getElementById('es-error');
  assert.strictEqual(errEl.textContent, 'Invalid or expired setup token.');
  assert.strictEqual(errEl.style.display, 'block');
  const title = ctx.doc.querySelector('.auth-title');
  assert.ok(title && title.textContent === 'Set your password',
    `The title changed away from "Set your password" despite the server rejecting the token: ${title && title.textContent}`);
  const btn = ctx.doc.getElementById('es-btn');
  assert.strictEqual(btn.textContent, 'Set password', 'The button was not restored to its label after a server failure.');
  assert.strictEqual(btn.disabled, false, 'The button stayed disabled after a failed setup.');
  console.log('PASS: a server-reported failure renders the server\'s own sentence and leaves the form in place');
}

// ─────────────────────────────────────────────────────────────────────────────

const CHECKS = [
  ['init() renders the setup screen and skips the onboarding check', testInitRendersSetupScreenAndSkipsOnboardingCheck],
  ['_employeeSetupSubmit success posts {token, password} and renders done', testEmployeeSetupSubmitSuccessPostsSetupAndRendersDone],
  ['_employeeSetupSubmit correct-route check is mutation-proved', testEmployeeSetupSubmitPostsCorrectRouteIsMutationProved],
  ['a too-short password rejects without a network call', testShortPasswordRejectsWithoutNetworkCall],
  ['mismatched passwords reject without a network call', testMismatchedPasswordsRejectWithoutNetworkCall],
  ['a server-reported failure renders the reason and keeps the form', testServerFailureRendersServerReasonAndKeepsForm],
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
    console.error(`\nFAIL: retail_employee_setup_link_test.js — ${failures.length} of ${CHECKS.length} checks failed:`);
    for (const name of failures) console.error(`  - ${name}`);
    process.exitCode = 1;
    return;
  }
  console.log(`PASS: retail_employee_setup_link_test.js — ${CHECKS.length} checks`);
}

main().catch((err) => {
  console.error('FAIL: retail_employee_setup_link_test.js (runner)');
  console.error(err);
  process.exitCode = 1;
});
