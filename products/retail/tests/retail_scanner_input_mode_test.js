/**
 * Regression test for: Scanner Settings "Input Mode" (Auto Detect vs Keyboard
 * HID) was saved to localStorage but never read by the actual scan-detection
 * logic (_onScannerKey / _dispatchScan) -- both options behaved identically.
 *
 * Loads the REAL products/retail/frontend/subsystem-retail.js source (via
 * Node's vm module, with minimal DOM/browser mocks) and drives its actual
 * _onScannerKey() handler with a simulated keystroke burst, rather than
 * re-implementing the detection algorithm here -- so this test exercises the
 * exact code path a real browser would run.
 *
 * Scenario: a barcode burst with 120ms gaps between keystrokes (moderate
 * jitter -- slower than a crisp scanner, faster than a human typist) and the
 * default 50ms timeoutMs.
 *   - "auto" mode: 120ms > 50ms resets the buffer on every keystroke, so the
 *     accumulated code never reaches minLength by the time Enter arrives.
 *     No dispatch.
 *   - "keyboard" mode: the fix widens the per-keystroke reset window to 3x
 *     timeoutMs (150ms) when the admin has explicitly selected Keyboard HID,
 *     so 120ms gaps never trigger a reset. The full code survives to Enter
 *     and a scan is dispatched.
 *
 * Before the fix, both modes behaved like "auto" (inputMode was never read),
 * so this test would have failed on the "keyboard" assertion.
 *
 * Run: node products/retail/tests/retail_scanner_input_mode_test.js
 */
'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const SRC_PATH = path.join(__dirname, '..', 'frontend', 'subsystem-retail.js');
const source = fs.readFileSync(SRC_PATH, 'utf8');

function loadRetailSystem() {
  const sandbox = {};
  sandbox.window = sandbox; // browser-style global aliasing (window === global)
  sandbox.navigator = { userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)' }; // non-Android => desktop scanner path
  sandbox.document = {
    activeElement: null, // focus outside any input field -> auto-detect path is eligible
    getElementById: () => null,
    addEventListener: () => {},
  };
  sandbox.localStorage = {
    getItem: () => null,
    setItem: () => {},
  };
  sandbox.performance = { now: () => sandbox.__clock };
  // Start well above 0: _scan.lastAt/firstAt default to 0, and in a real
  // session performance.now() is already large by the time any key is
  // pressed, so the very first keystroke's gap (now - 0) is always huge and
  // reliably triggers the initial buffer reset. Starting the mock clock at 0
  // would make that first gap artificially small instead.
  sandbox.__clock = 10000;
  sandbox.console = console;

  vm.createContext(sandbox);
  vm.runInContext(source, sandbox, { filename: SRC_PATH });

  const RetailSystem = sandbox.RetailSystem;
  assert.ok(RetailSystem, 'RetailSystem failed to load from subsystem-retail.js');

  // Isolate the test to the scan-detection gating logic itself: stub out
  // SubsystemApp (screen-active guard) and _dispatchScan (record calls
  // instead of running the real routing/DOM/audio side effects).
  sandbox.SubsystemApp = { active: 'retail', showToast: () => {} };
  const dispatched = [];
  RetailSystem._dispatchScan = (code) => { dispatched.push(code); };

  return { RetailSystem, sandbox, dispatched };
}

// Feeds a barcode + Enter as a sequence of keydown events with a fixed gap
// (ms) between each keystroke, advancing the mocked clock accordingly.
function simulateScan(RetailSystem, sandbox, code, gapMs) {
  const press = (key) => {
    sandbox.__clock += gapMs;
    RetailSystem._onScannerKey({ key, preventDefault: () => {}, stopPropagation: () => {} });
  };
  for (const ch of code) press(ch);
  press('Enter');
}

function run() {
  const baseCfg = {
    enabled: true,
    timeoutMs: 50,
    minLength: 3,
    prefix: '',
    suffix: '',
    sound: false,
  };
  const GAP_MS = 120; // > timeoutMs(50), <= 3*timeoutMs(150)
  const CODE = '123456';

  // -- 'auto' mode: 120ms gaps exceed the 50ms window on every keystroke,
  //    so the buffer never accumulates -> no dispatch.
  {
    const { RetailSystem, sandbox, dispatched } = loadRetailSystem();
    RetailSystem.scannerCfg = () => Object.assign({}, baseCfg, { inputMode: 'auto' });
    simulateScan(RetailSystem, sandbox, CODE, GAP_MS);
    assert.deepStrictEqual(
      dispatched, [],
      `expected no dispatch in 'auto' mode with ${GAP_MS}ms gaps, got: ${JSON.stringify(dispatched)}`
    );
    console.log("PASS: 'auto' mode does not dispatch a scan with 120ms inter-key gaps (unchanged behavior)");
  }

  // -- 'keyboard' mode: same timing, but the fix triples the per-keystroke
  //    reset window (150ms) for this explicit mode, so the full code survives
  //    to Enter and IS dispatched. This is the assertion that fails on the
  //    pre-fix code (inputMode was ignored, so this would equal the 'auto' case).
  {
    const { RetailSystem, sandbox, dispatched } = loadRetailSystem();
    RetailSystem.scannerCfg = () => Object.assign({}, baseCfg, { inputMode: 'keyboard' });
    simulateScan(RetailSystem, sandbox, CODE, GAP_MS);
    assert.deepStrictEqual(
      dispatched, [CODE],
      `expected a dispatch of '${CODE}' in 'keyboard' mode with ${GAP_MS}ms gaps, got: ${JSON.stringify(dispatched)}`
    );
    console.log("PASS: 'keyboard' mode DOES dispatch the same scan (Input Mode now has a real effect)");
  }

  console.log('\nAll scanner input-mode tests passed.');
}

try {
  run();
  process.exit(0);
} catch (err) {
  console.error('FAIL:', err.message);
  process.exit(1);
}
