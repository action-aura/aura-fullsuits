/**
 * PHASE 0 ITEM #4 + #15(a) — cash-drawer.js and import-wizard.js modals.
 *
 * WHY THIS TEST EXISTS
 * retail_modal_a11y_coverage_test.js (widened by this same item) proves the
 * STRUCTURE is right: an id, role="dialog", aria-modal, an aria-labelledby
 * that resolves, and no bare .remove() left in the source. It cannot prove
 * the WIRING actually behaves, because it never runs a line of the file --
 * it is a text scan. This file drives the REAL _wireModalA11y, the REAL
 * CashDrawer.openMovementModal/_previewVariance and the REAL
 * ImportWizard._smartShell from the real source, through a stubbed DOM, the
 * same harness shape retail_modal_stack_test.js already uses for
 * subsystem-retail.js's own modals. Structure and behaviour together are the
 * argument; either alone is half of it (see that file's own docstring).
 *
 * WHAT IS PINNED
 *   1. Escape detaches the Cash In/Out (Movement) modal and returns focus to
 *      whatever was focused before it opened.
 *   2. A _confirm raised on top of Cash In/Out takes the FIRST Escape (the
 *      modal underneath survives); a SECOND Escape then closes Cash In/Out.
 *      Mirrors retail_modal_stack_test.js's check 1/2 for subsystem-retail.js
 *      modals, run here against a DIFFERENT object (CashDrawer, not
 *      RetailSystem) sharing the SAME _modalKeyStack on RetailSystem.
 *   3. Escape detaches the Import Wizard shell and returns focus.
 *   4. _previewVariance rounds at the currency's own minor unit, not a
 *      hardcoded decimal count, proved from THREE angles at once (a
 *      reviewer of the first version of this check found each of the first
 *      two vacuous on their own -- see "MUTATION-PROVED" below):
 *        4a. 3dp (JOD), a shortfall: 50.000 counted against an expected
 *            50.005 previews EXACTLY "−JD 0.005 short" (typographic minus,
 *            the word, stripped of markup) -- not merely "contains 0.005"
 *            and not merely "contains a hyphen", because
 *            `.money--accounting`'s own class name contains a hyphen on
 *            EVERY negative render, balanced or not.
 *        4b. 3dp, the positive case: 50.010 counted against 50.005 previews
 *            EXACTLY "JD 0.005 over" -- pins the SIGN, not just the digits,
 *            which a swapped (expected − counted) mutant cannot fake.
 *        4c. 2dp, the currency-precision case: with _currencyDecimals
 *            forced to 2, 10.00 counted against 10.004 previews EXACTLY
 *            "JD 0.00 balanced" -- a real 2dp shop's fil-level noise must
 *            round AWAY, not show up as a phantom shortfall.
 *
 * MUTATION-PROVED, four mutants, quoted in the Phase 0 C5 report:
 *   remove the _wireModalA11y call from openMovementModal   -> check 1 RED
 *   remove the _wireModalA11y call from ImportWizard._shell -> (covered by
 *     the ratchet's count-parity check instead; _smartShell here is the one
 *     actually driven, since it needs no _schema fixture)
 *   restore Math.round(x*100)/100 in _previewVariance        -> check 4a/4c RED
 *   swap to Math.round((expected-counted)*q)/q (sign flip)   -> check 4a/4b RED
 *   hardcode Math.round(x*1000)/1000 (bakes in 3dp always)   -> check 4c RED
 *
 * Run: node products/retail/tests/retail_p0_modals_test.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const FRONTEND_DIR = path.join(__dirname, '..', 'frontend');
const RETAIL_SRC = fs.readFileSync(path.join(FRONTEND_DIR, 'subsystem-retail.js'), 'utf8');
const CASH_DRAWER_SRC = fs.readFileSync(path.join(FRONTEND_DIR, 'cash-drawer.js'), 'utf8');
const IMPORT_WIZARD_SRC = fs.readFileSync(path.join(FRONTEND_DIR, 'import-wizard.js'), 'utf8');

/* Same stub shape as retail_modal_stack_test.js's makeStubElement, PLUS
   `className` and a real `.closest()` -- this file's own close() routes
   through RetailSystem._closeModalOverlay(document.getElementById(...))
   rather than a closure captured at open time (see import-wizard.js's own
   comment on why: it has to tear down whichever step is currently open, not
   one fixed overlay), so unlike retail_modal_stack_test.js this harness must
   simulate `.closest()` walking to the overlay for that lookup to resolve to
   anything. Flat stubs have no real parent chain, so `.closest('.x')` here
   just asks "does THIS element itself carry class x" -- sufficient because
   every call site in these two files passes either the overlay itself or
   (from inline markup) `this`, and `_closeModalOverlay` never needs to climb
   more than one level in practice. */
function makeStubElement(overrides) {
  const handlers = Object.create(null);
  return Object.assign({
    id: '', className: '', innerHTML: '', value: '', textContent: '', disabled: false,
    focusCount: 0, removeCount: 0,
    isConnected: true,
    style: {},
    addEventListener(type, fn) { (handlers[type] || (handlers[type] = [])).push(fn); },
    removeEventListener() {},
    fire(type, evt) { (handlers[type] || []).forEach((fn) => fn(evt || {})); },
    focus() { this.focusCount += 1; },
    remove() { this.removeCount += 1; this.isConnected = false; },
    setAttribute() {},
    getAttribute() { return null; },
    classList: { add() {}, remove() {}, contains() { return false; } },
    closest(sel) {
      if (sel[0] !== '.') return null;
      const cls = sel.slice(1);
      return String(this.className || '').split(/\s+/).includes(cls) ? this : null;
    },
  }, overrides);
}

/* Loads subsystem-retail.js (RetailSystem, _confirm, the modal key stack),
   THEN cash-drawer.js and import-wizard.js (both reference RetailSystem only
   inside function bodies, resolved lazily at call time, so load order after
   RetailSystem exists is all that matters -- exactly like the real
   index.html loads them, script tags aside; see that file's own comment on
   this). Neither file's TOP-LEVEL code needs anything beyond `window` (both
   end with `window.X = X;`), which the sandbox already provides. */
function loadModalsContext() {
  const sandbox = {};
  sandbox.window = sandbox;
  sandbox.t = (s) => s;
  sandbox.console = console;
  sandbox.navigator = { userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)' };
  sandbox.localStorage = { getItem: () => null, setItem: () => {} };
  sandbox.setTimeout = setTimeout;
  sandbox.clearTimeout = clearTimeout;

  // `els` is a pre-seed/fallback map (a test can plant `ctx.els['some-id'] =
  // {...}` before an id is ever created, e.g. an <input> this file never
  // actually appends because innerHTML is never parsed). `byId` is the
  // AUTHORITATIVE map for elements this harness actually appended to
  // document.body -- getElementById prefers a still-connected entry there,
  // matching a real DOM: after an element is appended, getElementById(its
  // id) resolves to that SAME object, and stops resolving to it once it is
  // removed. Without this a close() that looks itself up by id (as
  // import-wizard.js's does, on purpose -- see that file's comment) would
  // always find a disconnected stand-in and silently no-op.
  const els = Object.create(null);
  const byId = Object.create(null);
  const getEl = (id) => {
    if (byId[id] && byId[id].isConnected !== false) return byId[id];
    if (!els[id]) els[id] = makeStubElement({ id });
    return els[id];
  };
  const trigger = makeStubElement({ id: 'trigger-btn' });
  const docHandlers = Object.create(null);
  const appended = [];

  sandbox.document = {
    activeElement: trigger,
    getElementById: getEl,
    createElement: () => makeStubElement(),
    addEventListener(type, fn, capture) {
      (docHandlers[type] || (docHandlers[type] = [])).push({ fn, capture });
    },
    removeEventListener(type, fn) {
      if (!docHandlers[type]) return;
      docHandlers[type] = docHandlers[type].filter((h) => h.fn !== fn);
    },
    body: {
      appendChild(el) {
        appended.push(el);
        if (el.id) byId[el.id] = el;
      },
    },
  };

  vm.createContext(sandbox);
  vm.runInContext(RETAIL_SRC, sandbox, { filename: 'subsystem-retail.js' });
  assert.ok(sandbox.RetailSystem, 'RetailSystem failed to load from subsystem-retail.js');
  vm.runInContext(CASH_DRAWER_SRC, sandbox, { filename: 'cash-drawer.js' });
  assert.ok(sandbox.CashDrawer, 'CashDrawer failed to load from cash-drawer.js');
  vm.runInContext(IMPORT_WIZARD_SRC, sandbox, { filename: 'import-wizard.js' });
  assert.ok(sandbox.ImportWizard, 'ImportWizard failed to load from import-wizard.js');

  return {
    RetailSystem: sandbox.RetailSystem,
    CashDrawer: sandbox.CashDrawer,
    ImportWizard: sandbox.ImportWizard,
    els, trigger, appended,
    fireDocKeydown(evt) {
      const e = Object.assign({ preventDefault() {}, stopPropagation() {} }, evt);
      (docHandlers.keydown || []).slice().forEach((h) => h.fn(e));
    },
    docKeydownCount() { return (docHandlers.keydown || []).length; },
  };
}

function withTimeout(promise, ms, label) {
  return Promise.race([
    promise,
    new Promise((_, reject) => setTimeout(() => reject(new Error(`${label} — never resolved within ${ms}ms`)), ms)),
  ]);
}

// ═════════════════════════════════════════════════════════════════════════
// 1 — Escape detaches Cash In/Out and returns focus  [MUTATION-PROVED]
// ═════════════════════════════════════════════════════════════════════════

function testEscapeDetachesCashInOutAndReturnsFocus() {
  const ctx = loadModalsContext();
  ctx.CashDrawer._session = { is_this_terminal: true };
  ctx.CashDrawer.openMovementModal();

  assert.strictEqual(ctx.appended.length, 1, 'openMovementModal() did not append an overlay');
  assert.strictEqual(ctx.docKeydownCount(), 1,
    'openMovementModal() did not register a document keydown handler -- it never called ' +
    '_wireModalA11y, so Escape does nothing');

  const overlay = ctx.appended[0];
  ctx.fireDocKeydown({ key: 'Escape' });

  assert.strictEqual(overlay.removeCount, 1, 'Escape did not remove the Cash In/Out overlay');
  assert.strictEqual(ctx.trigger.focusCount > 0, true, 'focus was not returned to the trigger after Escape');

  console.log('PASS: Escape detaches Cash In/Out and returns focus to the trigger');
}

// ═════════════════════════════════════════════════════════════════════════
// 2 — a _confirm stacked on Cash In/Out takes the Escape, not the drawer
// ═════════════════════════════════════════════════════════════════════════

async function testConfirmStackedOnCashInOutClosesOnlyTheConfirm() {
  const ctx = loadModalsContext();
  ctx.CashDrawer._session = { is_this_terminal: true };
  ctx.CashDrawer.openMovementModal();
  const overlay = ctx.appended[0];
  assert.strictEqual(ctx.docKeydownCount(), 1, 'the Cash In/Out modal did not register its keydown handler');

  const confirmPromise = ctx.RetailSystem._confirm({ title: 'Discard entry?', message: 'Sure?' });
  assert.strictEqual(ctx.docKeydownCount(), 2,
    'the _confirm raised on top of Cash In/Out did not register its own keydown handler -- ' +
    'this test cannot prove anything unless BOTH listeners are live');

  ctx.fireDocKeydown({ key: 'Escape' });
  const answer = await withTimeout(confirmPromise, 500, 'Escape on the stacked _confirm');
  assert.strictEqual(answer, false, 'the stacked _confirm did not resolve false on Escape');
  assert.strictEqual(
    overlay.removeCount, 0,
    'ONE Escape closed BOTH dialogs: the _confirm answered AND the Cash In/Out modal ' +
    'underneath it was removed in the same keystroke.'
  );

  ctx.fireDocKeydown({ key: 'Escape' });
  assert.strictEqual(
    overlay.removeCount, 1,
    'after the _confirm on top was dismissed, a second Escape did not close the Cash ' +
    'In/Out modal underneath it.'
  );

  console.log('PASS: a _confirm stacked on Cash In/Out takes one Escape; a second closes the drawer modal');
}

// ═════════════════════════════════════════════════════════════════════════
// 3 — Escape detaches the Import Wizard and returns focus  [MUTATION-PROVED]
// ═════════════════════════════════════════════════════════════════════════

function testEscapeDetachesTheImportWizardAndReturnsFocus() {
  const ctx = loadModalsContext();
  ctx.ImportWizard._smartShell('<p>body</p>', '<button>Next</button>');

  assert.strictEqual(ctx.appended.length, 1, '_smartShell() did not append an overlay');
  assert.strictEqual(ctx.docKeydownCount(), 1,
    '_smartShell() did not register a document keydown handler -- it never called ' +
    '_wireModalA11y, so Escape does nothing');

  const overlay = ctx.appended[0];
  ctx.fireDocKeydown({ key: 'Escape' });

  assert.strictEqual(overlay.removeCount, 1, 'Escape did not remove the Import Wizard overlay');
  assert.strictEqual(ctx.trigger.focusCount > 0, true, 'focus was not returned to the trigger after Escape');

  console.log('PASS: Escape detaches the Import Wizard and returns focus to the trigger');
}

// Companion to check 3: a step TRANSITION (a second _smartShell/_shell call
// on an already-open wizard) must tear the previous step's wiring down
// rather than leaving it live underneath the new one -- the trap the item's
// own investigation named ("calling _wireModalA11y twice on same overlay id
// would double-register keydown listeners"). Proven by driving two steps and
// checking exactly one keydown handler survives.
function testStepTransitionDoesNotLeakThePreviousListener() {
  const ctx = loadModalsContext();
  ctx.ImportWizard._smartShell('<p>step 1</p>', '<button>Next</button>');
  const step1Overlay = ctx.appended[0];
  assert.strictEqual(ctx.docKeydownCount(), 1, 'step 1 did not register exactly one keydown handler');

  ctx.ImportWizard._smartShell('<p>step 2</p>', '<button>Import</button>');
  assert.strictEqual(ctx.appended.length, 2, 'step 2 did not append a fresh overlay');
  assert.strictEqual(
    step1Overlay.removeCount, 1,
    "step 2 did not tear down step 1's overlay before replacing it -- a bare .remove() " +
    'would leave step 1 in the DOM count without ever running its close()'
  );
  assert.strictEqual(
    ctx.docKeydownCount(), 1,
    'moving to step 2 left more than one document keydown handler registered -- the ' +
    "previous step's listener leaked instead of being torn down by close()"
  );

  const step2Overlay = ctx.appended[1];
  ctx.fireDocKeydown({ key: 'Escape' });
  assert.strictEqual(step2Overlay.removeCount, 1, 'Escape did not remove the step-2 (current) overlay');

  console.log('PASS: a step transition tears down the previous step\'s wiring instead of leaking it');
}

// ═════════════════════════════════════════════════════════════════════════
// 4 — item #15(a): drawer close rounds at the currency's own minor unit
//     [MUTATION-PROVED, from three angles -- see the file docstring]
// ═════════════════════════════════════════════════════════════════════════

// Strips markup so the assertion pins RENDERED TEXT, not "some tag soup
// that happens to contain a substring". `_varianceCell` returns
// `<span class="money ...">DIGITS</span> <span class="cd-note">WORD</span>`;
// stripping tags collapses that to `DIGITS WORD`, which is exactly what a
// screen reader's accessible name and a sighted user's eye both see.
function stripTags(html) {
  return String(html).replace(/<[^>]+>/g, '');
}

function previewVarianceText(ctx, { counted, expected, currencyDp }) {
  if (currencyDp != null) ctx.RetailSystem._currencyDecimals = currencyDp;
  ctx.els['cd-close-counted'] = { value: String(counted) };
  ctx.els['cd-close-variance'] = { style: {} };
  ctx.els['cd-close-variance-val'] = { innerHTML: '' };
  ctx.CashDrawer._previewVariance(expected);
  assert.strictEqual(ctx.els['cd-close-variance'].style.display, 'flex', 'the variance row was not shown');
  return stripTags(ctx.els['cd-close-variance-val'].innerHTML);
}

// 4a (control) — 3dp (JOD), a genuine shortfall. Pins the EXACT text, not a
// substring: the old assertion here accepted rendered.includes('short') ||
// includes('−') || includes('-'), which a reviewer found vacuous twice over
// -- `.money--accounting`'s own class name contains a literal hyphen on
// EVERY negative render regardless of whether the rounding is right, and a
// sign-flip mutant (Math.round((expected-counted)*q)/q) renders the WRONG
// direction ("0.005 over") while still satisfying "contains 0.005".
function testDrawerCloseAt3dpShortfallReadsExactly() {
  const ctx = loadModalsContext();
  // _currencyDecimals defaults to 3 (JOD, this shop's home market -- see
  // subsystem-retail.js's own constructor comment); asserted, not assumed.
  assert.strictEqual(ctx.RetailSystem._currencyDp(), 3, 'test assumption: default currency precision is 3dp (JOD)');
  const text = previewVarianceText(ctx, { counted: '50.000', expected: 50.005 });
  assert.strictEqual(
    text, '−JD 0.005 short',
    `50.000 counted against an expected 50.005 must read exactly "−JD 0.005 short" at 3dp; got: ${JSON.stringify(text)}. ` +
    'A 2dp rounding (Math.round(x*100)/100) rounds a currency this precise to the wrong minor unit ' +
    '(measured -0.01, not 0.005), because the raw float difference is -0.005000000000002558.'
  );
  console.log('PASS: 50.000 vs 50.005 at 3dp previews exactly "−JD 0.005 short"');
}

// 4b — 3dp, the POSITIVE case. Pins the sign itself: a mutant that swaps the
// subtraction order (Math.round((expected-counted)*q)/q) renders "−JD 0.005
// short" here -- the exact opposite of what a counted-OVER drawer must say --
// while still passing 4a's own counted-under case with its sign merely
// flipped the other way. Neither case alone catches a sign flip; both do.
function testDrawerCloseAt3dpOverageReadsExactly() {
  const ctx = loadModalsContext();
  const text = previewVarianceText(ctx, { counted: '50.010', expected: 50.005 });
  assert.strictEqual(
    text, 'JD 0.005 over',
    `50.010 counted against an expected 50.005 must read exactly "JD 0.005 over" at 3dp; got: ${JSON.stringify(text)}`
  );
  console.log('PASS: 50.010 vs 50.005 at 3dp previews exactly "JD 0.005 over"');
}

// 4c — 2dp currency. Pins that the rounding reads its precision from
// _currencyDp() rather than a baked-in constant: a mutant that hardcodes
// Math.round(x*1000)/1000 (i.e. always rounds at 3dp, the value that made
// 4a and 4b pass) renders "−JD 0.00 short" here on a currency this shop's
// own settings say is 2dp, where the real code -- rounding at 2dp -- must
// call a quarter-fil-scale difference "balanced".
function testDrawerCloseAt2dpRoundsToBalanced() {
  const ctx = loadModalsContext();
  const text = previewVarianceText(ctx, { counted: '10.00', expected: 10.004, currencyDp: 2 });
  assert.strictEqual(
    text, 'JD 0.00 balanced',
    `10.00 counted against an expected 10.004 must read exactly "JD 0.00 balanced" at 2dp; got: ${JSON.stringify(text)}. ` +
    'A hardcoded *1000 rounding treats every currency as 3dp and renders a phantom shortfall instead.'
  );
  console.log('PASS: 10.00 vs 10.004 at 2dp previews exactly "JD 0.00 balanced"');
}

// ═════════════════════════════════════════════════════════════════════════

async function main() {
  const checks = [
    ['Escape detaches Cash In/Out and returns focus', testEscapeDetachesCashInOutAndReturnsFocus],
    ['a _confirm stacked on Cash In/Out closes only the confirm', testConfirmStackedOnCashInOutClosesOnlyTheConfirm],
    ['Escape detaches the Import Wizard and returns focus', testEscapeDetachesTheImportWizardAndReturnsFocus],
    ["a step transition does not leak the previous listener", testStepTransitionDoesNotLeakThePreviousListener],
    ['drawer close at 3dp (shortfall) reads exactly "−JD 0.005 short" (item #15a)', testDrawerCloseAt3dpShortfallReadsExactly],
    ['drawer close at 3dp (overage) reads exactly "JD 0.005 over" (item #15a)', testDrawerCloseAt3dpOverageReadsExactly],
    ['drawer close at 2dp reads exactly "JD 0.00 balanced" (item #15a)', testDrawerCloseAt2dpRoundsToBalanced],
  ];
  const failures = [];
  for (const [name, fn] of checks) {
    try {
      await fn();
    } catch (err) {
      failures.push(name);
      console.error(`FAIL: ${name}`);
      console.error('      ' + String((err && err.message) || err).replace(/\n/g, '\n      '));
    }
  }
  if (failures.length) {
    console.error(`\nFAIL: retail_p0_modals_test.js — ${failures.length} of ${checks.length} checks failed`);
    process.exitCode = 1;
    return;
  }
  console.log(`PASS: retail_p0_modals_test.js — ${checks.length} checks`);
}

main();
