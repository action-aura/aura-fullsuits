/**
 * retail_pos_keyboard_shortcuts_test.js — the POS keyboard-shortcut layer
 * added so the till is usable at "restaurant/fast-transaction" speed
 * (Enter to charge, "3*" quantity multiplier, X for exact-cash, "/" to
 * search, Delete to void a line, Escape to cancel) without breaking the
 * barcode scanner, which is a keyboard wedge sharing the same document-level
 * keydown stream.
 *
 * Loads the REAL products/retail/frontend/subsystem-retail.js source (via
 * Node's vm module, the same technique retail_scanner_input_mode_test.js and
 * retail_surface_pos_test.js already use), and drives its ACTUAL
 * _onScannerKey()/_onPOSShortcut() handlers rather than re-implementing the
 * key-handling logic here — so this exercises the exact code path a real
 * browser would run.
 *
 * THE SCANNER-SAFETY MODEL BEING TESTED
 *
 * _onScannerKey (the wedge listener) is registered on document with
 * useCapture=true; _onPOSShortcut (this layer) is registered on document
 * with the default bubble phase. For any single keydown dispatched at some
 * descendant of document, capture ALWAYS runs before bubble — that ordering
 * is structural, not a race. When _onScannerKey completes a genuine scan it
 * calls stopPropagation(), which aborts the event before it ever reaches the
 * bubble-phase listener, so a completed scan can never also be read as a
 * shortcut. fireKeydown() below reproduces exactly that capture-then-bubble
 * relationship (including honouring stopPropagation) without a real DOM, so
 * this test is checking the real interaction, not a paraphrase of it.
 *
 * ISOLATION CHOICE, stated rather than hidden: each test drives a REAL cart
 * (this._cart / this._currentTotals) through the REAL _addToCart,
 * _consumePendingQty, _removeLine, and _checkout (down to the network call,
 * which is mocked and its payload captured — the same technique
 * retail_checkout_error_toast_test.js and others already use). What is
 * stubbed is only _checkout's OWN downstream rendering — _clearCart,
 * _applyLocalStockDecrement, _showReceipt — because those belong to the
 * receipt/rendering pipeline other test files already cover, and pulling
 * them in for real here would require mocking a whole product-modal/receipt
 * DOM that has nothing to do with keyboard shortcuts.
 *
 * Run: node products/retail/tests/retail_pos_keyboard_shortcuts_test.js
 */
'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const SRC_PATH = path.join(__dirname, '..', 'frontend', 'subsystem-retail.js');
const source = fs.readFileSync(SRC_PATH, 'utf8');

function makeStubElement(overrides) {
  return Object.assign({
    id: '', value: '', disabled: false, textContent: '', style: {},
    tagName: 'DIV', isContentEditable: false,
    focus() {},
  }, overrides);
}

/**
 * Loads a fresh RetailSystem from the real source. `activeEl` seeds
 * document.activeElement (the "is the cashier typing somewhere" signal both
 * _onScannerKey and _onPOSShortcut read). Element ids are created lazily and
 * cached, exactly like retail_surface_pos_test.js's own harness, so the same
 * #pos-tendered element instance is read back after a shortcut writes to it.
 */
function loadRetailSystem(opts) {
  const options = opts || {};
  const sandbox = {};
  sandbox.window = sandbox;
  sandbox.navigator = { userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)' }; // desktop scanner path
  sandbox.t = (s) => s; // i18n.js's global shorthand
  sandbox.console = console;
  sandbox.localStorage = { getItem: () => null, setItem: () => {} };
  sandbox.performance = { now: () => sandbox.__clock };
  sandbox.__clock = 10000; // see retail_scanner_input_mode_test.js for why not 0

  const els = Object.create(null);
  const state = { activeEl: options.activeEl || null };
  const getEl = (id) => {
    if (!els[id]) els[id] = makeStubElement({ id });
    return els[id];
  };

  sandbox.document = {
    get activeElement() { return state.activeEl; },
    getElementById: getEl,
    querySelectorAll: () => [],
    addEventListener: () => {},
  };

  vm.createContext(sandbox);
  vm.runInContext(source, sandbox, { filename: SRC_PATH });
  const RetailSystem = sandbox.RetailSystem;
  assert.ok(RetailSystem, 'RetailSystem failed to load from subsystem-retail.js');

  sandbox.SubsystemApp = { active: 'retail', showToast: () => {} };
  RetailSystem._section = 'pos';
  RetailSystem._cart = [];
  RetailSystem._currentTotals = { subtotal: 0, discount: 0, tax: 0, total: 0 };
  RetailSystem._paymentMethod = 'cash';
  RetailSystem._pendingQty = null;
  RetailSystem._qtyKeyBuffer = '';
  RetailSystem._qtyKeyLastAt = 0;
  RetailSystem._products = [
    { id: 'p1', name: 'Widget', sell_price: 10, tax_rate: 0, total_stock: 999 },
    { id: 'p2', name: 'Gadget', sell_price: 5,  tax_rate: 0, total_stock: 999 },
  ];

  // Isolate the receipt/rendering pipeline (see file header) -- the cart and
  // checkout logic itself stays real.
  RetailSystem._clearCart = () => {};
  RetailSystem._applyLocalStockDecrement = () => {};
  RetailSystem._showReceipt = () => {};
  RetailSystem._renderCart = () => {};

  const posted = [];
  const dispatchedScans = [];
  RetailSystem._post = async (url, body) => {
    posted.push({ url, body });
    return { status: 'success', data: { lines: [] } };
  };
  RetailSystem._dispatchScan = (code) => { dispatchedScans.push(code); };

  return { RetailSystem, sandbox, els, state, posted, dispatchedScans };
}

/**
 * Fires one keydown through BOTH real handlers in the same order the
 * browser would: capture phase (_onScannerKey) first, bubble phase
 * (_onPOSShortcut) only if capture did not stopPropagation(). This is the
 * exact mechanism that keeps a completed scan from ever reaching the
 * shortcut layer -- see the file header.
 */
function fireKeydown(RetailSystem, key, extra) {
  let stopped = false;
  const evt = Object.assign({
    key,
    ctrlKey: false, metaKey: false, altKey: false,
    preventDefault() {},
    stopPropagation() { stopped = true; },
  }, extra || {});
  RetailSystem._onScannerKey(evt);
  if (!stopped) RetailSystem._onPOSShortcut(evt);
  return evt;
}

// Advances the mocked scanner clock by `gapMs` and feeds `code` + Enter as a
// keystroke burst through fireKeydown -- a real barcode scan, keystroke by
// keystroke, through BOTH handlers exactly as described above.
function simulateScan(ctx, code, gapMs) {
  const press = (key) => { ctx.sandbox.__clock += gapMs; fireKeydown(ctx.RetailSystem, key); };
  for (const ch of code) press(ch);
  press('Enter');
}

// A single DELIBERATE keystroke, spaced a realistic, unhurried gap (well
// over the scanner's own default 50ms burst window) from whatever came
// before it -- so the wedge listener's own reset-window logic naturally
// treats it as the start of its own thing, not a burst continuation, the
// same way a real cashier pressing one shortcut key at a time would. Tests
// that fire more than one deliberate key in a row (the "3*" multiplier,
// Escape) use this rather than fireKeydown directly; a run of zero-gap
// keydowns is not a real typing speed for anyone, human or scanner.
const HUMAN_GAP_MS = 400;
function pressKey(ctx, key) {
  ctx.sandbox.__clock += HUMAN_GAP_MS;
  return fireKeydown(ctx.RetailSystem, key);
}

async function run() {
  // ── 1. Enter charges a non-empty cart ────────────────────────────────────
  {
    const ctx = loadRetailSystem({ activeEl: null }); // focus nowhere -- not a text field
    ctx.RetailSystem._cart = [{ product_id: 'p1', name: 'Widget', quantity: 1, unit_price: 10, tax_rate: 0, line_total: 10, max_stock: 999 }];
    ctx.RetailSystem._currentTotals = { subtotal: 10, discount: 0, tax: 0, total: 10 };

    pressKey(ctx, 'Enter');
    await new Promise((r) => setImmediate(r)); // let _checkout's await settle

    assert.strictEqual(ctx.posted.length, 1, 'Enter with a non-empty cart and focus outside any text field did not charge the sale.');
    assert.strictEqual(ctx.posted[0].body.total, 10);
    console.log('PASS: Enter charges a non-empty cart when focus is not in a text field');
  }

  // ── 2. Enter does NOT charge while focus is in a text input ─────────────
  {
    // #pos-tendered, not #pos-search: the field the cashier is plainly mid-
    // keystroke in, entering a cash amount -- the least ambiguous case where
    // a stray Enter must not fire a charge underneath their fingers.
    const ctx = loadRetailSystem({ activeEl: { tagName: 'INPUT', id: 'pos-tendered', isContentEditable: false } });
    ctx.RetailSystem._cart = [{ product_id: 'p1', name: 'Widget', quantity: 1, unit_price: 10, tax_rate: 0, line_total: 10, max_stock: 999 }];
    ctx.RetailSystem._currentTotals = { subtotal: 10, discount: 0, tax: 0, total: 10 };

    pressKey(ctx, 'Enter');
    await new Promise((r) => setImmediate(r));

    assert.strictEqual(ctx.posted.length, 0, 'Enter charged the sale even though focus was in a text input (#pos-tendered).');
    console.log('PASS: Enter does NOT charge while focus is in a text input');
  }

  // ── 3. The quantity multiplier applies to the next item added, then resets ──
  {
    const ctx = loadRetailSystem({ activeEl: null });
    ctx.RetailSystem._cart = [];

    pressKey(ctx, '3');
    pressKey(ctx, '*');
    assert.strictEqual(ctx.RetailSystem._pendingQty, 3, '"3*" did not arm a pending quantity of 3.');

    ctx.RetailSystem._addToCart('p1');
    const line1 = ctx.RetailSystem._cart.find((i) => i.product_id === 'p1');
    assert.ok(line1, 'p1 was not added to the cart.');
    assert.strictEqual(line1.quantity, 3, `"3*" then adding p1 should set its quantity to 3, got ${line1.quantity}.`);
    assert.strictEqual(ctx.RetailSystem._pendingQty, null, 'The multiplier was not cleared after being consumed by _addToCart.');

    // The important half: a SECOND, unrelated add with NO fresh multiplier
    // must not inherit the one already spent on p1.
    ctx.RetailSystem._addToCart('p2');
    const line2 = ctx.RetailSystem._cart.find((i) => i.product_id === 'p2');
    assert.ok(line2, 'p2 was not added to the cart.');
    assert.strictEqual(line2.quantity, 1, `A stale multiplier leaked into the next unrelated item -- got quantity ${line2.quantity}, expected 1.`);

    console.log('PASS: "3*" sets the next item\'s quantity to 3, then resets so it never applies to a later item');
  }

  // ── 4. Exact-cash pays the exact total ───────────────────────────────────
  {
    const ctx = loadRetailSystem({ activeEl: null });
    ctx.RetailSystem._cart = [{ product_id: 'p1', name: 'Widget', quantity: 3, unit_price: 10, tax_rate: 0, line_total: 30, max_stock: 999 }];
    ctx.RetailSystem._currentTotals = { subtotal: 30, discount: 0, tax: 0, total: 30 };

    pressKey(ctx, 'x');
    await new Promise((r) => setImmediate(r));

    assert.strictEqual(ctx.posted.length, 1, 'The exact-cash key (X) did not charge the sale.');
    const body = ctx.posted[0].body;
    assert.strictEqual(body.payment_method, 'cash', `Exact-cash did not charge as cash, got '${body.payment_method}'.`);
    assert.strictEqual(body.amount_paid, 30, `Exact-cash paid ${body.amount_paid}, not the exact total of 30.`);
    assert.strictEqual(ctx.els['pos-tendered'].value, '30', 'Exact-cash did not tender the exact total into #pos-tendered.');
    console.log('PASS: exact-cash (X) tenders and charges the exact total, in cash');
  }

  // ── 5. A scan while the shortcut layer is active still scans, and does ──
  //      NOT trigger a charge -- the claim that protects the product's
  //      most-used function.
  {
    const ctx = loadRetailSystem({ activeEl: null });
    // Cart is non-empty and focus is exactly the state where Enter WOULD
    // charge on its own (see test 1) -- the scan must still win.
    ctx.RetailSystem._cart = [{ product_id: 'p1', name: 'Widget', quantity: 1, unit_price: 10, tax_rate: 0, line_total: 10, max_stock: 999 }];
    ctx.RetailSystem._currentTotals = { subtotal: 10, discount: 0, tax: 0, total: 10 };
    ctx.RetailSystem.scannerCfg = () => ({ enabled: true, inputMode: 'auto', timeoutMs: 50, minLength: 3, prefix: '', suffix: '', sound: false });

    simulateScan(ctx, '4901234567894', 10); // 10ms gaps, well inside the 50ms window -> a genuine fast burst

    assert.deepStrictEqual(ctx.dispatchedScans, ['4901234567894'], 'The barcode burst was not recognised as a scan while the shortcut layer was active.');
    assert.strictEqual(ctx.posted.length, 0, 'A completed scan also charged the sale -- the shortcut layer read the scan\'s own Enter as "charge".');
    assert.strictEqual(ctx.RetailSystem._pendingQty, null, 'Digits from the scan burst were misread as a quantity-multiplier digit run.');
    assert.strictEqual(ctx.RetailSystem._qtyKeyBuffer, '', 'Digits from the scan burst were left sitting in the quantity-multiplier buffer.');
    console.log('PASS: a scan while the shortcut layer is active still registers as a scan and does not charge');
  }

  // ── 6. Escape clears the pending entry state ─────────────────────────────
  {
    const ctx = loadRetailSystem({ activeEl: null });
    pressKey(ctx, '4');
    pressKey(ctx, '2');
    assert.strictEqual(ctx.RetailSystem._qtyKeyBuffer, '42', 'Digit keys did not accumulate into the quantity buffer.');

    pressKey(ctx, 'Escape');
    assert.strictEqual(ctx.RetailSystem._qtyKeyBuffer, '', 'Escape did not clear the in-progress digit buffer.');

    pressKey(ctx, '*');
    assert.strictEqual(ctx.RetailSystem._pendingQty, null, 'Escape did not cancel the pending entry -- "*" after Escape still armed a multiplier.');

    console.log('PASS: Escape cancels the pending quantity-multiplier entry state');
  }

  // ── Bonus: Delete voids the most recently touched line (feature exists,
  //    not one of the six required proofs above, but shipped and worth
  //    covering) ─────────────────────────────────────────────────────────
  {
    const ctx = loadRetailSystem({ activeEl: null });
    ctx.RetailSystem._cart = [
      { product_id: 'p1', name: 'Widget', quantity: 1, unit_price: 10, tax_rate: 0, line_total: 10, max_stock: 999 },
      { product_id: 'p2', name: 'Gadget', quantity: 1, unit_price: 5,  tax_rate: 0, line_total: 5,  max_stock: 999 },
    ];
    ctx.RetailSystem._lastAddedId = 'p2';

    pressKey(ctx, 'Delete');

    assert.strictEqual(ctx.RetailSystem._cart.length, 1, 'Delete did not remove a line.');
    assert.strictEqual(ctx.RetailSystem._cart[0].product_id, 'p1', 'Delete removed the wrong line -- it should void the most recently touched one (p2).');
    console.log('PASS: Delete voids the most recently touched cart line');
  }

  console.log('\nAll POS keyboard-shortcut tests passed.');
}

run().then(() => process.exit(0)).catch((err) => {
  console.error('FAIL:', err.message);
  process.exit(1);
});
