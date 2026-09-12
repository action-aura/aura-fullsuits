/**
 * retail_form_state_restore_test.js — Save-button state restore
 * (retail-hardware-viewports).
 *
 * FOUND BY DRIVING THE REAL PRODUCT: on a fresh (unlicensed) install,
 * opening the Add Product form and submitting gets the expected refusal --
 * the licence gate denies the mutation and a truthful toast appears
 * ("This action is not available in the current licensing state."). That
 * part was already correct. The defect was what the form was left in: the
 * submit button's label had been switched to a busy state ("Saving…") for
 * the in-flight request and was NOT restored afterwards -- the button was
 * left describing work that never happened, on a form that never saved.
 *
 * This till's licence gate makes a mutation refusal the NORMAL outcome on
 * an unlicensed device (see CLAUDE.md's licensing section), not a rare
 * edge -- so this is a routine-path bug, not a corner case.
 *
 * _saveProduct (subsystem-retail.js) restored the button on both its
 * failure branches, but to a HARDCODED 'Save' -- which is wrong for every
 * "Add X" button (this form's real original label is "Add Product", only
 * an EDIT's button ever said plain "Save"/"Save Changes"). The same
 * hardcoded-wrong-label shape was found by audit in five sibling
 * Add/Edit-modal save handlers: _saveCategory, _saveCustomer,
 * _savePromotion, _saveSupplierContact, _saveSupplier. All six now restore
 * through one shared helper, RetailSystem._restoreSaveButton(btn, label),
 * with the correct add-vs-edit label at each call site (mirroring the
 * ternary _saveBranch already used correctly, and the explicit restore
 * _checkout() already does on its own failure branch).
 *
 * THIS FILE'S JOB, run against the real code, not read from the diff:
 *
 *   1. THE BUG: a server refusal (the exact 403 shape) restores the Add
 *      Product button's ORIGINAL label ("Add Product") and re-enables it.
 *   2. A THROWN request (network failure) restores it too -- a different
 *      exit path, and the one most likely to be missed.
 *   3. THE ALLOW-HALF: a SUCCESSFUL save still closes the modal, reloads
 *      the product list, and shows the success toast -- unchanged.
 *   4. ANTI-VACUITY: the button's label is actually changed to the busy
 *      state during the in-flight window, so (1) and (2) cannot pass
 *      trivially against a handler that never touched it.
 *   5. ONE AUDITED SIBLING (_saveCategory) gets the same refusal-restores
 *      coverage, proving the fix is not a one-off special case.
 *
 * ── MUTATION-PROVED ─────────────────────────────────────────────────────────
 * M1: the restore calls are deleted from _saveProduct -- test (1) must FAIL,
 *     naming the stale busy label it found instead of the restored one.
 * M2: _saveProduct is forced to NEVER take the success branch (the "restores
 *     the button by never completing a save" failure mode test 3 exists to
 *     catch) -- test (3) must FAIL, since a real successful save would then
 *     be misreported as a refusal.
 *
 * No test framework is configured for this vanilla-JS, build-step-free
 * frontend (see CLAUDE.md), so this runs standalone on Node built-ins:
 *
 *   node products/retail/tests/retail_form_state_restore_test.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const FRONTEND = path.join(__dirname, '..', 'frontend');
const RETAIL_JS = path.join(FRONTEND, 'subsystem-retail.js');
const SRC = fs.readFileSync(RETAIL_JS, 'utf8');

/* Match the file's OWN line ending -- an anchor written with the wrong one
   would silently match nothing, making a mutation proof below pass while
   proving nothing (same hazard retail_branches_test.js's identical helper
   guards against). */
function eolOf(src) { return src.indexOf('\r\n') !== -1 ? '\r\n' : '\n'; }
function nlFor(src) { const eol = eolOf(src); return (s) => s.replace(/\n/g, eol); }

const PRODUCTS_URL = '/api/sub/retail/products';
const CATEGORIES_URL = '/api/sub/retail/categories';

// ─────────────────────────────────────────────────────────────────────────────
// FIXTURES
// ─────────────────────────────────────────────────────────────────────────────

// The exact refusal shape the licence gate returns (see CLAUDE.md's
// licensing section / require_license_capability): status !== 'success',
// a truthful message, still HTTP 200 (the frontend never inspects
// res.status for this -- only the JSON body's `status` field).
const LICENSE_REFUSAL = {
  status: 'error',
  message: 'This action is not available in the current licensing state.',
};
const SAVE_OK = { status: 'success', data: { id: 42 } };

// ─────────────────────────────────────────────────────────────────────────────
// THE SANDBOX — the real subsystem-retail.js, never a reimplementation
// ─────────────────────────────────────────────────────────────────────────────

function makeStub(over) {
  return Object.assign({
    innerHTML: '', outerHTML: '', textContent: '', value: '', id: '', disabled: false,
    style: {}, dataset: {},
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    appendChild() {}, getAttribute() { return null; }, setAttribute() {},
    querySelector() { return null; }, querySelectorAll() { return []; },
    addEventListener() {}, removeEventListener() {},
    focus() {}, blur() {}, remove() {}, closest() { return null; },
  }, over || {});
}

/**
 * @param {object} opts
 *   source        — subsystem-retail.js text (a mutant, for the proofs below)
 *   postProducts  — payload for POST /products (default SAVE_OK)
 *   postCategories— payload for POST /categories (default SAVE_OK)
 *   throwOnSave   — if true, any POST/PATCH/PUT save request rejects (network failure)
 */
function loadRetailSystem(opts) {
  const o = opts || {};
  const calls = [];
  const toasts = [];
  const els = Object.create(null);
  const getEl = (id) => (els[id] || (els[id] = makeStub({ id })));

  const sandbox = {
    console: { log() {}, warn() {}, error() {}, info() {} },
    t: (s) => s, // identity stub -- this file checks button STATE, catalog coverage is retail_surface_i18n_test.js's job
    fetch: (url, init) => {
      const method = ((init && init.method) || 'GET').toUpperCase();
      let body = null;
      if (init && typeof init.body === 'string') {
        try { body = JSON.parse(init.body); } catch (e) { body = init.body; }
      }
      const u = String(url);
      calls.push({ method, url: u, body });

      const isSaveVerb = method === 'POST' || method === 'PATCH' || method === 'PUT';
      if (o.throwOnSave && isSaveVerb) {
        return Promise.reject(new Error('Network failure'));
      }

      let payload = { status: 'success', data: [] }; // harmless default for any unlisted GET
      if (u.indexOf(PRODUCTS_URL) === 0 && (method === 'POST' || method === 'PATCH')) {
        payload = o.postProducts === undefined ? SAVE_OK : o.postProducts;
      } else if (u.indexOf(CATEGORIES_URL) === 0 && (method === 'POST' || method === 'PUT')) {
        payload = o.postCategories === undefined ? SAVE_OK : o.postCategories;
      }
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(payload) });
    },
    getComputedStyle: () => ({ getPropertyValue: () => '' }),
    navigator: { userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)' },
    localStorage: { getItem: () => null, setItem() {} },
    setTimeout: () => 0, clearTimeout() {}, setInterval: () => 0, clearInterval() {},
    document: {
      activeElement: null,
      getElementById(id) { return getEl(id); },
      createElement() { return makeStub(); },
      querySelector() { return null; },
      querySelectorAll() { return []; },
      head: { appendChild() {} },
      body: { appendChild() {} },
      documentElement: { getAttribute: () => 'light', style: { setProperty() {} } },
      addEventListener() {},
    },
  };
  sandbox.SubsystemApp = {
    active: 'retail',
    showToast(msg, type) { toasts.push({ msg, type }); },
    checkAuthAndSetup() {},
    _navigate() {},
    role: 'admin',
    hasCapability: () => true,
  };
  sandbox.window = sandbox;
  sandbox.Chart = function ChartStub() { return { destroy() {} }; };
  sandbox.Chart.getChart = () => null;

  vm.createContext(sandbox);
  vm.runInContext(o.source || SRC, sandbox, { filename: RETAIL_JS });
  assert.ok(sandbox.RetailSystem, 'subsystem-retail.js did not expose window.RetailSystem');
  return { rs: sandbox.RetailSystem, calls, toasts, els, getEl };
}

// ─────────────────────────────────────────────────────────────────────────────
// MUTATION HARNESS — same shape as retail_branches_test.js
// ─────────────────────────────────────────────────────────────────────────────

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

async function provesMutation(what, mutatedSrc, check) {
  let threw = null;
  try {
    await check(mutatedSrc);
  } catch (err) {
    threw = err;
  }
  assert.ok(
    threw,
    `MUTATION SURVIVED — ${what}\n` +
    'The guard for this passed against a build with the behaviour deliberately broken, so it is ' +
    'not actually watching it. Fix the check, not the mutation.'
  );
  return `${what}  [caught: ${String(threw.message || threw).split('\n')[0].slice(0, 140)}]`;
}

// ─────────────────────────────────────────────────────────────────────────────
// SETUP HELPERS — populate the Add Product form's fields
// ─────────────────────────────────────────────────────────────────────────────

function fillAddProductForm(ctx) {
  ctx.getEl('pm-name').value = 'Widget';
  ctx.getEl('pm-sku').value = 'SKU-1';
  // The button as it was actually rendered for a NEW product (_showProductModal,
  // isEdit=false) -- see subsystem-retail.js's "${isEdit?'Save Changes':'Add
  // Product'}". Set explicitly here since the stub has no rendering pass of
  // its own to produce it.
  ctx.getEl('pm-save-btn').textContent = 'Add Product';
  ctx.getEl('pm-save-btn').disabled = false;
}

// ─────────────────────────────────────────────────────────────────────────────
// (1) THE BUG — a server refusal restores the button's ORIGINAL label
// ─────────────────────────────────────────────────────────────────────────────

async function testRefusalRestoresProductButtonLabelAndEnabled(src) {
  const ctx = loadRetailSystem({ source: src, postProducts: LICENSE_REFUSAL });
  // The success path calls _renderProducts, an async RENDER function another
  // agent is actively changing (render-generation guard work) in parallel --
  // stubbing it keeps this test about the SAVE BUTTON, not about render
  // internals this file must not touch.
  ctx.rs._renderProducts = () => {};
  fillAddProductForm(ctx);

  await ctx.rs._saveProduct(null);

  const btn = ctx.getEl('pm-save-btn');
  assert.strictEqual(
    btn.textContent, 'Add Product',
    `THE BUG: after a licence-gate refusal, the Save button should read its original ` +
    `label "Add Product" again, but it is stuck on: ${JSON.stringify(btn.textContent)}`
  );
  assert.strictEqual(
    btn.disabled, false,
    'The Save button is still disabled after a licence-gate refusal -- the form looks broken.'
  );
  assert.ok(
    ctx.toasts.some((tt) => tt.type === 'error' && tt.msg === LICENSE_REFUSAL.message),
    `The licence-gate refusal toast did not surface verbatim. Toasts:\n${JSON.stringify(ctx.toasts)}`
  );
  console.log('PASS: a licence-gate refusal restores the Add Product button\'s label and enabled state');
}

// ─────────────────────────────────────────────────────────────────────────────
// (2) A THROWN REQUEST restores it too -- the exit path most likely missed
// ─────────────────────────────────────────────────────────────────────────────

async function testThrownRequestRestoresProductButton(src) {
  const ctx = loadRetailSystem({ source: src, throwOnSave: true });
  ctx.rs._renderProducts = () => {};
  fillAddProductForm(ctx);

  await ctx.rs._saveProduct(null);

  const btn = ctx.getEl('pm-save-btn');
  assert.strictEqual(
    btn.textContent, 'Add Product',
    `A thrown (network failure) request left the Save button on a stale label: ${JSON.stringify(btn.textContent)}`
  );
  assert.strictEqual(btn.disabled, false, 'A thrown request left the Save button disabled.');
  console.log('PASS: a thrown (network failure) request restores the Add Product button too');
}

// ─────────────────────────────────────────────────────────────────────────────
// (3) THE ALLOW-HALF — a successful save still behaves exactly as before
// ─────────────────────────────────────────────────────────────────────────────

async function testSuccessfulSaveStillClosesModalAndReloadsList(src) {
  const ctx = loadRetailSystem({ source: src, postProducts: SAVE_OK });
  let renderProductsCalled = false;
  ctx.rs._renderProducts = () => { renderProductsCalled = true; };
  let modalRemoved = false;
  ctx.els['ret-prod-modal'] = makeStub({ id: 'ret-prod-modal', remove() { modalRemoved = true; } });
  fillAddProductForm(ctx);

  await ctx.rs._saveProduct(null);

  const posts = ctx.calls.filter((c) => c.method === 'POST' && c.url.indexOf(PRODUCTS_URL) === 0);
  assert.strictEqual(posts.length, 1,
    `Expected exactly 1 POST to ${PRODUCTS_URL} on a successful save. Calls:\n${JSON.stringify(ctx.calls)}`);
  assert.ok(modalRemoved, 'A successful save did not close the Add Product modal.');
  assert.ok(renderProductsCalled, 'A successful save did not reload the product list.');
  assert.ok(
    ctx.toasts.some((tt) => tt.type === 'success' && tt.msg === 'Product added'),
    `The "Product added" success toast did not appear. Toasts:\n${JSON.stringify(ctx.toasts)}`
  );
  console.log('PASS: a successful save still closes the modal, reloads the list, and toasts success (unchanged)');
}

// ─────────────────────────────────────────────────────────────────────────────
// (4) ANTI-VACUITY — the button's label is ACTUALLY changed during in-flight
// ─────────────────────────────────────────────────────────────────────────────

async function testBusyLabelActuallyChangesDuringInFlightWindow(src) {
  const ctx = loadRetailSystem({ source: src, postProducts: SAVE_OK });
  ctx.rs._renderProducts = () => {};
  fillAddProductForm(ctx);

  // Deliberately NOT awaited yet: _saveProduct runs synchronously up to its
  // first `await` (inside this._post -> this._fetch -> `await fetch(...)`),
  // so the busy state must already be visible on the button right here, or
  // tests (1) and (2) above would be passing trivially against a handler
  // that never touched the button at all.
  const pending = ctx.rs._saveProduct(null);
  const btn = ctx.getEl('pm-save-btn');
  assert.strictEqual(
    btn.textContent, 'Saving…',
    `ANTI-VACUITY: the button never switched to its busy label during the in-flight ` +
    `window (saw ${JSON.stringify(btn.textContent)}) -- tests (1)/(2) would prove nothing.`
  );
  assert.strictEqual(btn.disabled, true, 'ANTI-VACUITY: the button was never disabled during the in-flight window.');

  await pending;
  console.log('PASS: the button label is actually switched to the busy state during the in-flight window');
}

// ─────────────────────────────────────────────────────────────────────────────
// (5) AN AUDITED SIBLING — _saveCategory gets the same refusal-restore proof
// ─────────────────────────────────────────────────────────────────────────────

async function testCategoryRefusalRestoresButtonLabel(src) {
  const ctx = loadRetailSystem({ source: src, postCategories: LICENSE_REFUSAL });
  ctx.rs._loadCategories = () => {};
  ctx.getEl('catm-name').value = 'Snacks';
  // Rendered for a NEW category (_showCategoryModal, isEdit=false):
  // "${isEdit ? t('Save') : t('Add Category')}".
  ctx.getEl('catm-btn').textContent = 'Add Category';
  ctx.getEl('catm-btn').disabled = false;

  await ctx.rs._saveCategory(null);

  const btn = ctx.getEl('catm-btn');
  assert.strictEqual(
    btn.textContent, 'Add Category',
    `Audited sibling _saveCategory: after a refusal the button should read "Add Category" ` +
    `again, but it is stuck on: ${JSON.stringify(btn.textContent)}`
  );
  assert.strictEqual(btn.disabled, false, '_saveCategory: the button is still disabled after a refusal.');
  console.log('PASS: _saveCategory (an audited sibling) also restores its Add-mode label after a refusal');
}

// ═════════════════════════════════════════════════════════════════════════════
// MAIN
// ═════════════════════════════════════════════════════════════════════════════

async function main() {
  const results = [];
  let failed = 0;

  async function run(name, fn) {
    try {
      const detail = await fn();
      results.push(`  ok   ${name}` + (detail ? `\n       ${detail}` : ''));
    } catch (err) {
      failed += 1;
      results.push(`  FAIL ${name}\n       ${(err && err.message) || err}`);
    }
  }

  // ── The five required behaviours, against the REAL, unmutated build ──────
  await run('testRefusalRestoresProductButtonLabelAndEnabled',
    () => testRefusalRestoresProductButtonLabelAndEnabled(SRC));
  await run('testThrownRequestRestoresProductButton',
    () => testThrownRequestRestoresProductButton(SRC));
  await run('testSuccessfulSaveStillClosesModalAndReloadsList',
    () => testSuccessfulSaveStillClosesModalAndReloadsList(SRC));
  await run('testBusyLabelActuallyChangesDuringInFlightWindow',
    () => testBusyLabelActuallyChangesDuringInFlightWindow(SRC));
  await run('testCategoryRefusalRestoresButtonLabel',
    () => testCategoryRefusalRestoresButtonLabel(SRC));

  // ── Mutation proofs ────────────────────────────────────────────────────────

  // M1: delete both restore calls from _saveProduct => test (1) FAILS, and
  // must name the stale busy label it found instead of the restored one.
  await run('M1: restore calls removed from _saveProduct => test (1) FAILS', async () => {
    const broken = mutate(SRC, [[
      "      } else {\n" +
      "        SubsystemApp.showToast(d.message||'Error','error');\n" +
      "        this._restoreSaveButton(btn, pid ? 'Save Changes' : 'Add Product');\n" +
      "      }\n" +
      "    } catch(e) { this._restoreSaveButton(btn, pid ? 'Save Changes' : 'Add Product'); }",
      "      } else {\n" +
      "        SubsystemApp.showToast(d.message||'Error','error');\n" +
      "        // MUTATED: restore removed -- button stays on its busy label\n" +
      "      }\n" +
      "    } catch(e) { /* MUTATED: restore removed -- button stays on its busy label */ }",
    ]]);
    return provesMutation(
      'M1 restore removed from _saveProduct',
      broken,
      (b) => testRefusalRestoresProductButtonLabelAndEnabled(b)
    );
  });

  // M2: force _saveProduct to NEVER take the success branch -- the exact
  // "restores the button by never completing a save" failure mode test (3)
  // exists to catch. A real successful save would then be misreported as a
  // refusal (wrong toast, modal never closes, list never reloads) even
  // though the button itself gets "restored" -- proving test (1) alone is
  // not enough, and test (3) is load-bearing.
  await run('M2: _saveProduct never completes a save => test (3) FAILS', async () => {
    const broken = mutate(SRC, [[
      "if (d.status === 'success') {\n        SubsystemApp.showToast(pid ? 'Product updated' : 'Product added', 'success');",
      "if (false) { // MUTATED: never takes the success branch, even on a real success\n        SubsystemApp.showToast(pid ? 'Product updated' : 'Product added', 'success');",
    ]]);
    return provesMutation(
      'M2 _saveProduct never completes a save',
      broken,
      (b) => testSuccessfulSaveStillClosesModalAndReloadsList(b)
    );
  });

  console.log(results.join('\n'));
  if (failed) {
    console.error(`\nFAIL: retail_form_state_restore_test.js — ${failed} of ${results.length} check(s) failed`);
    process.exitCode = 1;
  } else {
    console.log(`\nPASS: retail_form_state_restore_test.js — ${results.length} check(s)`);
  }
}

main().catch((err) => {
  console.error('FAIL: retail_form_state_restore_test.js (runner)');
  console.error(err && err.stack || err);
  process.exitCode = 1;
});
