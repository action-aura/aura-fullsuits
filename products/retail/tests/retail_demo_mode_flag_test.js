/**
 * app-shell.js's _demoModeFlag(): the one guarded read of the demo flag that
 * four boot/auth paths share. sessionStorage can THROW (blocked site data),
 * and an unguarded read there aborted init.
 *
 * Why this exists: the first cut of the guard was produced by a blanket
 * search-and-replace that also rewrote the helper's own body into
 * `return _demoModeFlag()`. The recursion's RangeError was swallowed by the
 * helper's catch, so it silently answered "not demo mode" for everyone -- and
 * the blocked-storage e2e stayed green, because it never turns demo mode ON.
 * So this test pins BOTH directions: flag set -> true, and the failure cases
 * -> false.
 *
 * Run: node products/retail/tests/retail_demo_mode_flag_test.js
 */
'use strict';
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const SRC = fs.readFileSync(path.join(__dirname, '..', 'frontend', 'app-shell.js'), 'utf8');
const m = SRC.match(/function _demoModeFlag\(\) \{[\s\S]*?\n\}/);
assert.ok(m, 'app-shell.js no longer defines function _demoModeFlag() -- this test cannot find what it guards');

function flagWith(sessionStorageGetter) {
  const sandbox = {};
  Object.defineProperty(sandbox, 'sessionStorage', { get: sessionStorageGetter });
  vm.createContext(sandbox);
  vm.runInContext(m[0] + '\nthis.result = _demoModeFlag();', sandbox);
  return sandbox.result;
}

const store = (v) => () => ({ getItem: (k) => (k === 'demo_mode' ? v : null) });
const CASES = [
  ['demo flag set', store('true'), true],
  ['flag absent', store(null), false],
  ['flag some other value', store('false'), false],
  ['sessionStorage getter throws', () => { throw new Error('The operation is insecure.'); }, false],
  ['getItem throws', () => ({ getItem() { throw new Error('SecurityError'); } }), false],
];

let failed = 0;
for (const [name, getter, want] of CASES) {
  try {
    assert.strictEqual(flagWith(getter), want);
    console.log(`PASS: ${name} -> ${want}`);
  } catch (e) {
    failed++;
    console.log(`FAIL: ${name}: ${e.message}`);
  }
}
if (failed) {
  console.log(`FAIL: retail_demo_mode_flag_test.js — ${failed} of ${CASES.length} checks failed`);
  process.exit(1);
}
console.log(`PASS: retail_demo_mode_flag_test.js — ${CASES.length} checks`);
