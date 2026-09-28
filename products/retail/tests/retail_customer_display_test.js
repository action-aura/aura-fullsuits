/**
 * retail_customer_display_test.js — the customer-facing second screen
 * (retail-hardware-viewports).
 *
 * WHY A VM HARNESS, NOT A REGEX SCAN
 *
 * "customer-display.js never throws with no BroadcastChannel" and "an empty
 * 'cart' message while the thank-you screen is up does not cut it short" are
 * claims about BEHAVIOUR, not shape -- a regex over the source can confirm a
 * try/catch exists, but not that the catch actually leaves the right screen
 * visible. So this loads the REAL customer-display.js (no copy, no fixture)
 * into a Node `vm` context with a minimal DOM/BroadcastChannel/timer stub and
 * drives its actual state machine, the same way
 * retail_attribution_i18n_test.py's probe.js runs the real app-shell.js under
 * node rather than re-describing it. Timers are FAKE and manually fired
 * (never a real `setTimeout` wait) so the 8-second auto-revert is proven
 * without the suite taking 8 seconds.
 *
 * WHAT THIS HARNESS CANNOT SEE (AUDIT #9 -- read before trusting a green run
 * here): the fake `makeFakeElement()`/`makeFakeDocument()` DOM below has NO
 * layout engine and NO CSS cascade at all -- `hidden` is just a JS boolean
 * property on a plain object, with no notion of specificity, `!important`,
 * or which of two competing rules actually wins. Before 2026-09-27 this
 * suite passed 12 of 12 checks while `.cd-screen[hidden]{display:none}` (no
 * `!important`) lost to the ID rules `#cd-idle/#cd-active/#cd-complete
 * {display:flex}` in every REAL browser, so all three screens rendered
 * stacked and simultaneously "visible" -- the state machine this file
 * exercises was entirely correct the whole time; the screen the state
 * machine drove was invisible. A green run here proves the STATE MACHINE
 * (which screen the code THINKS is showing); it can never prove or refute
 * the CSS defect itself. That proof -- to_be_in_viewport, elementFromPoint,
 * and #cd-idle's own COMPUTED style, none of which exist in a `vm` sandbox
 * with no renderer -- lives in retail_smoke_e2e.py's real-Chromium scenario,
 * and only there. `testCdScreenHiddenRuleUsesImportant` below is a cheap,
 * static, non-browser backstop for the exact regression (checks the source
 * text of the rule itself), not a substitute for that real-browser proof.
 *
 * MUTATION-PROVEN, PER CLAUDE.md's engineering standard: every guard below
 * (BroadcastChannel absent/throws, empty-cart-during-thank-you ignored,
 * non-empty-cart-during-thank-you interrupts, the 8s timer actually firing,
 * the #11 relay poll's seq dedupe, the #12 shop-name fallback latch) is
 * exercised via the state that TRIGGERS it, not merely inferred from the
 * source existing.
 *
 * MONEY: this file also grep-proves that neither customer-display.js nor
 * subsystem-retail.js's payload builder invents its own currency formatting
 * -- see testCustomerDisplayNeverReformatsMoney and
 * testPayloadBuilderUsesTillsOwnFormatter. The live-browser proof (POS total
 * JD 42.970 == display total JD 42.970, JOD's 3 decimals intact) is in the
 * task report; this is the regression guard that keeps it true.
 *
 *   node products/retail/tests/retail_customer_display_test.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const FRONTEND_DIR = path.join(__dirname, '..', 'frontend');
const CD_JS = path.join(FRONTEND_DIR, 'customer-display.js');
const CD_HTML = path.join(FRONTEND_DIR, 'customer-display.html');
const RETAIL_JS = path.join(FRONTEND_DIR, 'subsystem-retail.js');

const CD_SOURCE = fs.readFileSync(CD_JS, 'utf8');

// Every element id customer-display.js reads or writes, so the fake DOM can
// never silently hand back `null` for one the file actually uses -- if the
// markup and the script drift, THIS throws (TypeError: cannot set property
// of null) rather than the test passing while checking a no-op.
// DERIVED FROM THE MARKUP, not hand-listed. This was an array typed out by
// hand, which meant it described customer-display.html as it stood the day it
// was written: six ids added later (the loyalty row, the amount-due band) were
// missing from it, so byId() handed those branches `null` and they were never
// exercised -- precisely the silent no-op this list exists to prevent.
//
// Reading the ids out of the real markup keeps the original guarantee intact:
// an id the SCRIPT touches but the MARKUP never declares is still absent here,
// so it still returns null and still throws, which is the drift we want loud.
const ELEMENT_IDS = (function () {
  const html = fs.readFileSync(CD_HTML, 'utf8');
  const ids = [];
  const re = /\sid="([^"]+)"/g;
  let m;
  while ((m = re.exec(html))) ids.push(m[1]);
  const unique = Array.from(new Set(ids));
  // Anti-vacuity: an empty or near-empty scan would build a fake document with
  // no elements, every byId() would return null, and the suite would report on
  // nothing. Pin both a floor and two ids that must always exist.
  assert.ok(unique.length >= 15,
    'only ' + unique.length + ' ids scraped from customer-display.html -- the fake DOM would be empty');
  ['cd-idle', 'cd-total-value'].forEach(function (id) {
    assert.ok(unique.indexOf(id) !== -1, 'expected id ' + id + ' missing from customer-display.html');
  });
  return unique;
}());

function makeFakeElement() {
  return { hidden: undefined, textContent: '', innerHTML: '', src: '', title: '' };
}

// Records every attribute _applyShopMeta() (#11) sets on <html> (lang/dir/
// data-theme) so a test can assert on them directly, without needing a real
// DOM's own attribute storage.
function makeFakeDocumentElement() {
  const attrs = {};
  return {
    _attrs: attrs,
    setAttribute(name, value) { attrs[name] = value; },
    getAttribute(name) { return Object.prototype.hasOwnProperty.call(attrs, name) ? attrs[name] : null; },
  };
}

function makeFakeDocument() {
  const elements = {};
  ELEMENT_IDS.forEach((id) => { elements[id] = makeFakeElement(); });
  return {
    _elements: elements,
    readyState: 'complete',   // boot() runs synchronously, no DOMContentLoaded wait
    title: '',
    documentElement: makeFakeDocumentElement(),
    getElementById(id) { return elements[id] || null; },
    addEventListener() {},   // readyState is never 'loading' here, so unused -- present so a call never throws
  };
}

// Deterministic, manually-advanced timers: setTimeout/setInterval never fire
// on their own. fireAllTimeouts() simulates "however long it takes" without
// the suite actually waiting -- see the module comment.
function makeFakeTimers() {
  let nextId = 1;
  const timeouts = new Map();
  return {
    setTimeout(fn) { const id = nextId++; timeouts.set(id, fn); return id; },
    clearTimeout(id) { timeouts.delete(id); },
    setInterval(fn) { const id = nextId++; timeouts.set(id, fn); return id; }, // treated the same as a one-shot for this suite's purposes -- nothing here needs it to repeat
    clearInterval(id) { timeouts.delete(id); },
    pendingCount() { return timeouts.size; },
    fireAll() {
      const fns = Array.from(timeouts.values());
      timeouts.clear();
      fns.forEach((fn) => fn());
    },
  };
}

// One fake BroadcastChannel per test -- captures every posted message and
// lets the test itself play the till by invoking `.onmessage` directly. Two
// independent instances would each get their own queue in the real API;
// this suite only ever needs one side (the display), so it does not model
// channel-to-channel delivery -- the live-browser proof in the task report
// is what exercises the real two-window round trip.
function makeFakeBroadcastChannelClass(posted) {
  return class FakeBroadcastChannel {
    constructor(name) { this.name = name; this.onmessage = null; this.closed = false; }
    postMessage(msg) { if (this.closed) throw new Error('channel closed'); posted.push(msg); }
    close() { this.closed = true; }
  };
}

// A queue-free fake fetch: `handler(url)` decides the response per call, so
// a single fake can serve BOTH the one-time branding GET and the repeated
// #11 relay poll GET by inspecting the URL, the same way the real backend
// would route them to two different handlers. Returns a real Promise (this
// is plain Node/V8, not a fake) so `.then()` chains in the real source run
// unmodified; tests await `Promise.resolve().then(...)` (a microtask flush)
// to let them settle -- see AWAIT_MICROTASKS below.
function makeFakeFetch(handler) {
  const calls = [];
  const fn = function (url) {
    calls.push(url);
    const r = handler(url, calls.length);
    return Promise.resolve(r);
  };
  return { fn, calls };
}

function jsonResponse(status, body) {
  return { ok: status >= 200 && status < 300, status, json: () => Promise.resolve(body) };
}

// Waits for every already-queued promise job to drain -- including ones
// created INSIDE the vm sandbox (a separate V8 context, so counting a fixed
// number of `await Promise.resolve()` hops on the HOST's own Promise is not
// reliable: `fetch().then(r => r.json()).then(apply)` adopts a nested
// thenable, which costs a variable number of microtask turns). `setImmediate`
// runs in the event loop's check phase, strictly after the ENTIRE microtask
// queue has drained across every realm in this isolate, so one call is
// sufficient regardless of how deep a chain is. Still never a real timer
// wait -- same "fake but deterministic" spirit as makeFakeTimers().
function flushMicrotasks() {
  return new Promise((resolve) => setImmediate(resolve));
}

// Builds a fresh vm context, runs the REAL customer-display.js in it, and
// returns { sandbox, CustomerDisplay, timers, posted, channelInstance }.
// `withChannel` toggles whether BroadcastChannel exists at all -- the one
// thing customer-display.js is required to survive without. `fetchImpl`, if
// given, becomes `global.fetch` inside the sandbox (see makeFakeFetch) --
// left undefined by default, matching the ORIGINAL harness's deliberate
// choice to leave fetch absent for tests that have nothing to do with it.
function loadCustomerDisplay({ withChannel, fetchImpl }) {
  const document = makeFakeDocument();
  const timers = makeFakeTimers();
  const posted = [];
  let channelInstance = null;

  const sandbox = {
    document,
    console,
    setTimeout: timers.setTimeout,
    clearTimeout: timers.clearTimeout,
    setInterval: timers.setInterval,
    clearInterval: timers.clearInterval,
    // fetch deliberately absent unless a test supplies one: _loadBranding()'s
    // and _startPolling()'s own guards (`typeof global.fetch !== 'function'`)
    // cover that path, and the live-browser proof in the task report already
    // exercises the real branding fetch end to end -- this harness stays
    // synchronous on purpose for every test that has nothing to do with
    // fetch at all.
  };
  if (withChannel) {
    sandbox.BroadcastChannel = makeFakeBroadcastChannelClass(posted);
  }
  if (fetchImpl) {
    sandbox.fetch = fetchImpl;
  }
  vm.createContext(sandbox);
  vm.runInContext(CD_SOURCE, sandbox, { filename: 'customer-display.js' });

  const CustomerDisplay = sandbox.CustomerDisplay;
  if (withChannel) channelInstance = CustomerDisplay._channel;
  return { document, CustomerDisplay, timers, posted, channelInstance };
}

function elText(document, id) { return document.getElementById(id).textContent; }
function elHidden(document, id) { return document.getElementById(id).hidden; }

// ─────────────────────────────────────────────────────────────────────────────

function testNoBroadcastChannelNeverThrowsAndStaysIdle() {
  // The documented fallback (task spec: "If BroadcastChannel is unavailable
  // the display must NOT throw. It shows the idle screen"). Constructing the
  // harness itself is the mutation proof: vm.runInContext re-throws any
  // uncaught error from the module body, so a regression here fails LOUD.
  const { document, CustomerDisplay } = loadCustomerDisplay({ withChannel: false });
  assert.strictEqual(CustomerDisplay._channel, null,
    'no BroadcastChannel in the environment should leave _channel null, not throw');
  assert.strictEqual(elHidden(document, 'cd-idle'), false, 'idle screen must be visible');
  assert.strictEqual(elHidden(document, 'cd-active'), true, 'active screen must be hidden');
  assert.strictEqual(elHidden(document, 'cd-complete'), true, 'complete screen must be hidden');
  console.log('PASS: no BroadcastChannel -> no throw, idle screen shown');
}

function testConnectingAnnouncesReadiness() {
  const { CustomerDisplay, posted } = loadCustomerDisplay({ withChannel: true });
  assert.ok(CustomerDisplay._channel, 'a channel should have been constructed when BroadcastChannel exists');
  assert.ok(
    posted.some((m) => m && m.type === 'display_ready'),
    `expected a 'display_ready' announcement on connect; posted: ${JSON.stringify(posted)}`
  );
  console.log('PASS: connecting announces display_ready over the channel');
}

function testCartMessageRendersExactPassthroughStrings() {
  const { document, CustomerDisplay } = loadCustomerDisplay({ withChannel: true });
  // A real 3-decimal JOD-style string, exactly as RetailSystem._fmt() would
  // produce it -- this harness never computes it, only displays it.
  CustomerDisplay._onMessage({
    type: 'cart',
    items: [
      { name: 'Classic T-Shirt', quantity: 2, unitPrice: 'JD 14.990', lineTotal: 'JD 29.980' },
      // Deliberately XSS-shaped name: proves the item name is escaped, not
      // trusted -- product names are shop-authored data, same discipline
      // subsystem-retail.js's own _esc() applies to cart rows.
      { name: '<img src=x onerror=alert(1)>', quantity: 1, unitPrice: 'JD 1.000', lineTotal: 'JD 1.000' },
    ],
    subtotal: 'JD 30.980',
    discount: null,
    tax: 'JD 0.000',
    total: 'JD 30.980',
  });

  assert.strictEqual(elHidden(document, 'cd-idle'), true, 'idle must hide once a cart is active');
  assert.strictEqual(elHidden(document, 'cd-active'), false, 'active screen must show');
  assert.strictEqual(elHidden(document, 'cd-complete'), true);

  const itemsHtml = document.getElementById('cd-items').innerHTML;
  assert.ok(itemsHtml.includes('JD 14.990'), 'unit price string must appear verbatim');
  assert.ok(itemsHtml.includes('JD 29.980'), 'line total string must appear verbatim');
  assert.ok(itemsHtml.includes('&lt;img'), 'a product name must be HTML-escaped, not injected raw');
  assert.ok(!itemsHtml.includes('<img src=x'), 'the raw unescaped tag must never reach innerHTML');

  assert.strictEqual(elText(document, 'cd-total-value'), 'JD 30.980',
    'the total must be the EXACT string the till sent, not recomputed');
  assert.strictEqual(elHidden(document, 'cd-discount-row'), true, 'discount row hides when discount is null');
  console.log('PASS: a cart message renders the exact pre-formatted strings it was given, with escaping');
}

function testDiscountRowShowsOnlyWhenPresent() {
  const { document, CustomerDisplay } = loadCustomerDisplay({ withChannel: true });
  CustomerDisplay._onMessage({
    type: 'cart',
    items: [{ name: 'Denim Jeans', quantity: 1, unitPrice: 'JD 49.990', lineTotal: 'JD 49.990' }],
    subtotal: 'JD 49.990', discount: 'JD 5.000', tax: 'JD 0.000', total: 'JD 44.990',
  });
  assert.strictEqual(elHidden(document, 'cd-discount-row'), false, 'discount row must show when discount is set');
  assert.strictEqual(elText(document, 'cd-discount-value'), '-JD 5.000',
    'the discount row is prefixed with a literal minus over the till\'s own string, same convention as the printed receipt');
  console.log('PASS: the discount row shows only when a discount is present, prefixed like the receipt');
}

function testSaleCompleteIgnoresEmptyCartButNotNonEmptyCart() {
  const { document, CustomerDisplay, timers } = loadCustomerDisplay({ withChannel: true });
  CustomerDisplay._onMessage({ type: 'sale_complete', amountPaid: 'JD 50.000', change: 'JD 7.030' });
  assert.strictEqual(elHidden(document, 'cd-complete'), false, 'complete screen must show');
  assert.strictEqual(elText(document, 'cd-paid-value'), 'JD 50.000');
  assert.strictEqual(elHidden(document, 'cd-change-row'), false);
  assert.strictEqual(elText(document, 'cd-change-value'), 'JD 7.030');
  assert.ok(timers.pendingCount() >= 1, 'a revert-to-idle timer must be armed');

  // MUTATION PROOF #1: _clearCart()'s own broadcast (an EMPTY cart) fires
  // immediately after every real checkout -- it must NOT cut the thank-you
  // screen short.
  CustomerDisplay._onMessage({ type: 'cart', items: [] });
  assert.strictEqual(elHidden(document, 'cd-complete'), false,
    'an empty cart arriving right after checkout must not dismiss the thank-you screen early');

  // MUTATION PROOF #2: a NON-empty cart (the cashier already rang the next
  // sale) DOES interrupt it immediately -- the opposite branch of the same
  // guard, proven so a fix for #1 cannot silently break this one.
  CustomerDisplay._onMessage({
    type: 'cart',
    items: [{ name: 'Coffee Blend 500g', quantity: 1, unitPrice: 'JD 12.990', lineTotal: 'JD 12.990' }],
    subtotal: 'JD 12.990', discount: null, tax: 'JD 0.000', total: 'JD 12.990',
  });
  assert.strictEqual(elHidden(document, 'cd-complete'), true, 'a non-empty cart must interrupt the thank-you screen');
  assert.strictEqual(elHidden(document, 'cd-active'), false);
  console.log('PASS: an empty cart cannot dismiss the thank-you screen early; a non-empty one interrupts it immediately');
}

function testSaleCompleteRevertsToIdleWhenItsOwnTimerFires() {
  const { document, CustomerDisplay, timers } = loadCustomerDisplay({ withChannel: true });
  CustomerDisplay._onMessage({ type: 'sale_complete', amountPaid: 'JD 10.000', change: null });
  assert.strictEqual(elHidden(document, 'cd-complete'), false);
  assert.strictEqual(elHidden(document, 'cd-change-row'), true, 'no change due -> the change row stays hidden');
  assert.ok(timers.pendingCount() >= 1, 'expected the auto-revert timer to be armed');
  timers.fireAll();   // simulate however long SALE_COMPLETE_DISPLAY_MS is, without waiting
  assert.strictEqual(elHidden(document, 'cd-idle'), false, 'idle must show once the timer fires');
  assert.strictEqual(elHidden(document, 'cd-complete'), true);
  console.log('PASS: the thank-you screen reverts to idle once its own timer actually fires');
}

function testUnknownMessageTypeIsIgnoredNotThrown() {
  const { document, CustomerDisplay } = loadCustomerDisplay({ withChannel: true });
  // A message shape a future/older build might send. Must be forward/back
  // compatible: ignored, never an exception that could take the whole
  // display down mid-shift.
  assert.doesNotThrow(() => CustomerDisplay._onMessage({ type: 'something_this_build_predates', foo: 1 }));
  assert.strictEqual(elHidden(document, 'cd-idle'), false, 'an unrecognised message must leave the idle screen alone');
  console.log('PASS: an unrecognised message type is ignored, not thrown');
}

// ─────────────────────────────────────────────────────────────────────────────
// Money discipline: neither side of this feature may reimplement formatting.
// ─────────────────────────────────────────────────────────────────────────────

// Strips comments before scanning for banned patterns -- this file's own
// module comment NAMES _fmt()/_moneyDigits() (explaining what NOT to
// reimplement), so scanning raw text would flag its own documentation. Same
// shape as retail_design_rtl_test.js's stripComments/stripJsComments.
function stripJsCommentsForScan(js) {
  return js
    .replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, ' '))
    .replace(/^[ \t]*\/\/.*$/gm, (m) => ' '.repeat(m.length));
}

function testCustomerDisplayNeverReformatsMoney() {
  const code = stripJsCommentsForScan(CD_SOURCE);
  const offenders = [];
  if (/\.toFixed\s*\(/.test(code)) offenders.push('.toFixed(');
  if (/currencyDp|currencyPrefix|currencySymbol|_moneyDigits|_fmt\s*\(/.test(code)) {
    offenders.push('a currency-formatting helper name');
  }
  assert.deepStrictEqual(offenders, [],
    `customer-display.js appears to compute its own money formatting (${offenders.join(', ')}). ` +
    'Every figure must be the exact string RetailSystem._fmt()/_moneyDigits() already produced -- ' +
    'see the module comment. A second formatter is a second place for a currency (JOD\'s 3 decimals ' +
    'included) to drift from what the till is about to charge.'
  );
  console.log('PASS: customer-display.js contains no money-formatting logic of its own');
}

function testPayloadBuilderUsesTillsOwnFormatter() {
  const src = fs.readFileSync(RETAIL_JS, 'utf8');
  // Found-a-real-bug-in-this-test fix (2026-09-27): `indexOf('_customerDisplayCartPayload()')`
  // alone matches the FIRST occurrence of that literal text, which is not
  // guaranteed to be the method's own DEFINITION -- `_postToCustomerDisplayRelay`
  // (the #11(b) relay-hold fix) now CALLS `this._customerDisplayCartPayload()`
  // earlier in the file than the method is defined
  // (`this._postDisplayRelayNow(this._customerDisplayCartPayload())`), so this
  // search landed inside THAT caller's body instead and counted its zero
  // `this._fmt(` calls -- a false failure with no real defect behind it, not a
  // weakened assertion. The method DEFINITION is the unique
  // `_customerDisplayCartPayload() {` (the literal call sites end in `);`, never
  // in ` {`), so anchor on that instead of the bare call text. Mutation-proof:
  // reverting this one line back to the bare `'_customerDisplayCartPayload()'`
  // search reproduces the exact false RED this comment describes, on the
  // CURRENT file, with no change to the payload builder itself.
  const start = src.indexOf('_customerDisplayCartPayload() {');
  assert.ok(start !== -1, '_customerDisplayCartPayload() definition not found in subsystem-retail.js');
  const body = src.slice(start, src.indexOf('\n  },', start));
  const fmtCalls = (body.match(/this\._fmt\(/g) || []).length;
  assert.ok(fmtCalls >= 4,
    `_customerDisplayCartPayload() calls this._fmt() only ${fmtCalls} time(s) -- expected at least one ` +
    'per money field (two per line item, plus subtotal/discount/tax/total). A field built any other way ' +
    'is a figure that can disagree with what _checkout() actually charges.'
  );
  assert.ok(!/toFixed\s*\(/.test(body), '_customerDisplayCartPayload() must not call toFixed() directly');
  console.log(`PASS: _customerDisplayCartPayload() builds every money field through this._fmt() (${fmtCalls} call sites)`);
}

// ─────────────────────────────────────────────────────────────────────────────
// Static wiring: the button exists, is reachable, and cannot steal focus.
// ─────────────────────────────────────────────────────────────────────────────

function testPosButtonLivesInsideTheFocusGuardedRow() {
  const src = fs.readFileSync(RETAIL_JS, 'utf8');
  const rowStart = src.indexOf('class="pos-cat-row"');
  assert.ok(rowStart !== -1, '.pos-cat-row not found');
  const rowEnd = src.indexOf('</div>', src.indexOf('pos-product-grid', rowStart));
  const row = src.slice(rowStart - 40, rowEnd);
  assert.ok(row.includes('_keepScanFocus(event)'),
    '.pos-cat-row must carry the onmousedown _keepScanFocus guard it already uses for the Held button');
  assert.ok(row.includes('id="pos-display-btn"') && row.includes('_openCustomerDisplay()'),
    'the customer-display button must be inside the SAME guarded row, not bolted on elsewhere ' +
    '(a control outside this row would blur #pos-search on click)'
  );
  console.log('PASS: the customer-display button sits inside .pos-cat-row, under the existing focus guard');
}

function testEveryNewLocaleKeyExistsInBothCatalogs() {
  const en = JSON.parse(fs.readFileSync(path.join(FRONTEND_DIR, 'locales', 'en.json'), 'utf8'));
  const ar = JSON.parse(fs.readFileSync(path.join(FRONTEND_DIR, 'locales', 'ar.json'), 'utf8'));
  // DERIVED, not hardcoded. This list used to be a frozen array of the three
  // keys that existed the day it was written, which meant it could only ever
  // confirm that those three were fine -- a fourth key added later sailed
  // straight past it (`t('Amount Due')` did exactly that). Reading the keys
  // out of the file under test is what makes this catch the NEXT one.
  //
  // Literal single-quoted arguments only: a dynamic t(someVar) names no
  // dictionary key that can be checked here, and pretending otherwise would
  // put unresolvable entries in the list.
  const cdSrc = fs.readFileSync(CD_JS, 'utf8');
  const derived = [];
  const tRe = /\bt\(\s*'((?:[^'\\]|\\.)*)'/g;
  let tm;
  while ((tm = tRe.exec(cdSrc))) derived.push(tm[1].replace(/\\'/g, "'"));
  // These two live in subsystem-retail.js (the POS button and its title
  // attribute). Named explicitly rather than scraped: that file is shared and
  // carries hundreds of t() calls belonging to every other retail feature, so
  // scanning it would test those instead of this one.
  const SHELL_KEYS = ['Customer Display', 'Open the customer-facing display'];
  const NEW_KEYS = Array.from(new Set(derived.concat(SHELL_KEYS)));
  // Anti-vacuity: if the scan found nothing, every assertion below passes over
  // an empty list and proves nothing at all.
  assert.ok(derived.length > 0,
    'no literal t(...) keys found in customer-display.js -- this check has nothing to inspect');
  const missing = NEW_KEYS.filter((k) => !(k in en) || !(k in ar));
  assert.deepStrictEqual(missing, [], `new customer-display key(s) missing from a catalog: ${missing.join(', ')}`);
  NEW_KEYS.forEach((k) => {
    assert.notStrictEqual(ar[k], en[k], `'${k}' has no real Arabic translation (ar equals en verbatim)`);
  });
  console.log('PASS: every new customer-display locale key exists in both catalogs with a real Arabic value');
}

function testCustomerDisplayHtmlLinksTokensAndBothStylesheets() {
  const html = fs.readFileSync(CD_HTML, 'utf8');
  assert.ok(/href="css\/main\.css"/.test(html), 'customer-display.html must load css/main.css (design tokens + themes)');
  assert.ok(/href="css\/rtl\.css"/.test(html), 'customer-display.html must load css/rtl.css (money direction, chart exceptions)');
  assert.ok(/data-theme="light"/.test(html), 'customer-display.html must declare a default data-theme, matching index.html\'s boot contract');
  assert.ok(/aura_theme_v2/.test(html), 'customer-display.html must read the SAME theme key index.html does, or it will disagree with the till\'s own theme');
  console.log('PASS: customer-display.html wires up the shared token stylesheet, RTL stylesheet, and theme boot contract');
}

// ─────────────────────────────────────────────────────────────────────────────
// #12-display (P0 review-fix, 2026-09-27) -- the static <title> shown before
// ANY source has answered (a fresh Edge/browser popup, no session, or the
// branding fetch still pending) must be neutral, never the vendor's own
// "Aura Retail". _applyShopName (customer-display.js) already overwrites
// this once a real shop name arrives; this pins what shows before that.
// ─────────────────────────────────────────────────────────────────────────────
function testCustomerDisplayHtmlTitleIsNeutral() {
  const html = fs.readFileSync(CD_HTML, 'utf8');
  const titleMatch = html.match(/<title>([^<]*)<\/title>/);
  assert.ok(titleMatch, 'no <title> tag found in customer-display.html');
  assert.strictEqual(titleMatch[1], 'Customer Display',
    'customer-display.html\'s static <title> must be the neutral "Customer Display" -- what the title bar ' +
    '(browser tab, or the Edge/Chrome --app fallback popup) shows before any shop name is known -- and must ' +
    'never carry the vendor\'s own name.');
  console.log('PASS: customer-display.html\'s static <title> is the neutral "Customer Display", no vendor name');
}

function testMutationProvedNeutralTitle() {
  const html = fs.readFileSync(CD_HTML, 'utf8');
  const anchor = '<title>Customer Display</title>';
  const hits = html.split(anchor).length - 1;
  assert.strictEqual(hits, 1,
    `Mutation anchor occurs ${hits} time(s), expected exactly 1: ${JSON.stringify(anchor)} -- re-anchor this proof.`);
  const mutated = html.replace(anchor, '<title>Aura Retail — Customer Display</title>');
  const titleMatch = mutated.match(/<title>([^<]*)<\/title>/);
  assert.ok(titleMatch);
  let threw = null;
  try {
    assert.strictEqual(titleMatch[1], 'Customer Display');
  } catch (e) {
    threw = e;
  }
  assert.ok(threw,
    'MUTATION SURVIVED -- reintroducing the vendor-name <title> ("Aura Retail — Customer Display") did not ' +
    'fail the neutral-title check above, so it is not actually watching for the regression.');
  // Re-prove against the real, unmutated file.
  testCustomerDisplayHtmlTitleIsNeutral();
  console.log(`PASS: reverting to the vendor-name <title> is caught  [caught: ${String(threw.message).slice(0, 90)}]`);
  console.log('PASS: guard passes again against the restored, unmutated customer-display.html');
}

// ─────────────────────────────────────────────────────────────────────────────
// #9 -- static backstop for the CSS specificity trap. NOT a substitute for
// the real-browser proof (see the module docstring): this only pins the
// SOURCE TEXT of the rule, so it cannot see the cascade actually resolving,
// but it is cheap, runs in every `node` invocation of this suite (no
// Chromium needed), and catches the exact "someone 'cleaned up' the
// !important" regression immediately.
// ─────────────────────────────────────────────────────────────────────────────
function testCdScreenHiddenRuleUsesImportant() {
  const html = fs.readFileSync(CD_HTML, 'utf8');
  const styleMatch = html.match(/<style>([\s\S]*?)<\/style>/);
  assert.ok(styleMatch, 'no <style> block found in customer-display.html');
  // Comments stripped FIRST: the comment directly above the real rule quotes
  // the buggy selector verbatim ('.cd-screen[hidden]{display:none}', with no
  // !important, as the thing to never revert to) -- a naive regex over the
  // raw block matches that quoted text first and reports the fix as present
  // when it is actually only being described. Same shape as
  // stripJsCommentsForScan() below, for CSS block comments (CSS has no line
  // comment syntax to also strip).
  const style = styleMatch[1].replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, ' '));
  const ruleMatch = style.match(/\.cd-screen\[hidden\]\s*\{([^}]*)\}/);
  assert.ok(ruleMatch, '.cd-screen[hidden] rule not found in the <style> block');
  assert.ok(/display\s*:\s*none\s*!important/.test(ruleMatch[1]),
    '.cd-screen[hidden] must be `display: none !important` -- without !important this rule (specificity ' +
    '0,2,0) loses to the ID rules #cd-idle/#cd-active/#cd-complete{display:flex} (specificity 1,0,0) in ' +
    'every real browser, and all three screens render stacked on top of each other (AUDIT #9)'
  );
  console.log('PASS: .cd-screen[hidden] still carries the !important that beats the ID display rules');
}

// ─────────────────────────────────────────────────────────────────────────────
// #12 -- the shop-name fallback. Never the vendor name "Aura Retail"; a
// neutral bilingual placeholder until a real name arrives from EITHER
// source (branding fetch or relay `shop` payload), and once a real name
// has arrived, a later "no name" answer from the OTHER source must not
// blank it back out.
// ─────────────────────────────────────────────────────────────────────────────
function testNeutralWelcomeShownBeforeAnySourceAnswers() {
  // No fetch at all in this sandbox (matches "offline, or a build predating
  // the branding route") -- init() must still paint the neutral fallback
  // synchronously, never the empty string the markup ships with.
  const { document } = loadCustomerDisplay({ withChannel: false });
  assert.strictEqual(elText(document, 'cd-shop-name'), 'Welcome / أهلاً وسهلاً',
    'with no shop name known from any source yet, cd-shop-name must show the neutral bilingual greeting');
  assert.notStrictEqual(elText(document, 'cd-shop-name'), 'Aura Retail',
    'the vendor\'s own name must never be the fallback (AUDIT #12)');
  console.log('PASS: with no shop name known yet, the idle screen shows the neutral bilingual greeting, never the vendor name');
}

async function testRealShopNameFromBrandingLatchesOverLaterNull() {
  // Review fix (2026-09-27, #12-display, medium): this fixture used to send
  // {branding_business_name:'Sweets & Co'} -- a shape the REAL server NEVER
  // sends. branding_settings_get (retail_api.py) always returns the RAW
  // `branding_business_name` (empty on a fresh signup-only install) PLUS a
  // separate, already-resolved `business_name` (the I-3 fallback: branding
  // if set, else the signup Company Name) and `business_name_source`. The
  // old fixture could not see the #12-display bug (customer-display.js
  // reading `b.branding_business_name` alone) because it put the real name
  // in the field the bug already read -- ENGINEERING.md failure shape 2, the
  // fixture manufacturing the exact state that hides the defect. This is the
  // shape a shop that only typed a Company Name at signup (branding never
  // touched) actually gets back.
  const fake = makeFakeFetch((url) => {
    if (String(url).indexOf('/settings/branding/logo') !== -1) return jsonResponse(200, { data: {} });
    if (String(url).indexOf('/settings/branding') !== -1) {
      return jsonResponse(200, {
        status: 'success',
        data: { branding_business_name: '', business_name: 'Sweets & Co', business_name_source: 'signup', has_logo: false },
      });
    }
    // The #11 relay poll fires too (init() starts both). This test is about
    // the BRANDING fallback (#12-display), not the relay itself, so it just
    // answers the poll with a plain 404 stub -- any non-2xx/204 answer here
    // must not throw and must not touch the name branding already supplied.
    // P0 review-fix (2026-09-27): the relay route is landed now (see the
    // #11/I-1 block header below for what that changes and does not change).
    return jsonResponse(404, {});
  });
  const { document, CustomerDisplay } = loadCustomerDisplay({ withChannel: false, fetchImpl: fake.fn });
  await flushMicrotasks();
  // MUTATION-PROVEN (revert customer-display.js's `_loadBranding` back to
  // `self._applyShopName(b.branding_business_name)` alone, with THIS real
  // server shape in the fixture, and this assertion goes red -- the idle
  // screen shows the neutral 'Welcome / أهلاً وسهلاً' placeholder instead,
  // exactly the live bug the review finding measured):
  assert.strictEqual(elText(document, 'cd-shop-name'), 'Sweets & Co',
    'a signup-only shop (branding_business_name empty, business_name from the I-3 signup fallback) must show its own name, never the neutral placeholder');
  assert.strictEqual(document.title, 'Sweets & Co — Customer Display',
    'the document title must follow the resolved shop name');

  // MUTATION-SHAPED PROOF, in code rather than by reverting a line: a LATER
  // relay tick that has no name yet (shop.name === null, e.g. #11 landed but
  // this particular company has never set one) must not blank the real name
  // back out to the neutral placeholder -- that would be a visible
  // regression on every poll tick after the one real name arrived.
  CustomerDisplay._applyShopMeta({ name: null, lang: null, theme: null });
  assert.strictEqual(elText(document, 'cd-shop-name'), 'Sweets & Co',
    'a later "no name yet" answer from the relay must not overwrite a real name already shown');
  console.log('PASS: a real shop name from branding is shown and title-cased, and survives a later null from the relay');
}

// ─────────────────────────────────────────────────────────────────────────────
// #11/I-1 -- the backend relay poll. P0 review-fix (2026-09-27): the route
// IS landed (retail_api.py: customer_display_set_state / _get_state /
// _status) -- this comment used to say C2 had not shipped it yet, and that
// stopped being true. Every check below still runs against a STUBBED fetch
// on purpose, the same as every other check in this file: this is a `node`
// vm unit-test harness with no Flask process behind it (see the module
// docstring's own "WHY A VM HARNESS" section), not an integration suite. The
// stub is written to the SAME I-1 contract the real route now implements
// (200 body / 204 / 401 / seq, dedupe-by-seq), never a reimplementation of
// it. The REAL, non-stubbed round trip -- an actual till POST answered by an
// actual display GET, through the booted app -- is proven separately in
// retail_smoke_e2e.py's scenario 14; nothing here needs to change once that
// suite runs either, since the two cover different halves of the same
// contract (this file: the display's OWN handling of what it is told; that
// scenario: that the till and the server actually tell it the truth).
// ─────────────────────────────────────────────────────────────────────────────
async function testPollingAppliesShopMetaOnceAndDedupesBySeq() {
  const seenUrls = [];
  const fake = makeFakeFetch((url) => {
    seenUrls.push(String(url));
    if (String(url).indexOf('/settings/branding') !== -1) return jsonResponse(200, { status: 'success', data: {} });
    // Every poll call in THIS test answers with the SAME seq (3) -- proving
    // dedupe requires the display to see a repeat, not just a 204.
    return jsonResponse(200, {
      seq: 3,
      message: { type: 'cart', items: [{ name: 'Mug', quantity: 1, unitPrice: 'JD 2.000', lineTotal: 'JD 2.000' }],
        subtotal: 'JD 2.000', discount: null, tax: 'JD 0.000', total: 'JD 2.000' },
      shop: { name: 'Relay Shop', lang: 'ar', theme: 'sand' },
    });
  });
  const { document, CustomerDisplay } = loadCustomerDisplay({ withChannel: false, fetchImpl: fake.fn });
  await flushMicrotasks();   // settles the branding call + the very first poll tick init() already queued

  assert.strictEqual(elText(document, 'cd-shop-name'), 'Relay Shop', 'the relay\'s shop.name must be applied');
  assert.strictEqual(document.documentElement.getAttribute('data-theme'), 'sand', 'the relay\'s shop.theme must be applied to <html data-theme>');
  assert.strictEqual(document.documentElement.getAttribute('lang'), 'ar', 'the relay\'s shop.lang must be applied to <html lang>');
  assert.strictEqual(document.documentElement.getAttribute('dir'), 'rtl', '"ar" must set dir="rtl"');
  assert.strictEqual(elText(document, 'cd-total-value'), 'JD 2.000', 'the relayed cart message must render through the same _onMessage() the channel uses');

  const seqAfterFirstApply = CustomerDisplay._lastSeq;
  assert.strictEqual(seqAfterFirstApply, 3, 'the display must remember the seq it just applied');

  // The exact regression this proves: WITHOUT the `data.seq <= self._lastSeq`
  // guard, calling _pollOnce() again against the SAME seq=3 response would
  // re-render unconditionally every ~600ms even though nothing changed.
  document.getElementById('cd-shop-name').textContent = 'TAMPERED';   // if the next tick re-applies, this proves it did
  await CustomerDisplay._pollOnce();
  await flushMicrotasks();
  assert.strictEqual(document.getElementById('cd-shop-name').textContent, 'TAMPERED',
    'a poll response repeating the SAME seq must be ignored entirely (dedupe by seq) -- it must not even re-run _applyShopMeta()');
  assert.strictEqual(CustomerDisplay._lastSeq, 3, '_lastSeq must not move for a repeated/non-newer seq');
  console.log(`PASS: the relay poll applies shop.name/lang/theme and dedupes a repeated seq (urls polled: ${seenUrls.length})`);
}

async function testPollingAdvancesOnANewerSeq() {
  let call = 0;
  const fake = makeFakeFetch((url) => {
    if (String(url).indexOf('/settings/branding') !== -1) return jsonResponse(200, { status: 'success', data: {} });
    // P1a (idle-screen promotion, customer-display.js's _loadPromotion) --
    // init() now fires a THIRD one-time fetch alongside branding, to
    // /promotions/active. Routed here, before the `call` counter below,
    // for the same reason branding gets its own branch above: `call` is a
    // counter over exactly the RELAY POLL's own successive ticks (seq=1
    // then seq=2), and letting an unrelated one-time fetch increment it
    // would shift every later tick's seq by one -- which is exactly what
    // this test caught (RED) the first time _loadPromotion was added
    // without this branch: _lastSeq was 2 after the first flush, not 1,
    // because the promotion fetch silently ate the "call===1" branch meant
    // for the relay's own first tick. This branch removes that coupling;
    // it proves nothing about promotions itself (see
    // testPromotionRowRendersActivePromotion for that).
    if (String(url).indexOf('/promotions/active') !== -1) return jsonResponse(200, { status: 'success', data: [] });
    call++;
    if (call === 1) {
      return jsonResponse(200, { seq: 1, message: { type: 'cart', items: [], subtotal: 'JD 0.000', discount: null, tax: 'JD 0.000', total: 'JD 0.000' }, shop: null });
    }
    return jsonResponse(200, {
      seq: 2,
      message: { type: 'cart', items: [{ name: 'Pen', quantity: 3, unitPrice: 'JD 0.500', lineTotal: 'JD 1.500' }],
        subtotal: 'JD 1.500', discount: null, tax: 'JD 0.000', total: 'JD 1.500' },
      shop: null,
    });
  });
  const { document, CustomerDisplay } = loadCustomerDisplay({ withChannel: false, fetchImpl: fake.fn });
  await flushMicrotasks();   // consumes the seq=1 tick init() already queued
  assert.strictEqual(CustomerDisplay._lastSeq, 1);
  await CustomerDisplay._pollOnce();
  await flushMicrotasks();
  assert.strictEqual(CustomerDisplay._lastSeq, 2, 'a genuinely newer seq must advance _lastSeq and apply');
  assert.strictEqual(elText(document, 'cd-total-value'), 'JD 1.500', 'the newer message must actually render');
  console.log('PASS: a newer seq is applied and advances _lastSeq');
}

async function testPollingSurvivesMissingRouteAndUnauthenticatedContext() {
  // Two non-2xx/204 answers the poll must survive without throwing or
  // treating either as a real message: 401 (a genuinely separate,
  // cookie-less window/profile -- the deny half of I-1's own test list: "a
  // context without cookies gets 401 and stays idle") and 404. P0
  // review-fix (2026-09-27): 404 no longer means "the route is missing from
  // THIS build" -- it is landed (see the #11/I-1 block header above) and the
  // real route never itself answers 404 -- kept here as the defensive case
  // for a display polling an OLDER backend build that predates the route (a
  // display and its till are not guaranteed to be upgraded atomically) or
  // any other unrecognised status the display must not choke on.
  for (const status of [404, 401]) {
    const fake = makeFakeFetch((url) => {
      if (String(url).indexOf('/settings/branding') !== -1) return jsonResponse(200, { status: 'success', data: {} });
      return jsonResponse(status, {});
    });
    const { document, CustomerDisplay } = loadCustomerDisplay({ withChannel: false, fetchImpl: fake.fn });
    await flushMicrotasks();
    assert.strictEqual(elHidden(document, 'cd-idle'), false, `a ${status} from the relay must leave the idle screen showing, not throw`);
    assert.strictEqual(CustomerDisplay._lastSeq, 0, `a ${status} must not be mistaken for a real seq`);
  }
  // A network-level rejection (route genuinely unreachable) must be equally survivable.
  const throwingFetch = () => Promise.reject(new Error('ECONNREFUSED (simulated)'));
  const { document: doc2 } = loadCustomerDisplay({ withChannel: false, fetchImpl: throwingFetch });
  await flushMicrotasks();
  assert.strictEqual(elHidden(doc2, 'cd-idle'), false, 'a rejected fetch (network error) must not throw or blank the screen');
  console.log('PASS: a missing route (404), no session (401), and a network error all leave the display idle rather than throwing');
}

// ─────────────────────────────────────────────────────────────────────────────
// #8/I-2 -- promotions row and the struck-through original line price.
// ─────────────────────────────────────────────────────────────────────────────
function testPromotionsRowShowsOnlyWhenPresent() {
  const { document, CustomerDisplay } = loadCustomerDisplay({ withChannel: true });
  CustomerDisplay._onMessage({
    type: 'cart',
    items: [{ name: 'Shampoo', quantity: 1, unitPrice: 'JD 4.500', lineTotal: 'JD 4.050', originalLineTotal: 'JD 4.500' }],
    subtotal: 'JD 12.300', promotions: 'JD 0.450', discount: null, tax: 'JD 0.000', total: 'JD 11.850',
  });
  assert.strictEqual(elHidden(document, 'cd-promotions-row'), false, 'the Promotions row must show when msg.promotions is set');
  assert.strictEqual(elText(document, 'cd-promotions-value'), '-JD 0.450',
    'the promotions row is prefixed with a literal minus, same convention as the discount row');

  const itemsHtml = document.getElementById('cd-items').innerHTML;
  assert.ok(itemsHtml.includes('cd-item-original'), 'a line with originalLineTotal must render the struck-through original price');
  assert.ok(itemsHtml.indexOf('JD 4.500') < itemsHtml.indexOf('JD 4.050'),
    'the ORIGINAL (struck-through) price must appear before the post-promotion price on the line');

  // The allow-half's mirror: no promotions field at all -> row stays hidden,
  // exactly like the pre-existing discount row's own contract.
  CustomerDisplay._onMessage({
    type: 'cart',
    items: [{ name: 'Plain Item', quantity: 1, unitPrice: 'JD 1.000', lineTotal: 'JD 1.000' }],
    subtotal: 'JD 1.000', promotions: null, discount: null, tax: 'JD 0.000', total: 'JD 1.000',
  });
  assert.strictEqual(elHidden(document, 'cd-promotions-row'), true, 'the Promotions row must hide again when msg.promotions is null');
  const itemsHtml2 = document.getElementById('cd-items').innerHTML;
  assert.ok(!itemsHtml2.includes('cd-item-original'), 'a line with no originalLineTotal must not render a struck-through price');
  console.log('PASS: the Promotions row and the struck-through original line price show only when the till actually sends them');
}

// ─────────────────────────────────────────────────────────────────────────────
// P1a -- "the customer screen belongs to the shop" (idle-only: today's
// promotion, the shop's own WhatsApp QR).
// ─────────────────────────────────────────────────────────────────────────────

function testPromotionRowRendersActivePromotion() {
  const { document, CustomerDisplay } = loadCustomerDisplay({ withChannel: false });
  // No fetch in this sandbox -- calling _applyPromotion directly proves the
  // RENDERING half precisely (the fetch-plumbing half is covered by the
  // fetch-path tests below), same "direct method + fetch-path" split this
  // file already uses for branding vs _applyShopName.
  // (The fake DOM's makeFakeElement() starts every scraped id at `hidden:
  // undefined`, not the markup's real `hidden` attribute -- it has no HTML
  // parser -- so the pristine state is never asserted here, matching every
  // other row test in this file, e.g. testPromotionsRowShowsOnlyWhenPresent.)
  // F7 follow-up: a promotion is ALWAYS shown WITH its scope now (never a
  // bare name) -- category_id + a resolved category_name is the shape
  // retail_api.py's list_active_promotions actually sends.
  CustomerDisplay._applyPromotion({ id: 1, name: 'Weekend Sale', discount_pct: 20, category_id: 42, category_name: 'Snacks' });
  assert.strictEqual(elHidden(document, 'cd-promo'), false, 'a real promotion must reveal cd-promo');
  assert.strictEqual(elText(document, 'cd-promo'), '20% off Snacks',
    'the promotion row must show the percentage and the category/product it applies to, never a bare unscoped claim');

  // The allow-half's mirror: no active promotion (null, or an empty list
  // from _loadPromotion) hides the row again -- never an empty box.
  CustomerDisplay._applyPromotion(null);
  assert.strictEqual(elHidden(document, 'cd-promo'), true, 'no active promotion must hide cd-promo again, not leave stale text visible');
  console.log('PASS: the promotion row shows the active promotion WITH its scope, and hides with none, both directions');
}

async function testPromotionFetchAppliesHighestDiscountEligibleRow() {
  const fake = makeFakeFetch((url) => {
    if (String(url).indexOf('/settings/branding') !== -1) return jsonResponse(200, { status: 'success', data: {} });
    if (String(url).indexOf('/promotions/active') !== -1) {
      return jsonResponse(200, { status: 'success', data: [
        { id: 9, name: 'Back to School', discount_pct: 10, category_id: 1, category_name: 'Stationery' },
        { id: 10, name: 'Second Promo', discount_pct: 5, category_id: 2, category_name: 'Drinks' },
      ] });
    }
    return jsonResponse(404, {});   // the #11 relay poll -- not this test's concern
  });
  const { document } = loadCustomerDisplay({ withChannel: false, fetchImpl: fake.fn });
  await flushMicrotasks();
  // Deterministic pick (coordinator instruction): the HIGHEST discount_pct
  // wins when several are active -- Stationery (10%) over Drinks (5%) here,
  // regardless of list order or id order.
  assert.strictEqual(elText(document, 'cd-promo'), '10% off Stationery',
    'GET /promotions/active must be reached on load and the highest-discount eligible row rendered');
  console.log('PASS: the one-time /promotions/active fetch renders the highest-discount eligible row');
}

async function testPromotionPicksHighestDiscountTiebrokenByLowestId() {
  const fake = makeFakeFetch((url) => {
    if (String(url).indexOf('/settings/branding') !== -1) return jsonResponse(200, { status: 'success', data: {} });
    if (String(url).indexOf('/promotions/active') !== -1) {
      // Same discount_pct, deliberately listed with the HIGHER id first --
      // if the pick were "first in the list" rather than "lowest id",
      // this would pick id 20 instead of id 11.
      return jsonResponse(200, { status: 'success', data: [
        { id: 20, name: 'Later Promo', discount_pct: 10, category_id: 2, category_name: 'Drinks' },
        { id: 11, name: 'Earlier Promo', discount_pct: 10, category_id: 1, category_name: 'Snacks' },
      ] });
    }
    return jsonResponse(404, {});
  });
  const { document } = loadCustomerDisplay({ withChannel: false, fetchImpl: fake.fn });
  await flushMicrotasks();
  assert.strictEqual(elText(document, 'cd-promo'), '10% off Snacks',
    'a tie on discount_pct must be broken by the LOWEST id (the promotion created first), not list order');
  console.log('PASS: a discount_pct tie is broken by the lowest id');
}

function testPromotionExcludedWhenScopeNameUnresolved() {
  const { document, CustomerDisplay } = loadCustomerDisplay({ withChannel: false });
  // F7 follow-up (coordinator instruction, verbatim): "A promotion whose
  // name can't be resolved is not shown. Never claim it applies shop-wide."
  // category_id is set, but retail_api.py's lookup found no matching
  // category (deleted after the promotion was created, e.g.) -- no name
  // to show, so nothing is shown, and this must NOT fall back to a bare
  // "20% off" that would read as a shop-wide claim.
  CustomerDisplay._applyPromotion({ id: 6, name: 'Orphaned Promo', discount_pct: 20, category_id: 99, category_name: null });
  assert.strictEqual(elHidden(document, 'cd-promo'), true, 'an unresolved category name must hide the row, never fall back to a bare claim');

  // The other unresolved shape: neither product_id nor category_id set at
  // all. There is no "shop-wide" display mode any more -- every promotion
  // this screen shows must name what it applies to -- so this is ALSO
  // hidden, not treated as a shop-wide offer.
  CustomerDisplay._applyPromotion({ id: 7, name: 'No Scope At All', discount_pct: 20, category_id: null, product_id: null });
  assert.strictEqual(elHidden(document, 'cd-promo'), true, 'a promotion with no scope field at all must not be presented as shop-wide');
  console.log('PASS: an unresolved or absent scope name hides the row -- never a shop-wide-reading fallback');
}

function testWhatsappQrRendersOnlyWhenConfigured() {
  const { document, CustomerDisplay } = loadCustomerDisplay({ withChannel: false });
  // (Pristine-state note: see testPromotionRowRendersActivePromotion above --
  // the fake DOM cannot represent the markup's own `hidden` attribute.)
  CustomerDisplay._applyWhatsappQr('data:image/svg+xml;base64,QUJD');
  assert.strictEqual(elHidden(document, 'cd-whatsapp'), false, 'a real QR data URI must reveal cd-whatsapp');
  assert.strictEqual(document.getElementById('cd-whatsapp-qr').src, 'data:image/svg+xml;base64,QUJD',
    'the img must carry exactly the server-supplied data URI, never re-derived client-side');

  // The allow-half's mirror: no number configured (null/'') -> no QR, no
  // empty box left showing -- contract: "No number -> no QR and no empty box."
  CustomerDisplay._applyWhatsappQr(null);
  assert.strictEqual(elHidden(document, 'cd-whatsapp'), true, 'with no shop number, cd-whatsapp must hide again');
  console.log('PASS: the WhatsApp QR shows only when the server supplies one, both directions');
}

// ─────────────────────────────────────────────────────────────────────────────
// Fix round F7 (verifier finding): "Only show promotions that apply
// shop-wide. A category- or product-limited one must not be presented as a
// shop-wide offer" + "ignore a promotion whose ends_at has passed".
// ─────────────────────────────────────────────────────────────────────────────

function sortableDate(offsetDays) {
  const d = new Date(Date.now() + offsetDays * 86400000);
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

function testPromotionIncludedWhenScopeResolvedAndCurrent() {
  const { document, CustomerDisplay } = loadCustomerDisplay({ withChannel: false });
  // The category-scoped ALLOW case -- real shape, real name resolved,
  // ends_at in the future.
  CustomerDisplay._applyPromotion({
    id: 3, name: 'Weekend Sale', discount_pct: 15, category_id: 1, category_name: 'Snacks', product_id: null,
    ends_at: sortableDate(1),
  });
  assert.strictEqual(elHidden(document, 'cd-promo'), false, 'a category-scoped, resolved, not-yet-expired promotion must show');
  assert.strictEqual(elText(document, 'cd-promo'), '15% off Snacks');

  // The product-scoped ALLOW case -- same rule, the other branch of
  // _promotionScopeName.
  CustomerDisplay._applyPromotion({
    id: 8, name: 'Widget Deal', discount_pct: 25, category_id: null, product_id: 7, product_name: 'Cola 500ml',
    ends_at: null,
  });
  assert.strictEqual(elHidden(document, 'cd-promo'), false, 'a product-scoped, resolved promotion must show');
  assert.strictEqual(elText(document, 'cd-promo'), '25% off Cola 500ml');
  console.log('PASS: a category- or product-scoped promotion with a resolved name and not expired is shown, WITH its scope');
}

function testPromotionExcludedWhenExpired() {
  const { document, CustomerDisplay } = loadCustomerDisplay({ withChannel: false });
  CustomerDisplay._applyPromotion({
    id: 4, name: 'Yesterday\'s Sale', discount_pct: 15, category_id: 1, category_name: 'Snacks', product_id: null,
    ends_at: sortableDate(-1),
  });
  assert.strictEqual(elHidden(document, 'cd-promo'), true, 'a promotion whose ends_at has passed must not show, even with a resolved scope name');

  // No ends_at at all ("never ends", same NULL-is-unbounded convention
  // core/retail/promotions.py's own SQL uses) must still show.
  CustomerDisplay._applyPromotion({ id: 5, name: 'Ongoing Sale', discount_pct: 5, category_id: 1, category_name: 'Snacks', product_id: null, ends_at: null });
  assert.strictEqual(elHidden(document, 'cd-promo'), false, 'a promotion with no ends_at (never expires) must still show');
  console.log('PASS: an expired promotion is excluded even with a resolved scope; one with no ends_at is not');
}

async function testIdleTransitionRefetchesPromotion() {
  // F7's OTHER half: "re-fetch /promotions/active each time the display
  // transitions back to idle (no new timer)". Drives THREE separate idle
  // transitions (init, an emptied cart, and the sale-complete timer firing)
  // and counts how many times /promotions/active was actually requested --
  // proving this happens on each real transition, not merely once at load,
  // and WITHOUT any fake timer ever being armed (mutation-shaped proof: a
  // regression that only fires the fetch once at init would freeze this
  // count at 1).
  let promoCalls = 0;
  const fake = makeFakeFetch((url) => {
    if (String(url).indexOf('/promotions/active') !== -1) { promoCalls++; return jsonResponse(200, { status: 'success', data: [] }); }
    if (String(url).indexOf('/settings/branding') !== -1) return jsonResponse(200, { status: 'success', data: {} });
    return jsonResponse(404, {});
  });
  const { CustomerDisplay, timers } = loadCustomerDisplay({ withChannel: false, fetchImpl: fake.fn });
  await flushMicrotasks();
  assert.strictEqual(promoCalls, 1, 'init() must fetch /promotions/active once');

  // Transition 2: a non-empty cart, then an empty one (the emptied-cart path
  // through _onCart -> _renderIdle).
  CustomerDisplay._onCart({ type: 'cart', items: [{ name: 'X', quantity: 1, unitPrice: 'JD 1.000', lineTotal: 'JD 1.000' }] });
  CustomerDisplay._onCart({ type: 'cart', items: [] });
  await flushMicrotasks();
  assert.strictEqual(promoCalls, 2, 'an emptied cart transitioning back to idle must re-fetch the promotion');

  // Transition 3: a sale completes, then its own timer fires (no REAL
  // setTimeout wait -- makeFakeTimers().fireAll() simulates it, same
  // pattern every other timer test in this file already uses).
  CustomerDisplay._onSaleComplete({ amountPaid: 'JD 1.000', change: null });
  timers.fireAll();
  await flushMicrotasks();
  assert.strictEqual(promoCalls, 3, 'the sale-complete timer reverting to idle must re-fetch the promotion too');
  console.log('PASS: the idle-screen promotion re-fetches on every idle transition (init, emptied cart, sale-complete timeout) -- no new timer');
}

// ─────────────────────────────────────────────────────────────────────────────
// Item 2 (verifier fix round): "expired promotion stays on a quiet idle
// screen" -- a SINGLE deadline timer for the shown promotion's own
// ends_at, armed on apply, cleared on every re-apply. Proven with the
// SAME fake-timer harness every other timer test in this file already
// uses (makeFakeTimers().fireAll() invokes whatever is pending regardless
// of its requested delay -- there is no need to fake Date itself: this
// promotion's ends_at is set a few real seconds in the future, same
// "arm it, then fire it early" shape testSaleCompleteRevertsToIdleWhen
// ItsOwnTimerFires already uses for the sale-complete timer).
// ─────────────────────────────────────────────────────────────────────────────

async function testPromotionHidesAndRefetchesWhenItsOwnDeadlinePasses() {
  let promoCallCount = 0;
  // A few real seconds out -- comfortably in the future when this test
  // runs, but the FAKE setTimeout below never actually waits that long;
  // fireAll() invokes the callback immediately regardless of the delay it
  // was scheduled with.
  const nearFutureEndsAt = sortableDate(5 / 86400);
  const fake = makeFakeFetch((url) => {
    if (String(url).indexOf('/settings/branding') !== -1) return jsonResponse(200, { status: 'success', data: {} });
    if (String(url).indexOf('/promotions/active') !== -1) {
      promoCallCount++;
      if (promoCallCount === 1) {
        return jsonResponse(200, { status: 'success', data: [
          { id: 1, name: 'Timed Sale', discount_pct: 10, category_id: 1, category_name: 'Snacks', ends_at: nearFutureEndsAt },
        ] });
      }
      return jsonResponse(200, { status: 'success', data: [] });   // the re-fetch after the deadline -- nothing else active
    }
    return jsonResponse(404, {});
  });
  const { document, timers } = loadCustomerDisplay({ withChannel: false, fetchImpl: fake.fn });
  await flushMicrotasks();
  assert.strictEqual(elHidden(document, 'cd-promo'), false, 'the promotion must show before its own deadline');
  assert.strictEqual(promoCallCount, 1);

  // Fire the pending timer(s) -- NO sale, NO cart event, nothing else in
  // this file triggers a re-render. This is the whole point of the fix:
  // without a deadline timer of its own, nothing here would ever notice.
  timers.fireAll();
  await flushMicrotasks();
  assert.strictEqual(elHidden(document, 'cd-promo'), true, 'an expired promotion must hide itself the instant its OWN deadline passes, with no sale');
  assert.strictEqual(promoCallCount, 2, 'the deadline firing must also re-fetch, in case a different promotion is now active');
  console.log('PASS: a promotion hides itself and re-fetches the instant its own ends_at passes, with no sale or cart event');
}

async function testPromotionExpiryTimerIsClearedOnReapply() {
  // "Clear the timer on every re-apply" (verifier instruction, verbatim).
  // Applies a promotion with a near-future ends_at, then IMMEDIATELY
  // re-applies a DIFFERENT one with no ends_at at all -- if the FIRST
  // timer were not cleared, firing it would wrongly hide the SECOND
  // (still-current, never-expires) promotion.
  const { document, CustomerDisplay, timers } = loadCustomerDisplay({ withChannel: false });
  CustomerDisplay._applyPromotion({ id: 1, name: 'Expiring Soon', discount_pct: 10, category_id: 1, category_name: 'Snacks', ends_at: sortableDate(5 / 86400) });
  assert.strictEqual(elHidden(document, 'cd-promo'), false);
  CustomerDisplay._applyPromotion({ id: 2, name: 'Ongoing', discount_pct: 20, category_id: 2, category_name: 'Drinks', ends_at: null });
  assert.strictEqual(elHidden(document, 'cd-promo'), false);
  assert.strictEqual(elText(document, 'cd-promo'), '20% off Drinks');

  timers.fireAll();   // the FIRST promotion's (cleared) timer, if it still existed, would fire here
  assert.strictEqual(elHidden(document, 'cd-promo'), false, 'the stale first timer must not hide the SECOND, still-current promotion');
  assert.strictEqual(elText(document, 'cd-promo'), '20% off Drinks', 'the second promotion must still be the one shown');
  console.log('PASS: re-applying a promotion clears the previous one\'s deadline timer');
}

// ─────────────────────────────────────────────────────────────────────────────
// D2 re-verify: a REAL bug the Item 2 real-browser E2E scenario caught on a
// real run (retail_p1a_display_e2e.py), that no VM test here had caught --
// `_promotionIsCurrent` used to compare `ends_at` against `_nowLocalSortable()`
// as a plain STRING at SECOND granularity (matching core/retail/
// promotions.py's own inclusive `ends_at>=now_local` SQL), while the deadline
// timer's `_msUntilSortableTimestamp` has always needed MILLISECOND
// precision. The two disagreed for up to ~1 real second around every
// deadline: the timer fires (correct, ms-precise) and hides the row, then
// its own re-fetch lands within the SAME calendar second as `ends_at` (the
// server's inclusive boundary still returns the row) -- and the OLD
// second-granularity `_promotionIsCurrent` said "still current" (string
// equality, not "<"), showing it again, while the ms-precise scheduler's
// `deltaMs<=0` guard correctly refused to arm a new timer. Net effect: the
// promotion got stuck on screen forever, with no timer left to ever hide it
// short of an unrelated sale/cart event -- proven live on a real Chromium
// tab, wall clock, a 20-second promotion still visible after a 30-second
// wait. Fixed by making `_promotionIsCurrent` call the SAME
// `_msUntilSortableTimestamp` the scheduler already used, so there is only
// ONE definition of "current" in this file.
// ─────────────────────────────────────────────────────────────────────────────

function testPromotionAtExactBoundarySecondIsNotCurrent() {
  const { document, CustomerDisplay } = loadCustomerDisplay({ withChannel: false });
  // sortableDate(0) truncates "now" down to the START of the CURRENT second
  // (no milliseconds captured by the 'YYYY-MM-DD HH:MM:SS' shape) -- by the
  // time this line's own Date.now() read happens, real wall-clock
  // milliseconds have already elapsed within that same second, so this
  // instant's TRUE ms-precise deadline is already in the past even though
  // its second-truncated STRING is exactly equal to "now"'s own
  // second-truncated string. This is deterministic (never flaky): the
  // second read inside _promotionIsCurrent can only be equal to or later
  // than this one, never earlier, so the computed delta is always <= 0.
  CustomerDisplay._applyPromotion({
    id: 9, name: 'Boundary Sale', discount_pct: 10, category_id: 1, category_name: 'Snacks', product_id: null,
    ends_at: sortableDate(0),
  });
  assert.strictEqual(elHidden(document, 'cd-promo'), true,
    'a promotion whose ends_at truncates to the CURRENT second (already ms-past its true deadline, just not second-past it) must be treated as expired -- the exact boundary the second-granularity comparison used to get wrong');
  console.log('PASS: a promotion at the exact boundary second (ms-expired, second-equal) is correctly treated as not current');
}

// ─────────────────────────────────────────────────────────────────────────────
// PER-TEST ISOLATION -- a flat sequence reports only the first failure and
// hides the rest as "not run"; this runs every check regardless.
// ─────────────────────────────────────────────────────────────────────────────
const CHECKS = [
  ['no BroadcastChannel never throws and stays idle', testNoBroadcastChannelNeverThrowsAndStaysIdle],
  ['connecting announces display_ready', testConnectingAnnouncesReadiness],
  ['a cart message renders exact passthrough strings, escaped', testCartMessageRendersExactPassthroughStrings],
  ['the discount row shows only when present', testDiscountRowShowsOnlyWhenPresent],
  ['sale-complete ignores an empty cart but not a non-empty one', testSaleCompleteIgnoresEmptyCartButNotNonEmptyCart],
  ['sale-complete reverts to idle when its timer fires', testSaleCompleteRevertsToIdleWhenItsOwnTimerFires],
  ['an unrecognised message type is ignored, not thrown', testUnknownMessageTypeIsIgnoredNotThrown],
  ['customer-display.js never reformats money', testCustomerDisplayNeverReformatsMoney],
  ['the till-side payload builder uses this._fmt() throughout', testPayloadBuilderUsesTillsOwnFormatter],
  ['the POS button lives inside the focus-guarded row', testPosButtonLivesInsideTheFocusGuardedRow],
  ['every new locale key exists in both catalogs', testEveryNewLocaleKeyExistsInBothCatalogs],
  ['customer-display.html wires tokens/RTL/theme correctly', testCustomerDisplayHtmlLinksTokensAndBothStylesheets],
  ['#12-display customer-display.html <title> is neutral, no vendor name', testCustomerDisplayHtmlTitleIsNeutral],
  ['#12-display neutral <title> is mutation-proved', testMutationProvedNeutralTitle],
  ['#9 .cd-screen[hidden] rule still carries !important', testCdScreenHiddenRuleUsesImportant],
  ['#12 neutral bilingual welcome shown before any source answers', testNeutralWelcomeShownBeforeAnySourceAnswers],
  ['#12 a real branding name latches over a later null from the relay', testRealShopNameFromBrandingLatchesOverLaterNull],
  ['#11 the relay poll applies shop meta once and dedupes by seq', testPollingAppliesShopMetaOnceAndDedupesBySeq],
  ['#11 the relay poll advances on a genuinely newer seq', testPollingAdvancesOnANewerSeq],
  ['#11 the relay poll survives a missing route, 401, and a network error', testPollingSurvivesMissingRouteAndUnauthenticatedContext],
  ['#8 the Promotions row and struck-through original price show only when sent', testPromotionsRowShowsOnlyWhenPresent],
  ['P1a the promotion row shows/hides with the active promotion, WITH its scope', testPromotionRowRendersActivePromotion],
  ['P1a the one-time /promotions/active fetch renders the highest-discount eligible row', testPromotionFetchAppliesHighestDiscountEligibleRow],
  ['P1a the WhatsApp QR shows only when the server supplies one', testWhatsappQrRendersOnlyWhenConfigured],
  ['F7 follow-up: a discount_pct tie is broken by the lowest id', testPromotionPicksHighestDiscountTiebrokenByLowestId],
  ['F7 follow-up: an unresolved or absent scope name hides the row', testPromotionExcludedWhenScopeNameUnresolved],
  ['F7 a category- or product-scoped promotion with a resolved name is shown, WITH its scope', testPromotionIncludedWhenScopeResolvedAndCurrent],
  ['F7 an expired promotion is excluded even with a resolved scope; no-ends_at is not', testPromotionExcludedWhenExpired],
  ['F7 the promotion re-fetches on every idle transition, no new timer', testIdleTransitionRefetchesPromotion],
  ['Item 2: a promotion hides and re-fetches the instant its own deadline passes', testPromotionHidesAndRefetchesWhenItsOwnDeadlinePasses],
  ['Item 2: re-applying a promotion clears the previous deadline timer', testPromotionExpiryTimerIsClearedOnReapply],
  ['D2 re-verify: a promotion at the exact boundary second is not current (real bug, real fix)', testPromotionAtExactBoundarySecondIsNotCurrent],
];

async function runAll() {
  let failures = 0;
  for (const [name, fn] of CHECKS) {
    try {
      await fn();   // sync tests return undefined, which `await` resolves through immediately
    } catch (e) {
      failures++;
      console.error(`FAIL: ${name}`);
      console.error(e && e.message ? e.message : e);
    }
  }

  if (failures > 0) {
    console.error(`\n${failures}/${CHECKS.length} check(s) failed.`);
    process.exit(1);
  } else {
    console.log(`\nPASS: retail_customer_display_test.js — ${CHECKS.length} checks`);
  }
}

runAll();
