/**
 * retail_confirm_modal_test.js — _confirm(), the token-styled replacement for
 * native confirm() (owner brief, 2026-09-08: native dialogs "cannot be
 * themed, cannot be mirrored for Arabic, and cannot be translated", and this
 * file had 12 of them hardcoding destructive-action text in English).
 *
 * Pins:
 *   1. _confirm({...}) resolves true when the confirm button is clicked.
 *   2. _confirm({...}) resolves false when the cancel button is clicked.
 *   3. _confirm({...}) resolves false when Escape is pressed. MUTATION-
 *      PROVED: a mutant that drops the Escape branch's finish(false) call is
 *      required to make this fail (the promise would otherwise hang forever,
 *      which the proof turns into a bounded timeout so the mutant fails fast
 *      rather than wedging the suite).
 *   4. Bonus: a click on the overlay itself (outside the card) also cancels
 *      -- part of the same "resolves false" contract, on the other input
 *      path _confirm exposes for it.
 *   5. The rendered markup contains no left:/right: physical CSS properties
 *      -- this product ships Arabic (see retail_design_rtl_test.js, which
 *      only scans css/main.css and so never sees this file's own injected
 *      markup at all).
 *   6. subsystem-retail.js contains zero bare confirm( calls afterwards
 *      (comment-aware -- this file's own header prose legitimately says
 *      "confirm(" in English, describing the change; `_confirm(`/
 *      `this._confirm(` call sites are the replacement, not a leftover).
 *
 * Harness shape follows retail_shell_chrome_test.js: a vm sandbox with a
 * stubbed DOM, driving the REAL _confirm() from the REAL subsystem-retail.js
 * source rather than a reimplementation.
 *
 * Run: node products/retail/tests/retail_confirm_modal_test.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const FRONTEND_DIR = path.join(__dirname, '..', 'frontend');
const SRC_PATH = path.join(FRONTEND_DIR, 'subsystem-retail.js');
const SOURCE = fs.readFileSync(SRC_PATH, 'utf8');

// ── Stub DOM ────────────────────────────────────────────────────────────────
// Same shape as retail_shell_chrome_test.js / retail_pos_keyboard_shortcuts_
// test.js: elements are plain objects with just enough behaviour (classList,
// addEventListener storing handlers so a test can fire them, focus() as a
// spy) for _confirm()'s real code path to run end to end.

function makeStubElement(overrides) {
  const handlers = Object.create(null);
  return Object.assign({
    id: '', innerHTML: '', focusCount: 0,
    addEventListener(type, fn) {
      (handlers[type] || (handlers[type] = [])).push(fn);
    },
    removeEventListener() {},
    fire(type, evt) { (handlers[type] || []).forEach((fn) => fn(evt || {})); },
    focus() { this.focusCount += 1; },
    remove() {},
    classList: { add() {}, remove() {}, contains() { return false; } },
  }, overrides);
}

/**
 * Builds one fresh sandbox + RetailSystem for one _confirm() call. Isolated
 * per test case (not shared) so 'ret-confirm-ok'/'ret-confirm-cancel' -- ids
 * this file hardcodes and reuses on every call -- never leak handlers from
 * one test into another.
 */
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
  const overlays = [];
  const docHandlers = Object.create(null);

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
    body: { appendChild(el) { overlays.push(el); } },
  };

  vm.createContext(sandbox);
  vm.runInContext(SOURCE, sandbox, { filename: SRC_PATH });
  const RetailSystem = sandbox.RetailSystem;
  assert.ok(RetailSystem, 'RetailSystem failed to load from subsystem-retail.js');

  return {
    RetailSystem, els, trigger, overlays, docHandlers,
    fireDocKeydown(evt) { (docHandlers.keydown || []).forEach((h) => h.fn(evt)); },
  };
}

function baseOpts(extra) {
  return Object.assign({
    title: 'Delete Widget',
    message: 'Delete "Widget"?',
    confirmLabel: 'Delete',
  }, extra || {});
}

// A bounded wait so a mutant that breaks resolution (see the Escape mutation
// proof) fails FAST with a clear message instead of hanging the suite.
function withTimeout(promise, ms, label) {
  return Promise.race([
    promise,
    new Promise((_, reject) => setTimeout(() => reject(new Error(`${label} — never resolved within ${ms}ms`)), ms)),
  ]);
}

// ═════════════════════════════════════════════════════════════════════════
// 1 — resolves true on confirm
// ═════════════════════════════════════════════════════════════════════════

async function testResolvesTrueOnConfirmClick() {
  const ctx = loadRetailSystem();
  const resultPromise = ctx.RetailSystem._confirm(baseOpts());
  const okBtn = ctx.els['ret-confirm-ok'];
  assert.ok(okBtn, '_confirm() never looked up #ret-confirm-ok.');
  assert.strictEqual(okBtn.focusCount, 1, 'The confirm button was not focused on open.');
  okBtn.fire('click');
  const result = await withTimeout(resultPromise, 500, 'confirm click');
  assert.strictEqual(result, true, '_confirm() did not resolve true when the confirm button was clicked.');
  assert.strictEqual(ctx.trigger.focusCount, 1, 'Focus was not returned to the trigger element on close.');
  console.log('PASS: _confirm() resolves true on confirm and returns focus to the trigger');
}

// ═════════════════════════════════════════════════════════════════════════
// 2 — resolves false on cancel
// ═════════════════════════════════════════════════════════════════════════

async function testResolvesFalseOnCancelClick() {
  const ctx = loadRetailSystem();
  const resultPromise = ctx.RetailSystem._confirm(baseOpts());
  const cancelBtn = ctx.els['ret-confirm-cancel'];
  assert.ok(cancelBtn, '_confirm() never looked up #ret-confirm-cancel.');
  cancelBtn.fire('click');
  const result = await withTimeout(resultPromise, 500, 'cancel click');
  assert.strictEqual(result, false, '_confirm() did not resolve false when the cancel button was clicked.');
  console.log('PASS: _confirm() resolves false on cancel');
}

// ═════════════════════════════════════════════════════════════════════════
// 3 — resolves false on Escape [MUTATION-PROVED]
// ═════════════════════════════════════════════════════════════════════════

async function testResolvesFalseOnEscape() {
  const ctx = loadRetailSystem();
  const resultPromise = ctx.RetailSystem._confirm(baseOpts());
  ctx.fireDocKeydown({ key: 'Escape', preventDefault() {}, stopPropagation() {} });
  const result = await withTimeout(resultPromise, 500, 'Escape');
  assert.strictEqual(result, false, '_confirm() did not resolve false when Escape was pressed.');
  console.log('PASS: _confirm() resolves false on Escape');
}

// ═════════════════════════════════════════════════════════════════════════
// 4 — bonus: overlay click cancels
// ═════════════════════════════════════════════════════════════════════════

async function testOverlayClickCancels() {
  const ctx = loadRetailSystem();
  const resultPromise = ctx.RetailSystem._confirm(baseOpts());
  const overlay = ctx.overlays[ctx.overlays.length - 1];
  assert.ok(overlay, '_confirm() never appended an overlay to document.body.');
  overlay.fire('click', { target: overlay });
  const result = await withTimeout(resultPromise, 500, 'overlay click');
  assert.strictEqual(result, false, '_confirm() did not resolve false when the overlay itself was clicked.');
  console.log('PASS: clicking the overlay (outside the card) cancels');
}

// ═════════════════════════════════════════════════════════════════════════
// 5 — no left:/right: physical properties in the rendered markup
// ═════════════════════════════════════════════════════════════════════════

const PHYSICAL_RE = [
  { re: /margin-left\s*:/i, use: 'margin-inline-start' },
  { re: /margin-right\s*:/i, use: 'margin-inline-end' },
  { re: /padding-left\s*:/i, use: 'padding-inline-start' },
  { re: /padding-right\s*:/i, use: 'padding-inline-end' },
  { re: /(?:^|[\s;])left\s*:/i, use: 'inset-inline-start' },
  { re: /(?:^|[\s;])right\s*:/i, use: 'inset-inline-end' },
  { re: /text-align\s*:\s*left/i, use: 'text-align: start' },
  { re: /text-align\s*:\s*right/i, use: 'text-align: end' },
];

async function testNoPhysicalPropertiesInRenderedMarkup() {
  const ctx = loadRetailSystem();
  // Never resolved -- this test only inspects the markup _confirm() builds
  // before any input arrives.
  ctx.RetailSystem._confirm(baseOpts({
    title: 'Delete Product',
    message: 'Delete "Widget"? (The product will be deactivated, not permanently removed)',
  }));
  const overlay = ctx.overlays[ctx.overlays.length - 1];
  assert.ok(overlay && overlay.innerHTML, '_confirm() never wrote markup into the overlay.');
  const hits = PHYSICAL_RE.filter(({ re }) => re.test(overlay.innerHTML));
  assert.deepStrictEqual(
    hits.map((h) => h.use), [],
    `_confirm()'s rendered markup contains a physical direction property. Use: ${hits.map((h) => h.use).join(', ')}\n` +
    overlay.innerHTML
  );
  // Resolve it so the dangling keydown listener from this case doesn't leak
  // into whichever case Node happens to run next in the same process.
  ctx.els['ret-confirm-cancel'].fire('click');
  console.log('PASS: the rendered confirm-dialog markup contains no left:/right: physical properties');
}

// ═════════════════════════════════════════════════════════════════════════
// 6 — zero bare confirm( calls remain in subsystem-retail.js
// ═════════════════════════════════════════════════════════════════════════

function stripComments(src) {
  return src.replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, ' '))
    .replace(/(^|[^:])\/\/.*$/gm, '$1');
}

function testNoBareConfirmCallsRemain() {
  const clean = stripComments(SOURCE);
  // Matches a bare `confirm(` NOT immediately preceded by `_` (so `_confirm(`
  // and `this._confirm(` are excluded) and not preceded by a letter (so the
  // word inside a longer identifier can't false-positive).
  const re = /(?<![\w_])confirm\(/g;
  const hits = [...clean.matchAll(re)];
  assert.deepStrictEqual(
    hits.map((m) => clean.slice(0, m.index).split('\n').length),
    [],
    `Found ${hits.length} native confirm( call(s) still in subsystem-retail.js.`
  );
  console.log('PASS: subsystem-retail.js contains zero native confirm( calls');
}

// ─────────────────────────────────────────────────────────────────────────

const CASES = [
  testResolvesTrueOnConfirmClick,
  testResolvesFalseOnCancelClick,
  testResolvesFalseOnEscape,
  testOverlayClickCancels,
  testNoPhysicalPropertiesInRenderedMarkup,
  testNoBareConfirmCallsRemain,
];

// Mutation harness -- same shape as retail_shell_chrome_test.js.
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

function loadMutatedRetailSystem(mutatedSource) {
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
  const overlays = [];
  const docHandlers = Object.create(null);

  sandbox.document = {
    activeElement: trigger,
    getElementById: getEl,
    createElement: () => makeStubElement(),
    addEventListener(type, fn) { (docHandlers[type] || (docHandlers[type] = [])).push({ fn }); },
    removeEventListener(type, fn) {
      if (!docHandlers[type]) return;
      docHandlers[type] = docHandlers[type].filter((h) => h.fn !== fn);
    },
    body: { appendChild(el) { overlays.push(el); } },
  };

  vm.createContext(sandbox);
  vm.runInContext(mutatedSource, sandbox, { filename: SRC_PATH });
  return {
    RetailSystem: sandbox.RetailSystem, els, trigger, overlays,
    fireDocKeydown(evt) { (docHandlers.keydown || []).forEach((h) => h.fn(evt)); },
  };
}

async function testEscapeMutationIsCaught() {
  const mutated = mutate(SOURCE, [[
    `        if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); finish(false); return; }`,
    `        if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); return; }`,
  ]]);

  const msg = await provesMutation(
    'the "_confirm() resolves false on Escape" check survived a mutant that drops the Escape branch\'s finish(false) call',
    async () => {
      const ctx = loadMutatedRetailSystem(mutated);
      const resultPromise = ctx.RetailSystem._confirm(baseOpts());
      ctx.fireDocKeydown({ key: 'Escape', preventDefault() {}, stopPropagation() {} });
      const result = await withTimeout(resultPromise, 200, 'Escape (mutant)');
      assert.strictEqual(result, false, 'Escape did not resolve false.');
    }
  );
  console.log('PASS: ' + msg);
}

async function main() {
  let failed = 0;
  for (const fn of [...CASES, testEscapeMutationIsCaught]) {
    try {
      await fn();
    } catch (err) {
      failed += 1;
      console.error('  FAIL ' + fn.name);
      console.error('       ' + (err && err.message ? err.message : err));
    }
  }
  if (failed) {
    console.error(`FAIL: retail_confirm_modal_test.js — ${failed} of ${CASES.length + 1} case(s) failed`);
    process.exitCode = 1;
  } else {
    console.log(`PASS: retail_confirm_modal_test.js — ${CASES.length + 1} case(s)`);
  }
}

main();
