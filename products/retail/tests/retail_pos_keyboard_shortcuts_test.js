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

// AURA_SR_SRC lets a mutation proof point this same test at a deliberately
// broken copy of the source (see the till keyboard model's mutation proofs);
// unset, it is always the real file.
const SRC_PATH = process.env.AURA_SR_SRC || path.join(__dirname, '..', 'frontend', 'subsystem-retail.js');
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
  sandbox.setTimeout = setTimeout; sandbox.clearTimeout = clearTimeout; // _filterPOS debounces its search
  sandbox.localStorage = { getItem: () => null, setItem: () => {} };
  sandbox.performance = { now: () => sandbox.__clock };
  sandbox.__clock = 10000; // see retail_scanner_input_mode_test.js for why not 0

  const els = Object.create(null);
  const state = { activeEl: options.activeEl || null, focusLog: [] };
  const INPUT_IDS = ['pos-search', 'pos-tendered', 'pos-disc', 'pos-customer'];
  const getEl = (id) => {
    if (!els[id]) {
      els[id] = makeStubElement({
        id,
        tagName: INPUT_IDS.indexOf(id) !== -1 ? (id === 'pos-customer' ? 'SELECT' : 'INPUT') : 'DIV',
        // Records WHO took focus and moves activeElement, so a test can tell
        // "Enter went to the tendered field" from "nothing happened".
        focus() { state.focusLog.push(id); state.activeEl = els[id]; },
        select() { state.selectLog = (state.selectLog || []).concat(id); },
      });
    }
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

  return { RetailSystem, sandbox, els, state, posted, dispatchedScans, getEl };
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
    defaultPrevented: false,
    preventDefault() { this.defaultPrevented = true; },
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
function pressKey(ctx, key, extra) {
  ctx.sandbox.__clock += HUMAN_GAP_MS;
  return fireKeydown(ctx.RetailSystem, key, extra);
}

// A PHYSICAL key press: `code` is what the keyboard reports, `key` is whatever
// character the active layout made of it (pass an Arabic letter to prove the
// till does not care).
function pressCode(ctx, code, key, extra) {
  return pressKey(ctx, key == null ? '' : key, Object.assign({ code }, extra || {}));
}

const LINE = (id, name, qty, price) => ({ product_id: id, name, quantity: qty, unit_price: price, tax_rate: 0, line_total: qty * price, max_stock: 999 });
function seedCart(ctx, lines) {
  ctx.RetailSystem._cart = lines;
  const total = lines.reduce((a, l) => a + l.line_total, 0);
  ctx.RetailSystem._currentTotals = { subtotal: total, discount: 0, tax: 0, total };
}
const qtyOf = (ctx, id) => { const l = ctx.RetailSystem._cart.find((x) => x.product_id === id); return l ? l.quantity : null; };
const settle = () => new Promise((r) => setImmediate(r));

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

  // â”€â”€ 2. Enter does NOT charge from a text field that is not the tender field â”€â”€
  // WHAT THIS CAN NO LONGER CATCH (and why that is intended): this test used
  // to pin "Enter in #pos-tendered does NOT charge". The till keyboard model
  // reverses exactly that on the owner's request -- type the amount, press
  // Enter, sale done -- and test 2b below pins the new behaviour in both
  // directions. What stays pinned here is the half that is still true: a stray
  // Enter in any OTHER field (the discount box, a dialog's search box) must
  // not fire a charge underneath the cashier's fingers.
  {
    for (const id of ['pos-disc', 'cp-search']) {
      const ctx = loadRetailSystem({ activeEl: { tagName: 'INPUT', id, isContentEditable: false } });
      seedCart(ctx, [LINE('p1', 'Widget', 1, 10)]);
      pressKey(ctx, 'Enter');
      await settle();
      assert.strictEqual(ctx.posted.length, 0, `Enter charged the sale while focus was in #${id}.`);
    }
    console.log('PASS: Enter does NOT charge from the discount box or any other non-tender text field');
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



  // ═══ TILL KEYBOARD MODEL (P1c) ═══════════════════════════════════════════
  // Every row of the plan's key table, driven through the REAL handlers with
  // PHYSICAL key codes (an Arabic-layout event where a letter is involved).

  // ── 2b. Enter in the tender field charges; exact when blank; refuses short ──
  {
    const ctx = loadRetailSystem({});
    seedCart(ctx, [LINE('p1', 'Widget', 1, 10)]);
    ctx.state.activeEl = ctx.getEl('pos-tendered');
    pressCode(ctx, 'Enter', 'Enter');
    await settle();
    assert.strictEqual(ctx.posted.length, 1, 'Enter in #pos-tendered did not charge the sale.');
    assert.strictEqual(ctx.posted[0].body.amount_paid, 10, 'A blank tendered field must charge the exact amount.');
    assert.strictEqual(ctx.posted[0].body.payment_method, 'cash');

    const short = loadRetailSystem({});
    seedCart(short, [LINE('p1', 'Widget', 1, 10)]);
    short.state.activeEl = short.getEl('pos-tendered');
    short.getEl('pos-tendered').value = '5';
    pressCode(short, 'Enter', 'Enter');
    await settle();
    assert.strictEqual(short.posted.length, 0, 'Enter charged a cash sale whose tendered amount (5) does not cover the total (10).');
    console.log('PASS: Enter in Cash Tendered charges (blank = exact) and refuses a short amount');
  }

  // ── 2c. Enter in an EMPTY search with a cart = "go to pay", never a charge ──
  {
    const ctx = loadRetailSystem({});
    seedCart(ctx, [LINE('p1', 'Widget', 1, 10)]);
    ctx.state.activeEl = ctx.getEl('pos-search');
    pressCode(ctx, 'Enter', 'Enter');
    await settle();
    assert.strictEqual(ctx.posted.length, 0, 'Enter in an empty search charged the sale instead of going to pay.');
    assert.ok(ctx.state.focusLog.includes('pos-tendered'), 'Enter in an empty search did not move focus to Cash Tendered.');
    assert.ok((ctx.state.selectLog || []).includes('pos-tendered'), 'The tendered field was not selected on arrival.');

    // allow-half: with an EMPTY cart the same key does nothing at all.
    const empty = loadRetailSystem({});
    empty.state.activeEl = empty.getEl('pos-search');
    pressCode(empty, 'Enter', 'Enter');
    assert.ok(!empty.state.focusLog.includes('pos-tendered'), 'Go-to-pay fired with an empty cart.');
    console.log('PASS: Enter in an empty search goes to pay (focus -> Cash Tendered), charges nothing');
  }

  // ── 2d. F10 and Ctrl+Enter charge now, from the search box ────────────────
  {
    for (const [label, press] of [
      ['F10', (c) => pressCode(c, 'F10', 'F10')],
      ['Ctrl+Enter', (c) => pressCode(c, 'Enter', 'Enter', { ctrlKey: true })],
    ]) {
      const ctx = loadRetailSystem({});
      seedCart(ctx, [LINE('p1', 'Widget', 1, 10)]);
      ctx.state.activeEl = ctx.getEl('pos-search');
      const evt = press(ctx);
      await settle();
      assert.strictEqual(ctx.posted.length, 1, `${label} did not charge the sale.`);
      assert.strictEqual(evt.defaultPrevented, true, `${label} was not preventDefault()ed (a fallback browser would act on it).`);
    }
    console.log('PASS: F10 and Ctrl+Enter charge the sale with focus in the search box');
  }

  // ── 2e. Enter on a focused BUTTON presses that button only -- never charges ──
  {
    for (const kind of ['pos-qty-btn', 'pos-card', 'pos-pay-btn', 'pos-checkout-btn']) {
      const btn = { tagName: 'BUTTON', id: '', className: kind, isContentEditable: false, getAttribute: () => null };
      const ctx = loadRetailSystem({ activeEl: btn });
      seedCart(ctx, [LINE('p1', 'Widget', 1, 10)]);
      const evt = pressCode(ctx, 'Enter', 'Enter');
      await settle();
      assert.strictEqual(ctx.posted.length, 0, `Enter on a focused .${kind} button charged the sale.`);
      assert.strictEqual(evt.defaultPrevented, false, `Enter on .${kind} was preventDefault()ed, which would stop the button's own native press.`);
    }
    console.log('PASS: Enter on a focused qty / tile / tender / charge button never reaches the charge path');
  }

  // ── 2f. Enter in the discount box returns to the search box ────────────────
  {
    const ctx = loadRetailSystem({});
    seedCart(ctx, [LINE('p1', 'Widget', 1, 10)]);
    ctx.state.activeEl = ctx.getEl('pos-disc');
    pressCode(ctx, 'Enter', 'Enter');
    await settle();
    assert.strictEqual(ctx.posted.length, 0);
    assert.ok(ctx.state.focusLog.includes('pos-search'), 'Enter in the discount box did not return focus to the search box.');
    console.log('PASS: Enter in the discount box returns to the search box');
  }

  // ── 3b. Layout independence: an ARABIC layout still drives the same keys ──
  {
    // X: physical KeyX, but the layout made it an Arabic letter.
    const ctx = loadRetailSystem({ activeEl: null });
    seedCart(ctx, [LINE('p1', 'Widget', 3, 10)]);
    pressCode(ctx, 'KeyX', 'ء');
    await settle();
    assert.strictEqual(ctx.posted.length, 1, 'X (physical KeyX) did not exact-charge on an Arabic layout.');
    assert.strictEqual(ctx.posted[0].body.amount_paid, 30);

    // "/": physical Slash -> focus the search box.
    const s2 = loadRetailSystem({ activeEl: null });
    pressCode(s2, 'Slash', 'ظ');
    assert.ok(s2.state.focusLog.includes('pos-search'), '"/" (physical Slash) did not focus the search box on an Arabic layout.');

    // "3*": Arabic-Indic digit glyph + Shift+Digit8.
    const s3 = loadRetailSystem({ activeEl: null });
    pressCode(s3, 'Digit3', '٣');
    pressCode(s3, 'Digit8', 'ة', { shiftKey: true });
    assert.strictEqual(s3.RetailSystem._pendingQty, 3, '"3*" typed on an Arabic layout did not arm a quantity of 3.');

    // And exact-cash must NOT fire from a different physical key that merely
    // produces the same character -- the match is on the key, not the glyph.
    const s4 = loadRetailSystem({ activeEl: null });
    seedCart(s4, [LINE('p1', 'Widget', 1, 10)]);
    pressCode(s4, 'KeyZ', 'x');
    await settle();
    assert.strictEqual(s4.posted.length, 0, 'A layout that put an "x" glyph on KeyZ fired exact-cash: matching is on the glyph, not the physical key.');
    console.log('PASS: X, "/" and "N*" follow the physical key (e.code), not the layout\'s character');
  }

  // ── 4b. A scan burst with focus in an EMPTY search is a scan, never go-to-pay ──
  {
    const ctx = loadRetailSystem({});
    seedCart(ctx, [LINE('p1', 'Widget', 1, 10)]);
    ctx.state.activeEl = ctx.getEl('pos-search');
    ctx.RetailSystem.scannerCfg = () => ({ enabled: true, inputMode: 'auto', timeoutMs: 50, minLength: 3, prefix: '', suffix: '', sound: false });
    simulateScan(ctx, '4901234567894', 10);
    await settle();
    assert.deepStrictEqual(ctx.dispatchedScans, ['4901234567894'], 'The burst was not recognised as a scan.');
    assert.strictEqual(ctx.posted.length, 0, 'The scan\'s Enter charged the sale.');
    assert.ok(!ctx.state.focusLog.includes('pos-tendered'), 'The scan\'s Enter was read as "go to pay" (focus moved to Cash Tendered).');
    console.log('PASS: a scan burst in an empty search never triggers go-to-pay or a charge');
  }

  // ── 4c. Scan characters come from the PHYSICAL key on a non-Latin layout ──
  {
    const ctx = loadRetailSystem({});
    ctx.state.activeEl = ctx.getEl('pos-search');
    ctx.RetailSystem.scannerCfg = () => ({ enabled: true, inputMode: 'auto', timeoutMs: 50, minLength: 3, prefix: '', suffix: '', sound: false });
    // "AB12" scanned while an Arabic layout is active: letters arrive as Arabic
    // glyphs with Shift held; the digit row as Arabic-Indic digits.
    const events = [
      { key: 'ؤ', code: 'KeyA', shiftKey: true }, { key: 'ء', code: 'KeyB', shiftKey: true },
      { key: '١', code: 'Digit1' }, { key: '٢', code: 'Digit2' },
    ];
    for (const ev of events) { ctx.sandbox.__clock += 10; fireKeydown(ctx.RetailSystem, ev.key, ev); }
    ctx.sandbox.__clock += 10; fireKeydown(ctx.RetailSystem, 'Enter', { code: 'Enter' });
    assert.deepStrictEqual(ctx.dispatchedScans, ['AB12'], `A scan on an Arabic layout produced ${JSON.stringify(ctx.dispatchedScans)}, not ["AB12"].`);
    // An ASCII key is untouched (a normal layout behaves exactly as before).
    assert.strictEqual(ctx.RetailSystem._scanChar({ key: '-', code: 'Minus', shiftKey: false }), '-');
    assert.strictEqual(ctx.RetailSystem._scanChar({ key: 'a', code: 'KeyA', shiftKey: false }), 'a');
    console.log('PASS: a barcode scanned on an Arabic layout is built from the physical keys');
  }

  // ── 5b. Arrow keys: cart-line selection, + / -, Delete, and not typing +/- ──
  {
    const ctx = loadRetailSystem({});
    seedCart(ctx, [LINE('p1', 'Widget', 1, 10), LINE('p2', 'Gadget', 1, 5), LINE('p3', 'Gizmo', 1, 2)]);
    ctx.state.activeEl = ctx.getEl('pos-search');          // empty search
    const down = pressCode(ctx, 'ArrowDown', 'ArrowDown');
    assert.strictEqual(down.defaultPrevented, true);
    assert.strictEqual(ctx.RetailSystem._selLine, 0, 'ArrowDown with an empty search did not select the first line.');
    pressCode(ctx, 'ArrowDown', 'ArrowDown');
    assert.strictEqual(ctx.RetailSystem._selLine, 1);
    pressCode(ctx, 'ArrowUp', 'ArrowUp'); pressCode(ctx, 'ArrowUp', 'ArrowUp');
    assert.strictEqual(ctx.RetailSystem._selLine, 0, 'ArrowUp past the first line must stay on it.');

    // "+" is Equal (with Shift) / NumpadAdd; it must NOT be typed into the empty box.
    const plus = pressCode(ctx, 'NumpadAdd', '+');
    assert.strictEqual(plus.defaultPrevented, true, '"+" in an empty search was typed into the box.');
    assert.strictEqual(qtyOf(ctx, 'p1'), 2, '"+" did not add one to the SELECTED line.');
    pressCode(ctx, 'Equal', '+', { shiftKey: true });
    assert.strictEqual(qtyOf(ctx, 'p1'), 3, 'Shift+Equal ("+" on the digit row) did not add one.');
    pressCode(ctx, 'Minus', '-');
    pressCode(ctx, 'NumpadSubtract', '-');
    assert.strictEqual(qtyOf(ctx, 'p1'), 1, '"-" (Minus / NumpadSubtract) did not take one off the selected line.');
    assert.strictEqual(qtyOf(ctx, 'p3'), 1, '"+/-" touched a line that was not selected.');

    // Delete removes the SELECTED line, not the last one, with focus in the empty search.
    ctx.RetailSystem._selLine = 1;
    pressCode(ctx, 'Delete', 'Delete');
    assert.deepStrictEqual(ctx.RetailSystem._cart.map((l) => l.product_id), ['p1', 'p3'], 'Delete did not remove the selected line (p2).');

    // With TEXT in the search the same keys are typing, not shortcuts.
    const typed = loadRetailSystem({});
    seedCart(typed, [LINE('p1', 'Widget', 1, 10)]);
    typed.state.activeEl = typed.getEl('pos-search');
    typed.getEl('pos-search').value = 't-shirt';
    const minus = pressCode(typed, 'Minus', '-');
    assert.strictEqual(minus.defaultPrevented, false, '"-" was swallowed while the cashier was typing a search term.');
    assert.strictEqual(qtyOf(typed, 'p1'), 1);
    const del = pressCode(typed, 'Delete', 'Delete');
    assert.strictEqual(typed.RetailSystem._cart.length, 1, 'Delete removed a line while the cashier was editing search text.');
    assert.strictEqual(del.defaultPrevented, false);
    console.log('PASS: Up/Down select a line; +/- and Delete act on it; none of it fires while typing a search');
  }

  // ── 5c. "N*" typed in the search box is removed from it; with a line selected it sets THAT line ──
  {
    const ctx = loadRetailSystem({});
    ctx.RetailSystem._posInitialPage = ctx.RetailSystem._products;
    ctx.state.activeEl = ctx.getEl('pos-search');
    pressCode(ctx, 'Digit3', '3');
    ctx.getEl('pos-search').value = '3';                    // the browser inserts the char after keydown
    // On a US/Arabic digit row "*" is Shift-DOWN then 8: the Shift keydown must
    // not wipe the digit run (it did, which made "3*" impossible without a numpad).
    pressCode(ctx, 'ShiftLeft', 'Shift', { shiftKey: true });
    const star = pressCode(ctx, 'Digit8', '*', { shiftKey: true });
    assert.strictEqual(star.defaultPrevented, true, 'The "*" was typed into the search box.');
    assert.strictEqual(ctx.getEl('pos-search').value, '', 'The typed "3" was left in the search box after "3*".');
    assert.strictEqual(ctx.RetailSystem._pendingQty, 3);

    const sel = loadRetailSystem({});
    sel.RetailSystem._posInitialPage = sel.RetailSystem._products;
    seedCart(sel, [LINE('p1', 'Widget', 1, 10), LINE('p2', 'Gadget', 1, 5)]);
    sel.RetailSystem._selLine = 1;
    sel.state.activeEl = sel.getEl('pos-search');
    pressCode(sel, 'Digit7', '7');
    sel.getEl('pos-search').value = '7';
    pressCode(sel, 'NumpadMultiply', '*');
    assert.strictEqual(qtyOf(sel, 'p2'), 7, '"7*" with a line selected did not set THAT line\'s quantity.');
    assert.strictEqual(sel.RetailSystem._pendingQty, null, '"7*" with a line selected also armed the next-item multiplier.');
    assert.strictEqual(qtyOf(sel, 'p1'), 1);

    // A hand-typed barcode is not a multiplier: the box keeps it.
    const bc = loadRetailSystem({});
    bc.state.activeEl = bc.getEl('pos-search');
    for (const d of '1234') pressCode(bc, 'Digit' + d, d);
    bc.getEl('pos-search').value = '1234567';
    const bstar = pressCode(bc, 'NumpadMultiply', '*');
    assert.strictEqual(bc.getEl('pos-search').value, '1234567', 'A longer typed number lost its tail to the multiplier strip.');
    assert.strictEqual(bc.RetailSystem._pendingQty, null);
    assert.strictEqual(bstar.defaultPrevented, false);
    console.log('PASS: "N*" is stripped from the search box; with a selected line it sets that line; barcodes are left alone');
  }

  // ── 6b. Esc, in order: clear search > clear selection > cancel pending qty ──
  {
    const ctx = loadRetailSystem({});
    ctx.RetailSystem._posInitialPage = ctx.RetailSystem._products;
    seedCart(ctx, [LINE('p1', 'Widget', 1, 10)]);
    ctx.state.activeEl = ctx.getEl('pos-search');
    ctx.getEl('pos-search').value = 'abc';
    ctx.RetailSystem._selLine = 0;
    ctx.RetailSystem._pendingQty = 5;
    pressCode(ctx, 'Escape', 'Escape');
    assert.strictEqual(ctx.getEl('pos-search').value, '', 'First Esc did not clear the search.');
    assert.strictEqual(ctx.RetailSystem._selLine, 0, 'First Esc also cleared the selection.');
    assert.strictEqual(ctx.RetailSystem._pendingQty, 5, 'First Esc also cancelled the pending quantity.');
    pressCode(ctx, 'Escape', 'Escape');
    assert.strictEqual(ctx.RetailSystem._selLine, null, 'Second Esc did not clear the line selection.');
    assert.strictEqual(ctx.RetailSystem._pendingQty, 5, 'Second Esc cancelled the pending quantity too early.');
    pressCode(ctx, 'Escape', 'Escape');
    assert.strictEqual(ctx.RetailSystem._pendingQty, null, 'Third Esc did not cancel the pending quantity.');
    assert.strictEqual(ctx.RetailSystem._cart.length, 1, 'Esc voided the sale.');
    assert.strictEqual(ctx.posted.length, 0);
    console.log('PASS: Esc clears search, then selection, then pending quantity -- and never touches the sale');
  }

  // ── 7. Search results: Up/Down highlight, Enter adds highlighted / exact / single / nothing-if-ambiguous ──
  {
    const mk = () => {
      const ctx = loadRetailSystem({});
      const RS = ctx.RetailSystem;
      RS._posInitialPage = RS._products;
      RS._products = [
        { id: 'p1', name: 'Widget', sell_price: 10, tax_rate: 0, total_stock: 999, barcode: '111', sku: 'W-1' },
        { id: 'p2', name: 'Widget Pro', sell_price: 20, tax_rate: 0, total_stock: 999, barcode: '222', sku: 'W-2' },
      ];
      RS._gridVisible = RS._products.slice();
      RS._findByCode = async () => ({ product: null, error: null });
      ctx.state.activeEl = ctx.getEl('pos-search');
      return ctx;
    };
    const ids = (c) => c.RetailSystem._cart.map((l) => l.product_id);

    // arrows move a highlight; Enter adds the highlighted one
    let ctx = mk();
    ctx.getEl('pos-search').value = 'wid'; ctx.RetailSystem._posResultsTerm = 'wid';
    pressCode(ctx, 'ArrowDown', 'ArrowDown'); pressCode(ctx, 'ArrowDown', 'ArrowDown');
    assert.strictEqual(ctx.RetailSystem._resSel, 1, 'ArrowDown with search text did not walk the results.');
    pressCode(ctx, 'Enter', 'Enter'); await settle();
    assert.deepStrictEqual(ids(ctx), ['p2'], 'Enter did not add the HIGHLIGHTED result.');
    assert.strictEqual(ctx.getEl('pos-search').value, '', 'The search box was not cleared after adding.');
    assert.strictEqual(ctx.posted.length, 0, 'Enter in a non-empty search charged the sale.');

    // exact barcode beats "two results"
    ctx = mk();
    ctx.getEl('pos-search').value = '111'; ctx.RetailSystem._posResultsTerm = '111';
    pressCode(ctx, 'Enter', 'Enter'); await settle();
    assert.deepStrictEqual(ids(ctx), ['p1'], 'Enter did not add the exact barcode match.');

    // exact SKU, case-insensitive
    ctx = mk();
    ctx.getEl('pos-search').value = 'w-2'; ctx.RetailSystem._posResultsTerm = 'w-2';
    pressCode(ctx, 'Enter', 'Enter'); await settle();
    assert.deepStrictEqual(ids(ctx), ['p2'], 'Enter did not add the exact SKU match.');

    // the only result
    ctx = mk();
    ctx.RetailSystem._gridVisible = [ctx.RetailSystem._products[0]];
    ctx.getEl('pos-search').value = 'widg'; ctx.RetailSystem._posResultsTerm = 'widg';
    pressCode(ctx, 'Enter', 'Enter'); await settle();
    assert.deepStrictEqual(ids(ctx), ['p1'], 'Enter did not add the only result.');

    // ambiguous: guessing between two products on a money screen is refused
    ctx = mk();
    ctx.getEl('pos-search').value = 'wid'; ctx.RetailSystem._posResultsTerm = 'wid';
    pressCode(ctx, 'Enter', 'Enter'); await settle();
    assert.deepStrictEqual(ids(ctx), [], 'Enter guessed between two matching products.');
    assert.strictEqual(ctx.getEl('pos-search').value, 'wid', 'An ambiguous Enter cleared what the cashier typed.');

    // results belong to the PREVIOUS keystroke: Enter waits for this term's search
    ctx = mk();
    let ran = null;
    ctx.RetailSystem._posResultsTerm = 'w';
    ctx.RetailSystem._runPOSSearch = async (t) => { ran = t; ctx.RetailSystem._posResultsTerm = t; ctx.RetailSystem._gridVisible = [ctx.RetailSystem._products[1]]; };
    ctx.getEl('pos-search').value = 'pro';
    pressCode(ctx, 'Enter', 'Enter'); await settle(); await settle();
    assert.strictEqual(ran, 'pro', 'Enter did not wait for the search of the term now in the box.');
    assert.deepStrictEqual(ids(ctx), ['p2']);
    console.log('PASS: Enter in the search adds highlighted / exact barcode / exact SKU / single result, and refuses to guess');
  }

  // ── 8. Dialogs switch the till keys off ───────────────────────────────────
  {
    const ctx = loadRetailSystem({ activeEl: null });
    seedCart(ctx, [LINE('p1', 'Widget', 1, 10)]);
    ctx.RetailSystem._modalKeyStack.push({ overlay: { isConnected: true } });
    pressCode(ctx, 'F10', 'F10'); pressCode(ctx, 'Enter', 'Enter'); pressCode(ctx, 'KeyX', 'x'); pressCode(ctx, 'Delete', 'Delete');
    await settle();
    assert.strictEqual(ctx.posted.length, 0, 'A till key charged the sale while a dialog was open.');
    assert.strictEqual(ctx.RetailSystem._cart.length, 1, 'Delete removed a line while a dialog was open.');
    ctx.RetailSystem._modalKeyStack.length = 0;
    pressCode(ctx, 'F10', 'F10'); await settle();
    assert.strictEqual(ctx.posted.length, 1, 'With the dialog gone, F10 no longer charges (allow-half).');
    console.log('PASS: every till key is off while a dialog is open, and back on when it closes');
  }

  // ── 9. F-keys: F7-F12 and Shift+F1-F4/F9 are wired and preventDefault()ed ──
  {
    const ctx = loadRetailSystem({});
    const RS = ctx.RetailSystem;
    const calls = [];
    RS._openCustomerPicker = () => calls.push('F7');
    RS._openPriceCheck = () => calls.push('F11');
    RS._openShortcutHelp = () => calls.push('F12');
    RS._holdSale = () => calls.push('F9');
    RS._openHeldSalesModal = () => calls.push('SF9');
    ctx.sandbox.CashDrawer = { keyAction: (n) => calls.push('SF' + n) };
    seedCart(ctx, [LINE('p1', 'Widget', 1, 10)]);
    ctx.state.activeEl = ctx.getEl('pos-search');           // typing target: F-keys must still work

    const prevented = [];
    for (const [code, extra, label] of [
      ['F7', {}, 'F7'], ['F9', {}, 'F9'], ['F11', {}, 'F11'], ['F12', {}, 'F12'],
      ['F9', { shiftKey: true }, 'SF9'], ['F1', { shiftKey: true }, 'SF1'], ['F2', { shiftKey: true }, 'SF2'],
      ['F3', { shiftKey: true }, 'SF3'], ['F4', { shiftKey: true }, 'SF4'], ['Slash', { ctrlKey: true }, 'F12'],
    ]) {
      const before = calls.length;
      const evt = pressCode(ctx, code, code, extra);
      assert.strictEqual(calls[before], label, `${code}${extra.shiftKey ? ' +Shift' : ''}${extra.ctrlKey ? ' +Ctrl' : ''} did not reach ${label}.`);
      prevented.push(evt.defaultPrevented);
    }
    assert.ok(prevented.every(Boolean), 'A till F-key was not preventDefault()ed (the Edge/Chrome fallback window would act on F11/F12/F10).');

    // F8 focuses and selects the discount box.
    pressCode(ctx, 'F8', 'F8');
    assert.ok(ctx.state.focusLog.includes('pos-disc') && (ctx.state.selectLog || []).includes('pos-disc'), 'F8 did not focus + select the discount box.');

    // An unrelated key combo (Ctrl+P, the browser's print) is left alone.
    const before = calls.length;
    const ctrlP = pressCode(ctx, 'KeyP', 'p', { ctrlKey: true });
    assert.strictEqual(ctrlP.defaultPrevented, false, 'Ctrl+P (the browser\'s own) was swallowed.');
    assert.strictEqual(calls.length, before);
    console.log('PASS: F7 F8 F9 F10 F11 F12, Shift+F1-F4, Shift+F9 and Ctrl+/ are wired and preventDefault()ed');
  }

  console.log('\nAll POS keyboard-shortcut tests passed.');
}

run().then(() => process.exit(0)).catch((err) => {
  console.error('FAIL:', err.message);
  process.exit(1);
});
