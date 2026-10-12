/**
 * retail_ui2_returns_test.js -- returns that start from the sale (UI-2 item 2,
 * review A #5): the Code 128 encoder, the barcode on both receipts, the
 * Returns dialog's "Recent sales", the scan router, and the Sales nav door.
 *
 * What is pinned, and why each is not vacuous:
 *   1. THE ENCODER. The 107-pattern table is checked structurally (every symbol
 *      is 11 modules, STOP is 13, all distinct) and three strings are pinned to
 *      their exact symbol values INCLUDING THE CHECK DIGIT. Those digits were
 *      worked by hand (PJJ123C -> 55, SALE-000016 -> 16, MAI1-000001 -> 62) and,
 *      once, read back by an independent decoder (ZXing-cpp, outside CI) from the
 *      rendered bars. Mutation: swap two table rows -> red.
 *   2. THE PAINT. The bars the raster receipt actually draws (recorded from a fake
 *      canvas) are decoded back, through the table in reverse, to the same text --
 *      on 80 mm and 58 mm paper -- so the layout (integer dots, centring, quiet
 *      zone) is proved, not just the encoder. An unencodable value draws nothing.
 *   3. THE BROWSER-PRINT RECEIPT carries the same barcode (same value) and none
 *      when the number cannot be encoded.
 *   4. RECENT SALES lists newest first, at most 30, drops voided sales, and a click
 *      loads that sale through the SAME lookup the typed number uses.
 *   5. THE SCAN ROUTER: with the Returns dialog open, a scanned receipt number
 *      loads that sale (and does NOT fall through to the section handler); with it
 *      closed the same code is NOT routed to returns (deny half).
 *   6. THE NAV DOOR: 'Sales' is in the Sell group; shown to a cashier and an owner,
 *      hidden for a role without retail.sell (an Accountant) -- allow AND deny.
 *
 * Run: node products/retail/tests/retail_ui2_returns_test.js
 */
'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');
const { loadRetail, assert } = require('./_ui2_harness.js');

const FRONTEND = path.join(__dirname, '..', 'frontend');
const C128 = require(path.join(FRONTEND, 'code128.js'));

// ── 1. encoder ───────────────────────────────────────────────────────────────
function testEncoderStructureAndGoldens() {
  assert.strictEqual(C128.PATTERNS.length, 107);
  C128.PATTERNS.forEach((p, i) => {
    const sum = [...p].reduce((a, c) => a + Number(c), 0);
    assert.strictEqual(sum, i === 106 ? 13 : 11, `pattern ${i} (${p}) is ${sum} modules wide`);
    assert.strictEqual(p.length, i === 106 ? 7 : 6, `pattern ${i} has the wrong element count`);
  });
  assert.strictEqual(new Set(C128.PATTERNS).size, 107, 'two symbols share a pattern');
  assert.strictEqual(C128.PATTERNS[104], '211214', 'START B');
  assert.strictEqual(C128.PATTERNS[0], '212222', 'space');
  assert.strictEqual(C128.PATTERNS[106], '2331112', 'STOP');

  const golden = [
    // [text, symbol values start..stop, check digit]
    ['PJJ123C', [104, 48, 42, 42, 17, 18, 19, 35, 55, 106], 55],
    ['SALE-000016', [104, 51, 33, 44, 37, 13, 16, 16, 16, 16, 17, 22, 16, 106], 16],
    ['MAI1-000001', [104, 45, 33, 41, 17, 13, 16, 16, 16, 16, 16, 17, 62, 106], 62],
  ];
  golden.forEach(([text, codes, check]) => {
    const e = C128.encode(text);
    assert.deepStrictEqual(Array.from(e.codes), codes, `symbols for ${text}`);
    assert.strictEqual(e.checksum, check, `check digit for ${text}`);
    assert.strictEqual(e.modules, (codes.length - 1) * 11 + 13, `width of ${text}`);
    assert.strictEqual(e.runs.length % 2, 1, 'a Code 128 ends on a bar, so it has an odd number of runs');
  });
  assert.strictEqual(C128.encode(''), null);
  assert.strictEqual(C128.encode(null), null);
  assert.strictEqual(C128.encode('فاتورة'), null, 'non-ASCII must be refused, not mangled');
  assert.strictEqual(C128.encode('tab\there'), null, 'a control character is outside subset B');
  const bars = C128.bars(C128.encode('A'));
  assert.strictEqual(bars[0][0], 0, 'the first bar starts at module 0');
  console.log('PASS: encoder table structure + three golden strings with check digits');
}

// ── 2. paint ─────────────────────────────────────────────────────────────────
// Reverse decoder used ONLY to read the painted bars back (independent of encode()).
const BY_PATTERN = {};
C128.PATTERNS.forEach((p, i) => { BY_PATTERN[p] = i; });
function decodeRuns(runs) {
  const codes = [];
  let i = 0;
  while (i < runs.length) {
    const n = (codes.length && BY_PATTERN[runs.slice(i, i + 7).join('')] === 106) ? 7 : 6;
    const sym = BY_PATTERN[runs.slice(i, i + n).join('')];
    if (sym === undefined) throw new Error('no such symbol at run ' + i + ': ' + runs.slice(i, i + n).join(''));
    codes.push(sym); i += n;
    if (sym === 106) break;
  }
  if (codes[0] !== 104 || codes[codes.length - 1] !== 106) throw new Error('not START B ... STOP');
  const data = codes.slice(1, -2), check = codes[codes.length - 2];
  const want = (104 + data.reduce((a, v, k) => a + (k + 1) * v, 0)) % 103;
  if (want !== check) throw new Error('check digit ' + check + ' != ' + want);
  return data.map((v) => String.fromCharCode(v + 32)).join('');
}

function paintBarcode(widthDots, value) {
  const rects = [];
  const sandbox = {
    console,
    document: {
      createElement(tag) {
        const c = { width: 0, height: 0 };
        c.getContext = () => ({
          set font(v) {}, set direction(v) {}, set fillStyle(v) {}, set strokeStyle(v) {}, set textBaseline(v) {}, set textAlign(v) {},
          measureText: (s) => ({ width: String(s).length * 10 }),
          fillRect(x, y, w, h) { rects.push({ x, y, w, h }); },
          fillText() {},
          getImageData: (x, y, w, h) => ({ data: new Uint8ClampedArray(w * h * 4) }),
        });
        return c;
      },
      fonts: { add() {} },
    },
    FontFace: function FontFace() { this.load = () => Promise.resolve(this); },
    btoa: (s) => Buffer.from(s, 'binary').toString('base64'),
    window: undefined,
  };
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(path.join(FRONTEND, 'code128.js'), 'utf8'), sandbox);
  vm.runInContext(fs.readFileSync(path.join(FRONTEND, 'receipt-raster.js'), 'utf8'), sandbox);
  const model = { width_dots: widthDots, rtl: false, lines: [{ kind: 'text', text: 'x', align: 'center', size: 'normal', bold: false }, { kind: 'barcode', value }] };
  return sandbox.ReceiptRaster.render(model).then((out) => ({ rects, out }));
}

async function testPaintedBarsDecodeBackAndFit() {
  for (const widthDots of [576, 384]) {
    for (const value of ['SALE-000016', 'MAI1-000001']) {
      const { rects, out } = await paintBarcode(widthDots, value);
      const bars = rects.filter((r) => r.h === 64).sort((a, b) => a.x - b.x);
      assert.ok(bars.length > 20, `no barcode painted at ${widthDots} dots`);
      assert.ok(bars.every((b) => Number.isInteger(b.x) && Number.isInteger(b.w)), 'a bar is on a fractional dot');
      const mw = Math.min(...bars.map((b) => b.w));
      assert.ok(mw >= 1, 'module narrower than one dot');
      // Rebuild module runs from the painted rectangles.
      const runs = []; let cursor = bars[0].x;
      bars.forEach((b) => {
        if (b.x > cursor) runs.push((b.x - cursor) / mw);       // the gap before this bar is a space
        runs.push(b.w / mw); cursor = b.x + b.w;
      });
      assert.strictEqual(decodeRuns(runs), value, `${value} at ${widthDots} dots decoded to something else`);
      const left = bars[0].x, right = bars[bars.length - 1].x + bars[bars.length - 1].w;
      assert.ok(left >= 10 * mw && widthDots - right >= 10 * mw, `quiet zone under 10 modules at ${widthDots} dots (${left}/${widthDots - right}, module ${mw})`);
      assert.ok(Math.abs(left - (widthDots - right)) <= 1, 'barcode is not centred');
      assert.ok(out.height > 64, 'the barcode took no vertical room');
    }
  }
  const empty = await paintBarcode(576, 'فاتورة');
  assert.strictEqual(empty.rects.filter((r) => r.h === 64).length, 0, 'an unencodable value must paint no bars');
  console.log('PASS: painted bars decode back to the sale number on 80 mm and 58 mm paper; quiet zone + centring; unencodable paints nothing');
}

// ── 3. browser-print receipt ─────────────────────────────────────────────────
async function printHtml(h, saleNumber) {
  let html = null;
  h.R._receiptBranding = async () => ({});
  h.R._receiptLogoDataUri = async () => '';
  h.R._einvoiceReceiptBlock = async () => '';
  h.R._printerCfg = () => ({ paperWidth: '80mm' });
  await h.R._printReceipt({
    sale_number: saleNumber, subtotal: 5, total: 5, amount_paid: 5, tax_amount: 0, lines: [],
    __preview: { sink: (x) => { html = x; }, branding: {}, widthMm: 80 },
  });
  return html;
}

async function testBrowserPrintReceiptCarriesTheBarcode() {
  const h = loadRetail();
  const html = await printHtml(h, 'SALE-000016-a348be85-f57464f8');
  assert.ok(html && html.includes('class="rcpt-barcode"'), 'the printed receipt has no barcode');
  const rects = (html.match(/<rect x=/g) || []).length;
  assert.strictEqual(rects, C128.bars(C128.encode('SALE-000016')).length, 'barcode bars != the short number\'s bars');
  const svg = /<svg[^>]*aria-label="([^"]*)"/.exec(html);
  assert.ok(svg && svg[1] === 'SALE-000016', 'label is not the number the customer sees: ' + (svg && svg[1]));
  assert.ok(html.indexOf('rcpt-barcode') > html.indexOf('Thank you'), 'barcode belongs after the footer block');
  const none = await printHtml(loadRetail(), 'فاتورة-1');
  assert.ok(none && !none.includes('rcpt-barcode'), 'an unencodable number must print no barcode');
  const noCodec = await printHtml(loadRetail({ code128: false }), 'SALE-000001');
  assert.ok(noCodec && !noCodec.includes('rcpt-barcode') && noCodec.includes('SALE-000001'), 'without the encoder the receipt still prints, just without a barcode');
  console.log('PASS: browser-print receipt carries the barcode (short number), none for unencodable, receipt survives a missing encoder');
}

// ── 4 + 5. Returns dialog ────────────────────────────────────────────────────
function sale(id, n, minutes, extra) {
  return Object.assign({ id, sale_number: n, created_at: `2026-10-12 10:${String(minutes).padStart(2, '0')}:00`, customer_name: 'Walk-in', total: 4.5, status: 'completed' }, extra || {});
}
function fakeBook(h, rows) {
  const calls = [];
  h.R._get = async (url) => {
    calls.push(url);
    if (url.includes('/sales/recent')) {
      const q = new URL('http://x' + url.slice(url.indexOf('/sales/recent'))).searchParams.get('q');
      const lim = Number(new URL('http://x' + url.slice(url.indexOf('/sales/recent'))).searchParams.get('limit') || 50);
      return { status: 'success', data: rows.filter((s) => !q || s.sale_number.toLowerCase().includes(q.toLowerCase())).slice(0, lim) };
    }
    const m = /\/sales\/(\d+)$/.exec(url);
    if (m) return { status: 'success', data: { sale: { id: Number(m[1]), payment_method: 'cash', customer_id: null }, items: [{ product_id: 'p1', product_name: 'Lamp', quantity: 1, unit_price: 4.5 }] } };
    return { status: 'success', data: [] };
  };
  return calls;
}
const tick = () => new Promise((r) => setImmediate(r));

async function openReturns(rows) {
  const h = loadRetail();
  const calls = fakeBook(h, rows);
  await h.R._openCreateReturn();
  await tick(); await tick();
  return { h, calls };
}

async function testRecentSalesList() {
  const rows = [];
  for (let i = 1; i <= 40; i++) rows.push(sale(i, `MAI1-${String(i).padStart(6, '0')}`, i)); // newest-first is the server's job
  rows.reverse();
  rows.splice(3, 0, sale(900, 'MAI1-000900', 59, { status: 'voided' }));
  const { h, calls } = await openReturns(rows);
  const recentCall = calls.find((u) => u.includes('/sales/recent'));
  assert.ok(recentCall && /limit=30/.test(recentCall), 'asked for the last 30: ' + recentCall);
  const html = h.el('ret-recent').innerHTML;
  const numbers = [...html.matchAll(/data-sn="([^"]+)"/g)].map((m) => m[1]);
  assert.ok(numbers.length <= 30 && numbers.length >= 29, 'list length ' + numbers.length);
  assert.strictEqual(numbers[0], 'MAI1-000040', 'newest first');
  assert.ok(!numbers.includes('MAI1-000900'), 'a voided sale must not be offered for return');
  assert.ok(html.includes('ret-recent-cust') && html.includes('ret-recent-total') && html.includes('ret-recent-time'), 'row is missing time / customer / total');
  assert.ok(/<button type="button" class="ret-recent-row"/.test(html), 'rows must be real buttons (keyboard)');

  // a click loads that sale through the same lookup
  await h.R._pickRecentReturnSale('MAI1-000007');
  assert.strictEqual(h.R._returnSaleId, 7, 'the clicked sale was not loaded');
  assert.strictEqual(h.el('ret-sale-search').value, 'MAI1-000007');
  assert.strictEqual(h.el('ret-recent-wrap').hidden, true, 'the list should fold away once a sale is loaded');
  assert.ok(h.el('ret-sale-items').innerHTML.includes('Lamp'), 'the sale\'s items were not rendered');
  console.log('PASS: recent sales -- last 30, newest first, voided dropped, a click loads the sale');
}

async function testRecentSalesFailureIsSaidNotHidden() {
  const h = loadRetail();
  h.R._get = async () => { throw new Error('offline'); };
  await h.R._openCreateReturn(); await tick(); await tick();
  assert.ok(/Could not load recent sales/.test(h.el('ret-recent').innerHTML), 'a failed load must say so');
  assert.ok(h.el('ret-sale-search'), 'the number box must still be there');
  console.log('PASS: a failed recent-sales load says so and leaves the number box usable');
}

async function testScanLoadsTheSaleOnlyWhileReturnsIsOpen() {
  const rows = [sale(5, 'SALE-000016-a348be85-f57464f8', 5), sale(6, 'SALE-000017-a348be85-f57464f8', 6)];
  const { h } = await openReturns(rows);
  let sectionScan = 0;
  h.R._section = 'pos'; h.R._posScan = () => { sectionScan++; };
  h.R._beep = () => {};
  h.R._scan = { capture: null };
  // the receipt prints the SHORT number; the lookup resolves it exactly
  await h.R._dispatchScan('SALE-000016', { sound: false });
  await tick(); await tick();
  assert.strictEqual(h.R._returnSaleId, 5, 'scanning the receipt number did not load that sale');
  assert.strictEqual(sectionScan, 0, 'the scan leaked to the till (a receipt number must not ring up a product)');

  // DENY half: dialog closed -> the same code goes to the section, never to returns
  h.closeAll();
  h.R._returnSaleId = null;
  h.R._dispatchScan('SALE-000016', { sound: false });
  await tick();
  assert.strictEqual(h.R._returnSaleId, null, 'a scan with no Returns dialog open touched the returns state');
  assert.strictEqual(sectionScan, 1, 'with Returns closed the scan must reach the section handler');
  console.log('PASS: a scanned receipt number loads the sale while Returns is open; closed -> goes to the till instead');
}

// ── 6. nav door ──────────────────────────────────────────────────────────────
function navVisibility(capabilities, role) {
  const code = fs.readFileSync(path.join(FRONTEND, 'app-shell.js'), 'utf8');
  const store = {};
  const stub = () => ({ innerHTML: '', style: {}, dataset: {}, classList: { add() {}, remove() {}, toggle() {} }, appendChild() {}, addEventListener() {}, setAttribute() {}, getAttribute() { return null; }, querySelector() { return null; }, querySelectorAll() { return []; } });
  const sandbox = {
    console, t: (s) => s, setTimeout, clearTimeout, setInterval, clearInterval,
    navigator: { userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)' }, location: { hash: '', href: 'http://localhost/' },
    localStorage: { getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); }, removeItem() {} },
    document: { readyState: 'complete', body: stub(), head: { appendChild() {} }, documentElement: { getAttribute() { return null; }, setAttribute() {}, style: { setProperty() {} } }, getElementById() { return null; }, createElement: stub, querySelector() { return null; }, querySelectorAll() { return []; }, addEventListener() {} },
  };
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(code, sandbox);
  const App = sandbox.SubsystemApp;
  App.capabilities = capabilities; App.role = role; App.isAdminDevice = false; App.activeModules = []; App.active = 'retail';
  const sys = App.systems.retail;
  const item = sys.nav.find((n) => n.id === 'sales');
  const sell = sys.navGroups.find((g) => g.label === 'Sell');
  return { item, inSell: !!sell && sell.items.includes('sales'), visible: !!item && App._isNavItemVisible(item) };
}

function testSalesNavDoor() {
  const cashier = navVisibility(['retail.cash.close', 'retail.refund', 'retail.sell'], 'cashier');
  assert.ok(cashier.item, "there is no 'sales' nav entry");
  assert.ok(cashier.inSell, "'sales' is not in the Sell group");
  assert.strictEqual(cashier.visible, true, 'a cashier (retail.sell) must see Sales');
  const owner = navVisibility(['retail.cash.approve', 'retail.cash.close', 'retail.discount', 'retail.employees', 'retail.refund', 'retail.reports', 'retail.sell', 'retail.stock.adjust'], 'admin');
  assert.strictEqual(owner.visible, true, 'the owner must see Sales');
  const accountant = navVisibility(['retail.reports'], 'accountant');
  assert.strictEqual(accountant.visible, false, 'a role without retail.sell must NOT see the Sales door');
  assert.strictEqual(cashier.item.capability, 'retail.sell');
  console.log('PASS: Sales is in Sell; shown to cashier and owner, hidden for a role without retail.sell');
}

(async function main() {
  const checks = [
    ['encoder', testEncoderStructureAndGoldens],
    ['paint', testPaintedBarsDecodeBackAndFit],
    ['browser print', testBrowserPrintReceiptCarriesTheBarcode],
    ['recent sales', testRecentSalesList],
    ['recent sales failure', testRecentSalesFailureIsSaidNotHidden],
    ['scan router', testScanLoadsTheSaleOnlyWhileReturnsIsOpen],
    ['nav door', testSalesNavDoor],
  ];
  let failed = 0;
  for (const [name, fn] of checks) {
    try { await fn(); } catch (e) { failed++; console.log('FAIL: ' + name + '\n      ' + (e && e.stack || e)); }
  }
  if (failed) { console.log(`\nFAIL: retail_ui2_returns_test.js -- ${failed} of ${checks.length} checks failed`); process.exit(1); }
  console.log(`\nPASS: retail_ui2_returns_test.js -- ${checks.length} checks`);
})();
