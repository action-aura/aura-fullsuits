/**
 * Regression test for the `.ret-btn-danger` button class missing a `:hover`
 * rule in the shared retail subsystem stylesheet.
 *
 * Bug: RetailSystem._injectStyles() (products/retail/frontend/subsystem-retail.js)
 * defines `.ret-btn-primary` and `.ret-btn-ghost` with an accompanying
 * `:hover` rule right next to each base rule, but `.ret-btn-danger` had none
 * anywhere in this file or in css/main.css. `.ret-btn-danger` backs the
 * Held Sales modal's "Discard" button, every list's "Delete" button, Cash
 * Drawer's "Close Shift (Z)" / "Confirm Close" buttons, and the Returns
 * screen's "Process Refund" button -- all reachable directly from the POS
 * screen. Hovering any of them gave zero visual feedback that the element
 * was interactive/dangerous, unlike its ghost/primary siblings.
 *
 * Fix: added `.ret-btn-danger:hover { background:rgba(239,68,68,0.22); }`,
 * matching the existing rgba(239,68,68,...) color language already used by
 * `.ret-btn-danger`'s base rule and border, and the same "darken on hover"
 * pattern `.ret-btn-ghost:hover` already uses for its own background.
 *
 * This test loads the REAL products/retail/frontend/subsystem-retail.js (via
 * Node's vm module, not a reimplementation), triggers `_injectStyles()`, and
 * asserts the injected stylesheet text contains a `.ret-btn-danger:hover`
 * rule -- mirroring how `.ret-btn-primary:hover` / `.ret-btn-ghost:hover`
 * are already present.
 *
 * No test framework is configured for this vanilla-JS, build-step-free frontend
 * (see CLAUDE.md), so this runs standalone with only Node built-ins:
 *
 *   node products/retail/tests/retail_btn_danger_hover_test.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const FRONTEND_FILE = path.join(__dirname, '..', 'frontend', 'subsystem-retail.js');

function makeElementStub() {
  return {
    innerHTML: '',
    textContent: '',
    id: '',
    style: {},
    appendChild() {},
    getAttribute() { return null; },
    setAttribute() {},
    querySelectorAll() { return []; },
  };
}

function loadRetailSystem({ styleEl }) {
  const code = fs.readFileSync(FRONTEND_FILE, 'utf8');

  const sandbox = {
    console,
    t: (s) => s, // stand-in for i18n.js's global `t()` shorthand
    fetch: () => Promise.reject(new Error('fetch should not be called in this test')),
    getComputedStyle: () => ({ getPropertyValue: () => '' }),
    document: {
      // Return null so _injectStyles() does NOT take its early-return path --
      // this test needs the stylesheet to actually be built and appended.
      getElementById(id) {
        if (id === 'ret-styles') return null;
        return makeElementStub();
      },
      createElement() { return styleEl; },
      querySelector() { return makeElementStub(); },
      head: { appendChild() {} },
      documentElement: { getAttribute() { return null; } },
    },
  };
  sandbox.window = sandbox; // enough for the `window.Chart` / `window.RetailSystem` refs used here

  vm.createContext(sandbox);
  vm.runInContext(code, sandbox, { filename: FRONTEND_FILE });

  assert.ok(sandbox.RetailSystem, 'subsystem-retail.js did not expose window.RetailSystem');
  return sandbox.RetailSystem;
}

function testRetBtnDangerHasHoverRule() {
  const styleEl = makeElementStub();
  const RetailSystem = loadRetailSystem({ styleEl });

  RetailSystem._injectStyles();

  const css = styleEl.textContent;
  assert.ok(css && css.length > 0, 'Expected _injectStyles() to populate the <style> element');

  // Sanity check the test harness itself: the sibling variants this bug
  // report compared against must still have their hover rules, or this
  // test would be checking against a moved-goalposts stylesheet.
  assert.ok(
    /\.ret-btn-primary:hover\s*\{/.test(css),
    'Expected .ret-btn-primary:hover to still be present (sanity check)'
  );
  assert.ok(
    /\.ret-btn-ghost:hover\s*\{/.test(css),
    'Expected .ret-btn-ghost:hover to still be present (sanity check)'
  );

  assert.ok(
    /\.ret-btn-danger:hover\s*\{/.test(css),
    '.ret-btn-danger has no :hover rule -- the Discard/Delete/Close Shift/Process ' +
    'Refund buttons give zero visual feedback on hover, unlike .ret-btn-primary and ' +
    '.ret-btn-ghost which both have one.'
  );

  console.log('PASS: .ret-btn-danger has a :hover rule');
}

function main() {
  testRetBtnDangerHasHoverRule();
  console.log('PASS: retail_btn_danger_hover_test.js');
}

try {
  main();
} catch (err) {
  console.error('FAIL: retail_btn_danger_hover_test.js');
  console.error(err);
  process.exitCode = 1;
}
