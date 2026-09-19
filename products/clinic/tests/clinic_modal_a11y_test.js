/**
 * clinic_modal_a11y_test.js — dialog semantics, Escape, and focus for the
 * eight hand-rolled `.cl-modal-overlay` dialogs in subsystem-clinic.js.
 *
 * WHY THIS EXISTS
 * An audit found all eight custom modals in this file (add patient, patient
 * detail, book appointment, add doctor, new invoice, record payment, record
 * lab expense, new prescription) with no role="dialog", no aria-modal, no
 * Escape-to-close, no focus-return to the trigger, and only three of the
 * eight setting initial focus at all. Patient records, bookings, invoices
 * and prescriptions -- a clinic's whole workload -- were reachable by mouse
 * only. This file is the guard that keeps that fixed.
 *
 * The fix ports products/retail/frontend/subsystem-retail.js's
 * _wireModalA11y / _closeModalOverlay / _modalKeyStack trio into this file's
 * own idiom (`.cl-modal-overlay` / `.cl-modal`; no import -- separate
 * products, no shared frontend module system). This test is the clinic-side
 * counterpart of retail_modal_a11y_coverage_test.js (structural coverage)
 * and retail_modal_stack_test.js (behavioural, mutation-proved), combined
 * into one file because clinic has eight modals where retail has thirty-one.
 *
 * TWO KINDS OF CHECK, AND WHY BOTH MATTER (see ENGINEERING.md: a passing
 * test is not evidence)
 *
 *   STRUCTURAL (checks 1-3): regex over the file's own source, proving every
 *   `.cl-modal-overlay` build site is wired, every dialog is named AND the
 *   name resolves to a real id (a dangling aria-labelledby reads as done and
 *   announces nothing -- worse than no name), and no close path bypasses the
 *   helpers with a bare `.remove()`. Derived from the file's own structure,
 *   not a hand-curated list of eight modals, so a ninth modal added later is
 *   caught the day it ships unwired.
 *
 *   BEHAVIOURAL (checks 4-7): a vm sandbox with a stubbed DOM, driving the
 *   REAL _wireModalA11y / _closeModalOverlay / _topModalKeyLayer functions
 *   from the REAL source -- never a reimplementation. Proves the wiring
 *   actually behaves: focus moves in, Escape closes and returns focus, a
 *   leaked layer does not wedge the queue, and -- the one real near-miss
 *   this file has today -- the Patient Detail -> Book Appointment hand-off
 *   correctly drops its layer instead of leaking it in front of the next
 *   dialog. The two kinds of check are not redundant: structural proves
 *   coverage, behavioural proves the coverage isn't decorative.
 *
 * CAN CLINIC STACK DIALOGS TODAY? Evidence, not assertion:
 *   `.cl-modal-overlay` is `position:fixed; inset:0` (see
 *   subsystem-clinic.js's `_injectStyles`), so it covers the whole page and
 *   nothing behind it is clickable while one is open -- there is no path for
 *   a second custom overlay to open from underneath a first one that is
 *   still visibly showing. All eight destructive confirms in this file
 *   (_deletePatient, _deleteFollowup, _cancelAppt, _deleteLabExpense) use
 *   the native `confirm()`, not a custom dialog -- a real browser confirm()
 *   is a separate, blocking, OS-level modal that no document-level keydown
 *   listener can fire during, so it cannot collide with the queue the way a
 *   custom dialog raised on top of another would. The one place a modal
 *   opens a second one -- Patient Detail's Book/New Invoice/New
 *   Prescription buttons -- closes Patient Detail SYNCHRONOUSLY in the same
 *   click handler (`ClinicSystem._openBookModal(...);
 *   ClinicSystem._closeModalOverlay(document.getElementById('cl-pt-detail-overlay'))`),
 *   before the event loop can ever deliver a keypress, so no Escape can land
 *   while both are simultaneously live. So: NOT stackable through any path a
 *   user can reach today. The queue is ported anyway (check 6 below proves
 *   it works) because wiring a *future* stacked dialog -- a custom confirm()
 *   replacing the native ones above, say -- is exactly what would otherwise
 *   INTRODUCE the bug, as it would have on the retail side (see that file's
 *   _modalKeyStack comment).
 *
 * ENTER-TO-CONFIRM ON A DESTRUCTIVE ACTION: not applicable here. Retail's
 * bug was a *custom* confirm dialog that copied the shell's Enter-guard
 * logic and dropped it. Clinic has no custom confirm dialog at all --
 * every destructive action (_deletePatient/archive, _deleteFollowup,
 * _cancelAppt, _deleteLabExpense) calls the native `confirm()`, whose
 * Enter/OK behaviour is the browser's own chrome, not this file's code.
 * There is no guard to have lost.
 *
 * MUTATION-PROVED, two mutants, both directions quoted in the assertions
 * below (check 6 and check 7):
 *   drop the top-of-queue guard from _wireModalA11y's onKeydown -> RED
 *   drop the prune loop from _topModalKeyLayer                  -> RED
 *
 * Run: node products/clinic/tests/clinic_modal_a11y_test.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const SRC_PATH = path.join(__dirname, '..', 'frontend', 'subsystem-clinic.js');
const SOURCE = fs.readFileSync(SRC_PATH, 'utf8');

// ═════════════════════════════════════════════════════════════════════════
// STRUCTURAL — every overlay wired, every dialog named and resolving,
// no close path bypasses the helpers.
// ═════════════════════════════════════════════════════════════════════════

// Anti-vacuity floor: there were exactly 8 `.cl-modal-overlay` build sites
// when this was written (add patient, patient detail, book, add doctor,
// invoice, payment, lab expense, prescription). A scan matching fewer than
// this has stopped matching how the file builds modals.
const KNOWN_OVERLAYS = 8;

function stripBlockComments(src) {
  return src.replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, ' '));
}

function codeLines(src) {
  return stripBlockComments(src)
    .split('\n')
    .map((line) => line.replace(/\/\/.*$/, ''));
}

function countAll(src, re) {
  return (src.match(re) || []).length;
}

function testEveryOverlayIsWired() {
  const code = codeLines(SOURCE).join('\n');
  const overlays = countAll(code, /className\s*=\s*'cl-modal-overlay'/g);
  const wired = countAll(code, /this\._wireModalA11y\(/g);

  assert.ok(overlays >= KNOWN_OVERLAYS,
    `Only ${overlays} modal-overlay build sites found (expected at least ${KNOWN_OVERLAYS}). ` +
    'The scan has stopped matching how this file builds modals.');

  assert.strictEqual(wired, overlays,
    `${overlays} modal overlays are built but only ${wired} call _wireModalA11y. ` +
    `${overlays - wired} modal(s) are unwired: no dialog role, no focus management, no ` +
    'Escape, closable only with a mouse.');

  console.log(`PASS: all ${overlays} modal overlays call _wireModalA11y`);
}

function testEveryDialogIsNamedAndTheNameResolves() {
  const code = codeLines(SOURCE).join('\n');
  const tags = code.match(/<div[^>]*role="dialog"[^>]*>/g) || [];
  assert.ok(tags.length >= KNOWN_OVERLAYS,
    `Only ${tags.length} dialog-role elements found; the scan is not matching the markup.`);

  const ids = new Set((code.match(/\bid="([^"$]+)"/g) || []).map((m) => m.slice(4, -1)));
  const unnamed = [];
  const dangling = [];
  const notModal = [];

  for (const tag of tags) {
    if (!/aria-modal="true"/.test(tag)) notModal.push(tag.slice(0, 110));
    const labelled = /aria-labelledby="([^"]+)"/.exec(tag);
    if (!labelled) { unnamed.push(tag.slice(0, 110)); continue; }
    if (!labelled[1].includes('${') && !ids.has(labelled[1])) {
      dangling.push(`${labelled[1]}  in  ${tag.slice(0, 90)}`);
    }
  }

  assert.deepStrictEqual(notModal, [],
    `${notModal.length} dialog(s) lack aria-modal="true":\n  ` + notModal.join('\n  '));
  assert.deepStrictEqual(unnamed, [],
    `${unnamed.length} dialog(s) have no aria-labelledby at all:\n  ` + unnamed.join('\n  '));
  assert.deepStrictEqual(dangling, [],
    `${dangling.length} dialog(s) point aria-labelledby at an id that does not exist -- ` +
    'worse than no name, because it reads as done and still announces nothing:\n  ' +
    dangling.join('\n  '));

  console.log(`PASS: all ${tags.length} dialogs carry aria-modal and a name whose target resolves`);
}

function testNoBareOverlayRemovalOutsideTheHelpers() {
  const lines = codeLines(SOURCE);
  const offenders = [];
  lines.forEach((line, i) => {
    if (/\.cl-modal-overlay'\)\s*\.remove\(\)/.test(line)
        || /getElementById\('cl-[A-Za-z-]*overlay'\)\??\.remove\(\)/.test(line)) {
      offenders.push(`${i + 1}: ${line.trim().slice(0, 110)}`);
    }
  });
  assert.deepStrictEqual(offenders, [],
    `${offenders.length} close path(s) remove a modal overlay directly instead of going ` +
    'through the wired close():\n  ' + offenders.join('\n  '));
  console.log('PASS: no close path removes a modal overlay behind the helpers\' back');
}

// ═════════════════════════════════════════════════════════════════════════
// BEHAVIOURAL — vm sandbox, real functions from the real source.
// ═════════════════════════════════════════════════════════════════════════

function makeStubElement(overrides) {
  const handlers = Object.create(null);
  return Object.assign({
    id: '', innerHTML: '', focusCount: 0, removeCount: 0,
    className: '',
    // Mirrors a real element's default; _topModalKeyLayer prunes only on an
    // explicit `false`, so a live stub must say so rather than leaving it
    // undefined.
    isConnected: true,
    addEventListener(type, fn) { (handlers[type] || (handlers[type] = [])).push(fn); },
    removeEventListener() {},
    fire(type, evt) { (handlers[type] || []).forEach((fn) => fn(evt || {})); },
    focus() { this.focusCount += 1; },
    remove() { this.removeCount += 1; this.isConnected = false; },
    setAttribute() {},
    getAttribute() { return null; },
    querySelector() { return null; },
    querySelectorAll() { return []; },
    closest(sel) {
      const cls = sel.startsWith('.') ? sel.slice(1) : sel;
      return (this.className || '').split(/\s+/).includes(cls) ? this : null;
    },
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
  }, overrides);
}

function loadClinicSystem(fetchImpl) {
  const els = Object.create(null);
  const trigger = makeStubElement({ id: 'trigger-btn' });
  const docHandlers = Object.create(null);
  const appended = [];
  // A real `document.getElementById('cl-pt-detail-overlay')` finds the exact
  // element `document.createElement` + `.id = ...` + `appendChild` put on
  // the page -- so this stub must too, or _openPatientDetail's own overlay
  // and a later getElementById lookup for its id would silently diverge
  // into two different objects. Top-level appended elements (the overlays
  // themselves) are checked first; anything else (dialog ids, field ids
  // that a caller never actually created via createElement) falls back to
  // the memoized map, same simplification retail's test harness makes.
  const getEl = (id) => {
    const found = appended.find((el) => el.id === id);
    if (found) return found;
    if (!els[id]) els[id] = makeStubElement({ id });
    return els[id];
  };

  const sandbox = {
    console,
    t: (s) => s,
    fetch: fetchImpl || (() => Promise.reject(new Error('unstubbed fetch'))),
    setTimeout, clearTimeout,
    localStorage: { getItem: () => null, setItem() {} },
    SubsystemApp: { showToast() {}, canClinic: () => true },
    document: {
      activeElement: trigger,
      getElementById: getEl,
      createElement: () => makeStubElement(),
      querySelector: () => null,
      querySelectorAll: () => [],
      addEventListener(type, fn, capture) {
        (docHandlers[type] || (docHandlers[type] = [])).push({ fn, capture });
      },
      removeEventListener(type, fn) {
        if (!docHandlers[type]) return;
        docHandlers[type] = docHandlers[type].filter((h) => h.fn !== fn);
      },
      body: { appendChild(el) { appended.push(el); } },
    },
  };
  sandbox.window = sandbox;

  vm.createContext(sandbox);
  vm.runInContext(SOURCE, sandbox, { filename: SRC_PATH });
  assert.ok(sandbox.ClinicSystem, 'ClinicSystem failed to load from subsystem-clinic.js');

  return {
    ClinicSystem: sandbox.ClinicSystem, els, trigger, appended,
    // The same lookup `document.getElementById(id)` performs inside the
    // sandbox -- exposed so a test can reproduce a real onclick's
    // `document.getElementById('some-id')` call exactly, instead of reaching
    // into `els`/`appended` directly.
    getById: getEl,
    // Every registered handler, exactly as one real dispatch would. Firing
    // only the newest would hide the sibling-listener problem the queue
    // exists to catch.
    fireDocKeydown(evt) {
      const e = Object.assign({ preventDefault() {}, stopPropagation() {} }, evt);
      (docHandlers.keydown || []).slice().forEach((h) => h.fn(e));
    },
    docKeydownCount() { return (docHandlers.keydown || []).length; },
  };
}

function openWiredModal(ctx, dialogId) {
  const overlay = makeStubElement({ id: dialogId + '-overlay', className: 'cl-modal-overlay' });
  const close = ctx.ClinicSystem._wireModalA11y(overlay, dialogId);
  return { overlay, close };
}

// ── 4. A single wired modal: focus in on open, Escape closes it, focus
//       returns to the trigger. Exercised through a REAL caller
//       (_openAddDoctor), not just the helper directly, so this also proves
//       a real modal-opening function wires correctly end to end. ──────────

function testARealModalOpensWithFocusAndClosesOnEscape() {
  const ctx = loadClinicSystem();
  ctx.ClinicSystem._openAddDoctor();

  assert.ok(ctx.appended.length >= 1, '_openAddDoctor did not append an overlay to document.body');
  assert.strictEqual(ctx.docKeydownCount(), 1,
    '_openAddDoctor did not register a document keydown handler via _wireModalA11y');

  // Only 3 of the 8 modals call focus() on a specific field; _openAddDoctor
  // is one of them (cl-doc-name). _wireModalA11y focuses the dialog
  // container FIRST and the caller's field focus() runs after, overriding
  // it -- same order as retail's _wireModalA11y callers.
  const dialogEl = ctx.els['cl-add-doctor-dialog'];
  const fieldEl = ctx.els['cl-doc-name'];
  assert.ok(dialogEl && dialogEl.focusCount >= 1, 'the dialog container was never focused on open');
  assert.ok(fieldEl && fieldEl.focusCount >= 1, 'the first field (cl-doc-name) was never focused on open');

  ctx.fireDocKeydown({ key: 'Escape' });
  const overlay = ctx.appended[ctx.appended.length - 1];
  assert.strictEqual(overlay.removeCount, 1, 'Escape did not close the modal _openAddDoctor built');
  assert.ok(ctx.trigger.focusCount >= 1, 'focus was never returned to the trigger after Escape');
  assert.strictEqual(ctx.docKeydownCount(), 0, 'the keydown listener was not torn down on close');

  console.log('PASS: a real modal (_openAddDoctor) focuses on open and closes on Escape, returning focus');
}

// ── 5. _closeModalOverlay routes through the wired close(), matching the
//       Patient Detail transition buttons' own call shape:
//       `ClinicSystem._closeModalOverlay(document.getElementById(overlayId))`.

function testCloseModalOverlayRoutesThroughTheWiredClose() {
  const ctx = loadClinicSystem();
  const modal = openWiredModal(ctx, 'cl-book-dialog');
  ctx.ClinicSystem._closeModalOverlay(modal.overlay);
  assert.strictEqual(modal.overlay.removeCount, 1,
    '_closeModalOverlay did not remove the overlay');
  assert.strictEqual(ctx.docKeydownCount(), 0,
    '_closeModalOverlay removed the overlay but left the document keydown listener behind -- ' +
    'exactly the leak a bare .remove() would cause');
  console.log('PASS: _closeModalOverlay tears down the same listener Escape would have');
}

// ── 6. THE QUEUE: two wired dialogs, only the front answers Escape.
//       [MUTATION-PROVED] ──────────────────────────────────────────────────
//
// Clinic cannot reach this through today's UI (see the file header), but the
// queue exists so that WHEN a future dialog stacks -- a custom confirm()
// replacing the native ones, say -- wiring it is safe rather than the thing
// that introduces the bug. This proves the ported mechanism itself works,
// using two real dialog ids from this file.

function testOnlyTheFrontDialogAnswersEscape() {
  const ctx = loadClinicSystem();
  const under = openWiredModal(ctx, 'cl-patient-detail-dialog');
  assert.strictEqual(ctx.docKeydownCount(), 1, 'the first modal did not register a keydown handler');

  const onTop = openWiredModal(ctx, 'cl-book-dialog');
  assert.strictEqual(ctx.docKeydownCount(), 2, 'the second modal did not register its own keydown handler');

  ctx.fireDocKeydown({ key: 'Escape' });
  assert.strictEqual(onTop.overlay.removeCount, 1, 'the dialog on top did not close on its own Escape');
  assert.strictEqual(under.overlay.removeCount, 0,
    'ONE Escape closed BOTH dialogs: the one on top AND the one underneath it. Both handlers ' +
    'are siblings on the same document node, so stopPropagation on one does not stop the ' +
    'other -- only the _topModalKeyLayer() guard can.');

  ctx.fireDocKeydown({ key: 'Escape' });
  assert.strictEqual(under.overlay.removeCount, 1,
    'after the top dialog was dismissed, a second Escape did not close the one underneath -- ' +
    'a queue that swallows everything after the first Escape is as broken as one with no guard.');

  console.log('PASS: one Escape closes the top dialog only; the next closes the one beneath');
}

function testOnlyTheFrontDialogAnswersEscapeIsMutationProved() {
  const anchor = 'if (this._topModalKeyLayer() !== layer) return;';
  const hits = SOURCE.split(anchor).length - 1;
  assert.strictEqual(hits, 1,
    `Mutation anchor occurs ${hits} time(s), expected exactly 1: ${JSON.stringify(anchor)}`);
  const mutated = SOURCE.split(anchor).join('/* guard removed by mutation */');

  const sandbox = { console, setTimeout, clearTimeout, localStorage: { getItem: () => null, setItem() {} } };
  sandbox.window = sandbox;
  const docHandlers = Object.create(null);
  sandbox.document = {
    activeElement: makeStubElement({ id: 'trigger' }),
    getElementById: (id) => makeStubElement({ id }),
    createElement: () => makeStubElement(),
    addEventListener(type, fn) { (docHandlers[type] || (docHandlers[type] = [])).push(fn); },
    removeEventListener(type, fn) {
      if (!docHandlers[type]) return;
      docHandlers[type] = docHandlers[type].filter((f) => f !== fn);
    },
    body: { appendChild() {} },
  };
  vm.createContext(sandbox);
  vm.runInContext(mutated, sandbox, { filename: SRC_PATH + ' (mutated: no top-of-queue guard)' });

  const under = makeStubElement({ id: 'under-overlay', className: 'cl-modal-overlay' });
  sandbox.ClinicSystem._wireModalA11y(under, 'cl-patient-detail-dialog');
  const onTop = makeStubElement({ id: 'top-overlay', className: 'cl-modal-overlay' });
  sandbox.ClinicSystem._wireModalA11y(onTop, 'cl-book-dialog');

  const e = { preventDefault() {}, stopPropagation() {} };
  (docHandlers.keydown || []).slice().forEach((fn) => fn(Object.assign({ key: 'Escape' }, e)));

  let threw = null;
  try {
    assert.strictEqual(under.removeCount, 0,
      'expected the mutant (no top-of-queue guard) to close BOTH dialogs on one Escape');
  } catch (err) { threw = err; }

  assert.ok(threw === null ? false : true,
    'MUTATION SURVIVED -- removing the top-of-queue guard from _wireModalA11y should have ' +
    'let one Escape close both dialogs, but the dialog underneath stayed open anyway. The ' +
    'check is not actually watching for the sibling-listener bug.');
  assert.strictEqual(under.removeCount, 1,
    'sanity: the mutant should still close the UNDERNEATH dialog too (that is the bug) -- ' +
    `got removeCount=${under.removeCount}`);

  console.log(`PASS: check 6 is mutation-proved  [RED without the guard: ${String(threw.message).slice(0, 90)}...]`);
  console.log('      RESTORED: the real source (with the guard) passes check 6 above  [GREEN]');
}

// ── 7. A layer left behind by a bypassed close() does not wedge the queue.
//       [MUTATION-PROVED] ──────────────────────────────────────────────────

function testALeakedLayerDoesNotSwallowLaterEscapes() {
  const ctx = loadClinicSystem();
  // ORDER IS THE WHOLE TEST: the abandoned dialog must be at the FRONT of
  // the queue when Escape arrives -- the only position a leaked layer can
  // do harm from, and the only position the prune inspects.
  const live = openWiredModal(ctx, 'cl-add-patient-dialog');
  const abandoned = openWiredModal(ctx, 'cl-lab-expense-dialog');
  abandoned.overlay.remove();           // bypasses close() entirely

  ctx.fireDocKeydown({ key: 'Escape' });
  assert.strictEqual(live.overlay.removeCount, 1,
    'a dialog removed without running its close() stayed at the front of the queue and ' +
    'swallowed the Escape meant for the dialog actually on screen');
  console.log('PASS: a layer left behind by a bypassed close() does not wedge the queue');
}

function testLeakedLayerPruneIsMutationProved() {
  const anchor = "while (stack.length && stack[stack.length - 1].overlay\n           && stack[stack.length - 1].overlay.isConnected === false) stack.pop();";
  const hits = SOURCE.split(anchor).length - 1;
  assert.strictEqual(hits, 1,
    `Mutation anchor occurs ${hits} time(s), expected exactly 1 -- the anchor text has drifted ` +
    'from the real source, which would make this proof measure nothing.');
  const mutated = SOURCE.split(anchor).join('/* prune removed by mutation */');

  const sandbox = { console, setTimeout, clearTimeout, localStorage: { getItem: () => null, setItem() {} } };
  sandbox.window = sandbox;
  const docHandlers = Object.create(null);
  sandbox.document = {
    activeElement: makeStubElement({ id: 'trigger' }),
    getElementById: (id) => makeStubElement({ id }),
    createElement: () => makeStubElement(),
    addEventListener(type, fn) { (docHandlers[type] || (docHandlers[type] = [])).push(fn); },
    removeEventListener(type, fn) {
      if (!docHandlers[type]) return;
      docHandlers[type] = docHandlers[type].filter((f) => f !== fn);
    },
    body: { appendChild() {} },
  };
  vm.createContext(sandbox);
  vm.runInContext(mutated, sandbox, { filename: SRC_PATH + ' (mutated: no prune)' });

  const live = makeStubElement({ id: 'live-overlay', className: 'cl-modal-overlay' });
  sandbox.ClinicSystem._wireModalA11y(live, 'cl-add-patient-dialog');
  const abandoned = makeStubElement({ id: 'abandoned-overlay', className: 'cl-modal-overlay' });
  sandbox.ClinicSystem._wireModalA11y(abandoned, 'cl-lab-expense-dialog');
  abandoned.remove();

  const e = { preventDefault() {}, stopPropagation() {} };
  (docHandlers.keydown || []).slice().forEach((fn) => fn(Object.assign({ key: 'Escape' }, e)));

  let threw = null;
  try {
    assert.strictEqual(live.removeCount, 1,
      'expected the mutant (no prune) to leave the live dialog unresponsive to Escape');
  } catch (err) { threw = err; }

  assert.ok(threw !== null,
    'MUTATION SURVIVED -- removing the prune loop from _topModalKeyLayer should have left the ' +
    'leaked layer swallowing Escape, but the live dialog closed anyway. The check does not ' +
    'actually depend on pruning.');
  assert.strictEqual(live.removeCount, 0,
    `sanity: without the prune the live dialog should stay OPEN (the bug) -- got removeCount=${live.removeCount}`);

  console.log(`PASS: check 7 is mutation-proved  [RED without the prune: ${String(threw.message).slice(0, 90)}...]`);
  console.log('      RESTORED: the real source (with the prune) passes check 7 above  [GREEN]');
}

// ── 8. The one real near-miss: Patient Detail -> Book Appointment. Drives
//       the REAL _openPatientDetail and _openBookModal, in the exact
//       two-statement sequence the onclick markup uses
//       (`ClinicSystem._openBookModal(...);
//         ClinicSystem._closeModalOverlay(document.getElementById('cl-pt-detail-overlay'))`),
//       and proves the detail dialog's layer is dropped rather than left in
//       front of the book dialog's own Escape handling. ────────────────────

async function testPatientDetailToBookTransitionDropsItsLayerCleanly() {
  const fetchImpl = (url) => {
    if (String(url).includes('/patients/')) {
      return Promise.resolve({
        status: 200, ok: true,
        json: () => Promise.resolve({ data: {
          patient: { id: 9, name: 'Jane Doe', patient_code: 'P-0009', status: 'active' },
          visits: [], appointments: [],
        } }),
      });
    }
    return Promise.reject(new Error('unstubbed fetch: ' + url));
  };
  const ctx = loadClinicSystem(fetchImpl);
  // In real use _patients is already populated by the time Patient Detail's
  // transition buttons are clickable (the Patients screen loads it first).
  // Priming it here keeps _openBookModal's overlay build synchronous,
  // matching the real timing this test exists to prove.
  ctx.ClinicSystem._patients = [{ id: 9, name: 'Jane Doe', patient_code: 'P-0009' }];

  await ctx.ClinicSystem._openPatientDetail(9);
  assert.strictEqual(ctx.docKeydownCount(), 1, 'Patient Detail did not register its keydown handler');
  const detailOverlay = ctx.appended[ctx.appended.length - 1];

  // The exact two statements the real onclick markup runs, in the real
  // order -- including going back through getElementById by id (as the
  // markup's own `document.getElementById('cl-pt-detail-overlay')` does)
  // rather than reusing the `detailOverlay` reference directly.
  ctx.ClinicSystem._openBookModal(9, 'Jane Doe');
  ctx.ClinicSystem._closeModalOverlay(ctx.getById('cl-pt-detail-overlay'));

  assert.strictEqual(detailOverlay.removeCount, 1,
    'the Patient Detail overlay was not removed by the transition');
  assert.strictEqual(ctx.docKeydownCount(), 1,
    'after the hand-off exactly one dialog (Book Appointment) should hold the keydown ' +
    'listener -- Patient Detail\'s was either never torn down (leak) or Book\'s never ' +
    'registered (broken wiring)');

  const bookOverlay = ctx.appended[ctx.appended.length - 1];
  ctx.fireDocKeydown({ key: 'Escape' });
  assert.strictEqual(bookOverlay.removeCount, 1,
    'Escape did not close the Book Appointment dialog after the Patient Detail hand-off -- ' +
    'a leaked front-of-queue layer from Patient Detail would produce exactly this symptom');

  console.log('PASS: the Patient Detail -> Book Appointment hand-off drops its layer cleanly');
}

// ═════════════════════════════════════════════════════════════════════════

async function main() {
  const checks = [
    ['every overlay is wired', testEveryOverlayIsWired],
    ['every dialog is named and the name resolves', testEveryDialogIsNamedAndTheNameResolves],
    ['no bare overlay removal outside the helpers', testNoBareOverlayRemovalOutsideTheHelpers],
    ['a real modal focuses on open and closes on Escape', testARealModalOpensWithFocusAndClosesOnEscape],
    ['_closeModalOverlay routes through the wired close()', testCloseModalOverlayRoutesThroughTheWiredClose],
    ['only the front dialog answers Escape', testOnlyTheFrontDialogAnswersEscape],
    ['check 6 is mutation-proved', testOnlyTheFrontDialogAnswersEscapeIsMutationProved],
    ['a leaked layer does not swallow later Escapes', testALeakedLayerDoesNotSwallowLaterEscapes],
    ['check 7 is mutation-proved', testLeakedLayerPruneIsMutationProved],
    ['Patient Detail -> Book Appointment drops its layer cleanly', testPatientDetailToBookTransitionDropsItsLayerCleanly],
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
    console.error(`\nFAIL: clinic_modal_a11y_test.js — ${failures.length} of ${checks.length} checks failed:`);
    for (const name of failures) console.error(`  - ${name}`);
    process.exitCode = 1;
    return;
  }
  console.log(`PASS: clinic_modal_a11y_test.js — ${checks.length} checks`);
}

main().catch((err) => {
  console.error('FAIL: clinic_modal_a11y_test.js (runner)');
  console.error(err.stack || err);
  process.exitCode = 1;
});
