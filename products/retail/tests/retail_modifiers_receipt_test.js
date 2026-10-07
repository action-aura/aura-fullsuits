/**
 * retail_modifiers_receipt_test.js -- EXTRAS: every receipt surface NAMES the
 * extras a line was sold with ("Large", "Extra cheese"), and none of them ever
 * changes a figure.
 *
 * Surfaces covered: the on-screen receipt modal (_showReceipt), the printed
 * slip (_printReceipt, via its __preview sink so no iframe is needed), the
 * WhatsApp receipt text (_shareReceiptOnWhatsApp), the reprint from Sales
 * History (_reprintSale), the idempotent-replay adapter
 * (_replaySaleReceiptData) and the customer-display cart payload.
 *
 * Two data shapes meet here and are normalised in ONE place
 * (RetailSystem._lineExtraNames): create_sale's response lines carry
 * `modifier_snapshots`; GET /sales/<id> items (and the live cart) carry
 * `modifiers`. Both read from the server's SNAPSHOT, never from today's option
 * rows -- the backend half is pinned in retail_modifiers_test.py.
 *
 * THE MONEY RULE: the line amount already includes the price deltas, so an
 * extra is a NAME only. The mutants below prove (a) dropping the names goes
 * red and (b) adding the delta to the amount goes red.
 *
 *   node products/retail/tests/retail_modifiers_receipt_test.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const FRONTEND = path.join(__dirname, '..', 'frontend');
const SRC_PATH = path.join(FRONTEND, 'subsystem-retail.js');
const SOURCE = fs.readFileSync(SRC_PATH, 'utf8');
const SHARE_SOURCE = fs.readFileSync(path.join(FRONTEND, 'share.js'), 'utf8');
const DISPLAY_SOURCE = fs.readFileSync(path.join(FRONTEND, 'customer-display.js'), 'utf8');

function stub(overrides) {
  return Object.assign({
    innerHTML: '', textContent: '', value: '', style: {}, dataset: {}, id: '',
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    appendChild() {}, getAttribute() { return null; }, setAttribute() {}, remove() {},
    querySelector() { return null; }, querySelectorAll() { return []; },
    addEventListener() {}, removeEventListener() {}, focus() {}, closest() { return null; },
  }, overrides);
}

/** Fresh RetailSystem from `source`; `created` collects every element made. */
function load(source) {
  const created = [];
  const opened = [];
  const sandbox = {
    console,
    t: (s) => s,
    fetch: () => Promise.reject(new Error('no network')),
    getComputedStyle: () => ({ getPropertyValue: () => '' }),
    navigator: { userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)' },
    localStorage: { getItem: () => null, setItem() {} },
    setTimeout: () => 0, clearTimeout() {}, setInterval() {}, clearInterval() {},
    document: {
      activeElement: null,
      getElementById: () => stub(),
      createElement() { const e = stub(); created.push(e); return e; },
      querySelector: () => stub(), querySelectorAll: () => [],
      head: { appendChild() {} }, body: { appendChild() {} },
      documentElement: { getAttribute: () => 'light', style: { setProperty() {} } },
      addEventListener() {}, removeEventListener() {},
    },
    SubsystemApp: {
      active: 'retail', showToast() {}, checkAuthAndSetup() {}, hasCapability: () => true,
      _shopDisplayName: () => 'Shop',
    },
  };
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(SHARE_SOURCE, sandbox, { filename: 'share.js' });
  vm.runInContext(source, sandbox, { filename: SRC_PATH });
  const R = sandbox.RetailSystem;
  assert.ok(R, 'RetailSystem not exposed');
  R._currencySymbol = 'JD'; R._currencyDecimals = 3;
  R._wireModalA11y = () => () => {};
  R._receiptBranding = async () => ({});
  R._receiptLogoDataUri = async () => '';
  R._topModalKeyLayer = () => null;
  sandbox.AuraShare.whatsapp = (phone, text) => { opened.push(text); return true; };
  sandbox.AuraShare.logShare = () => {};
  return { R, created, opened, sandbox };
}

// A line sold at 5.500 = 5.000 base + 0.500 Large. 2 extras, one with no price.
function createSaleLine(extra) {
  return Object.assign({
    product_id: 7, name: 'Burger', quantity: 1, unit_price: 5.5, line_total: 5.5,
    discount_pct: 0, discount_amount: 0, applied_promotion: null,
    modifier_snapshots: [
      { modifier_option_id: 'o1', group_name: 'Size', name: 'Large', price_delta: 0.5, cost_delta: 0 },
      { modifier_option_id: 'o2', group_name: 'Extras', name: 'Extra <cheese>', price_delta: 0, cost_delta: 0 },
    ],
  }, extra || {});
}
function saleData(line) {
  return { sale_number: 'SALE-1', subtotal: 5.5, discount_amount: 0, tax_amount: 0, total: 5.5,
    amount_paid: 10, change: 4.5, lines: [line] };
}

async function printHtml(R, sd) {
  let html = '';
  await R._printReceipt(Object.assign({}, sd, { __preview: { sink: (h) => { html = h; }, branding: {}, widthMm: 80 } }));
  return html;
}
function modalHtml(R, created, sd) {
  created.length = 0;
  R._showReceipt(sd, {});
  return created[0].innerHTML;
}

let passed = 0;

async function suite(source, report) {
  await report('receipt modal names the extras (escaped, bdi) and keeps the amount', async () => {
    const { R, created } = load(source);
    const html = modalHtml(R, created, saleData(createSaleLine()));
    assert.ok(html.includes('<bdi>Large · Extra &lt;cheese&gt;</bdi>'), html.slice(0, 1500));
    assert.ok(!html.includes('Extra <cheese>'), 'user text must be escaped');
    assert.ok(html.includes('JD 5.500'), 'line amount must stay what was charged');
    assert.ok(!html.includes('JD 6.000'), 'delta must never be added to the amount');
    assert.ok(/margin-inline-start/.test(html.slice(html.indexOf('Large') - 200, html.indexOf('Large'))), 'logical margin');
  });

  await report('a line with no extras renders no extras row', async () => {
    const { R, created } = load(source);
    const html = modalHtml(R, created, saleData(createSaleLine({ modifier_snapshots: [] })));
    assert.ok(!html.includes('rcpt-extras'), 'no extras container for a plain line');
    const gs = modalHtml(R, created, saleData(createSaleLine({ modifier_snapshots: undefined })));
    assert.ok(!gs.includes('rcpt-extras'));
  });

  await report('printed slip names the extras inside the lines, amount unchanged', async () => {
    const { R } = load(source);
    const html = await printHtml(R, saleData(createSaleLine()));
    const i = html.indexOf('Burger');
    assert.ok(i > 0, 'line present');
    const j = html.indexOf('Large · Extra &lt;cheese&gt;', i);
    assert.ok(j > i && j - i < 400, 'extras follow their own line');
    assert.ok(html.includes('JD 5.500') && !html.includes('JD 6.000'));
  });

  await report('get_sale items shape (`modifiers`) is read the same way', async () => {
    const { R } = load(source);
    const line = { product_id: 7, name: 'Burger', quantity: 1, unit_price: 5.5, line_total: 5.5, discount_pct: 0,
      modifiers: [{ group_name: 'Size', name: 'Large', price_delta: 0.5 }] };
    const html = await printHtml(R, saleData(line));
    assert.ok(html.includes('<bdi>Large</bdi>'), 'reprint shape');
  });

  await report('WhatsApp text appends the extras to the item, total unchanged', async () => {
    const { R, opened, sandbox } = load(source);
    R._lastSaleData = saleData(createSaleLine());
    sandbox.document.getElementById = () => stub({ value: '+962780069485' });
    await R._shareReceiptOnWhatsApp();
    assert.strictEqual(opened.length, 1, 'one message sent');
    assert.ok(opened[0].includes('Burger (Large, Extra <cheese>)'), opened[0]);
    assert.ok(opened[0].includes('5.500') && !opened[0].includes('6.000'), opened[0]);
  });

  await report('_reprintSale passes the extras through from get_sale', async () => {
    const { R } = load(source);
    let printed = null;
    R._printReceipt = async (sd) => { printed = sd; };
    R._lastViewedSale = { sale: { sale_number: 'S', total: 5.5 }, items: [
      { product_id: 7, product_name: 'Burger', quantity: 1, unit_price: 5.5, discount_pct: 0, line_total: 5.5,
        modifiers: [{ group_name: 'Size', name: 'Large', price_delta: 0.5 }] }] };
    await R._reprintSale();
    assert.deepStrictEqual(Array.from(printed.lines[0].modifiers).map((m) => m.name), ['Large']);
    assert.strictEqual(printed.lines[0].line_total, 5.5);
  });

  await report('_replaySaleReceiptData passes the extras through from get_sale', async () => {
    const { R } = load(source);
    R._get = async () => ({ data: { sale: { sale_number: 'S', total: 5.5 }, items: [
      { product_id: 7, product_name: 'Burger', quantity: 1, line_total: 5.5,
        modifiers: [{ group_name: 'Size', name: 'Large', price_delta: 0.5 }] }] } });
    const sd = await R._replaySaleReceiptData(1, 'S');
    assert.deepStrictEqual(Array.from(sd.lines[0].modifiers).map((m) => m.name), ['Large']);
    assert.strictEqual(sd.lines[0].line_total, 5.5);
  });

  await report('customer display payload carries a plain `extras` string per item', async () => {
    const { R } = load(source);
    R._cart = [
      { product_id: 7, name: 'Burger', quantity: 1, unit_price: 5.5, line_total: 5.5,
        modifiers: [{ name: 'Large', price_delta: 0.5 }, { name: 'Extra cheese', price_delta: 0 }] },
      { product_id: 8, name: 'Tea', quantity: 1, unit_price: 1, line_total: 1 },
    ];
    const p = R._customerDisplayCartPayload();
    assert.strictEqual(p.items[0].extras, 'Large · Extra cheese');
    assert.strictEqual(p.items[1].extras, '', 'a plain line sends an empty string');
    assert.strictEqual(p.items[0].lineTotal, 'JD 5.500', 'amount untouched');
  });
}

async function displayChecks(report) {
  await report('customer display renders extras as an escaped meta line, only when present', async () => {
    const html = { v: '' };
    const els = {};
    const sb = {
      console, t: (s) => s, setTimeout: () => 0, clearTimeout() {}, setInterval() {}, clearInterval() {},
      localStorage: { getItem: () => null, setItem() {} },
      document: { getElementById: (id) => (els[id] = els[id] || stub({ id })), addEventListener() {},
        documentElement: { setAttribute() {}, getAttribute: () => null, style: { setProperty() {} } },
        querySelector: () => stub(), querySelectorAll: () => [] },
      navigator: { userAgent: 'x' }, location: { search: '' },
      addEventListener() {}, BroadcastChannel: undefined,
    };
    sb.window = sb;
    vm.createContext(sb);
    vm.runInContext(DISPLAY_SOURCE, sb, { filename: 'customer-display.js' });
    const D = sb.CustomerDisplay || sb.AuraCustomerDisplay || Object.values(sb).find((v) => v && typeof v._renderCart === 'function');
    assert.ok(D && typeof D._renderCart === 'function', 'display module not found');
    D._renderCart({ subtotal: '', total: '' }, [
      { name: 'Burger', quantity: 1, unitPrice: 'JD 5.500', lineTotal: 'JD 5.500', extras: 'Large · <b>x</b>' },
      { name: 'Tea', quantity: 1, unitPrice: 'JD 1.000', lineTotal: 'JD 1.000', extras: '' },
    ]);
    const out = els['cd-items'].innerHTML;
    assert.ok(out.includes('<bdi>Large · &lt;b&gt;x&lt;/b&gt;</bdi>'), out);
    assert.strictEqual((out.match(/cd-item-extras/g) || []).length, 1, 'only the line with extras gets the row');
  });
}

(async () => {
  const results = [];
  const report = async (name, fn) => {
    try { await fn(); results.push([name, true]); } catch (e) { results.push([name, false, e]); }
  };

  await suite(SOURCE, report);
  await displayChecks(report);
  for (const [name, ok, e] of results) {
    if (ok) { passed++; console.log('PASS: ' + name); }
    else { console.error('FAIL: ' + name + '\n  ' + (e && e.message || e)); process.exitCode = 1; }
  }

  // ── IN-MEMORY MUTANTS: each must turn the suite red ────────────────────────
  const mutate = (label, from, to, src = SOURCE) => {
    assert.ok(src.includes(from), 'mutant anchor missing: ' + label);
    return src.replace(from, to);
  };
  const mutants = [
    ['extras dropped from every surface', SOURCE, '_lineExtraNames(line) {', '_lineExtraNames(line) { return [];'],
    ['delta added to the printed amount', SOURCE,
      '_receiptLineAmountHTML(line) {\n    const { gross, discountAmount } = this._receiptLineFigures(line);',
      '_receiptLineAmountHTML(line) {\n    const { gross: g0, discountAmount } = this._receiptLineFigures(line);\n    const gross = g0 + (line.modifier_snapshots || line.modifiers || []).reduce((s, m) => s + (m.price_delta || 0), 0);'],
  ];
  for (const [label, src, from, to] of mutants) {
    const mutated = mutate(label, from, to, src);
    const r = [];
    await suite(mutated, async (n, f) => { try { await f(); r.push(true); } catch (e) { r.push(false); } });
    const red = r.filter((x) => !x).length;
    try { assert.ok(red > 0, 'MUTANT SURVIVED (suite stayed green): ' + label); passed++; console.log(`PASS: mutant "${label}" turns ${red} check(s) red`); }
    catch (e) { console.error('FAIL: ' + e.message); process.exitCode = 1; }
  }
  console.log(`\nretail_modifiers_receipt_test.js -- ${passed} checks passed`);
  process.exit(process.exitCode || 0);
})();
