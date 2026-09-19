/**
 * ONE ESCAPE CLOSES ONE DIALOG — the modal key stack.
 *
 * WHY THIS TEST EXISTS
 * Every dialog in subsystem-retail.js registers its Escape handler on
 * `document`, because the focused element at the time is unknowable. That is
 * correct while exactly one dialog is open, and wrong the moment two are —
 * and this file stacks dialogs deliberately in at least three places:
 *
 *     _showSupplierModal  -> delete a contact     -> _confirm(danger)
 *     _openHeldSalesModal -> resume/discard       -> _confirm
 *     _openCreateReturn   -> change refund method -> _confirm
 *
 * Both handlers are on the SAME document node, so they are siblings in one
 * dispatch, not a bubble chain: `stopPropagation` on one does not stop the
 * other. One Escape press fires BOTH. The shopkeeper answers "are you sure?"
 * and the question and the half-filled form behind it disappear together.
 *
 * This was found while planning the wiring of the 27 remaining hand-rolled
 * modals, BEFORE any of them were wired — which matters, because wiring them
 * is precisely what would have introduced it. Today only four dialogs run
 * through the helpers, and the three stacking paths above all happen to raise
 * _confirm from a modal that is NOT yet wired, so no second listener exists
 * and the bug is latent. It would have arrived with the fix.
 *
 * WHAT IS PINNED
 *   1. With a wired modal open and a _confirm raised on top of it, ONE Escape
 *      resolves the _confirm and leaves the modal underneath standing.
 *   2. A SECOND Escape then closes that modal. (The other half: a stack that
 *      simply swallowed every Escape after the first would pass check 1 and
 *      be useless — see ENGINEERING.md on proving both directions.)
 *   3. A single wired modal with nothing on top still closes on Escape, so
 *      the queue does not cost the ordinary case anything.
 *   4. A dialog removed by a path that bypassed its close() — an auto-dismiss
 *      timeout, a caller's bare .remove() — does not leave a layer at the
 *      front of the queue swallowing every later Escape.
 *
 * MUTATION-PROVED, four mutants, three caught:
 *
 *   drop the top-layer guard from _wireModalA11y  -> check 1 RED (the defect)
 *   drop the top-layer guard from _confirm        -> check 5 RED
 *   stop pruning detached layers                  -> check 4 RED
 *   never drop a layer on close()                 -> SURVIVES
 *
 * The survivor is reported rather than papered over, because it says
 * something true about the design: close() removes the overlay, which makes
 * that layer detached, which the prune then collects. The explicit
 * _dropModalKeyLayer is the fast path and the prune is the backstop, and they
 * genuinely overlap — so no test can distinguish them by behaviour alone.
 * Deleting the explicit drop would leave the queue correct but growing until
 * the next keypress swept it. That is a reason to keep it, not evidence that
 * this file proves it.
 *
 * Two of these checks only became real after mutation. Check 4 originally
 * opened the abandoned dialog FIRST, leaving the leaked layer underneath
 * where it could never be consulted — it passed with the prune deleted.
 * Check 5 did not exist; check 1 always puts the _confirm on top, where its
 * own guard is satisfied no matter what, so the _confirm mutant sailed
 * through. Both are recorded here because "the test passed" was, in both
 * cases, the wrong conclusion.
 *
 * Harness shape follows retail_confirm_modal_test.js: a vm sandbox with a
 * stubbed DOM, driving the REAL _wireModalA11y and the REAL _confirm from the
 * REAL source rather than a reimplementation. `fireDocKeydown` deliberately
 * calls EVERY registered document handler, because firing only one would
 * simulate away the very thing under test.
 *
 * Run: node products/retail/tests/retail_modal_stack_test.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const SRC_PATH = path.join(__dirname, '..', 'frontend', 'subsystem-retail.js');
const SOURCE = fs.readFileSync(SRC_PATH, 'utf8');

function makeStubElement(overrides) {
  const handlers = Object.create(null);
  return Object.assign({
    id: '', innerHTML: '', focusCount: 0, removeCount: 0,
    // Mirrors a real element's default. _topModalKeyLayer prunes only on an
    // explicit `false`, so a live stub must say so rather than leaving it
    // undefined; remove() below is what flips it.
    isConnected: true,
    addEventListener(type, fn) { (handlers[type] || (handlers[type] = [])).push(fn); },
    removeEventListener() {},
    fire(type, evt) { (handlers[type] || []).forEach((fn) => fn(evt || {})); },
    focus() { this.focusCount += 1; },
    remove() { this.removeCount += 1; this.isConnected = false; },
    setAttribute() {},
    getAttribute() { return null; },
    classList: { add() {}, remove() {}, contains() { return false; } },
  }, overrides);
}

function loadRetailSystem() {
  const sandbox = {};
  sandbox.window = sandbox;
  sandbox.t = (s) => s;
  sandbox.console = console;
  sandbox.navigator = { userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)' };
  sandbox.localStorage = { getItem: () => null, setItem: () => {} };
  sandbox.setTimeout = setTimeout;
  sandbox.clearTimeout = clearTimeout;

  const els = Object.create(null);
  const getEl = (id) => { if (!els[id]) els[id] = makeStubElement({ id }); return els[id]; };
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
    body: { appendChild(el) { appended.push(el); } },
  };

  vm.createContext(sandbox);
  vm.runInContext(SOURCE, sandbox, { filename: SRC_PATH });
  assert.ok(sandbox.RetailSystem, 'RetailSystem failed to load from subsystem-retail.js');

  return {
    RetailSystem: sandbox.RetailSystem, els, trigger, appended,
    // EVERY handler, exactly as one real dispatch would. Firing a single one
    // would hide the sibling-listener problem this file exists to catch.
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

/* Opens a wired hand-rolled modal the way a real caller does: build an
   overlay, wire it, hand back the overlay so the test can watch remove(). */
function openWiredModal(ctx, dialogId) {
  const overlay = makeStubElement({ id: dialogId + '-overlay' });
  const close = ctx.RetailSystem._wireModalA11y(overlay, dialogId);
  return { overlay, close };
}

// ═════════════════════════════════════════════════════════════════════════
// 1 + 2 — one Escape per dialog, top first  [MUTATION-PROVED]
// ═════════════════════════════════════════════════════════════════════════

async function testEscapeClosesOnlyTheTopDialogThenTheOneBeneath() {
  const ctx = loadRetailSystem();
  const modal = openWiredModal(ctx, 'ret-supplier-dialog');
  assert.strictEqual(ctx.docKeydownCount(), 1, 'the wired modal did not register a document keydown handler');

  const confirmPromise = ctx.RetailSystem._confirm({ title: 'Delete contact', message: 'Sure?', danger: true });
  assert.strictEqual(ctx.docKeydownCount(), 2,
    'the _confirm raised on top did not register its own document keydown handler — ' +
    'this test cannot prove anything unless BOTH listeners are live');

  ctx.fireDocKeydown({ key: 'Escape' });

  const answer = await withTimeout(confirmPromise, 500, 'Escape on the stacked _confirm');
  assert.strictEqual(answer, false, 'the _confirm on top did not resolve false on Escape');
  assert.strictEqual(
    modal.overlay.removeCount, 0,
    'ONE Escape closed BOTH dialogs: the _confirm answered AND the supplier modal ' +
    'underneath it was removed. The shopkeeper answers "are you sure?" and loses the ' +
    'half-filled form behind it in the same keystroke. Both handlers are on the same ' +
    'document node, so stopPropagation on one does not stop the other — only the ' +
    '_topModalKeyLayer() check can.'
  );

  // The other direction: the queue must not simply swallow everything after
  // the first Escape. The modal underneath is now the front of the queue.
  ctx.fireDocKeydown({ key: 'Escape' });
  assert.strictEqual(
    modal.overlay.removeCount, 1,
    'after the dialog on top was dismissed, a second Escape did not close the modal ' +
    'underneath it. A stack that eats every later Escape passes the first assertion ' +
    'and leaves a dialog the keyboard cannot dismiss at all.'
  );
  assert.strictEqual(ctx.trigger.focusCount > 0, true, 'focus was never returned to the trigger');

  console.log('PASS: one Escape closes the top dialog only; the next closes the one beneath');
}

// ═════════════════════════════════════════════════════════════════════════
// 3 — the ordinary single-dialog case still works
// ═════════════════════════════════════════════════════════════════════════

async function testASingleWiredModalStillClosesOnEscape() {
  const ctx = loadRetailSystem();
  const modal = openWiredModal(ctx, 'ret-product-dialog');
  ctx.fireDocKeydown({ key: 'Escape' });
  assert.strictEqual(
    modal.overlay.removeCount, 1,
    'a lone wired modal no longer closes on Escape — the queue has cost the ordinary ' +
    'case the behaviour it was added to protect.'
  );
  console.log('PASS: a single wired modal still closes on Escape');
}

// ═════════════════════════════════════════════════════════════════════════
// 4 — a dialog that left by a path bypassing close() does not wedge the queue
// ═════════════════════════════════════════════════════════════════════════

async function testALeakedLayerDoesNotSwallowLaterEscapes() {
  const ctx = loadRetailSystem();

  // ORDER IS THE WHOLE TEST. The abandoned dialog has to be the one at the
  // FRONT of the queue when Escape arrives, because that is the only position
  // from which a leaked layer can do harm — and the only position the prune
  // looks at.
  //
  // Written the other way round first (abandoned opened, then the live one on
  // top of it) this check passed whether the prune existed or not: the leaked
  // layer sat harmlessly UNDERNEATH and was never consulted. It measured
  // nothing. Caught by mutating the prune away and watching it stay green.
  const live = openWiredModal(ctx, 'ret-category-dialog');

  // _showScanNotFound gives itself a 12s auto-dismiss and _showReceipt 8s,
  // both of which remove the overlay directly. Simulate that: removed, but
  // its close() never ran, so its layer is still queued — and still in front.
  const abandoned = openWiredModal(ctx, 'ret-scan-dialog');
  abandoned.overlay.remove();           // bypasses close() entirely

  ctx.fireDocKeydown({ key: 'Escape' });

  assert.strictEqual(
    live.overlay.removeCount, 1,
    'a dialog that was removed without running its close() stayed at the front of the ' +
    'queue and swallowed the Escape meant for the dialog actually on screen. The one ' +
    'you can see stops responding because of one you cannot.'
  );
  console.log('PASS: a layer left behind by a bypassed close() does not wedge the queue');
}

// ═════════════════════════════════════════════════════════════════════════
// 5 — the queue protects _confirm too, not just the hand-rolled modals
// ═════════════════════════════════════════════════════════════════════════

/* The mirror of check 1, with the stack the other way up. Check 1 always has
   the _confirm on top, where its own guard is satisfied no matter what — so
   deleting that guard left check 1 green. This is the case that needs it:
   something opened ON TOP of a _confirm must take the Escape, and the
   question underneath must still be waiting when the operator gets back to
   it. Without the guard in _confirm the answer is silently decided as "no"
   by a keystroke aimed at a different dialog entirely. */
async function testAConfirmUnderneathDoesNotAnswerAKeyAimedAbove() {
  const ctx = loadRetailSystem();

  const confirmPromise = ctx.RetailSystem._confirm({ title: 'Change refund method', message: 'Switch?' });
  let settled = false;
  confirmPromise.then(() => { settled = true; });

  const onTop = openWiredModal(ctx, 'ret-return-dialog');
  ctx.fireDocKeydown({ key: 'Escape' });

  assert.strictEqual(
    onTop.overlay.removeCount, 1,
    'the dialog on top did not close on Escape while a _confirm sat underneath it'
  );
  // A resolved promise runs its .then on the microtask queue, so let it drain
  // before concluding the confirm is still open. Without this the assertion
  // below would pass even when the confirm HAD answered.
  await Promise.resolve(); await Promise.resolve();
  assert.strictEqual(
    settled, false,
    'the _confirm underneath answered an Escape aimed at the dialog on top of it. ' +
    'The operator dismissed one thing and silently answered "no" to a different ' +
    'question they had not read yet.'
  );

  // And it is still live: the next Escape, now that it is at the front, does
  // answer. A guard that permanently deafened the confirm would be no better.
  ctx.fireDocKeydown({ key: 'Escape' });
  const answer = await withTimeout(confirmPromise, 500, 'Escape once the _confirm is back on top');
  assert.strictEqual(answer, false, 'the _confirm never answered even once it was back at the front of the queue');

  console.log('PASS: a _confirm underneath ignores a key aimed above it, and still answers its own');
}

// ═════════════════════════════════════════════════════════════════════════

async function main() {
  const checks = [
    ['Escape closes only the top dialog, then the one beneath', testEscapeClosesOnlyTheTopDialogThenTheOneBeneath],
    ['a single wired modal still closes on Escape', testASingleWiredModalStillClosesOnEscape],
    ['a leaked layer does not swallow later Escapes', testALeakedLayerDoesNotSwallowLaterEscapes],
    ['a _confirm underneath does not answer a key aimed above it', testAConfirmUnderneathDoesNotAnswerAKeyAimedAbove],
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
    console.error(`\nFAIL: retail_modal_stack_test.js — ${failures.length} of ${checks.length} checks failed`);
    process.exitCode = 1;
    return;
  }
  console.log(`PASS: retail_modal_stack_test.js — ${checks.length} checks`);
}

main().catch((err) => {
  console.error('FAIL: retail_modal_stack_test.js (runner)');
  console.error(err.message || err);
  process.exitCode = 1;
});
