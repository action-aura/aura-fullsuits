/**
 * retail_p0_shell_icons_test.js -- P0 #14e: every sidebar nav icon resolves
 * to a real icon, none the placeholder.
 *
 * P0 review-fix (2026-09-27): the FIRST version of this file pinned exactly
 * one code point -- U+1F4B5 (💵, Cash Flow's emoji) -- as a literal constant
 * copy-pasted from app-shell.js's own source. It caught the ONE regression
 * it was written against (💵 missing from icons.js's EMOJI map) and nothing
 * else. The item's own rule is general ("no nav icon resolves to the
 * placeholder"), and a check pinned to one code point cannot see a
 * regression on any OTHER nav item -- a fresh nav entry with an unmapped
 * emoji, or Cash Flow's own emoji changing to something else, would sail
 * through untouched.
 *
 * Fix: load the REAL app-shell.js into a vm sandbox next to the REAL
 * icons.js (same two-file sandbox retail_p0_shell_ai_status_test.js's own
 * loadShell() uses) and read `App.systems.retail.nav` back as LIVE DATA --
 * never a regex/text scrape of app-shell.js's source, and never a
 * hand-typed id/emoji list -- then assert every `.icon` on it resolves to a
 * real AuraIcons EMOJI entry. The rule is DERIVED fresh from the nav array
 * every run, so it grows and shrinks with the nav array automatically and
 * needs no maintenance when a nav item is added, removed or re-iconed.
 *
 * Run: node products/retail/tests/retail_p0_shell_icons_test.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const FRONTEND_DIR = path.join(__dirname, '..', 'frontend');
const ICONS_FILE = path.join(FRONTEND_DIR, 'icons.js');
const SHELL_FILE = path.join(FRONTEND_DIR, 'app-shell.js');
const ICONS_SRC = fs.readFileSync(ICONS_FILE, 'utf8');
const SHELL_SRC = fs.readFileSync(SHELL_FILE, 'utf8');

function mutate(src, find, replace) {
  const hits = src.split(find).length - 1;
  assert.strictEqual(
    hits, 1,
    `Mutation anchor occurs ${hits} time(s), expected exactly 1:\n  ${JSON.stringify(find)}\n\n` +
    'A mutation that no longer applies would let the proof below pass while proving nothing. Re-anchor it.'
  );
  return src.replace(find, replace);
}

// Minimal DOM stub -- app-shell.js's top-level code only needs enough to run
// without throwing while its IIFE assigns window.SubsystemApp; nothing here
// is ever rendered to a page. Mirrors retail_p0_shell_ai_status_test.js's
// own loadShell()/makeElementStub() (kept local rather than imported -- see
// that file's own header comment on why every test file in this frontend
// carries its own small stub tailored to what it drives).
function makeElementStub() {
  return {
    style: {}, dataset: {},
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    appendChild() {}, addEventListener() {}, removeEventListener() {}, remove() {},
    getAttribute() { return null; }, setAttribute() {},
    querySelector() { return null; }, querySelectorAll() { return []; },
  };
}

// Loads the REAL icons.js and the REAL app-shell.js into ONE sandbox (icons
// first: app-shell.js's own render helpers call window.AuraIcons.render()
// at USE time, not at load time, so load order only matters for whatever
// runs eagerly, and nothing evaluated here does). Returns both live objects
// so a caller can read `App.systems.retail.nav` and `Icons.EMOJI` back as
// plain data -- never a re-parse of either file's source text.
function loadAppAndIcons(iconsSrc, shellSrc) {
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
  vm.runInContext(iconsSrc || ICONS_SRC, sandbox, { filename: ICONS_FILE });
  assert.ok(sandbox.AuraIcons, 'icons.js did not expose window.AuraIcons');
  vm.runInContext(shellSrc || SHELL_SRC, sandbox, { filename: SHELL_FILE });
  assert.ok(sandbox.SubsystemApp, 'app-shell.js did not expose window.SubsystemApp');
  return { Icons: sandbox.AuraIcons, App: sandbox.SubsystemApp };
}

// ─────────────────────────────────────────────────────────────────────────────
// The rule itself -- derived fresh from the LIVE `App.systems.retail.nav`
// array every call, never a hand-typed id/emoji list. Returns [{id, icon}]
// for every nav entry whose icon does NOT resolve to a real EMOJI entry.
// ─────────────────────────────────────────────────────────────────────────────
function unmappedNavIcons(iconsSrc, shellSrc) {
  const { Icons, App } = loadAppAndIcons(iconsSrc, shellSrc);
  const nav = App.systems.retail.nav;
  assert.ok(Array.isArray(nav) && nav.length > 0,
    'App.systems.retail.nav is empty or missing -- has the nav array moved or been renamed?');
  const placeholder = Icons.render('circle-help', 18);
  const bad = [];
  for (const item of nav) {
    const mapped = Icons.EMOJI[item.icon];
    const rendered = Icons.render(item.icon, 18);
    if (!mapped || rendered === placeholder) {
      bad.push({ id: item.id, icon: item.icon });
    }
  }
  return bad;
}

function navLength() {
  return loadAppAndIcons().App.systems.retail.nav.length;
}

// ─────────────────────────────────────────────────────────────────────────────
// (1) every nav icon resolves to a real icon, none the placeholder
// ─────────────────────────────────────────────────────────────────────────────

function testEveryNavIconResolvesToARealIcon() {
  const bad = unmappedNavIcons();
  assert.deepStrictEqual(bad, [],
    `${bad.length} sidebar nav item(s) resolve to the '?' placeholder (circle-help) instead of a real icon: ` +
    `${JSON.stringify(bad)}`);
  console.log(`PASS: all ${navLength()} sidebar nav items render a real icon, none is the placeholder`);
}

// ─────────────────────────────────────────────────────────────────────────────
// (2) the allow-half for the item's own original bug: Cash Flow specifically
// ─────────────────────────────────────────────────────────────────────────────

function testCashFlowIconResolvesToBanknote() {
  const { Icons, App } = loadAppAndIcons();
  const item = App.systems.retail.nav.find((n) => n.id === 'cash-flow');
  assert.ok(item, "App.systems.retail.nav has no 'cash-flow' entry -- has the nav array changed shape?");
  assert.strictEqual(Icons.EMOJI[item.icon], 'banknote',
    `Cash Flow's own nav icon (${JSON.stringify(item.icon)}) should map to 'banknote', got ` +
    `${JSON.stringify(Icons.EMOJI[item.icon])}`);
  console.log(`PASS: Cash Flow's own nav icon (${JSON.stringify(item.icon)}) maps to 'banknote'`);
}

// ─────────────────────────────────────────────────────────────────────────────
// (3) [MUTATION-PROVED, TWICE] -- the derived rule must catch a regression
// on ANY nav icon, not only the one this file used to hardcode. Proved once
// by breaking Cash Flow's own mapping (the item's original bug) and once by
// breaking a COMPLETELY DIFFERENT nav item's mapping (Dashboard's 🏠). The
// second proof is the whole point of deriving the rule from the nav list: a
// check pinned to a single hardcoded code point could never have caught it.
// ─────────────────────────────────────────────────────────────────────────────

function testMutationProvedForCashFlowIcon() {
  const mutated = mutate(ICONS_SRC, '    "\u{1F4B5}": "banknote",\n', '');
  const bad = unmappedNavIcons(mutated);
  assert.ok(bad.some((b) => b.id === 'cash-flow'),
    `MUTATION SURVIVED -- removing the 💵 EMOJI entry did not surface 'cash-flow' in the derived bad-icon ` +
    `list. Got: ${JSON.stringify(bad)}`);
  assert.deepStrictEqual(unmappedNavIcons(), [], 'guard must pass again against the restored, unmutated icons.js');
  console.log(`PASS: removing 💵's EMOJI entry is caught by the derived nav check  ` +
    `[caught: ${JSON.stringify(bad.find((b) => b.id === 'cash-flow'))}]`);
}

function testMutationProvedForAnArbitraryOtherNavIcon() {
  const mutated = mutate(ICONS_SRC, '    "\u{1F3E0}": "home",\n', '');
  const bad = unmappedNavIcons(mutated);
  assert.ok(bad.some((b) => b.id === 'dashboard'),
    'MUTATION SURVIVED -- removing Dashboard\'s 🏠 EMOJI entry (NOT the code point the old, pinned version ' +
    'of this file knew anything about) did not surface \'dashboard\' in the derived bad-icon list. This is ' +
    'exactly the gap the P0 review flagged: a check pinned to one code point cannot see a regression on any ' +
    `other nav item. Got: ${JSON.stringify(bad)}`);
  assert.deepStrictEqual(unmappedNavIcons(), [], 'guard must pass again against the restored, unmutated icons.js');
  console.log('PASS: removing an UNRELATED nav icon\'s mapping (🏠 Dashboard) is ALSO caught by the derived ' +
    `check  [caught: ${JSON.stringify(bad.find((b) => b.id === 'dashboard'))}]`);
}

// ─────────────────────────────────────────────────────────────────────────────

const CHECKS = [
  ['every sidebar nav icon (derived from the live nav array) resolves to a real icon, none the placeholder', testEveryNavIconResolvesToARealIcon],
  ["Cash Flow's own nav icon maps to 'banknote' (the item's original bug, allow-half)", testCashFlowIconResolvesToBanknote],
  ["mutation-proved: breaking Cash Flow's own mapping is caught", testMutationProvedForCashFlowIcon],
  ["mutation-proved: breaking an UNRELATED nav icon's mapping is ALSO caught (the point of deriving from the nav list)", testMutationProvedForAnArbitraryOtherNavIcon],
];

function main() {
  const failures = [];
  for (const [name, fn] of CHECKS) {
    try {
      fn();
    } catch (err) {
      failures.push(name);
      console.error(`FAIL: ${name}`);
      console.error('      ' + String((err && err.stack) || err).replace(/\n/g, '\n      '));
    }
  }
  if (failures.length) {
    console.error(`\nFAIL: retail_p0_shell_icons_test.js — ${failures.length} of ${CHECKS.length} checks failed:`);
    for (const name of failures) console.error(`  - ${name}`);
    process.exitCode = 1;
    return;
  }
  console.log(`PASS: retail_p0_shell_icons_test.js — ${CHECKS.length} checks`);
}

main();
