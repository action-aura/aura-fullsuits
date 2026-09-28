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
 * WIDENED 2026-09-27 (Phase 0 item #4). This file itself was the SAME bug at
 * one remove: it parsed only subsystem-retail.js, so the five hand-rolled
 * overlays in cash-drawer.js and the two in import-wizard.js were invisible
 * to it — the "31 of 31 wired" CLAUDE.md recorded on 2026-09-20 was true of
 * one file and silently false of the product. It now reads every top-level
 * `frontend/*.js` file (derived from the directory listing, not a hand-typed
 * list — `frontend/lib/**` is excluded because it is vendored, not this
 * product's own code) and checks each file's own count independently, so a
 * mismatch in one file cannot be masked by slack in another's.
 *
 * That widening immediately found a SIXTH pre-existing unwired overlay this
 * item's own scope does not cover: employees.js's shared `_modal(id, ...)`
 * helper builds a `.ret-modal-overlay` and closes it with a bare
 * `document.getElementById(elementId)?.remove()`. employees.js is owned by a
 * different crew in this phase (file-ownership table, contracts.md) and is
 * out of scope for this item to fix — left failing here, on purpose, rather
 * than exempted, because exempting it would be exactly the "loosen the test
 * to get green" ENGINEERING.md forbids. See the Phase 0 C5 report's
 * out_of_scope_requests for the handoff.
 *
 * The SAME widening also made check 2 below (every dialog is named and the
 * name resolves) scan role="dialog"/role="alertdialog" elements in EVERY
 * frontend/*.js file, not just the ones that build a `.ret-modal-overlay` --
 * and that surfaced a SEVENTH gap outside this item's scope: sub-ai.js's AI
 * Assistant panel already carries `role="dialog"` and `aria-label`, but no
 * `aria-modal="true"`. It is a different SHAPE of overlay than the other six
 * (a persistent element toggled `.hidden`/un-hidden, like the mobile POS cart
 * bottom-sheet CLAUDE.md already says "needs its own answer" -- never
 * `createElement`'d or `.remove()`'d, so it is invisible to check 1's
 * `.ret-modal-overlay` count and to `_wireModalA11y` the way that helper is
 * shaped today), which is why count-parity above never named it. sub-ai.js is
 * owned by a different crew (file-ownership table, contracts.md); same
 * handling as employees.js above: left failing on purpose rather than
 * exempted, handed off in the Phase 0 C5 report's out_of_scope_requests.
 *
 * WHAT IT PINS, AND WHY THIS SHAPE
 * A derived rule over each file's own structure, not a list of known modals.
 * A frozen list goes stale on the next commit and has to be curated by hand
 * forever; a derived rule puts modal #N under test the day it is written, in
 * whichever file it was written in.
 *
 *   1. COUNT PARITY, PER FILE. Every site in a file that builds a
 *      `.ret-modal-overlay` must be accounted for by a call of the shape
 *      `<receiver>._wireModalA11y(`, plus exactly one exemption:
 *      subsystem-retail.js's `_confirm`, which predates the helpers and
 *      carries its own equivalent implementation (its own Escape handling,
 *      its own focus restore, and the `role="alertdialog"` that makes it
 *      findable here). Add a modal without wiring it and that file's two
 *      counts diverge.
 *
 *   2. EVERY DIALOG IS NAMED, AND THE NAME RESOLVES, per file. `role="dialog"`
 *      with an `aria-labelledby` pointing at an id that does not exist in
 *      that same file is worse than no name at all: it reads as done, and a
 *      screen reader announces an unnamed dialog anyway. Same failure the
 *      form-label sweep found with dangling `for=`.
 *
 *   3. NO DIALOG IS MISSING `aria-modal`. Without it assistive tech keeps
 *      offering the whole page behind the dialog, so a user can "leave" a
 *      modal that is visually blocking and act on controls that are not
 *      really available.
 *
 *   4. NO BARE OVERLAY REMOVAL OUTSIDE THE HELPERS. `_wireModalA11y`
 *      registers a document-level keydown listener and a queue entry
 *      (see _modalKeyStack); a bare `.remove()` drops the element and leaves
 *      both behind. The ids checked are derived per file from that file's OWN
 *      `overlay.id = '...'` literal sitting next to its own
 *      `overlay.className = 'ret-modal-overlay'` assignment — not a generic
 *      "modal"/"overlay" substring — so this cannot false-positive against an
 *      unrelated overlay convention such as app-shell.js's `auth-overlay` /
 *      `sub-confirm-overlay`, which were never part of this ratchet's scope
 *      (see CLAUDE.md's note on the mobile POS cart sheet needing "its own
 *      answer" for the same reason). A dynamically-assigned id
 *      (`overlay.id = id`, employees.js's shape) cannot be named this way and
 *      is outside what this specific check can see — check 1 above still
 *      catches that file's gap by count alone.
 *
 * WHAT IT DELIBERATELY DOES NOT CLAIM
 * This is a STRUCTURAL check. It proves each modal is wired, not that the
 * wiring behaves. Behaviour is retail_modal_stack_test.js's job (subsystem
 * modals + _confirm) and retail_p0_modals_test.js's job (cash-drawer.js +
 * import-wizard.js), both of which drive the real helpers through a stubbed
 * DOM and are mutation-proved. Structure and behaviour together are the
 * argument; either alone is half of it.
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

const FRONTEND_DIR = path.join(__dirname, '..', 'frontend');

/* Every top-level frontend/*.js file, exactly what the glob `frontend/*.js`
   names -- NOT frontend/lib/** (vendored, e.g. chart.umd.min.js), which
   fs.readdirSync with no recursion already excludes on its own. */
function frontendFiles() {
  return fs.readdirSync(FRONTEND_DIR)
    .filter((f) => f.endsWith('.js'))
    .sort();
}

/* Anti-vacuity floor. The checks below are regexes over source text, so the
   failure that matters is matching NOTHING and sweeping an empty set. There
   were 32 in subsystem-retail.js + 5 in cash-drawer.js + 2 in
   import-wizard.js + 1 (unwired) in employees.js = 40 when this was widened;
   the floor sits comfortably below that so the check only trips if the scan
   itself stops matching how the product builds modals. */
const MIN_OVERLAYS_TOTAL = 35;

/* Per file, how many of its own `.ret-modal-overlay` builders are exempt from
   calling _wireModalA11y because they carry their own equivalent
   implementation. subsystem-retail.js's _confirm is the ONLY one in the
   product -- see its own comment in that file. Every other file's overlays
   are expected to be fully wired. */
const SELF_WIRED_OVERLAYS = { 'subsystem-retail.js': 1 };

/* Comments in these files quote the patterns below while explaining them, so
   scanning raw text would count the documentation as code. */
function stripBlockComments(src) {
  return src.replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, ' '));
}

function codeLines(src) {
  return stripBlockComments(src)
    .split('\n')
    .map((line) => line.replace(/\/\/.*$/, ''));
}

function readCode(file) {
  return codeLines(fs.readFileSync(path.join(FRONTEND_DIR, file), 'utf8'));
}

function countAll(src, re) {
  return (src.match(re) || []).length;
}

/* Matches `className = 'ret-modal-overlay'` (the sole class, subsystem-
   retail.js's/cash-drawer.js's/employees.js's shape) AND `className =
   'iw-overlay ret-modal-overlay'` (import-wizard.js's shape, which keeps its
   own presentational class alongside the shared dialog one). The word
   boundaries stop this matching a class that merely CONTAINS the substring
   (there is no such class in this product; this is defensive, not observed).
   An earlier version of this regex required an exact single-class string and
   silently counted import-wizard.js as zero overlays -- a mutation that
   removed its _wireModalA11y call went undetected because it never entered
   the mismatch check at all. Caught by mutation-proving this file itself
   (see the C5 Phase 0 report) before it ever ran green. */
const RET_MODAL_OVERLAY_CLASS_RE = /className\s*=\s*'[^']*\bret-modal-overlay\b[^']*'/g;

function testEveryOverlayIsWired() {
  const files = frontendFiles();
  let totalOverlays = 0;
  const mismatches = [];

  for (const file of files) {
    const code = readCode(file).join('\n');
    const overlays = countAll(code, RET_MODAL_OVERLAY_CLASS_RE);
    if (overlays === 0) continue;
    totalOverlays += overlays;
    // Any receiver: subsystem-retail.js's own methods call `this._wireModalA11y(`
    // (this === RetailSystem there); cash-drawer.js and import-wizard.js are
    // separate objects and call `RetailSystem._wireModalA11y(` explicitly.
    const wired = countAll(code, /\._wireModalA11y\(/g);
    const exempt = SELF_WIRED_OVERLAYS[file] || 0;
    if (wired + exempt !== overlays) {
      mismatches.push(
        `${file}: ${overlays} modal overlay(s) built, ${wired} call _wireModalA11y` +
        (exempt ? ` (+${exempt} self-wired)` : '') +
        ` — ${overlays - wired - exempt} unwired: no dialog role, no focus management, ` +
        'no Escape, closable only with a mouse. Wire it with ' +
        "_wireModalA11y(overlay, '<its-dialog-id>') after the overlay is appended, and " +
        'route every close path through the close() it returns.'
      );
    }
  }

  assert.ok(
    totalOverlays >= MIN_OVERLAYS_TOTAL,
    `Only ${totalOverlays} modal-overlay build sites were found across ${files.length} ` +
    `frontend/*.js files (floor ${MIN_OVERLAYS_TOTAL}). The scan has stopped matching how ` +
    'this product builds modals, so a green result here would be measuring an empty set.'
  );

  assert.deepStrictEqual(mismatches, [],
    `${mismatches.length} file(s) have unwired modal overlay(s):\n  ` + mismatches.join('\n  '));

  console.log(`PASS: all ${totalOverlays} modal overlays across ${files.length} frontend/*.js files are wired`);
}

function testEveryDialogIsNamedAndTheNameResolves() {
  const files = frontendFiles();
  let totalTags = 0;
  const notModal = [];
  const unnamed = [];
  const dangling = [];

  for (const file of files) {
    const code = readCode(file).join('\n');
    // Every element carrying role="dialog" or role="alertdialog", with its tag.
    const tags = code.match(/<div[^>]*role="(?:alert)?dialog"[^>]*>/g) || [];
    if (!tags.length) continue;
    totalTags += tags.length;

    const ids = new Set((code.match(/\bid="([^"$]+)"/g) || []).map((m) => m.slice(4, -1)));

    for (const tag of tags) {
      const label = `${file}: ${tag.slice(0, 110)}`;
      if (!/aria-modal="true"/.test(tag)) notModal.push(label);
      const labelledby = /aria-labelledby="([^"]+)"/.exec(tag);
      const ariaLabel = /aria-label="([^"]+)"/.exec(tag);
      if (!labelledby && !ariaLabel) { unnamed.push(label); continue; }
      if (labelledby && !labelledby[1].includes('${') && !ids.has(labelledby[1])) {
        dangling.push(`${file}: ${labelledby[1]}  in  ${tag.slice(0, 90)}`);
      }
    }
  }

  assert.ok(totalTags >= MIN_OVERLAYS_TOTAL,
    `Only ${totalTags} dialog-role elements found across ${files.length} frontend/*.js ` +
    'files; the scan is not matching the markup.');

  assert.deepStrictEqual(notModal, [],
    `${notModal.length} dialog(s) lack aria-modal="true". Without it, assistive tech ` +
    'keeps offering the page behind the dialog, so the user can act on controls that ' +
    'are visually blocked:\n  ' + notModal.join('\n  '));

  assert.deepStrictEqual(unnamed, [],
    `${unnamed.length} dialog(s) have no accessible name at all — announced as just ` +
    '"dialog":\n  ' + unnamed.join('\n  '));

  assert.deepStrictEqual(dangling, [],
    `${dangling.length} dialog(s) point aria-labelledby at an id that does not exist ` +
    'in that same file. That is worse than no name: it reads as done and still ' +
    'announces nothing.\n  ' + dangling.join('\n  '));

  console.log(`PASS: all ${totalTags} dialogs across ${files.length} frontend/*.js files carry aria-modal and a name whose target exists`);
}

/* Every `overlay.id = 'LITERAL'` sitting within reach of that SAME file's own
   `overlay.className = 'ret-modal-overlay'` assignment (order varies; the
   window is generous because some builders set id first, others className
   first, with a few lines of unrelated code between them in a couple of
   files). Deliberately keyed off the actual convention this ratchet counts,
   not a generic "modal"/"overlay" name guess, so a totally different overlay
   convention elsewhere in the same file (app-shell.js's `auth-overlay` /
   `sub-confirm-overlay`) can never be swept in here by coincidence of
   spelling. A dynamically-assigned id (`overlay.id = id`, a variable, not a
   string literal — employees.js's `_modal(id, ...)` helper) yields no literal
   and is simply invisible to this one check; count-parity above still finds
   that file's gap. */
function retModalOverlayIds(code) {
  const ids = new Set();
  // The `\bret-modal-overlay\b` class token allows a compound className
  // (import-wizard.js's `'iw-overlay ret-modal-overlay'`) the same way
  // RET_MODAL_OVERLAY_CLASS_RE above does.
  const idThenClass = /overlay\.id\s*=\s*'([^']+)'[\s\S]{0,200}?overlay\.className\s*=\s*'[^']*\bret-modal-overlay\b[^']*'/g;
  const classThenId = /overlay\.className\s*=\s*'[^']*\bret-modal-overlay\b[^']*'[\s\S]{0,200}?overlay\.id\s*=\s*'([^']+)'/g;
  let m;
  while ((m = idThenClass.exec(code))) ids.add(m[1]);
  while ((m = classThenId.exec(code))) ids.add(m[1]);
  return ids;
}

function testNoBareOverlayRemovalOutsideTheHelpers() {
  const files = frontendFiles();
  const offenders = [];

  for (const file of files) {
    const lines = readCode(file);
    const joined = lines.join('\n');
    const ids = retModalOverlayIds(joined);
    if (!ids.size) continue;
    const idAlt = [...ids].map((id) => id.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|');
    const bareGetById = new RegExp(`getElementById\\('(?:${idAlt})'\\)\\??\\.remove\\(\\)`);
    const bareChained = /\.ret-modal-overlay'\)\s*\.remove\(\)/;

    lines.forEach((line, i) => {
      if (bareGetById.test(line) || bareChained.test(line)) {
        offenders.push(`${file}:${i + 1}: ${line.trim().slice(0, 110)}`);
      }
    });
  }

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
