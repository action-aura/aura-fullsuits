/**
 * retail_p0_shell_ai_status_test.js -- P0 review-fix gaps handed to crew C4
 * that had no committed test anywhere before this file:
 *
 *   1. sub-ai.js's status pill (item #18, AI). No repo test drove the real
 *      GET /api/sub/<sys>/ai/status contract or a failed chat reply -- the
 *      only proof was a job-scratch live_check.py CI never runs. Reverting
 *      _idleStatusText() to the old unconditional `t('Online')` would pass
 *      every OTHER suite in this repo untouched.
 *   2. app-shell.js's `retail.reports` capability gate on the AI button
 *      (item #18/#12-shell), in BOTH places it is applied: the desktop
 *      sidebar (_renderShell) and the phone "More" sheet (_openMoreSheet).
 *      Both allow- and deny-halves.
 *   3. app-shell.js's _shopDisplayName() fallback chain (item #12-shell):
 *      branding business_name wins over the legacy branding_business_name.
 *      Only Android/C2's server-side test existed for the underlying
 *      fallback; this client method itself had nothing.
 *
 * P0 review-fix (2026-09-27), added to this same file rather than a new one
 * (both are the AI panel/status system this file already drives):
 *   4. The sidebar '.ai-pulse' dot (app-shell.js render + sub-ai.js
 *      _syncPulseDot()) must render only once SubAI has a real
 *      configured:true answer, in both directions -- absent until then,
 *      appears once true, and disappears again if a later check answers
 *      false (an endpoint that stops being configured must not keep a
 *      stale "online" dot).
 *   5. sub-ai.js's _finishStream(): a stream that reports SUCCESS but
 *      produced zero fragments must still set _lastCallFailed = true, or
 *      _setBusy(false) right after it leaves the pill on "Online" beside a
 *      bubble telling the user the assistant is unavailable -- the same lie
 *      part 1 above already removes for a genuinely FAILED reply, reached
 *      here instead through the "succeeded but said nothing" gap.
 *
 * All five drive the REAL frontend files (never a reimplementation), the
 * same way retail_einvoicing_status_test.js drives einvoicing.js and
 * retail_shell_chrome_test.js drives app-shell.js -- see each section below
 * for why its own DOM stub is shaped the way it is.
 *
 * Run: node products/retail/tests/retail_p0_shell_ai_status_test.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const FRONTEND_DIR = path.join(__dirname, '..', 'frontend');
const SUB_AI_FILE = path.join(FRONTEND_DIR, 'sub-ai.js');
const SHELL_FILE = path.join(FRONTEND_DIR, 'app-shell.js');
const ICONS_FILE = path.join(FRONTEND_DIR, 'icons.js');
const SUB_AI_SRC = fs.readFileSync(SUB_AI_FILE, 'utf8');
const SHELL_SRC = fs.readFileSync(SHELL_FILE, 'utf8');
const ICONS_SRC = fs.readFileSync(ICONS_FILE, 'utf8');

// These four are literal id constants inside sub-ai.js's IIFE (PANEL_ID,
// MESSAGES_ID, INPUT_ID, STATUS_ID, SEND_ID, ORB_ID) -- not reachable from
// outside it, so mirrored here by their known values. TYPING_ID is never
// needed: it is created and destroyed entirely inside sub-ai.js and this
// file never asserts on it directly.
const PANEL_ID = 'sub-ai-panel';
const MESSAGES_ID = 'sub-ai-messages';
const INPUT_ID = 'sub-ai-input';
const ORB_ID = 'sub-ai-orb';
const STATUS_ID = 'sub-ai-status';
const SEND_ID = 'sub-ai-send';
// The pulse-dot pair (P0 review-fix, 2026-09-27) -- SIDEBAR_BTN_ID/PULSE_ID
// inside sub-ai.js, and the same SIDEBAR_BTN_ID literal app-shell.js writes
// on `.sub-ai-btn`'s id attribute (see that render site's own comment).
const SIDEBAR_BTN_ID = 'sub-ai-sidebar-btn';
const PULSE_ID = 'sub-ai-pulse-dot';

// ─────────────────────────────────────────────────────────────────────────────
// Mutation harness (same shape as retail_einvoicing_status_test.js /
// retail_shell_chrome_test.js)
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

// ═════════════════════════════════════════════════════════════════════════════
// PART 1 — sub-ai.js's status pill: never claims "Online" unless the
//          endpoint is actually configured, across a real status check
//          (configured/not-configured/network-error) and a real failed chat.
//          [MUTATION-PROVED]
// ═════════════════════════════════════════════════════════════════════════════

// A minimal, mutable node -- children/textContent/classList/attrs/style, the
// shapes sub-ai.js actually touches (see its own open/close/_checkStatus/
// _setBusy/_buildMessageRow). Unlike retail_einvoicing_status_test.js's
// makeNode, this one supports classList (open()/close() toggle 'hidden')
// and an optional onTextSet hook -- the STATUS node uses it below to record
// every value sub-ai.js ever writes into the pill, in order, which is what
// "the observed status sequence never contains Online unless configured"
// (the item's own required test) needs: not a sample at one instant, but
// the whole transcript.
function makeNode(tag, opts) {
  const onTextSet = (opts && opts.onTextSet) || null;
  const node = {
    tag: (tag || 'div').toLowerCase(),
    id: '',
    className: '',
    disabled: false,
    value: '',
    parent: null,
    children: [],
    _text: '',
    _innerHTML: '',
    _attrs: {},
    _listeners: {},
    style: {},
    scrollTop: 0,
    scrollHeight: 0,
    get firstChild() { return this.children.length ? this.children[0] : null; },
    get textContent() {
      if (!this.children.length) return this._text;
      return this.children.map((c) => c.textContent).join('');
    },
    set textContent(v) {
      this._text = v == null ? '' : String(v);
      this.children = [];
      if (onTextSet) onTextSet(this._text);
    },
    set innerHTML(v) { this._innerHTML = v == null ? '' : String(v); this.children = []; this._text = ''; },
    get innerHTML() { return this._innerHTML; },
    appendChild(child) { child.parent = this; this.children.push(child); return child; },
    removeChild(child) {
      const i = this.children.indexOf(child);
      if (i !== -1) this.children.splice(i, 1);
      return child;
    },
    remove() { if (this.parent) this.parent.removeChild(this); },
    setAttribute(name, v) { this._attrs[name] = String(v); },
    getAttribute(name) { return name in this._attrs ? this._attrs[name] : null; },
    addEventListener(type, fn) { this._listeners[type] = fn; },
    // P0 review-fix -- open() schedules a REAL `setTimeout(() =>
    // input.focus(), 50)` against this sandbox's real setTimeout (see
    // loadSubAI's sandbox below: setTimeout/clearTimeout are passed through
    // unmocked, deliberately, because _checkStatus()'s own timing is part of
    // what this file proves). Without a stub here, that timer fires 50ms
    // later against Node's real event loop, calls .focus() on a plain
    // object, and throws OUTSIDE every test's own try/catch -- an uncaught
    // exception that crashed this whole file partway through a run (it
    // surfaced on whichever check happened to be executing 50ms after some
    // earlier check's open() call, not on the check that actually caused
    // it, so it looked unrelated to the panel-focus behaviour that triggered
    // it). A no-op is correct here: nothing in this file asserts on focus.
    focus() {},
    classList: {
      _set: new Set(),
      add(c) { this._set.add(c); },
      remove(c) { this._set.delete(c); },
      contains(c) { return this._set.has(c); },
    },
  };
  return node;
}

function findAllNodes(root, predicate) {
  const out = [];
  const walk = (n) => {
    if (predicate(n)) out.push(n);
    for (const c of n.children) walk(c);
  };
  walk(root);
  return out;
}

function makeFetch(routes) {
  return (url, init) => {
    const method = ((init && init.method) || 'GET').toUpperCase();
    const u = String(url).split('?')[0];
    const key = method + ' ' + u;
    const handler = routes[key];
    if (!handler) {
      return Promise.reject(new Error(`No stubbed route for ${key}`));
    }
    // Promise.resolve().then(...) so a handler that THROWS or returns a
    // rejected promise (the "network error" scenarios below) rejects this
    // fetch the same way a real network failure would -- sub-ai.js's own
    // try/catch around fetch() is exactly what is under test there.
    return Promise.resolve().then(() => handler(init));
  };
}

function okJson(body) {
  return { ok: true, status: 200, headers: { get: () => 'application/json' }, json: () => Promise.resolve(body) };
}

async function settle() {
  for (let i = 0; i < 8; i++) await Promise.resolve();
  await new Promise((resolve) => setImmediate(resolve));
}

// Loads the REAL sub-ai.js. Its own `_inject()` short-circuits the instant
// `document.getElementById(PANEL_ID)` returns something truthy (see its
// source), so pre-seeding a panel node here means the real HTML template in
// `_panelHTML()` is never parsed -- deliberate: nothing this file asserts on
// reads that markup, and reproducing an HTML parser here would be exactly
// the kind of machinery retail_shell_chrome_test.js's own header explains
// this frontend's test files avoid when a string/tree check is enough. The
// STATUS_ID node's initial text is seeded directly on `_text` (bypassing the
// logging setter) so `statusLog` below captures only what sub-ai.js itself
// writes, matching the item's own "observed status sequence" framing.
function loadSubAI(opts) {
  const o = opts || {};
  // P0 review-fix -- this used to ignore `o.scriptSrc` entirely and always
  // run the real, unmutated SUB_AI_SRC below, regardless of what a caller
  // passed. That made every mutation proof in this section vacuous: the
  // mutated source was built and handed in, but the sandbox never actually
  // ran it, so `testMutationProvedIdleStatusTextGuard` "passed" only
  // because the guard it was supposed to be testing was never exercised in
  // its broken form at all -- MUTATION SURVIVED, caught by re-running this
  // file after the focus() fix below. Same fallback shape as
  // retail_einvoicing_status_test.js's loadAndRenderStatus.
  const scriptSrc = o.scriptSrc || SUB_AI_SRC;
  const statusLog = [];

  const root = makeNode('div');
  const panel = makeNode('div'); panel.id = PANEL_ID; panel.classList.add('hidden'); panel.setAttribute('aria-hidden', 'true');
  const messages = makeNode('div'); messages.id = MESSAGES_ID;
  const input = makeNode('textarea'); input.id = INPUT_ID;
  const orb = makeNode('div'); orb.id = ORB_ID;
  const status = makeNode('span', { onTextSet: (v) => statusLog.push(v) }); status.id = STATUS_ID; status._text = 'Checking…';
  const send = makeNode('button'); send.id = SEND_ID;
  [panel, messages, input, orb, status, send].forEach((n) => root.appendChild(n));
  // P0 review-fix -- app-shell.js's `.sub-ai-btn` (the pulse dot lives
  // inside it, see sub-ai.js's own _syncPulseDot()). Present by default,
  // matching the ordinary case (a role with retail.reports); `o.noSidebarBtn`
  // opts a test OUT, to prove the dot-sync never throws when that button
  // was never rendered at all (no retail.reports -- the deny half app-
  // shell.js's own `hasAI` gate already covers, see that check's own test
  // section below).
  let sidebarBtn = null;
  if (!o.noSidebarBtn) {
    sidebarBtn = makeNode('button');
    sidebarBtn.id = SIDEBAR_BTN_ID;
    root.appendChild(sidebarBtn);
  }

  const doc = {
    body: { insertAdjacentHTML() { /* never reached -- panel is pre-seeded, see comment above */ } },
    getElementById: (id) => findAllNodes(root, (n) => n.id === id)[0] || null,
    createElement: (tag) => makeNode(tag),
  };

  const fetchFn = makeFetch(o.routes || {});
  const sandbox = {
    console,
    document: doc,
    fetch: fetchFn,
    setTimeout, clearTimeout,
    AbortController,
    // sub-ai.js calls the bare global `t(...)` directly (it depends on
    // i18n.js having already defined it -- see the file's own header), never
    // through a `window.t` fallback the way einvoicing.js does. Identity
    // stand-in, same as retail_shell_chrome_test.js's own sandbox.
    t: (s) => s,
    window: {},
  };
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(scriptSrc, sandbox, { filename: SUB_AI_FILE });
  assert.ok(sandbox.SubAI, 'sub-ai.js did not expose window.SubAI');

  return { SubAI: sandbox.SubAI, statusLog, inputNode: input, panelNode: panel, sidebarBtnNode: sidebarBtn, findAllNodes: (pred) => findAllNodes(root, pred) };
}

// One status route (configured/not-configured/network-error) + optionally a
// chat attempt against a stubbed /ai/chat, returning the full ordered
// transcript of every value the status pill was set to.
async function driveStatusSequence({ statusRoute, chatRoute, scriptSrc, noSidebarBtn }) {
  const routes = { 'GET /api/sub/retail/ai/status': statusRoute };
  if (chatRoute) routes['POST /api/sub/retail/ai/chat'] = chatRoute;
  const { SubAI, statusLog, inputNode } = loadSubAI({ routes, scriptSrc, noSidebarBtn });

  SubAI.open('retail');
  await settle();
  if (chatRoute) {
    inputNode.value = 'hello';
    await SubAI.send();
    await settle();
  }
  return statusLog;
}

function assertNeverOnlineUnlessConfigured(log, configured, label) {
  const sawOnline = log.includes('Online');
  if (!configured) {
    assert.ok(
      !sawOnline,
      `${label}: the status pill claimed "Online" at some point while the endpoint was NOT configured. ` +
      `Full sequence: ${JSON.stringify(log)}`
    );
  }
}

async function testStatusSequenceWhenConfigured() {
  const log = await driveStatusSequence({ statusRoute: () => okJson({ configured: true }) });
  assert.deepStrictEqual(log, ['Checking…', 'Online'],
    `Expected ['Checking…','Online'], got ${JSON.stringify(log)}`);
  assertNeverOnlineUnlessConfigured(log, true, 'configured=true');
  console.log('PASS: configured=true -> status sequence is exactly [Checking…, Online]');
}

async function testStatusSequenceWhenNotConfigured() {
  const log = await driveStatusSequence({ statusRoute: () => okJson({ configured: false }) });
  assert.deepStrictEqual(log, ['Checking…', 'Not set up'],
    `Expected ['Checking…','Not set up'], got ${JSON.stringify(log)}`);
  assertNeverOnlineUnlessConfigured(log, false, 'configured=false');
  console.log('PASS: configured=false -> status sequence is exactly [Checking…, Not set up], never "Online"');
}

async function testStatusSequenceOnNetworkError() {
  const log = await driveStatusSequence({ statusRoute: () => { throw new Error('network down'); } });
  assert.deepStrictEqual(log, ['Checking…', 'Not set up'],
    `A network failure on the STATUS check itself must resolve to "Not set up" (there is no reply that ` +
    `failed yet, so "Unavailable" would overclaim) -- got ${JSON.stringify(log)}`);
  assertNeverOnlineUnlessConfigured(log, false, 'status check network error');
  console.log('PASS: a network error on GET .../ai/status -> [Checking…, Not set up], never "Online"');
}

async function testStatusSequenceAfterFailedChat(scriptSrc) {
  const log = await driveStatusSequence({
    statusRoute: () => okJson({ configured: true }),
    // Non-streaming legacy-fallback shape (see sub-ai.js's own branch on
    // Content-Type: application/json) with success:false -- the real,
    // supported "server answered but the reply failed" path, not a
    // test-only shortcut. Exercises exactly the same throw/catch/
    // _finishStream(..., true) route a genuine 503 or empty reply would.
    chatRoute: () => okJson({ success: false, error: 'boom' }),
    scriptSrc,
  });
  assert.deepStrictEqual(log, ['Checking…', 'Online', 'Thinking…', 'Unavailable'],
    `Expected [Checking…, Online, Thinking…, Unavailable], got ${JSON.stringify(log)}`);
  // The specific regression this guards: _setBusy(false) used to hardcode
  // t('Online') regardless of what just happened, so a FAILED reply still
  // left the pill claiming the endpoint was fine.
  assert.notStrictEqual(log[log.length - 1], 'Online',
    `A failed chat reply must never leave the pill on "Online". Full sequence: ${JSON.stringify(log)}`);
  return log;
}

async function testAssertNeverOnlineAfterFailedChat() {
  await testStatusSequenceAfterFailedChat();
  console.log('PASS: a failed chat reply (configured=true) ends the sequence on "Unavailable", never "Online"');
}

async function testMutationProvedIdleStatusTextGuard() {
  // The exact old bug: _idleStatusText() unconditionally claimed 'Online'
  // once past the null/lastCallFailed checks, ignoring `_configured`. This
  // is the regression retail_ai_unconfigured_test.py's server-side test
  // (GET /ai/status itself) cannot see at all -- it never touches this
  // CLIENT method, which is exactly the gap item #18/#12-shell's own AI
  // bullet named ("With no endpoint configured, the panel never shows
  // Online") and nothing in this repo tested before this file.
  const mutated = mutate(SUB_AI_SRC, [[
    `      return this._configured ? t('Online') : t('Not set up');`,
    `      return t('Online'); /* mutant: always claim Online once past null/lastCallFailed */`,
  ]]);
  const { message } = { message: await provesMutation(
    '"never Online unless configured" guard survived a mutant that always claims Online',
    async () => {
      const log = await driveStatusSequence({ statusRoute: () => okJson({ configured: false }), scriptSrc: mutated });
      assertNeverOnlineUnlessConfigured(log, false, 'mutated configured=false');
    }
  ) };
  // Re-prove both directions against the REAL, unmutated file.
  await testStatusSequenceWhenNotConfigured();
  await testAssertNeverOnlineAfterFailedChat();
  console.log('PASS: ' + message);
  console.log('PASS: guard passes again against the restored, unmutated sub-ai.js');
}

// ═════════════════════════════════════════════════════════════════════════════
// PART 1b — sub-ai.js _finishStream(): a stream that reports SUCCESS but
//           produced zero fragments must still flip the pill off "Online",
//           the same way a genuinely failed reply already does.
//           [MUTATION-PROVED]
// ═════════════════════════════════════════════════════════════════════════════

// The real NDJSON shape send() falls back to reading whole when
// `res.body.getReader` is unavailable (see send()'s own "No ReadableStream
// support" branch) -- a single `{"done":true}` line with no `{"delta":...}`
// line before it, i.e. exactly what a healthy request that produced no
// tokens at all looks like on the wire. `Content-Type` is deliberately NOT
// 'application/json' (that is the OTHER, non-streaming legacy branch
// testStatusSequenceAfterFailedChat above already covers) -- this must hit
// the streaming branch and still end up with `bubble === null, acc === ''`.
function emptyNdjsonStreamRoute() {
  return {
    ok: true,
    status: 200,
    headers: { get: (h) => (String(h).toLowerCase() === 'content-type' ? 'application/x-ndjson' : null) },
    text: () => Promise.resolve('{"done":true}\n'),
  };
}

async function testEmptySuccessfulStreamEndsOnUnavailableNotOnline(scriptSrc) {
  const log = await driveStatusSequence({
    statusRoute: () => okJson({ configured: true }),
    chatRoute: emptyNdjsonStreamRoute,
    scriptSrc,
  });
  assert.deepStrictEqual(log, ['Checking…', 'Online', 'Thinking…', 'Unavailable'],
    `A successful-but-empty stream must show the unavailable message AND flip the pill to "Unavailable", ` +
    `never leave it claiming "Online" beside that message -- expected [Checking…, Online, Thinking…, ` +
    `Unavailable], got ${JSON.stringify(log)}`);
  assert.notStrictEqual(log[log.length - 1], 'Online',
    `A successful-but-empty stream must never leave the pill on "Online". Full sequence: ${JSON.stringify(log)}`);
  return log;
}

async function testAssertNeverOnlineAfterEmptySuccessfulStream() {
  await testEmptySuccessfulStreamEndsOnUnavailableNotOnline();
  console.log('PASS: a successful-but-empty stream (configured=true) ends the sequence on "Unavailable", never "Online"');
}

async function testMutationProvedEmptyStreamSetsLastCallFailed() {
  // The exact gap this guards: _finishStream()'s `else if (!bubble)` branch
  // (stream succeeded, zero fragments) used to append UNAVAILABLE_MSG()
  // without ever touching `_lastCallFailed`, so `_setBusy(false)` right
  // after it (send()'s `finally`) fell through `_idleStatusText()`'s
  // `_configured` branch and re-claimed "Online".
  const mutated = mutate(SUB_AI_SRC, [[
    `      } else if (!bubble) {
        // Stream reported success but produced zero fragments -- treat the
        // same as the non-streaming route's "empty reply" 503 case. P0
        // review-fix (2026-09-27): this branch used to leave _lastCallFailed
        // untouched, so _setBusy(false) right after this (send()'s \`finally\`)
        // fell through to _idleStatusText()'s _configured branch and claimed
        // "Online" beside a bubble telling the user the assistant is
        // unavailable -- the exact lie the \`failed\` branch above already
        // removes for a genuinely FAILED reply, reached here instead through
        // the "succeeded but said nothing" gap.
        this._lastCallFailed = true;
        this._appendMessage('assistant', UNAVAILABLE_MSG());
      }`,
    `      } else if (!bubble) {
        this._appendMessage('assistant', UNAVAILABLE_MSG()); /* mutant: drop _lastCallFailed = true */
      }`,
  ]]);
  const message = await provesMutation(
    'empty-successful-stream "must not return to Online" guard survived a mutant that drops _lastCallFailed',
    async () => {
      const log = await driveStatusSequence({
        statusRoute: () => okJson({ configured: true }),
        chatRoute: emptyNdjsonStreamRoute,
        scriptSrc: mutated,
      });
      assert.notStrictEqual(log[log.length - 1], 'Online',
        `pill must not be "Online" after an empty stream. Full sequence: ${JSON.stringify(log)}`);
    }
  );
  // Re-prove against the REAL, unmutated file.
  await testAssertNeverOnlineAfterEmptySuccessfulStream();
  console.log('PASS: ' + message);
  console.log('PASS: guard passes again against the restored, unmutated sub-ai.js');
}

// ═════════════════════════════════════════════════════════════════════════════
// PART 1c — the sidebar '.ai-pulse' dot's LIVE half: sub-ai.js's own
//           _syncPulseDot(), called from _checkStatus(). Proved here on the
//           real sub-ai.js in isolation; app-shell.js's RENDER-time half
//           (the dot is absent from the very first paint, before any status
//           answer exists) is proved separately in PART 2b below, on the
//           real app-shell.js. [MUTATION-PROVED, both directions]
// ═════════════════════════════════════════════════════════════════════════════

function hasPulseDot(sidebarBtnNode) {
  return !!(sidebarBtnNode && sidebarBtnNode.children.some((c) => c.id === PULSE_ID));
}

async function testPulseDotAbsentBeforeAnyStatusCheck() {
  const { sidebarBtnNode } = loadSubAI({});
  assert.strictEqual(hasPulseDot(sidebarBtnNode), false,
    'the sidebar button must carry no pulse dot before _checkStatus() has ever run (no answer yet).');
  console.log('PASS: the sidebar pulse dot is absent before any status check has ever run');
}

async function testPulseDotAppearsOnceConfiguredTrue(scriptSrc) {
  const routes = { 'GET /api/sub/retail/ai/status': () => okJson({ configured: true }) };
  const { SubAI, sidebarBtnNode } = loadSubAI({ routes, scriptSrc });
  assert.strictEqual(hasPulseDot(sidebarBtnNode), false, 'must start with no dot');
  SubAI.open('retail');
  await settle();
  assert.strictEqual(hasPulseDot(sidebarBtnNode), true,
    'the sidebar button must gain the pulse dot once GET .../ai/status answers configured:true.');
}

async function testPulseDotAllowHalf() {
  await testPulseDotAppearsOnceConfiguredTrue();
  console.log('PASS: the sidebar pulse dot appears once SubAI has a real configured:true answer (allow-half)');
}

async function testPulseDotStaysAbsentWhenConfiguredFalse() {
  const routes = { 'GET /api/sub/retail/ai/status': () => okJson({ configured: false }) };
  const { SubAI, sidebarBtnNode } = loadSubAI({ routes });
  SubAI.open('retail');
  await settle();
  assert.strictEqual(hasPulseDot(sidebarBtnNode), false,
    'the sidebar button must carry no pulse dot when GET .../ai/status answers configured:false (deny-half).');
  console.log('PASS: the sidebar pulse dot stays absent when the endpoint answers configured:false (deny-half)');
}

async function testPulseDotIsRemovedWhenALaterCheckAnswersFalse() {
  // Both directions of the LIVE sync, not just "turn it on": an endpoint
  // that answered true once and later answers false (re-opening the panel
  // after the endpoint was unconfigured, or a flaky config) must lose the
  // dot again -- a dot that can only ever turn on is a ratchet, not a
  // status indicator, and would itself become a stale "online" claim.
  let configured = true;
  const routes = { 'GET /api/sub/retail/ai/status': () => okJson({ configured }) };
  const { SubAI, sidebarBtnNode } = loadSubAI({ routes });
  SubAI.open('retail');
  await settle();
  assert.strictEqual(hasPulseDot(sidebarBtnNode), true, 'precondition: dot must be present after configured:true');
  configured = false;
  await SubAI._checkStatus();
  await settle();
  assert.strictEqual(hasPulseDot(sidebarBtnNode), false,
    'the pulse dot must be REMOVED once a later status check answers configured:false, not left stale.');
  console.log('PASS: the sidebar pulse dot is removed again once a later status check answers configured:false');
}

async function testPulseDotSyncNeverThrowsWithoutASidebarButton() {
  // The deny half of app-shell.js's OWN hasAI gate (no retail.reports, or
  // the module is not licensed): no `.sub-ai-btn` is ever rendered at all.
  // _syncPulseDot() must be a no-op here, never throw.
  const routes = { 'GET /api/sub/retail/ai/status': () => okJson({ configured: true }) };
  const { SubAI } = loadSubAI({ routes, noSidebarBtn: true });
  await assert.doesNotReject(async () => {
    SubAI.open('retail');
    await settle();
  }, 'SubAI.open()/_checkStatus() must not throw when the sidebar AI button was never rendered.');
  console.log('PASS: the pulse-dot sync is a no-op (never throws) when no sidebar AI button was rendered');
}

async function testMutationProvedPulseDotSync() {
  const mutated = mutate(SUB_AI_SRC, [[
    `      if (!this._sending) this._setStatusText(this._idleStatusText());
      this._syncPulseDot();
    },`,
    `      if (!this._sending) this._setStatusText(this._idleStatusText());
      /* mutant: drop the pulse-dot sync call entirely */
    },`,
  ]]);
  const message = await provesMutation(
    'sidebar pulse-dot sync survived a mutant that never calls _syncPulseDot() from _checkStatus()',
    async () => {
      const routes = { 'GET /api/sub/retail/ai/status': () => okJson({ configured: true }) };
      const { SubAI, sidebarBtnNode } = loadSubAI({ routes, scriptSrc: mutated });
      SubAI.open('retail');
      await settle();
      assert.strictEqual(hasPulseDot(sidebarBtnNode), true, 'dot should have appeared');
    }
  );
  // Re-prove both directions against the REAL, unmutated file.
  await testPulseDotAllowHalf();
  await testPulseDotStaysAbsentWhenConfiguredFalse();
  console.log('PASS: ' + message);
  console.log('PASS: guard passes again against the restored, unmutated sub-ai.js');
}

// ═════════════════════════════════════════════════════════════════════════════
// PART 2 — app-shell.js's `retail.reports` capability gate on the AI button,
//          in BOTH places it applies (_renderShell sidebar, _openMoreSheet
//          phone row). [MUTATION-PROVED, both sites]
// ═════════════════════════════════════════════════════════════════════════════

// Same shape as retail_shell_chrome_test.js's own makeElementStub/loadShell/
// renderShellHTML/renderMoreSheetHTML -- kept local rather than imported
// (that file exports nothing; every test file in this frontend carries its
// own small stub tailored to what it drives, e.g. this one adds nothing
// retail_shell_chrome_test.js does not already have, it is simply not a
// shared module). Only what App._renderShell()/_openMoreSheet() actually
// touch during render: innerHTML as a plain string, not a live tree.
function makeElementStub() {
  return {
    innerHTML: '',
    textContent: '',
    value: '',
    id: '',
    className: '',
    style: {},
    dataset: {},
    classList: { toggle() {}, add() {}, remove() {}, contains() { return false; } },
    appendChild() {},
    addEventListener() {},
    removeEventListener() {},
    remove() {},
    getAttribute() { return null; },
    setAttribute() {},
    querySelector() { return makeElementStub(); },
    querySelectorAll() { return []; },
  };
}

function loadShell(shellSrc) {
  const store = {};
  const sandbox = {
    console,
    t: (s) => s,
    setTimeout, clearTimeout, setInterval, clearInterval,
    navigator: { userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)' },
    location: { hash: '', href: 'http://localhost/' },
    localStorage: {
      getItem: (k) => (k in store ? store[k] : null),
      setItem: (k, v) => { store[k] = String(v); },
      removeItem: (k) => { delete store[k]; },
    },
    document: {
      readyState: 'complete',
      body: makeElementStub(),
      head: { appendChild() {} },
      documentElement: {
        getAttribute() { return null; },
        setAttribute() {},
        style: { setProperty() {} },
      },
      getElementById() { return null; },
      createElement() { return makeElementStub(); },
      querySelector() { return null; },
      querySelectorAll() { return []; },
      addEventListener() {},
    },
  };
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(ICONS_SRC, sandbox, { filename: ICONS_FILE });
  assert.ok(sandbox.AuraIcons, 'icons.js did not expose window.AuraIcons');
  vm.runInContext(shellSrc || SHELL_SRC, sandbox, { filename: SHELL_FILE });
  assert.ok(sandbox.SubsystemApp, 'app-shell.js did not expose window.SubsystemApp');
  return sandbox;
}

// capabilities: array (hasCapability checks .includes) -- NEVER null/
// undefined here, unlike retail_shell_chrome_test.js's own fixture, which
// deliberately relies on hasCapability()'s fail-OPEN default. This file's
// whole point is the DENY half, which fail-open would silently hide.
//
// subAI: what `window.SubAI` is at render time (P0 review-fix, 2026-09-27,
// pulse-dot gating below) -- defaults to `{_configured: null}`, the REAL
// shape at first paint (sub-ai.js's own load-time default, before
// _checkStatus() has ever run; see PART 2b). A caller proving the allow-half
// passes `{_configured: true}`; `null`/`undefined` here proves the
// button must survive `window.SubAI` not existing at all (script load order
// changed, or a future refactor) without throwing.
function renderShellHTML(shellSrc, capabilities, subAI) {
  const sandbox = loadShell(shellSrc);
  sandbox.window.SubAI = subAI === undefined ? { _configured: null } : subAI;
  const App = sandbox.SubsystemApp;
  App.capabilities = capabilities;
  App.role = 'admin';
  App.isAdminDevice = true;
  App.activeModules = ['all'];
  App.active = 'retail';
  App.branding = {};

  const shellEl = makeElementStub();
  sandbox.document.getElementById = (id) => (id === 'subsystem-shell' ? shellEl : null);
  App._renderShell(App.systems.retail, 'retail');
  return shellEl.innerHTML;
}

function renderMoreSheetHTML(shellSrc, capabilities) {
  const sandbox = loadShell(shellSrc);
  const App = sandbox.SubsystemApp;
  App.capabilities = capabilities;
  App.role = 'admin';
  App.isAdminDevice = true;
  App.activeModules = ['all'];
  App.active = 'retail';
  App.branding = {};
  App.currentSection = 'dashboard';

  const appended = [];
  sandbox.document.body = Object.assign(makeElementStub(), {
    appendChild(el) { appended.push(el); },
  });
  sandbox.document.getElementById = (id) => appended.find((el) => el.id === id) || null;
  App._openMoreSheet();
  const sheet = appended.find((el) => el.id === 'sub-more-sheet');
  assert.ok(sheet, '_openMoreSheet() never appended a #sub-more-sheet element.');
  return sheet.innerHTML;
}

function testShellAIButtonShownWithReportsCapability() {
  const html = renderShellHTML(undefined, ['retail.reports']);
  assert.ok(html.includes('SubAI.open('),
    'The sidebar AI button did not render for a role that HAS retail.reports.');
  console.log('PASS: sidebar AI button renders when the session has retail.reports (allow-half)');
}

function testShellAIButtonHiddenWithoutReportsCapability() {
  const html = renderShellHTML(undefined, ['retail.sell']);
  assert.ok(!html.includes('SubAI.open('),
    'The sidebar AI button rendered for a role with NO retail.reports grant -- the same capability ' +
    'the /ai/status and /ai/chat routes themselves are gated on server-side (retail_api.py).');
  console.log('PASS: sidebar AI button is hidden when the session lacks retail.reports (deny-half)');
}

function testMoreSheetAIRowShownWithReportsCapability() {
  const html = renderMoreSheetHTML(undefined, ['retail.reports']);
  assert.ok(html.includes('SubAI.open('),
    'The More sheet AI row did not render for a role that HAS retail.reports.');
  console.log('PASS: More-sheet AI row renders when the session has retail.reports (allow-half)');
}

function testMoreSheetAIRowHiddenWithoutReportsCapability() {
  const html = renderMoreSheetHTML(undefined, ['retail.sell']);
  assert.ok(!html.includes('SubAI.open('),
    'The More sheet AI row rendered for a role with NO retail.reports grant.');
  console.log('PASS: More-sheet AI row is hidden when the session lacks retail.reports (deny-half)');
}

async function testMutationProvedShellCapabilityGate() {
  const mutated = mutate(SHELL_SRC, [[
    `    // door that only ever answers "no" for that person.
    const hasAI = this.activeModules && (this.activeModules.includes('all') || this.activeModules.includes('ai_agent'))
      && this.hasCapability('retail.reports');`,
    `    // door that only ever answers "no" for that person.
    const hasAI = this.activeModules && (this.activeModules.includes('all') || this.activeModules.includes('ai_agent'));
      /* mutant: drop the SIDEBAR retail.reports gate */`,
  ]]);
  const message = await provesMutation(
    'sidebar AI-button capability gate survived a mutant that drops the retail.reports check',
    async () => {
      const html = renderShellHTML(mutated, ['retail.sell']);
      assert.ok(!html.includes('SubAI.open('), 'button should have been hidden');
    }
  );
  testShellAIButtonShownWithReportsCapability();
  testShellAIButtonHiddenWithoutReportsCapability();
  console.log('PASS: ' + message);
  console.log('PASS: guard passes again against the restored, unmutated app-shell.js');
}

async function testMutationProvedMoreSheetCapabilityGate() {
  const mutated = mutate(SHELL_SRC, [[
    `    // above; see that comment for why.
    const hasAI = this.activeModules && (this.activeModules.includes('all') || this.activeModules.includes('ai_agent'))
      && this.hasCapability('retail.reports');`,
    `    // above; see that comment for why.
    const hasAI = this.activeModules && (this.activeModules.includes('all') || this.activeModules.includes('ai_agent'));
      /* mutant: drop the MORE-SHEET retail.reports gate */`,
  ]]);
  const message = await provesMutation(
    'More-sheet AI-row capability gate survived a mutant that drops the retail.reports check',
    async () => {
      const html = renderMoreSheetHTML(mutated, ['retail.sell']);
      assert.ok(!html.includes('SubAI.open('), 'row should have been hidden');
    }
  );
  testMoreSheetAIRowShownWithReportsCapability();
  testMoreSheetAIRowHiddenWithoutReportsCapability();
  console.log('PASS: ' + message);
  console.log('PASS: guard passes again against the restored, unmutated app-shell.js');
}

// ═════════════════════════════════════════════════════════════════════════════
// PART 2b — app-shell.js's RENDER-time half of the sidebar pulse dot: the
//           button's markup must include the dot ONLY when
//           `window.SubAI._configured === true` at the moment `_renderShell`
//           builds it. The LIVE half (sub-ai.js learning the real answer
//           afterward and updating the already-rendered button) is PART 1c
//           above, on the real sub-ai.js; this proves the OTHER file,
//           app-shell.js, on its own. [MUTATION-PROVED]
// ═════════════════════════════════════════════════════════════════════════════

function testSidebarPulseDotAbsentAtFirstPaint() {
  // The real default: window.SubAI._configured starts null (sub-ai.js has
  // not checked yet) -- this is what every first render of the shell sees.
  const html = renderShellHTML(undefined, ['retail.reports'], { _configured: null });
  assert.ok(!html.includes('class="ai-pulse"'),
    'the sidebar AI button must carry no pulse dot at first paint, before any status answer exists.');
  console.log('PASS: the sidebar AI button renders with no pulse dot before any status answer exists (window.SubAI._configured === null)');
}

function testSidebarPulseDotShownWhenConfiguredTrue() {
  const html = renderShellHTML(undefined, ['retail.reports'], { _configured: true });
  assert.ok(html.includes('class="ai-pulse"'),
    'the sidebar AI button must render the pulse dot when window.SubAI._configured === true.');
  console.log('PASS: the sidebar AI button renders the pulse dot when window.SubAI._configured === true (allow-half)');
}

function testSidebarPulseDotHiddenWhenConfiguredFalse() {
  const html = renderShellHTML(undefined, ['retail.reports'], { _configured: false });
  assert.ok(!html.includes('class="ai-pulse"'),
    'the sidebar AI button must carry no pulse dot when window.SubAI._configured === false.');
  console.log('PASS: the sidebar AI button renders with no pulse dot when window.SubAI._configured === false (deny-half)');
}

function testSidebarRendersWithNoThrowWhenSubAIMissingEntirely() {
  // window.SubAI not existing at all (a future script-order change, or this
  // script loading standalone) must never throw the whole render.
  const html = renderShellHTML(undefined, ['retail.reports'], null);
  assert.ok(html.includes('SubAI.open('), 'the button itself must still render');
  assert.ok(!html.includes('class="ai-pulse"'), 'and with no dot, since there is no real answer to show one for');
  console.log('PASS: the sidebar renders (button, no dot) without throwing when window.SubAI does not exist at all');
}

async function testMutationProvedSidebarPulseDotGating() {
  const mutated = mutate(SHELL_SRC, [[
    `            \${window.SubAI && window.SubAI._configured === true ? '<span class="ai-pulse" id="sub-ai-pulse-dot"></span>' : ''}`,
    `            <span class="ai-pulse" id="sub-ai-pulse-dot"></span> <!-- mutant: always-on pulse dot -->`,
  ]]);
  const message = await provesMutation(
    'sidebar pulse-dot render gate survived a mutant that always renders the dot',
    () => {
      const html = renderShellHTML(mutated, ['retail.reports'], { _configured: false });
      assert.ok(!html.includes('class="ai-pulse"'), 'dot should have been absent for configured:false');
    }
  );
  // Re-prove all three renders against the REAL, unmutated app-shell.js.
  testSidebarPulseDotAbsentAtFirstPaint();
  testSidebarPulseDotShownWhenConfiguredTrue();
  testSidebarPulseDotHiddenWhenConfiguredFalse();
  console.log('PASS: ' + message);
  console.log('PASS: guard passes again against the restored, unmutated app-shell.js');
}

// ═════════════════════════════════════════════════════════════════════════════
// PART 3 — _shopDisplayName(): business_name wins, falls back to the legacy
//          branding_business_name. [MUTATION-PROVED]
// ═════════════════════════════════════════════════════════════════════════════

function testShopDisplayNamePrefersBusinessName() {
  const sandbox = loadShell();
  const App = sandbox.SubsystemApp;
  App.branding = { business_name: 'Sweets & Co', branding_business_name: 'Old Name Ltd' };
  assert.strictEqual(App._shopDisplayName(), 'Sweets & Co',
    'business_name must win over the legacy branding_business_name when both are present.');
  console.log('PASS: _shopDisplayName() prefers business_name over branding_business_name');
}

function testShopDisplayNameFallsBackToLegacyField() {
  const sandbox = loadShell();
  const App = sandbox.SubsystemApp;
  App.branding = { business_name: '', branding_business_name: 'Old Name Ltd' };
  assert.strictEqual(App._shopDisplayName(), 'Old Name Ltd',
    'With business_name empty, _shopDisplayName() must fall back to branding_business_name -- ' +
    'backward compatible with a server that has not deployed the new field yet.');
  console.log('PASS: _shopDisplayName() falls back to branding_business_name when business_name is empty');
}

function testShopDisplayNameNullWhenBothEmpty() {
  const sandbox = loadShell();
  const App = sandbox.SubsystemApp;
  App.branding = {};
  assert.strictEqual(App._shopDisplayName(), null,
    'With neither field set, _shopDisplayName() must return null so callers can fall back to a ' +
    'product-name default (document.title\'s own "|| \'Aura Retail\'") rather than rendering an empty span.');
  console.log('PASS: _shopDisplayName() returns null when neither field is set (no empty span/title)');
}

async function testMutationProvedShopDisplayNamePreference() {
  const mutated = mutate(SHELL_SRC, [[
    `    return (b.business_name || b.branding_business_name || '').trim() || null;`,
    `    return (b.branding_business_name || b.business_name || '').trim() || null; /* mutant: legacy field wins */`,
  ]]);
  const message = await provesMutation(
    '"business_name wins" guard survived a mutant that prefers the legacy field instead',
    () => {
      const sandbox = loadShell(mutated);
      const App = sandbox.SubsystemApp;
      App.branding = { business_name: 'Sweets & Co', branding_business_name: 'Old Name Ltd' };
      assert.strictEqual(App._shopDisplayName(), 'Sweets & Co');
    }
  );
  testShopDisplayNamePrefersBusinessName();
  testShopDisplayNameFallsBackToLegacyField();
  console.log('PASS: ' + message);
  console.log('PASS: guard passes again against the restored, unmutated app-shell.js');
}

// ─────────────────────────────────────────────────────────────────────────────

const CHECKS = [
  ['AI status: configured=true -> [Checking…, Online]', testStatusSequenceWhenConfigured],
  ['AI status: configured=false -> never "Online"', testStatusSequenceWhenNotConfigured],
  ['AI status: network error on status check -> "Not set up", never "Online"', testStatusSequenceOnNetworkError],
  ['AI status: failed chat reply ends on "Unavailable", never "Online"', testAssertNeverOnlineAfterFailedChat],
  ['AI status: "never Online unless configured" guard is mutation-proved', testMutationProvedIdleStatusTextGuard],
  ['AI status: a successful-but-empty stream ends on "Unavailable", never "Online"', testAssertNeverOnlineAfterEmptySuccessfulStream],
  ['AI status: empty-stream _lastCallFailed guard is mutation-proved', testMutationProvedEmptyStreamSetsLastCallFailed],
  ['AI pulse dot: absent before any status check has run', testPulseDotAbsentBeforeAnyStatusCheck],
  ['AI pulse dot: appears once configured:true (allow-half)', testPulseDotAllowHalf],
  ['AI pulse dot: stays absent when configured:false (deny-half)', testPulseDotStaysAbsentWhenConfiguredFalse],
  ['AI pulse dot: removed again once a later check answers false', testPulseDotIsRemovedWhenALaterCheckAnswersFalse],
  ['AI pulse dot: sync never throws with no sidebar button rendered', testPulseDotSyncNeverThrowsWithoutASidebarButton],
  ['AI pulse dot: live sync (sub-ai.js) is mutation-proved', testMutationProvedPulseDotSync],
  ['shell: sidebar AI button shown with retail.reports', testShellAIButtonShownWithReportsCapability],
  ['shell: sidebar AI button hidden without retail.reports', testShellAIButtonHiddenWithoutReportsCapability],
  ['shell: More-sheet AI row shown with retail.reports', testMoreSheetAIRowShownWithReportsCapability],
  ['shell: More-sheet AI row hidden without retail.reports', testMoreSheetAIRowHiddenWithoutReportsCapability],
  ['shell: sidebar capability gate is mutation-proved', testMutationProvedShellCapabilityGate],
  ['shell: More-sheet capability gate is mutation-proved', testMutationProvedMoreSheetCapabilityGate],
  ['shell: sidebar pulse dot absent at first paint (render-time gate)', testSidebarPulseDotAbsentAtFirstPaint],
  ['shell: sidebar pulse dot shown when window.SubAI._configured===true', testSidebarPulseDotShownWhenConfiguredTrue],
  ['shell: sidebar pulse dot hidden when window.SubAI._configured===false', testSidebarPulseDotHiddenWhenConfiguredFalse],
  ['shell: sidebar renders with no dot and no throw when window.SubAI is missing entirely', testSidebarRendersWithNoThrowWhenSubAIMissingEntirely],
  ['shell: sidebar pulse-dot render gate is mutation-proved', testMutationProvedSidebarPulseDotGating],
  ['shell: _shopDisplayName() prefers business_name', testShopDisplayNamePrefersBusinessName],
  ['shell: _shopDisplayName() falls back to branding_business_name', testShopDisplayNameFallsBackToLegacyField],
  ['shell: _shopDisplayName() returns null when both are empty', testShopDisplayNameNullWhenBothEmpty],
  ['shell: _shopDisplayName() preference is mutation-proved', testMutationProvedShopDisplayNamePreference],
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
    console.error(`\nFAIL: retail_p0_shell_ai_status_test.js — ${failures.length} of ${CHECKS.length} checks failed:`);
    for (const name of failures) console.error(`  - ${name}`);
    process.exitCode = 1;
    return;
  }
  console.log(`PASS: retail_p0_shell_ai_status_test.js — ${CHECKS.length} checks`);
}

main().catch((err) => {
  console.error('FAIL: retail_p0_shell_ai_status_test.js (runner)');
  console.error(err);
  process.exitCode = 1;
});
