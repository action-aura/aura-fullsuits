/**
 * retail_po_labels_i18n_test.js -- REHEARSAL-1007 B11: the Purchase Orders
 * screen left "+ New PO" and "PO #" in English under Arabic because both were
 * bare literals, not t() calls. Renders the screen with a marking t() and
 * requires both strings to arrive through it (and to exist in en.json/ar.json).
 *
 *   node products/retail/tests/retail_po_labels_i18n_test.js
 */
'use strict';
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const FRONTEND = path.join(__dirname, '..', 'frontend');
const SRC_PATH = path.join(FRONTEND, 'subsystem-retail.js');
const SOURCE = fs.readFileSync(SRC_PATH, 'utf8');
const KEYS = ['+ New PO', 'PO #'];

function stub() {
  return {
    innerHTML: '', textContent: '', value: '', style: {}, dataset: {}, id: '',
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    appendChild() {}, getAttribute() { return null; }, setAttribute() {}, remove() {},
    querySelector() { return null; }, querySelectorAll() { return []; },
    addEventListener() {}, removeEventListener() {}, focus() {}, closest() { return null; },
  };
}

async function renderPurchases(source) {
  const sandbox = {
    console: { log() {}, error() {}, warn() {} }, // loaders log their expected "no network" rejection
    t: (s) => '<<' + s + '>>',
    fetch: () => Promise.reject(new Error('no network')),
    getComputedStyle: () => ({ getPropertyValue: () => '' }),
    navigator: { userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)' },
    localStorage: { getItem: () => null, setItem() {} },
    setTimeout: () => 0, clearTimeout() {}, setInterval() {}, clearInterval() {},
    document: {
      activeElement: null, getElementById: () => stub(), createElement: () => stub(),
      querySelector: () => stub(), querySelectorAll: () => [],
      head: { appendChild() {} }, body: { appendChild() {} },
      documentElement: { getAttribute: () => 'light', style: { setProperty() {} } },
      addEventListener() {}, removeEventListener() {},
    },
    SubsystemApp: { active: 'retail', showToast() {}, checkAuthAndSetup() {}, hasCapability: () => true },
  };
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(source, sandbox, { filename: SRC_PATH });
  const R = sandbox.RetailSystem;
  R._injectStyles = () => {};
  const c = stub();
  try { await R._renderPurchases(c); } catch (e) { /* loaders may reject; the markup is already set */ }
  return c.innerHTML;
}

async function check(source) {
  const html = await renderPurchases(source);
  for (const k of KEYS) assert.ok(html.includes('<<' + k + '>>'), `"${k}" did not go through t()`);
}

(async () => {
  await check(SOURCE);
  for (const loc of ['en', 'ar']) {
    const d = JSON.parse(fs.readFileSync(path.join(FRONTEND, 'locales', loc + '.json'), 'utf8'));
    for (const k of KEYS) assert.ok(typeof d[k] === 'string' && d[k], `${loc}.json lacks "${k}"`);
    if (loc === 'ar') for (const k of KEYS) assert.notStrictEqual(d[k], k, `ar.json "${k}" is not translated`);
  }
  console.log('PASS: PO screen routes "+ New PO" and "PO #" through t(), keys present in en+ar');
  for (const [from, to] of [["${t('+ New PO')}", '+ New PO'], ["${t('PO #')}", 'PO #']]) {
    assert.ok(SOURCE.includes(from), 'mutant anchor missing: ' + from);
    let threw = false;
    try { await check(SOURCE.replace(from, to)); } catch (e) { threw = true; }
    assert.ok(threw, 'MUTANT SURVIVED: ' + from);
    console.log('PASS: mutant (literal ' + to + ') turns it red');
  }
})().catch((e) => { console.error('FAIL: ' + (e && e.stack || e)); process.exit(1); });
