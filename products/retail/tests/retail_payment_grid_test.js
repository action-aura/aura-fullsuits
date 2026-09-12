/**
 * retail_payment_grid_test.js — the tender grid rebuild (owner brief,
 * 2026-09-08: "the single control a cashier touches on every sale" carried
 * six raw emoji glyphs with no keyboard path at all).
 *
 * Pins:
 *   1. Six buttons in the documented grid order -- Cash, Card, Mobile/CliQ,
 *      Transfer, Credit, Voucher -- each rendering a real <svg> (AuraIcons),
 *      never an emoji character.
 *   2. Each button carries its own F1-F6 badge, in grid order.
 *   3. The grid is declared 3 columns (css/main.css's own ratchet tests only
 *      scan main.css, so this reads the ACTUAL rule this screen ships,
 *      subsystem-retail.js's own injected <style>, the same way
 *      retail_design_rtl_test.js reads main.css directly rather than a
 *      paraphrase of it).
 *   4. Cash is the default -- '.pos-pay-btn active' on mount, unchanged from
 *      before this pass.
 *   5. Every label resolves through the REAL locale catalogues (both must
 *      carry the exact key t() is called with).
 *   6. F1-F6 actually select the corresponding tender while the POS screen
 *      is active, through the REAL _onPOSShortcut handler (not a
 *      reimplementation) -- specifically WITH THE SCAN BOX FOCUSED, which is
 *      where this till keeps focus essentially all the time it is open
 *      (_keepScanFocus, so a barcode wedge always lands somewhere useful).
 *      Enter and the other TYPED keys still respect the text-field guard.
 *      MUTATION-PROVED: a mutant that ignores F-keys while an input has
 *      focus is required to make this check fail.
 *
 *      This point used to read the other way round -- that F-keys must NOT
 *      fire while a text field has focus -- and the suite passed on a
 *      fixture with "focus nowhere", a state the running till never reaches.
 *      Six badges were printed on the tender buttons and, measured in a real
 *      browser on 2026-09-10, one of six worked; that one was F1, which only
 *      looked right because cash is the default. The tests were describing
 *      the bug as the specification.
 *
 * Harness shape follows retail_shell_chrome_test.js (vm sandbox, stubbed
 * DOM, the REAL icons.js loaded alongside the REAL subsystem-retail.js) for
 * the render assertions, and retail_pos_keyboard_shortcuts_test.js's
 * loadRetailSystem/fireKeydown/pressKey shape for the F-key wiring, since
 * both already exist and this is exercising the same _onPOSShortcut code
 * path they do -- not a paraphrase of it.
 *
 * Run: node products/retail/tests/retail_payment_grid_test.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const FRONTEND_DIR = path.join(__dirname, '..', 'frontend');
const SRC_PATH = path.join(FRONTEND_DIR, 'subsystem-retail.js');
const ICONS_PATH = path.join(FRONTEND_DIR, 'icons.js');
const EN_PATH = path.join(FRONTEND_DIR, 'locales', 'en.json');
const AR_PATH = path.join(FRONTEND_DIR, 'locales', 'ar.json');

const SOURCE = fs.readFileSync(SRC_PATH, 'utf8');
const ICONS_SRC = fs.readFileSync(ICONS_PATH, 'utf8');
const EN = JSON.parse(fs.readFileSync(EN_PATH, 'utf8'));
const AR = JSON.parse(fs.readFileSync(AR_PATH, 'utf8'));

const EXPECTED_ORDER = [
  { method: 'cash', label: 'Cash', fkey: 'F1' },
  { method: 'card', label: 'Card', fkey: 'F2' },
  { method: 'mobile', label: 'Mobile / CliQ', fkey: 'F3' },
  { method: 'transfer', label: 'Transfer', fkey: 'F4' },
  { method: 'credit', label: 'Credit', fkey: 'F5' },
  { method: 'voucher', label: 'Voucher', fkey: 'F6' },
];

// Broad enough to catch every emoji block this file has historically shipped
// (see the emoji sweep this pass was scoped from) without also flagging
// plain ASCII/Latin/Arabic text or the SVG markup itself.
const EMOJI_RE = /[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}\u{2B00}-\u{2BFF}\u{2300}-\u{23FF}\u{2190}-\u{21FF}]/u;

function makeStubElement(overrides) {
  return Object.assign({
    id: '', value: '', disabled: false, textContent: '', style: {},
    tagName: 'DIV', isContentEditable: false,
    _classes: new Set(),
    classList: {
      add(...cls) { cls.forEach((c) => this._set.add(c)); },
      remove(...cls) { cls.forEach((c) => this._set.delete(c)); },
      contains(c) { return this._set.has(c); },
      toggle(c, on) { if (on) this._set.add(c); else this._set.delete(c); },
    },
    focus() {},
  }, overrides);
}
// classList needs to close over the SAME set instance as the element itself.
function attachClassList(el) {
  el.classList._set = el._classes;
  return el;
}

/**
 * Loads the REAL icons.js + subsystem-retail.js into one sandbox, the same
 * technique retail_shell_chrome_test.js uses -- window.AuraIcons is the
 * genuine module, so the SVG assertions below are checking real render()
 * output, not a stub.
 */
function loadRetailSystem(opts) {
  const options = opts || {};
  const sandbox = {};
  sandbox.window = sandbox;
  sandbox.navigator = { userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)' };
  sandbox.t = (s) => s;
  sandbox.console = console;
  sandbox.localStorage = { getItem: () => null, setItem: () => {} };
  sandbox.performance = { now: () => sandbox.__clock };
  sandbox.__clock = 10000;

  // The six tender buttons, pre-built as stubs carrying dataset.method --
  // exactly what document.querySelectorAll('.pos-pay-btn') needs to hand
  // back for _setPayment/_onPOSShortcut's F-key branch to find the right one.
  const payButtons = EXPECTED_ORDER.map(({ method }) => {
    const btn = attachClassList(makeStubElement({ tagName: 'BUTTON', dataset: { method } }));
    if (method === 'cash') btn.classList.add('active');
    return btn;
  });

  const els = Object.create(null);
  const state = { activeEl: options.activeEl || null };
  const getEl = (id) => {
    if (!els[id]) els[id] = makeStubElement({ id });
    return els[id];
  };

  sandbox.document = {
    get activeElement() { return state.activeEl; },
    getElementById: getEl,
    querySelectorAll: (sel) => (sel === '.pos-pay-btn' ? payButtons : []),
    createElement: () => makeStubElement(),
    addEventListener: () => {},
  };

  vm.createContext(sandbox);
  vm.runInContext(ICONS_SRC, sandbox, { filename: ICONS_PATH });
  assert.ok(sandbox.AuraIcons, 'icons.js did not expose window.AuraIcons');
  vm.runInContext(SOURCE, sandbox, { filename: SRC_PATH });
  const RetailSystem = sandbox.RetailSystem;
  assert.ok(RetailSystem, 'RetailSystem failed to load from subsystem-retail.js');

  sandbox.SubsystemApp = { active: 'retail', showToast: () => {} };
  RetailSystem._section = 'pos';
  RetailSystem._paymentMethod = 'cash';
  RetailSystem._pendingQty = null;
  RetailSystem._qtyKeyBuffer = '';
  RetailSystem._qtyKeyLastAt = 0;

  return { RetailSystem, sandbox, els, state, payButtons };
}

function fireKeydown(RetailSystem, key, extra) {
  const evt = Object.assign({
    key,
    ctrlKey: false, metaKey: false, altKey: false,
    preventDefault() {}, stopPropagation() {},
  }, extra || {});
  RetailSystem._onPOSShortcut(evt);
  return evt;
}

// ═════════════════════════════════════════════════════════════════════════
// 1-5 — the rendered grid itself
// ═════════════════════════════════════════════════════════════════════════

function testSixButtonsInDocumentedOrderNoEmojiRealSVG() {
  const { RetailSystem } = loadRetailSystem({ activeEl: null });
  const html = RetailSystem._renderPayButtons();

  const buttonRe = /<button class="pos-pay-btn([^"]*)" data-method="([^"]+)"[\s\S]*?<\/button>/g;
  const matches = [...html.matchAll(buttonRe)];
  assert.strictEqual(matches.length, 6, `Expected 6 rendered tender buttons, found ${matches.length}.`);

  matches.forEach((m, i) => {
    const [whole, classes, method] = m;
    const expected = EXPECTED_ORDER[i];
    assert.strictEqual(method, expected.method,
      `Button ${i + 1} should be data-method="${expected.method}", got "${method}".`);
    assert.ok(whole.includes('<svg'), `Button ${i + 1} (${method}) does not render an <svg> icon.`);
    assert.ok(!EMOJI_RE.test(whole), `Button ${i + 1} (${method}) still contains an emoji character:\n  ${whole}`);
    assert.ok(whole.includes(`class="pos-pay-kbd"`) , `Button ${i + 1} (${method}) has no F-key badge element.`);
    assert.ok(whole.includes(`>${expected.fkey}<`), `Button ${i + 1} (${method}) badge does not read "${expected.fkey}".`);
    if (method === 'cash') {
      assert.ok(classes.includes('active'), 'Cash is not marked active by default.');
    } else {
      assert.ok(!classes.includes('active'), `${method} should not be active by default.`);
    }
  });
  console.log('PASS: 6 tender buttons render in documented order, each a real <svg> with an F-key badge and no emoji, Cash active by default');
}

function testGridIsThreeColumns() {
  const m = /\.pos-pay-btns\s*\{[^}]*grid-template-columns\s*:\s*repeat\(\s*3\s*,\s*1fr\s*\)/.exec(SOURCE);
  assert.ok(m, '.pos-pay-btns is not declared as a 3-column grid in subsystem-retail.js.');
  console.log('PASS: .pos-pay-btns is a 3-column grid');
}

function testEveryLabelResolvesThroughBothCatalogues() {
  for (const { method, label } of EXPECTED_ORDER) {
    assert.ok(Object.prototype.hasOwnProperty.call(EN, label),
      `en.json has no entry for "${label}" (${method} tender label).`);
    assert.ok(Object.prototype.hasOwnProperty.call(AR, label),
      `ar.json has no entry for "${label}" (${method} tender label).`);
    assert.ok(EN[label] && EN[label].length > 0, `en.json's "${label}" entry is empty.`);
    assert.ok(AR[label] && AR[label].length > 0, `ar.json's "${label}" entry is empty.`);
  }
  console.log('PASS: every tender label resolves through both locale catalogues');
}

// ═════════════════════════════════════════════════════════════════════════
// 6 — F1-F6 actually select the method, and respect the text-field guard
//     [MUTATION-PROVED]
// ═════════════════════════════════════════════════════════════════════════

// The fixture below focuses #pos-search ON PURPOSE, and that is the whole
// lesson of this pair.
//
// This test used to run with `activeEl: null` -- "focus nowhere" -- and its
// sibling asserted that F3 must NOT fire while an input was focused. Both
// passed for months. But the till PARKS focus in #pos-search so a barcode
// wedge always lands somewhere useful (_keepScanFocus), so "an input is
// focused" is not an edge case here: it is the only state the till is ever
// in while open. `activeEl: null` was a state the product never reaches.
//
// So the suite was simultaneously asserting the shortcuts work (in a state
// that never happens) and that they are blocked (in the state that always
// happens), while the tender buttons shipped with "F1".."F6" printed on
// them. Measured in a real browser 2026-09-10, before the fix: 1 of 6 keys
// worked, and that one was F1, which only looked correct because cash is
// already the default tender.
function testF3SelectsMobileWithTheScanBoxFocused() {
  // The real, ever-present state of the till: focus sitting in the scan box.
  const ctx = loadRetailSystem({
    activeEl: { tagName: 'INPUT', id: 'pos-search', isContentEditable: false },
  });
  fireKeydown(ctx.RetailSystem, 'F3');
  assert.strictEqual(ctx.RetailSystem._paymentMethod, 'mobile',
    'F3 did not select the Mobile / CliQ tender while focus was in the scan box -- '
    + 'which is where this till keeps focus essentially all the time, so a badge '
    + 'printed on the button is a promise the product does not keep.');
  const mobileBtn = ctx.payButtons.find((b) => b.dataset.method === 'mobile');
  assert.ok(mobileBtn.classList.contains('active'), 'F3 selected mobile in state but never marked its button active.');
  const cashBtn = ctx.payButtons.find((b) => b.dataset.method === 'cash');
  assert.ok(!cashBtn.classList.contains('active'), 'F3 left Cash marked active alongside Mobile.');
  console.log('PASS: F3 selects Mobile / CliQ with the scan box focused, and updates the active button');
}

function testEveryFunctionKeyFiresWithTheScanBoxFocused() {
  // One key working is not the same as the printed grid working. F1 in
  // particular passes trivially, because cash is the default -- so a check
  // that only tried F1 would have reported this feature healthy while five
  // of the six badges were dead.
  const expected = ['cash', 'card', 'mobile', 'transfer', 'credit', 'voucher'];
  expected.forEach((method, i) => {
    const ctx = loadRetailSystem({
      activeEl: { tagName: 'INPUT', id: 'pos-search', isContentEditable: false },
    });
    fireKeydown(ctx.RetailSystem, `F${i + 1}`);
    assert.strictEqual(ctx.RetailSystem._paymentMethod, method,
      `F${i + 1} should select '${method}' but selected '${ctx.RetailSystem._paymentMethod}'.`);
  });
  console.log('PASS: all six function keys select their printed tender with the scan box focused');
}

function testTypedKeysStillRespectTheTextFieldGuard() {
  // The ALLOW half above must not have cost the DENY half. The guard exists
  // so a cashier typing a search term and hitting Enter out of habit cannot
  // charge whatever is sitting in the cart. A function key emits no
  // character and so cannot be part of typing; Enter can, and is still
  // blocked.
  const ctx = loadRetailSystem({
    activeEl: { tagName: 'INPUT', id: 'pos-search', isContentEditable: false },
  });
  let charged = 0;
  ctx.RetailSystem._checkout = () => { charged += 1; };
  fireKeydown(ctx.RetailSystem, 'Enter');
  assert.strictEqual(charged, 0,
    'Enter charged the sale while focus was in a text field -- the guard that '
    + 'stops a habitual Enter from taking money has been lost.');
  console.log('PASS: Enter is still ignored while a text field has focus');
}

// Mutation harness -- same shape as retail_shell_chrome_test.js: re-anchor to
// exact source text, require the anchor to occur exactly once so a stale
// mutation cannot silently no-op.
function eolOf(src) { return src.indexOf('\r\n') !== -1 ? '\r\n' : '\n'; }
function nlFor(src) { const eol = eolOf(src); return (s) => s.replace(/\n/g, eol); }

function mutate(src, pairs) {
  const nl = nlFor(src);
  let out = src;
  for (const [rawFind, rawReplace] of pairs) {
    const find = nl(rawFind);
    const replace = nl(rawReplace);
    const hits = out.split(find).length - 1;
    assert.strictEqual(
      hits, 1,
      `Mutation anchor occurs ${hits} time(s), expected exactly 1:\n  ${JSON.stringify(find)}\n\n` +
      'A mutation that no longer applies would let the proof below pass while proving nothing. Re-anchor it.'
    );
    out = out.replace(find, replace);
  }
  return out;
}

async function provesMutation(what, check) {
  let threw = null;
  try {
    await check();
  } catch (err) {
    threw = err;
  }
  assert.ok(
    threw,
    `MUTATION SURVIVED — ${what}\n` +
    'The guard for this passed against a build with the behaviour deliberately broken, so it is ' +
    'not actually watching it. Fix the check, not the mutation.'
  );
  return `${what}  [caught: ${String(threw.message || threw).split('\n')[0].slice(0, 160)}]`;
}

function loadMutatedRetailSystem(mutatedSource, opts) {
  const options = opts || {};
  const sandbox = {};
  sandbox.window = sandbox;
  sandbox.navigator = { userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)' };
  sandbox.t = (s) => s;
  sandbox.console = console;
  sandbox.localStorage = { getItem: () => null, setItem: () => {} };
  sandbox.performance = { now: () => sandbox.__clock };
  sandbox.__clock = 10000;

  const payButtons = EXPECTED_ORDER.map(({ method }) => {
    const btn = attachClassList(makeStubElement({ tagName: 'BUTTON', dataset: { method } }));
    if (method === 'cash') btn.classList.add('active');
    return btn;
  });
  const els = Object.create(null);
  const state = { activeEl: options.activeEl || null };
  const getEl = (id) => { if (!els[id]) els[id] = makeStubElement({ id }); return els[id]; };

  sandbox.document = {
    get activeElement() { return state.activeEl; },
    getElementById: getEl,
    querySelectorAll: (sel) => (sel === '.pos-pay-btn' ? payButtons : []),
    createElement: () => makeStubElement(),
    addEventListener: () => {},
  };

  vm.createContext(sandbox);
  vm.runInContext(ICONS_SRC, sandbox, { filename: ICONS_PATH });
  vm.runInContext(mutatedSource, sandbox, { filename: SRC_PATH });
  const RetailSystem = sandbox.RetailSystem;
  sandbox.SubsystemApp = { active: 'retail', showToast: () => {} };
  RetailSystem._section = 'pos';
  RetailSystem._paymentMethod = 'cash';
  RetailSystem._pendingQty = null;
  RetailSystem._qtyKeyBuffer = '';
  RetailSystem._qtyKeyLastAt = 0;
  return { RetailSystem, payButtons };
}

async function testFunctionKeysBeatingTheTextGuardIsProved() {
  // The mutation this file used to run has been inverted, and deliberately.
  //
  // It used to delete the text-field guard sitting AHEAD of the F-key branch
  // and require the suite to notice. That proved the guard blocked function
  // keys -- which was the bug, not the feature: the till parks focus in
  // #pos-search, so the guard blocked all six printed badges in every real
  // session. The old mutation was faithfully proving the defect was present.
  //
  // So the mutant now REINTRODUCES that defect: make the F-key lookup yield
  // nothing whenever an input has focus, exactly as the old ordering did.
  // Written as its own condition rather than by moving the guard back,
  // because `inTextField` is declared below this branch now and re-inserting
  // it here would throw on the temporal dead zone -- a mutant that fails for
  // the wrong reason proves nothing.
  const mutated = mutate(SOURCE, [[
    `    const fMatch = /^F([1-6])$/.exec(e.key);`,
    `    const fMatch = (document.activeElement && document.activeElement.tagName === 'INPUT')
      ? null : /^F([1-6])$/.exec(e.key);`,
  ]]);

  const msg = await provesMutation(
    'the six-function-key check survived a mutant that ignores F-keys while the scan box has focus',
    async () => {
      const ctx = loadMutatedRetailSystem(mutated, {
        activeEl: { tagName: 'INPUT', id: 'pos-search', isContentEditable: false },
      });
      fireKeydown(ctx.RetailSystem, 'F3');
      assert.strictEqual(ctx.RetailSystem._paymentMethod, 'mobile',
        'F3 should select the Mobile / CliQ tender with the scan box focused.');
    }
  );
  console.log('PASS: ' + msg);
}

// ─────────────────────────────────────────────────────────────────────────

const CASES = [
  testSixButtonsInDocumentedOrderNoEmojiRealSVG,
  testGridIsThreeColumns,
  testEveryLabelResolvesThroughBothCatalogues,
  testF3SelectsMobileWithTheScanBoxFocused,
  testEveryFunctionKeyFiresWithTheScanBoxFocused,
  testTypedKeysStillRespectTheTextFieldGuard,
  testFunctionKeysBeatingTheTextGuardIsProved,
];

async function main() {
  let failed = 0;
  for (const fn of CASES) {
    try {
      await fn();
    } catch (err) {
      failed += 1;
      console.error('  FAIL ' + fn.name);
      console.error('       ' + (err && err.message ? err.message : err));
    }
  }
  if (failed) {
    console.error(`FAIL: retail_payment_grid_test.js — ${failed} of ${CASES.length} case(s) failed`);
    process.exitCode = 1;
  } else {
    console.log(`PASS: retail_payment_grid_test.js — ${CASES.length} case(s)`);
  }
}

main();
