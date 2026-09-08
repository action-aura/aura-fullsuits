/**
 * retail_toast_tokens_test.js — SubsystemApp.showToast() follows the theme.
 *
 * WHY THIS TEST EXISTS
 * showToast() used to build its inline style from hardcoded hex: a success
 * green (#34d399), an error red (#f87171), a near-black background
 * (#1e1e2e), and plain `color:white`. The toast is the ONE surface a user
 * is guaranteed to see over every other screen in the product, so it was
 * the single most visible thing left ignoring the theme after the
 * 2026-09-08 palette re-grounding (DESIGN.md §4): a shop on Sand or Night
 * still got a fixed near-black snackbar with fixed accent colours, exactly
 * the "one dark island" failure mode retail_design_tokens_test.js's own
 * header comment describes for the stylesheet.
 *
 * THE FIX
 * Every literal is now a `var(--state-*-surface|-border|-text)` reference,
 * the same success/danger/info triad `.btn-mini.approve` / `.btn-mini.reject`
 * already use in css/main.css -- so success/error/info are correct AND
 * already contrast-proven (DESIGN.md §4.4) in all five themes, not just the
 * two shapes (light text on dark background) the old literals assumed.
 *
 * SCOPE
 * retail_design_tokens_test.js only scans css/main.css for stray colour
 * literals -- it has no visibility into inline styles built by JS, which is
 * exactly how this toast's literals survived every existing colour-literal
 * guard. This file closes that gap for showToast() specifically: it RUNS
 * the real function (via Node's vm module, not a reimplementation) for
 * every toast type and inspects the actual style string it builds.
 *
 * MUTATION-PROVED: the "no hex literal" assertion is re-run against a
 * deliberately mutated copy of app-shell.js that reintroduces exactly the
 * original bug (a literal #1e1e2e background), and is required to fail
 * there -- see testHexLiteralCheckIsMutationProved().
 *
 * No test framework is configured for this vanilla-JS, build-step-free
 * frontend (see CLAUDE.md), so this runs standalone on Node built-ins:
 *
 *   node products/retail/tests/retail_toast_tokens_test.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const SHELL_FILE = path.join(__dirname, '..', 'frontend', 'app-shell.js');
const SHELL_SRC = fs.readFileSync(SHELL_FILE, 'utf8');

// ─────────────────────────────────────────────────────────────────────────────
// Minimal sandbox -- showToast() only ever touches document.createElement,
// the returned element's .style/.textContent, document.body.appendChild and
// setTimeout, so nothing heavier than retail_shell_chrome_test.js's stub is
// needed. The whole file is loaded (not just showToast in isolation) so this
// runs the REAL function, byte for byte, the same discipline every other
// standalone test in this directory follows.
// ─────────────────────────────────────────────────────────────────────────────

function makeElementStub() {
  return {
    style: {},
    textContent: '',
    appendChild() {},
    remove() {},
  };
}

function loadShellForToast(shellSrc) {
  const appended = [];
  const sandbox = {
    console,
    t: (s) => s,
    setTimeout: () => 0,   // never actually fires -- nothing here waits 3s to see a toast removed
    clearTimeout: () => {},
    navigator: { userAgent: 'Mozilla/5.0 (test)' },
    location: { hash: '', href: 'http://localhost/' },
    localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
    document: {
      readyState: 'complete',
      body: { appendChild(el) { appended.push(el); } },
      head: { appendChild() {} },
      documentElement: { getAttribute: () => 'light', setAttribute() {}, style: { setProperty() {} } },
      createElement: () => makeElementStub(),
      getElementById: () => null,
      querySelector: () => null,
      querySelectorAll: () => [],
      addEventListener() {},
    },
  };
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(shellSrc, sandbox, { filename: SHELL_FILE });
  assert.ok(sandbox.SubsystemApp, 'app-shell.js did not expose window.SubsystemApp');
  assert.strictEqual(typeof sandbox.SubsystemApp.showToast, 'function',
    'SubsystemApp.showToast is not a function -- the fixture is asserting about nothing');
  return { sandbox, App: sandbox.SubsystemApp, appended };
}

// One call, one captured toast element -- showToast() appends synchronously.
function fireToast(App, appended, msg, type) {
  const before = appended.length;
  if (type === undefined) App.showToast(msg);
  else App.showToast(msg, type);
  assert.strictEqual(appended.length, before + 1,
    `showToast(${JSON.stringify(msg)}, ${JSON.stringify(type)}) did not append exactly one element ` +
    `(before=${before}, after=${appended.length}) -- the fixture cannot see what it renders.`);
  return appended[appended.length - 1];
}

const HEX_LITERAL = /#[0-9a-fA-F]{3,8}\b/;

// ═════════════════════════════════════════════════════════════════════════════
// 1 — no hex literal in any toast type's rendered style, for real, per type
// ═════════════════════════════════════════════════════════════════════════════

const CASES = [
  { type: 'success', tokens: ['--state-success-surface', '--state-success-border', '--state-success-text'] },
  { type: 'error',    tokens: ['--state-danger-surface',  '--state-danger-border',  '--state-danger-text'] },
  { type: 'info',     tokens: ['--state-info-surface',    '--state-info-border',    '--state-info-text'] },
];

function testNoHexLiteralPerType() {
  const { App, appended } = loadShellForToast(SHELL_SRC);
  for (const { type } of CASES) {
    const toast = fireToast(App, appended, 'a message', type);
    const css = toast.style.cssText || '';
    assert.ok(css.length > 20, `showToast(..., '${type}') produced an implausibly short style string: ${JSON.stringify(css)}`);
    assert.ok(!HEX_LITERAL.test(css),
      `showToast(..., '${type}') still contains a hex colour literal: ${css}`);
    assert.ok(!/color:\s*white\b/i.test(css),
      `showToast(..., '${type}') still hardcodes color:white instead of a text token: ${css}`);
  }
  console.log('PASS: no hex colour literal or hardcoded white text in any toast type\'s rendered style');
}

// =============================================================================
// 1b - the toast is positioned LOGICALLY, so it mirrors with the shell
//
// retail_design_rtl_test.js ratchets physical properties in the STYLESHEETS.
// This style string is assembled in JavaScript, so that ratchet cannot see it
// at all -- and a `right:24px` duly appeared here during the token rewrite on
// 2026-09-08. The toast floats over every screen, and the whole shell mirrors
// in Arabic, so a physical corner is a real defect rather than a nitpick.
// This is the only guard that can see it.
// =============================================================================

const PHYSICAL_IN_INLINE_STYLE = /(?:^|;)\s*(left|right|margin-left|margin-right|padding-left|padding-right)\s*:/i;

function testToastIsPositionedLogically() {
  const { App, appended } = loadShellForToast(SHELL_SRC);
  for (const { type } of CASES) {
    const toast = fireToast(App, appended, 'a message', type);
    const css = toast.style.cssText || '';
    assert.ok(!PHYSICAL_IN_INLINE_STYLE.test(css),
      `showToast(..., '${type}') pins itself with a PHYSICAL property, so it lands in the wrong ` +
      `corner when the shell mirrors for Arabic. Use inset-inline-end. Got: ${css}`);
    assert.ok(/inset-inline-end\s*:/.test(css),
      `showToast(..., '${type}') should position its trailing edge with inset-inline-end. Got: ${css}`);
  }
  console.log('PASS: the toast positions itself logically, so it mirrors in Arabic');
}

// ═════════════════════════════════════════════════════════════════════════════
// 2 — every toast type resolves through its own surface/border/text token
// ═════════════════════════════════════════════════════════════════════════════

function testEveryTypeUsesItsOwnStateTokens() {
  const { App, appended } = loadShellForToast(SHELL_SRC);
  for (const { type, tokens } of CASES) {
    const toast = fireToast(App, appended, 'a message', type);
    const css = toast.style.cssText || '';
    for (const tok of tokens) {
      assert.ok(css.includes(`var(${tok})`),
        `showToast(..., '${type}') does not reference var(${tok}) -- it should resolve THROUGH the token, ` +
        `not just avoid a literal, so it moves with the palette. Got: ${css}`);
    }
  }
  console.log('PASS: success/error/info each resolve through their own --state-*-surface/-border/-text tokens');
}

// ═════════════════════════════════════════════════════════════════════════════
// 3 — an unknown/omitted type falls back to 'info', not to `undefined` styling
// ═════════════════════════════════════════════════════════════════════════════

function testUnknownOrOmittedTypeFallsBackToInfo() {
  const { App, appended } = loadShellForToast(SHELL_SRC);
  for (const type of [undefined, 'not-a-real-type']) {
    const toast = fireToast(App, appended, 'a message', type);
    const css = toast.style.cssText || '';
    assert.ok(css.includes('var(--state-info-surface)') && css.includes('var(--state-info-text)'),
      `showToast(..., ${JSON.stringify(type)}) did not fall back to the info tokens. Got: ${css}`);
    assert.ok(!HEX_LITERAL.test(css), `fallback toast for type ${JSON.stringify(type)} contains a hex literal: ${css}`);
  }
  console.log('PASS: an omitted or unrecognised type falls back to the info tokens, never to undefined styling');
}

// ═════════════════════════════════════════════════════════════════════════════
// 4 — MUTATION PROOF: reintroducing the original #1e1e2e background is caught
// ═════════════════════════════════════════════════════════════════════════════

function mutateBackgroundToOriginalHex(src) {
  const find = 'background:var(${tok.surface});';
  const hits = src.split(find).length - 1;
  assert.strictEqual(hits, 1,
    `Mutation anchor occurs ${hits} time(s), expected exactly 1: ${JSON.stringify(find)}\n` +
    'A mutation that no longer applies would let the proof below pass while proving nothing. Re-anchor it.');
  // Reintroduces exactly the original bug: a literal near-black background,
  // the one the intro of this file describes.
  return src.split(find).join('background:#1e1e2e;');
}

function testHexLiteralCheckIsMutationProved() {
  const mutated = mutateBackgroundToOriginalHex(SHELL_SRC);
  const { App, appended } = loadShellForToast(mutated);
  let threw = null;
  try {
    for (const { type } of CASES) {
      const toast = fireToast(App, appended, 'a message', type);
      const css = toast.style.cssText || '';
      assert.ok(!HEX_LITERAL.test(css), `showToast(..., '${type}') still contains a hex colour literal: ${css}`);
    }
  } catch (err) {
    threw = err;
  }
  assert.ok(
    threw,
    'MUTATION SURVIVED — the "no hex literal" check passed against a build with the original ' +
    '#1e1e2e background literal deliberately reintroduced, so it is not actually watching for it. ' +
    'Fix the check, not the mutation.'
  );
  console.log(`PASS: the "no hex literal" check is mutation-proved  [caught: ${String(threw.message).split('\n')[0].slice(0, 120)}]`);
}

// ─────────────────────────────────────────────────────────────────────────────

const RUN = [
  testNoHexLiteralPerType,
  testToastIsPositionedLogically,
  testEveryTypeUsesItsOwnStateTokens,
  testUnknownOrOmittedTypeFallsBackToInfo,
  testHexLiteralCheckIsMutationProved,
];

let failed = 0;
for (const fn of RUN) {
  try {
    fn();
  } catch (err) {
    failed += 1;
    console.error('FAIL: ' + fn.name);
    console.error('  ' + (err && err.message ? err.message : err));
  }
}
if (failed) {
  console.error(`FAIL: retail_toast_tokens_test.js — ${failed} of ${RUN.length} case(s) failed`);
  process.exitCode = 1;
} else {
  console.log(`PASS: retail_toast_tokens_test.js — ${RUN.length} case(s)`);
}
