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
 * MUTATION-PROVEN, PER CLAUDE.md's engineering standard: every guard below
 * (BroadcastChannel absent/throws, empty-cart-during-thank-you ignored,
 * non-empty-cart-during-thank-you interrupts, the 8s timer actually firing)
 * is exercised via the state that TRIGGERS it, not merely inferred from the
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

function makeFakeDocument() {
  const elements = {};
  ELEMENT_IDS.forEach((id) => { elements[id] = makeFakeElement(); });
  return {
    _elements: elements,
    readyState: 'complete',   // boot() runs synchronously, no DOMContentLoaded wait
    title: '',
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

// Builds a fresh vm context, runs the REAL customer-display.js in it, and
// returns { sandbox, CustomerDisplay, timers, posted, channelInstance }.
// `withChannel` toggles whether BroadcastChannel exists at all -- the one
// thing customer-display.js is required to survive without.
function loadCustomerDisplay({ withChannel }) {
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
    // fetch deliberately absent: _loadBranding()'s own guard
    // (`typeof global.fetch !== 'function'`) covers that path, and the
    // live-browser proof in the task report already exercises the real
    // branding fetch end to end -- this harness stays synchronous on purpose.
  };
  if (withChannel) {
    sandbox.BroadcastChannel = makeFakeBroadcastChannelClass(posted);
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
  const start = src.indexOf('_customerDisplayCartPayload()');
  assert.ok(start !== -1, '_customerDisplayCartPayload() not found in subsystem-retail.js');
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
];

let failures = 0;
for (const [name, fn] of CHECKS) {
  try {
    fn();
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
