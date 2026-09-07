/**
 * retail_join_shop_modal_test.js — "join an existing shop" doorway on the
 * setup modal (docs/launch-readiness/join-existing-shop-design.md).
 *
 * A second device (a phone, a second till) used to have to invent a
 * placeholder admin at first run just to reach activation, because
 * showSetupModal() only ever offered "create an administrator account".
 * The backend half of the real fix already existed: POST
 * /api/licensing/activate takes no session at all, and the identity rebind
 * it triggers seeds company_settings for a device that activates with no
 * admin -- the owner's real account then arrives by sync and
 * GET /api/onboarding/status flips needs_setup to false on its own. This
 * file exercises the frontend door app-shell.js now has onto that path:
 *
 *   1. showSetupModal()'s "Already have a shop?" link, gated on `needsKey`
 *      the same way the key field itself is (an install with no Owner
 *      wired up has no shop to join).
 *   2/3. _toggleJoinMode() -- hides the setup-only fields IN PLACE (so a
 *      typed name/email survives a mind change) and rewires the submit
 *      button to _joinSubmit(); toggling back re-renders from scratch.
 *   4/5. _joinSubmit() -- activation-only. The single most important
 *      behavioural claim in this file is the ABSENCE of a call: this
 *      function must never POST /api/onboarding/create-admin, because
 *      there is no admin to create on a joining device -- see the
 *      mutation proof below, which proves that absence is actually being
 *      watched and not just true by accident.
 *   6/7. checkAuthAndSetup()'s new branch -- a device that is already
 *      ACTIVE but still has no admin session (the very next launch after a
 *      successful join, once the relay address takes effect) gets the
 *      waiting screen, not the create-admin form; a device that still
 *      needs a key gets the ordinary form exactly as before.
 *   8/9. _waitForShopAccount()'s poll -- resolves into the relogin modal
 *      once the owner's account arrives, or into a two-button timeout
 *      state ("keep waiting" / "set up a new shop instead") if it never
 *      does.
 *
 * WHY A HAND-ROLLED LIVE DOM
 *
 * Every other standalone test in this directory either (a) runs
 * subsystem-retail.js in a vm sandbox against ID-keyed element STUBS that
 * are never actually linked into a tree (fine when the code under test
 * never queries by class/attribute after the fact), or (b) parses a
 * rendered HTML STRING with retail_surface_domlite.js for structural
 * checks. Neither fits here: showSetupModal()'s join-mode toggle mutates
 * individual live elements found via `overlay.querySelectorAll('[data-role
 * ="setup-only"]')` and `overlay.querySelector('.auth-card')`, then later
 * code (`_joinSubmit`, `_waitForShopAccount`) looks those same nodes back
 * up by id -- a real tree, not a redraw, has to exist for that round trip
 * to mean anything. So this file carries about 130 lines of the smallest
 * live DOM that makes createElement/innerHTML/querySelector/getElementById
 * agree with each other, and nothing more (no bundler, no jsdom, no
 * package.json in this frontend -- see CLAUDE.md).
 *
 * MUTATION-PROVED: case 5's "no create-admin call" assertion is re-run
 * against a deliberately broken copy of app-shell.js that adds exactly
 * that call, and is required to fail there.
 *
 * Run: node products/retail/tests/retail_join_shop_modal_test.js
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
// Mutation harness (same shape as retail_branches_test.js)
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
// agree on, because the code under test round-trips through all of them on
// the SAME nodes (see file header). Deliberately not a general parser: it
// handles the markup app-shell.js's auth-* family actually emits (div/p/h2/
// label/input/button/a), nothing more.
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
// Sandbox loader — the REAL app-shell.js, never a reimplementation. Only
// app-shell.js is loaded: none of the functions under test read RetailSystem
// (unlike, e.g., the sync-banner code retail_offline_banner_*_test.js covers),
// so subsystem-retail.js would add nothing but load time here.
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
    // Faithful double of window.AuraIcons: it must expose every method the
    // shell calls, or this sandbox proves the shell works against an icons
    // module that does not exist. `mark` was added to icons.js on 2026-09-08
    // (the brand mark, rendered inline in the sidebar and the setup modal)
    // and this stub did not follow, so showSetupModal threw
    // "AuraIcons.mark is not a function" -- a real crash the shell's own
    // window.AuraIcons guards would not have caught, because a PARTIAL
    // module is truthy.
    AuraIcons: { render: () => '', mark: () => '' },
    fetch: fetchFn,
    getComputedStyle: () => ({ getPropertyValue: () => '' }),
    navigator: { userAgent: 'Mozilla/5.0 (test)' },
    localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
    sessionStorage: { getItem: () => null, setItem() {} },
    isDemoMode: false,
    setTimeout, clearTimeout, setInterval, clearInterval, // REAL timers -- see _waitForShopAccount's own comment on why
    location: { hash: '', href: '' }, // init() reads location.hash before anything else
    document: doc,
  };
  sandbox.window = sandbox;

  vm.createContext(sandbox);
  vm.runInContext(shellCode, sandbox, { filename: SHELL_FILE });
  assert.ok(sandbox.SubsystemApp, 'app-shell.js did not expose window.SubsystemApp');
  return { SubsystemApp: sandbox.SubsystemApp, doc, calls: fetchFn.calls };
}

function sleep(ms) { return new Promise((resolve) => setTimeout(resolve, ms)); }

// checkAuthAndSetup()'s pre-existing fall-through call --
// `this.showSetupModal();`, unchanged by this feature -- is fire-and-forget,
// same as it always was: checkAuthAndSetup() does not await it, so its own
// promise can resolve before showSetupModal()'s internal fetch/render
// finishes. Flush a few microtask turns (same pattern retail_branches_test.js
// calls `settle()`) rather than depending on that ordering.
async function settle() {
  for (let i = 0; i < 8; i++) await Promise.resolve();
  await new Promise((resolve) => setImmediate(resolve));
}

// Real payload shapes, per commercial_runtime/licensing_contracts/
// status_presenter.py and routes.py -- NOT invented states. The backend never
// emits a bare 'ACTIVE'; an activated device answers ACTIVE_ONLINE/
// ACTIVE_OFFLINE and carries `installation_id` (present only once Owner has
// issued one). The first cut of this file used `current_state: 'ACTIVE'`,
// which passed only because the guard then admitted EVERY unlisted state --
// the exact defect these fixtures now pin.
const ACTIVE_LIC = { current_state: 'ACTIVE_ONLINE', installation_id: 'inst-rehearsal-0001' };
const NEEDS_KEY_LIC = { current_state: 'ACTIVATION_REQUIRED' };
// Activation accepted by Owner but the device still awaits approval: the
// record already carries the installation id (ActivationPending exposes it).
const PENDING_LIC = { current_state: 'ACTIVATING', installation_id: 'inst-rehearsal-0001' };
// routes.py's _not_configured_response(): no Owner URL in this build, so no
// installation_id can ever exist.
const NO_OWNER_LIC = { current_state: 'NOT_CONFIGURED', detail: 'Owner licensing URL is not configured.' };

// ─────────────────────────────────────────────────────────────────────────────
// (1) needsKey true -> the join link renders
// ─────────────────────────────────────────────────────────────────────────────

async function testJoinLinkRendersWhenKeyNeeded() {
  const ctx = loadApp({ routes: { 'GET /api/licensing/status': () => NEEDS_KEY_LIC } });
  await ctx.SubsystemApp.showSetupModal();
  const link = ctx.doc.getElementById('su-join-link');
  assert.ok(link, 'The "Already have a shop?" join link did not render when the install needs a key.');
  assert.strictEqual(link.textContent, 'Already have a shop? Join it with your licence key');
  console.log('PASS: the join link renders when the install needs a licence key');
}

// ─────────────────────────────────────────────────────────────────────────────
// (2) needsKey false -> the join link does NOT render (allow-half of (1))
// ─────────────────────────────────────────────────────────────────────────────

async function testJoinLinkAbsentWhenNoOwnerWired() {
  const ctx = loadApp({ routes: { 'GET /api/licensing/status': () => NO_OWNER_LIC } });
  await ctx.SubsystemApp.showSetupModal();
  assert.strictEqual(ctx.doc.getElementById('su-join-link'), null,
    'The join link rendered on a build with no Owner wired up -- there is no shop for it to join.');
  console.log('PASS: the join link does not render on a build with no licensing configured');
}

// ─────────────────────────────────────────────────────────────────────────────
// (3) Toggling join mode hides the setup-only fields, keeps the key field,
//     and rewires the submit button to _joinSubmit()
// ─────────────────────────────────────────────────────────────────────────────

async function testToggleJoinModeHidesFieldsAndRewiresButton(shellSrc) {
  const ctx = loadApp({ shellSrc, routes: { 'GET /api/licensing/status': () => NEEDS_KEY_LIC } });
  await ctx.SubsystemApp.showSetupModal();

  const keyField = ctx.doc.getElementById('su-key');
  assert.ok(keyField, 'No su-key field rendered even though the licence needs activating.');

  await ctx.SubsystemApp._toggleJoinMode({ preventDefault() {} });

  const setupOnly = ctx.doc.querySelectorAll('[data-role="setup-only"]');
  assert.strictEqual(setupOnly.length, 4,
    `Expected 4 setup-only wrappers (name/company grid, email field, password grid, the "runs only once" note), found ${setupOnly.length}.`);
  assert.ok(setupOnly.every((el) => el.style.display === 'none'),
    'Not every setup-only wrapper was hidden after switching to join mode.');
  assert.notStrictEqual(keyField.parent.style.display, 'none',
    'The licence-key field was hidden by join mode -- it is the one field join mode needs.');
  // The setup layout's key hint promises an account form "below" that join
  // mode does not have (seen in the Chromium screenshot, 2026-09-06).
  assert.strictEqual(
    ctx.doc.getElementById('su-key-hint').textContent,
    "The same key your shop's other device was activated with. It is in your Aura order confirmation.",
    'Join mode left the setup layout\'s "activated together with your account below" hint in place.'
  );

  assert.strictEqual(ctx.doc.getElementById('su-title').textContent, 'Join your shop');
  assert.strictEqual(
    ctx.doc.getElementById('su-sub').textContent,
    "Enter the licence key your shop already uses. Your account will arrive from your shop's other device."
  );
  const btn = ctx.doc.getElementById('su-btn');
  assert.strictEqual(btn.textContent, 'Join shop');
  assert.strictEqual(btn.getAttribute('onclick'), 'SubsystemApp._joinSubmit()',
    'The submit button was not rewired to _joinSubmit() by join mode.');
  assert.strictEqual(ctx.doc.getElementById('su-join-link').textContent, 'Set up a new shop instead');

  console.log('PASS: toggling join mode hides the setup-only fields, keeps the key field, and rewires the button');
}

// ─────────────────────────────────────────────────────────────────────────────
// (4) Toggling back restores the ordinary setup form (allow-half of (3))
// ─────────────────────────────────────────────────────────────────────────────

async function testToggleBackRestoresSetupForm() {
  const ctx = loadApp({ routes: { 'GET /api/licensing/status': () => NEEDS_KEY_LIC } });
  await ctx.SubsystemApp.showSetupModal();
  await ctx.SubsystemApp._toggleJoinMode({ preventDefault() {} });
  await ctx.SubsystemApp._toggleJoinMode({ preventDefault() {} }); // back

  assert.strictEqual(ctx.doc.getElementById('su-title').textContent, 'Welcome to Action Aura');
  assert.strictEqual(ctx.doc.getElementById('su-btn').getAttribute('onclick'), 'SubsystemApp._setupSubmit()');
  assert.strictEqual(ctx.doc.getElementById('su-join-link').textContent,
    'Already have a shop? Join it with your licence key');
  const setupOnly = ctx.doc.querySelectorAll('[data-role="setup-only"]');
  assert.ok(setupOnly.every((el) => el.style.display !== 'none'),
    'A setup-only field stayed hidden after toggling back out of join mode.');
  console.log('PASS: toggling back out of join mode restores the ordinary setup form');
}

// ─────────────────────────────────────────────────────────────────────────────
// (5) _joinSubmit success — POSTs ONLY to /api/licensing/activate, never to
//     /api/onboarding/create-admin, and renders the "Connected" state
//     [MUTATION-PROVED]
// ─────────────────────────────────────────────────────────────────────────────

async function joinSubmitSuccessFlow(shellSrc) {
  const ctx = loadApp({
    shellSrc,
    routes: { 'GET /api/licensing/status': () => NEEDS_KEY_LIC, 'POST /api/licensing/activate': () => ({ result: 'SUCCESS' }) },
  });
  await ctx.SubsystemApp.showSetupModal();
  await ctx.SubsystemApp._toggleJoinMode({ preventDefault() {} });
  ctx.doc.getElementById('su-key').value = '  aura-retail-join-code  ';
  await ctx.SubsystemApp._joinSubmit();

  const createAdminCalls = ctx.calls.filter((c) => c.url === '/api/onboarding/create-admin');
  assert.strictEqual(createAdminCalls.length, 0,
    `_joinSubmit() called /api/onboarding/create-admin (${createAdminCalls.length} time(s)) -- a joining device ` +
    'has no admin to create. Calls:\n' + JSON.stringify(ctx.calls, null, 2));

  const activateCalls = ctx.calls.filter((c) => c.method === 'POST' && c.url === '/api/licensing/activate');
  assert.strictEqual(activateCalls.length, 1, `Expected exactly 1 POST to /api/licensing/activate, saw ${activateCalls.length}.`);
  assert.strictEqual(activateCalls[0].body.license_key, 'AURA-RETAIL-JOIN-CODE',
    'The activate POST did not carry the trimmed/uppercased key.');

  const overlay = ctx.doc.getElementById('aura-relogin-modal');
  assert.ok(overlay, 'The overlay disappeared after a successful join.');
  const card = overlay.querySelector('.auth-card');
  assert.ok(card, 'No .auth-card left in the overlay after a successful join.');
  const title = card.querySelector('.auth-title');
  assert.ok(title && title.textContent === 'Connected to your shop',
    `Expected the "Connected to your shop" state, got: ${title && title.textContent}`);
  const continueBtn = ctx.doc.getElementById('su-join-continue-btn');
  assert.ok(continueBtn, 'No continue button rendered after a successful join.');
  assert.strictEqual(continueBtn.getAttribute('onclick'), 'SubsystemApp._waitForShopAccount()');
}

async function testJoinSubmitSuccessPostsOnlyActivateAndRendersConnected() {
  await joinSubmitSuccessFlow();
  console.log('PASS: _joinSubmit() on SUCCESS posts only to /api/licensing/activate and renders "Connected to your shop"');
}

async function testJoinSubmitNoCreateAdminCallIsMutationProved() {
  // Insert exactly the call this function must never make, anchored to text
  // unique to _joinSubmit (NOT _setupSubmit's differently-worded key guard).
  const mutated = mutate(SHELL_SRC, [[
    `    if (!key) return showErr('A license key is required.');

    if (btn) { btn.textContent = t('Joining…'); btn.disabled = true; }
    if (errEl) errEl.style.display = 'none';`,
    `    if (!key) return showErr('A license key is required.');

    if (btn) { btn.textContent = t('Joining…'); btn.disabled = true; }
    if (errEl) errEl.style.display = 'none';
    await fetch('/api/onboarding/create-admin', { method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: 'Mutant', email: 'mutant@test.local', password: 'x', company_name: 'Mutant Co' }) }).catch(() => {});`,
  ]]);
  const msg = await provesMutation(
    'joinSubmit no-create-admin check survived a mutant that adds exactly that call',
    () => joinSubmitSuccessFlow(mutated)
  );
  console.log('PASS: ' + msg);
}

// ─────────────────────────────────────────────────────────────────────────────
// (6) _joinSubmit failure — renders the reason, never the connected state
//     (allow-half of (5))
// ─────────────────────────────────────────────────────────────────────────────

async function testJoinSubmitFailureRendersReasonNotConnected() {
  const ctx = loadApp({
    routes: {
      'GET /api/licensing/status': () => NEEDS_KEY_LIC,
      'POST /api/licensing/activate': () => ({ result: 'FAILED', detail: 'Key already used by another company.' }),
    },
  });
  await ctx.SubsystemApp.showSetupModal();
  await ctx.SubsystemApp._toggleJoinMode({ preventDefault() {} });
  ctx.doc.getElementById('su-key').value = 'AURA-RETAIL-USED-CODE';
  await ctx.SubsystemApp._joinSubmit();

  const errEl = ctx.doc.getElementById('su-error');
  assert.strictEqual(errEl.textContent, 'Key already used by another company.');
  assert.strictEqual(errEl.style.display, 'block');
  const btn = ctx.doc.getElementById('su-btn');
  assert.strictEqual(btn.textContent, 'Join shop', 'The button was not restored to its join-mode label after a failure.');
  assert.strictEqual(btn.disabled, false, 'The button stayed disabled after a failed join.');
  assert.strictEqual(ctx.doc.getElementById('su-join-continue-btn'), null,
    'The "Connected to your shop" state rendered despite the activation failing.');
  console.log('PASS: _joinSubmit() on failure renders the reason and never the connected state');
}

// ─────────────────────────────────────────────────────────────────────────────
// (7)/(8) checkAuthAndSetup() — ACTIVE+needs_setup waits for the shop
//     account; an unactivated licence still gets the ordinary setup modal
// ─────────────────────────────────────────────────────────────────────────────

async function testCheckAuthAndSetupWaitsWhenAlreadyActive() {
  const ctx = loadApp({
    routes: {
      'GET /api/onboarding/status': () => ({ needs_setup: true }),
      'GET /api/licensing/status': () => ACTIVE_LIC,
    },
  });
  await ctx.SubsystemApp.checkAuthAndSetup();
  const title = ctx.doc.querySelector('.auth-title');
  assert.ok(title && title.textContent === 'Connecting to your shop…',
    `Expected the "Connecting to your shop…" waiting screen for an ACTIVE, needs_setup device, got: ${title && title.textContent}`);
  ctx.SubsystemApp._stopWaitForShopAccountPoll();
  console.log('PASS: checkAuthAndSetup() waits for the shop account on an already-ACTIVE device');
}

async function testCheckAuthAndSetupShowsSetupModalWhenUnactivated() {
  const ctx = loadApp({
    routes: {
      'GET /api/onboarding/status': () => ({ needs_setup: true }),
      'GET /api/licensing/status': () => NEEDS_KEY_LIC,
    },
  });
  await ctx.SubsystemApp.checkAuthAndSetup();
  await settle();
  const title = ctx.doc.getElementById('su-title');
  assert.ok(title && title.textContent === 'Welcome to Action Aura',
    `Expected the ordinary setup modal for an unactivated device, got: ${title && title.textContent}`);
  assert.ok(ctx.doc.getElementById('su-join-link'), 'The join link is missing even though this device still needs a key.');
  ctx.SubsystemApp._stopWaitForShopAccountPoll();
  console.log('PASS: checkAuthAndSetup() still shows the ordinary setup modal for an unactivated device');
}

// (8b) A build with no Owner wired up -- /api/licensing/status answers
// NOT_CONFIGURED with a `detail` and, decisively, no `installation_id` -- has
// no shop to join, so needs_setup must still open the ordinary setup form,
// never the "Connecting to your shop…" wait. This is the case the first cut
// of the guard got wrong: it inverted _needsActivation(), which is false here
// too, so a never-licensed install would have sat on the waiting screen for
// the full 120 s ceiling before being offered setup.
async function testCheckAuthAndSetupShowsSetupModalWhenNoOwnerWired() {
  const ctx = loadApp({
    routes: {
      'GET /api/onboarding/status': () => ({ needs_setup: true }),
      'GET /api/licensing/status': () => NO_OWNER_LIC,
    },
  });
  // try/finally: when this check FAILS the app is sitting on the waiting
  // screen with a real 3 s interval armed, and without the stop the node
  // process never exits (the red run before the guard fix hung for 120 s).
  try {
    await ctx.SubsystemApp.checkAuthAndSetup();
    await settle();
    const title = ctx.doc.getElementById('su-title');
    assert.ok(title && title.textContent === 'Welcome to Action Aura',
      `Expected the ordinary setup modal on a build with no Owner wired up, got: ${title && title.textContent}`);
    assert.ok(!ctx.doc.getElementById('su-join-link'), 'The join link rendered on a build that has no shop to join.');
  } finally {
    ctx.SubsystemApp._stopWaitForShopAccountPoll();
  }
  console.log('PASS: checkAuthAndSetup() shows the ordinary setup modal on a build with no Owner wired up');
}

// (8c) A device whose activation is still PENDING Owner approval (ACTIVATING)
// already carries an installation_id, so "holds an installation" on its own
// would send it to the waiting screen -- but its owner's account cannot
// arrive until Owner approves the device, and the setup modal is where the
// pending state is rendered. Pins the second half of the guard.
async function testCheckAuthAndSetupShowsSetupModalWhilePendingApproval() {
  const ctx = loadApp({
    routes: {
      'GET /api/onboarding/status': () => ({ needs_setup: true }),
      'GET /api/licensing/status': () => PENDING_LIC,
    },
  });
  try {
    await ctx.SubsystemApp.checkAuthAndSetup();
    await settle();
    const title = ctx.doc.getElementById('su-title');
    assert.ok(title && title.textContent === 'Welcome to Action Aura',
      `Expected the ordinary setup modal while activation is pending approval, got: ${title && title.textContent}`);
  } finally {
    ctx.SubsystemApp._stopWaitForShopAccountPoll();
  }
  console.log('PASS: checkAuthAndSetup() shows the ordinary setup modal while activation is pending approval');
}

// ─────────────────────────────────────────────────────────────────────────────
// (8d)/(8e) init() -- the REAL boot path. The first cut of the door put the
//     join-aware branch in checkAuthAndSetup() only, and the checks above
//     call that function directly, so they passed while a real launch --
//     init() reads needs_setup itself, first -- still opened the setup modal
//     on a till that had just joined (measured in Chromium, 2026-09-06).
//     These two drive init() and would have gone red on that build.
// ─────────────────────────────────────────────────────────────────────────────

async function testInitWaitsForShopAccountOnJoinedDevice() {
  const ctx = loadApp({
    routes: {
      'GET /api/onboarding/status': () => ({ needs_setup: true }),
      'GET /api/licensing/status': () => ACTIVE_LIC,
    },
  });
  try {
    await ctx.SubsystemApp.init();
    await settle();
    const title = ctx.doc.querySelector('.auth-title');
    assert.ok(title && title.textContent === 'Connecting to your shop…',
      `Expected init() to open the "Connecting to your shop…" wait on a joined device, got: ${title && title.textContent}`);
    assert.ok(!ctx.doc.getElementById('su-btn'), 'init() offered the setup modal to a device that has already joined a shop.');
  } finally {
    ctx.SubsystemApp._stopWaitForShopAccountPoll();
  }
  console.log('PASS: init() waits for the shop account on a device that has already joined');
}

async function testInitShowsSetupModalWhenUnactivated() {
  const ctx = loadApp({
    routes: {
      'GET /api/onboarding/status': () => ({ needs_setup: true }),
      'GET /api/licensing/status': () => NEEDS_KEY_LIC,
    },
  });
  try {
    await ctx.SubsystemApp.init();
    await settle();
    const title = ctx.doc.getElementById('su-title');
    assert.ok(title && title.textContent === 'Welcome to Action Aura',
      `Expected init() to open the ordinary setup modal on an unactivated device, got: ${title && title.textContent}`);
  } finally {
    ctx.SubsystemApp._stopWaitForShopAccountPoll();
  }
  console.log('PASS: init() still opens the ordinary setup modal on an unactivated device');
}

// ─────────────────────────────────────────────────────────────────────────────
// (9)/(10) _waitForShopAccount() polling — flips to the relogin modal, or
//     times out into the two-button "keep waiting / set up new" state
// ─────────────────────────────────────────────────────────────────────────────

async function testWaitForShopAccountFlipsToReloginOnceSynced() {
  const ctx = loadApp({
    routes: {
      'GET /api/licensing/status': () => NO_OWNER_LIC,
      // First poll: still waiting. Second poll: the owner's account has landed.
      'GET /api/onboarding/status': (n) => ({ needs_setup: n < 2 }),
    },
  });
  await ctx.SubsystemApp.showSetupModal(); // builds the overlay _waitForShopAccount reuses
  ctx.SubsystemApp._waitForShopAccount(5, 200); // 5ms interval, 200ms ceiling -- real, but short
  await sleep(60); // several ticks -- comfortably enough for the 2nd poll to land

  const overlay = ctx.doc.getElementById('aura-relogin-modal');
  assert.ok(overlay, 'No modal left after the shop account arrived.');
  const sub = overlay.querySelector('.auth-sub');
  assert.ok(sub && sub.textContent === 'Your shop is connected. Sign in with your account.',
    `Expected the relogin modal with the connected sentence, got: ${sub && sub.textContent}`);
  assert.strictEqual(ctx.SubsystemApp._waitApprovalTimer, null, 'The poll timer was not stopped after success.');
  console.log('PASS: _waitForShopAccount() shows the relogin modal once the shop account arrives');
}

async function testWaitForShopAccountTimesOutToTwoButtonState() {
  const ctx = loadApp({
    routes: {
      'GET /api/licensing/status': () => NO_OWNER_LIC,
      'GET /api/onboarding/status': () => ({ needs_setup: true }), // never flips
    },
  });
  await ctx.SubsystemApp.showSetupModal();
  ctx.SubsystemApp._waitForShopAccount(5, 15); // 3 ticks (5/10/15) to a 15ms ceiling
  await sleep(60);

  const overlay = ctx.doc.getElementById('aura-relogin-modal');
  assert.ok(overlay, 'The overlay disappeared on timeout -- it should stay, with a way forward.');
  const title = overlay.querySelector('.auth-title');
  assert.ok(title && title.textContent === 'Still waiting for your account. Check the connection, or set up a new shop instead.',
    `Expected the timeout headline, got: ${title && title.textContent}`);
  const retryBtn = ctx.doc.getElementById('su-wait-retry-btn');
  const setupBtn = ctx.doc.getElementById('su-wait-setup-btn');
  assert.ok(retryBtn, 'No "Keep waiting" button rendered on timeout.');
  assert.strictEqual(retryBtn.textContent, 'Keep waiting');
  assert.ok(setupBtn, 'No "Set up a new shop instead" button rendered on timeout.');
  assert.strictEqual(setupBtn.getAttribute('onclick'), 'SubsystemApp.showSetupModal()');
  assert.strictEqual(ctx.SubsystemApp._waitApprovalTimer, null, 'The poll timer was not stopped after timing out.');
  console.log('PASS: _waitForShopAccount() times out into the "keep waiting / set up new shop" state');
}

// ─────────────────────────────────────────────────────────────────────────────

const CHECKS = [
  ['join link renders when a key is needed', testJoinLinkRendersWhenKeyNeeded],
  ['join link absent when no Owner is wired up', testJoinLinkAbsentWhenNoOwnerWired],
  ['toggling join mode hides fields and rewires the button', () => testToggleJoinModeHidesFieldsAndRewiresButton()],
  ['toggling back restores the ordinary setup form', testToggleBackRestoresSetupForm],
  ['_joinSubmit success posts only activate and renders connected', testJoinSubmitSuccessPostsOnlyActivateAndRendersConnected],
  ['_joinSubmit no-create-admin check is mutation-proved', testJoinSubmitNoCreateAdminCallIsMutationProved],
  ['_joinSubmit failure renders the reason, not connected', testJoinSubmitFailureRendersReasonNotConnected],
  ['checkAuthAndSetup waits when already ACTIVE', testCheckAuthAndSetupWaitsWhenAlreadyActive],
  ['checkAuthAndSetup shows setup modal when unactivated', testCheckAuthAndSetupShowsSetupModalWhenUnactivated],
  ['checkAuthAndSetup shows setup modal when no Owner is wired up', testCheckAuthAndSetupShowsSetupModalWhenNoOwnerWired],
  ['checkAuthAndSetup shows setup modal while pending approval', testCheckAuthAndSetupShowsSetupModalWhilePendingApproval],
  ['init() waits for the shop account on a joined device', testInitWaitsForShopAccountOnJoinedDevice],
  ['init() shows setup modal when unactivated', testInitShowsSetupModalWhenUnactivated],
  ['_waitForShopAccount flips to relogin once synced', testWaitForShopAccountFlipsToReloginOnceSynced],
  ['_waitForShopAccount times out to the two-button state', testWaitForShopAccountTimesOutToTwoButtonState],
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
    console.error(`\nFAIL: retail_join_shop_modal_test.js — ${failures.length} of ${CHECKS.length} checks failed:`);
    for (const name of failures) console.error(`  - ${name}`);
    process.exitCode = 1;
    return;
  }
  console.log(`PASS: retail_join_shop_modal_test.js — ${CHECKS.length} checks`);
}

main().catch((err) => {
  console.error('FAIL: retail_join_shop_modal_test.js (runner)');
  console.error(err);
  process.exitCode = 1;
});
