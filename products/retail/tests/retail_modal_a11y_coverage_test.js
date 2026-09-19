/**
 * EVERY MODAL IS WIRED — the coverage ratchet.
 *
 * WHY THIS TEST EXISTS
 * On 2026-09-19 the 27 remaining hand-rolled modals in subsystem-retail.js
 * were given dialog semantics and keyboard behaviour. The agent that did the
 * work mutation-proved its own result and reported, correctly and honestly,
 * that **nothing went red**: it removed a `_wireModalA11y` call from one modal
 * and turned a close path back into a bare `.remove()` on another, re-ran all
 * fourteen suites the change touches, and every one still passed with
 * byte-identical output.
 *
 * That is the finding this file answers. Fourteen green suites were not
 * evidence about this change at all — they verify XSS escaping, i18n
 * coverage, contrast, focus rings, reload races. None of them asks whether a
 * modal is wired. So the work shipped with no coverage, and the NEXT modal
 * someone adds would ship unwired with everything still green. That is the
 * exact shape ENGINEERING.md warns about: a passing test is not evidence.
 *
 * WHAT IT PINS, AND WHY THIS SHAPE
 * A derived rule over the file's own structure, not a list of 31 known
 * modals. A frozen list goes stale on the next commit and has to be curated
 * by hand forever; a derived rule puts modal #32 under test the day it is
 * written.
 *
 *   1. COUNT PARITY. Every site that builds a `.ret-modal-overlay` must be
 *      accounted for by a `_wireModalA11y` call, plus exactly one exemption:
 *      `_confirm`, which predates the helpers and carries its own equivalent
 *      implementation (its own Escape handling, its own focus restore, and
 *      the `role="alertdialog"` that makes it findable here). Add a modal
 *      without wiring it and the two counts diverge.
 *
 *   2. EVERY DIALOG IS NAMED, AND THE NAME RESOLVES. `role="dialog"` with an
 *      `aria-labelledby` pointing at an id that does not exist is worse than
 *      no name at all: it reads as done, and a screen reader announces an
 *      unnamed dialog anyway. Same failure the form-label sweep found with
 *      dangling `for=`.
 *
 *   3. NO DIALOG IS MISSING `aria-modal`. Without it assistive tech keeps
 *      offering the whole page behind the dialog, so a user can "leave" a
 *      modal that is visually blocking and act on controls that are not
 *      really available.
 *
 *   4. NO BARE OVERLAY REMOVAL OUTSIDE THE HELPERS. `_wireModalA11y`
 *      registers a document-level keydown listener and a queue entry
 *      (see _modalKeyStack); a bare `.remove()` drops the element and leaves
 *      both behind. Exactly three `overlay.remove()` sites are legitimate —
 *      inside `_confirm`, inside `_wireModalA11y`'s own close(), and the
 *      untracked-overlay fallback in `_closeModalOverlay`.
 *
 * WHAT IT DELIBERATELY DOES NOT CLAIM
 * This is a STRUCTURAL check. It proves each modal is wired, not that the
 * wiring behaves. Behaviour is retail_modal_stack_test.js's job, which drives
 * the real helpers through a stubbed DOM and is mutation-proved three ways.
 * The two together are the argument; either alone is half of it.
 *
 * No test framework is configured for this vanilla-JS, build-step-free
 * frontend (see CLAUDE.md), so this runs standalone on Node built-ins:
 *
 *   node products/retail/tests/retail_modal_a11y_coverage_test.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');

const SRC = path.join(__dirname, '..', 'frontend', 'subsystem-retail.js');

/* Anti-vacuity floor. The checks below are regexes over source text, so the
   failure that matters is matching NOTHING and sweeping an empty set. There
   were 31 overlay-building sites when this was written. */
const MIN_OVERLAYS = 25;

/* _confirm builds its own overlay and does its own Escape/focus handling
   rather than calling _wireModalA11y. It is the ORIGINAL reference
   implementation, it is driven and mutation-proved end to end by
   retail_confirm_modal_test.js, and it participates in the shared modal key
   queue. One exemption, named, with a reason -- not an allowlist that can
   grow. */
const SELF_WIRED_OVERLAYS = 1;

function read() {
  return fs.readFileSync(SRC, 'utf8');
}

/* Comments in this file quote the patterns below while explaining them, so
   scanning raw text would count the documentation as code. */
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
  const code = codeLines(read()).join('\n');
  const overlays = countAll(code, /className\s*=\s*'ret-modal-overlay'/g);
  const wired = countAll(code, /this\._wireModalA11y\(/g);

  assert.ok(
    overlays >= MIN_OVERLAYS,
    `Only ${overlays} modal-overlay build sites were found (there were 31 when this ` +
    `was written, floor ${MIN_OVERLAYS}). The scan has stopped matching how this file ` +
    'builds modals, so a green result here would be measuring an empty set.'
  );

  assert.strictEqual(
    wired + SELF_WIRED_OVERLAYS, overlays,
    `${overlays} modal overlays are built in this file but only ${wired} call ` +
    `_wireModalA11y (+${SELF_WIRED_OVERLAYS} self-wired: _confirm). ` +
    `${overlays - wired - SELF_WIRED_OVERLAYS} modal(s) are unwired: no dialog role, ` +
    'no focus management, no Escape, closable only with a mouse. Wire it with ' +
    '_wireModalA11y(overlay, \'<its-dialog-id>\') after the overlay is appended, and ' +
    'route every close path through the close() it returns.'
  );

  console.log(`PASS: all ${overlays} modal overlays are wired (${wired} via _wireModalA11y, ${SELF_WIRED_OVERLAYS} self-wired)`);
}

function testEveryDialogIsNamedAndTheNameResolves() {
  const src = read();
  const code = codeLines(src).join('\n');

  // Every element carrying role="dialog" or role="alertdialog", with its tag.
  const tags = code.match(/<div[^>]*role="(?:alert)?dialog"[^>]*>/g) || [];
  assert.ok(tags.length >= MIN_OVERLAYS,
    `Only ${tags.length} dialog-role elements found; the scan is not matching the markup.`);

  const ids = new Set((code.match(/\bid="([^"$]+)"/g) || []).map((m) => m.slice(4, -1)));

  const unnamed = [];
  const dangling = [];
  const notModal = [];

  for (const tag of tags) {
    if (!/aria-modal="true"/.test(tag)) notModal.push(tag.slice(0, 110));
    const labelled = /aria-labelledby="([^"]+)"/.exec(tag);
    const label = /aria-label="([^"]+)"/.exec(tag);
    if (!labelled && !label) { unnamed.push(tag.slice(0, 110)); continue; }
    if (labelled && !labelled[1].includes('${') && !ids.has(labelled[1])) {
      dangling.push(`${labelled[1]}  in  ${tag.slice(0, 90)}`);
    }
  }

  assert.deepStrictEqual(notModal, [],
    `${notModal.length} dialog(s) lack aria-modal="true". Without it, assistive tech ` +
    'keeps offering the page behind the dialog, so the user can act on controls that ' +
    'are visually blocked:\n  ' + notModal.join('\n  '));

  assert.deepStrictEqual(unnamed, [],
    `${unnamed.length} dialog(s) have no accessible name at all — announced as just ` +
    '"dialog":\n  ' + unnamed.join('\n  '));

  assert.deepStrictEqual(dangling, [],
    `${dangling.length} dialog(s) point aria-labelledby at an id that does not exist ` +
    'in this file. That is worse than no name: it reads as done and still announces ' +
    'nothing.\n  ' + dangling.join('\n  '));

  console.log(`PASS: all ${tags.length} dialogs carry aria-modal and a name whose target exists`);
}

function testNoBareOverlayRemovalOutsideTheHelpers() {
  const lines = codeLines(read());
  const offenders = [];

  lines.forEach((line, i) => {
    if (/\.ret-modal-overlay'\)\s*\.remove\(\)/.test(line)
        || /getElementById\('ret-[A-Za-z-]*modal'\)\??\.remove\(\)/.test(line)) {
      offenders.push(`${i + 1}: ${line.trim().slice(0, 110)}`);
    }
  });

  assert.deepStrictEqual(offenders, [],
    `${offenders.length} close path(s) remove a modal overlay directly instead of going ` +
    'through the wired close():\n  ' + offenders.join('\n  ') +
    '\n\n_wireModalA11y registers a document-level keydown listener and a modal-queue ' +
    'entry. A bare .remove() takes the element out of the page and leaves both behind — ' +
    'the listener leaks, and the queue entry sits in front of the next dialog and ' +
    'swallows its Escape. Use RetailSystem._closeModalOverlay(this) from inline markup, ' +
    'or the close() returned by _wireModalA11y from code.');

  console.log('PASS: no close path removes a modal overlay behind the helpers\' back');
}

function main() {
  const checks = [
    ['every overlay is wired', testEveryOverlayIsWired],
    ['every dialog is named and the name resolves', testEveryDialogIsNamedAndTheNameResolves],
    ['no bare overlay removal outside the helpers', testNoBareOverlayRemovalOutsideTheHelpers],
  ];
  const failures = [];
  for (const [name, fn] of checks) {
    try {
      fn();
    } catch (err) {
      failures.push(name);
      console.error(`FAIL: ${name}`);
      console.error('      ' + String((err && err.message) || err).replace(/\n/g, '\n      '));
    }
  }
  if (failures.length) {
    console.error(`\nFAIL: retail_modal_a11y_coverage_test.js — ${failures.length} of ${checks.length} checks failed`);
    process.exitCode = 1;
    return;
  }
  console.log(`PASS: retail_modal_a11y_coverage_test.js — ${checks.length} checks`);
}

main();
