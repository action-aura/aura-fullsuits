'use strict';
// PRINT-1: frontend/receipt-raster.js pure helpers + the sale-path wiring.
//
// The canvas drawing itself needs a browser and is proven by the real-artefact
// run (bitmap decoded back to a PNG and read -- see the PRINT-1 report). What a
// node test CAN pin, and what a wrong edit would silently break, is:
//   * packBits: MSB-first, 1 = black, leftmost dot = bit 7 of byte 0, threshold,
//     and alpha composited on white (a transparent pixel is paper);
//   * bytesToBase64 over a bitmap bigger than one String.fromCharCode chunk;
//   * wrapWords never overflows maxW (a long unbroken word is broken by chars);
//   * the sale path prints through the raster route and does NOT also fire the
//     separate kick in the same branch (the double-kick race this wave removed).
//
// Run: node products/retail/tests/retail_receipt_raster_test.js
const assert = require('assert');
const fs = require('fs');
const path = require('path');

const FRONTEND = path.join(__dirname, '..', 'frontend');
const R = require(path.join(FRONTEND, 'receipt-raster.js'));
let checks = 0;
function ok(name, fn) { fn(); checks++; console.log('  ok   ' + name); }

function px(width, height, fill) {  // RGBA, all one colour
  const a = new Uint8ClampedArray(width * height * 4);
  for (let i = 0; i < a.length; i += 4) { a[i] = a[i + 1] = a[i + 2] = fill; a[i + 3] = 255; }
  return a;
}
function setPx(a, width, x, y, v, alpha) {
  const i = (y * width + x) * 4; a[i] = a[i + 1] = a[i + 2] = v; a[i + 3] = alpha == null ? 255 : alpha;
}

ok('packBits is MSB-first: leftmost dot is bit 7 of the first byte', () => {
  const w = 16, h = 1, a = px(w, h, 255);
  setPx(a, w, 0, 0, 0);          // leftmost dot black
  setPx(a, w, 15, 0, 0);         // rightmost dot black
  assert.deepStrictEqual(Array.from(R.packBits(a, w, h)), [0x80, 0x01]);
});

ok('packBits packs width/8 bytes per row and keeps rows apart', () => {
  const w = 8, h = 3, a = px(w, h, 255);
  setPx(a, w, 3, 1, 0);
  assert.deepStrictEqual(Array.from(R.packBits(a, w, h)), [0x00, 0x10, 0x00]);
});

ok('packBits threshold: grey below it prints, grey above it does not', () => {
  const w = 8, h = 1, a = px(w, h, 255);
  setPx(a, w, 0, 0, R.THRESHOLD - 1);
  setPx(a, w, 1, 0, R.THRESHOLD + 1);
  assert.strictEqual(R.packBits(a, w, h)[0], 0x80);
});

ok('packBits composites on white: a transparent black pixel is paper', () => {
  const w = 8, h = 1, a = px(w, h, 255);
  setPx(a, w, 0, 0, 0, 0);       // black but fully transparent
  assert.strictEqual(R.packBits(a, w, h)[0], 0x00);
});

ok('bytesToBase64 round-trips a bitmap larger than one chunk', () => {
  const bytes = new Uint8Array(0x8000 * 3 + 17).map((_, i) => (i * 31 + 7) & 0xff);
  const back = Buffer.from(R.bytesToBase64(bytes), 'base64');
  assert.strictEqual(back.length, bytes.length);
  assert.ok(back.equals(Buffer.from(bytes)));
});

ok('wrapWords never exceeds maxW, even for one very long unbroken word', () => {
  const measure = (s) => s.length * 10;
  const lines = R.wrapWords('aaa bbb ccc ' + 'x'.repeat(45) + ' end', 100, measure);
  lines.forEach((l) => assert.ok(measure(l) <= 100, 'line too wide: ' + l));
  assert.strictEqual(lines.join('').replace(/ /g, ''), 'aaabbbccc' + 'x'.repeat(45) + 'end');
});

ok('hasArabic detects Arabic script only', () => {
  assert.strictEqual(R.hasArabic('قهوة'), true);
  assert.strictEqual(R.hasArabic('Mint tea 12.500'), false);
});

// ── sale-path wiring (source-level: the branch structure IS the guarantee) ──
const src = fs.readFileSync(path.join(FRONTEND, 'subsystem-retail.js'), 'utf8');
const at = src.indexOf('if (_kickCfg.autoPrint && _hasPrinter) {');
assert.ok(at > 0, 'sale path no longer has the autoPrint branch');
const elseAt = src.indexOf('} else if (_kickCfg.autoKick && _hasPrinter) {', at);
assert.ok(elseAt > at, 'kick-only must be the ELSE of the autoPrint branch');

ok('auto-print branch prints via the raster path with kick riding in the same job', () => {
  // Code only: the branch's own explanatory comments legitimately name /printer/kick.
  const branch = src.slice(at, elseAt).split(String.fromCharCode(10)).filter((l) => !l.trim().startsWith('//')).join(String.fromCharCode(10));
  assert.ok(branch.includes('_printEscPosReceipt(data.data.id, { kick: !!_kickCfg.autoKick })'));
  assert.ok(!branch.includes('/printer/kick'), 'the print branch must not fire a second, separate kick');
});

ok('kick-only call survives only for autoKick WITHOUT autoPrint', () => {
  const tail = src.slice(elseAt, elseAt + 700);
  assert.ok(tail.includes("'/api/sub/retail/printer/kick'"));
});

ok('receipt modal Print and its P key use the raster-aware path', () => {
  assert.ok(src.includes('onclick="RetailSystem._printFromModal()"'));
  assert.ok(!src.includes('onclick="RetailSystem._printReceipt(RetailSystem._lastSaleData)"'));
});

ok('autoPrint defaults OFF', () => {
  assert.ok(src.includes("autoKick: false, autoPrint: false, host: '', port: 9100 }, JSON.parse"));
});

console.log('PASS: retail_receipt_raster_test.js -- ' + checks + ' checks');
