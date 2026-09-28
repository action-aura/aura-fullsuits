/**
 * retail_promotions_ui_test.js — Promotions wave 1, frontend half
 * (ROADMAP.md "retail schema v23 CLAIMED").
 *
 * Loads the REAL products/retail/frontend/subsystem-retail.js (via Node's vm
 * module, the same technique retail_checkout_error_toast_test.js and
 * retail_pos_scale_test.js already use) into a minimal sandboxed DOM and
 * drives its ACTUAL _recalc/_renderCart/_bestPromoFor/_checkout/_loadPOSData
 * code paths, rather than reimplementing the pricing logic here.
 *
 * THE ONE RULE THIS FILE EXISTS TO PROTECT
 *
 * `_checkout()` must keep sending the CASHIER-TYPED manual discount_pct on
 * every cart line, never a promotion-inflated one. `create_sale` (retail_api.py)
 * refuses a discounted sale from a cashier lacking CAP_DISCOUNT, judged on the
 * value actually submitted -- so if the client started sending a promotion's
 * discount_pct instead of the manual one, a cashier without that capability
 * could no longer sell a PROMOTED item at all, on the shop's own weekend
 * offer. testCheckoutWireSendsManualDiscountNotPromoted is that guard.
 *
 * THE OTHER RULE THIS FILE EXISTS TO PROTECT
 *
 * With zero promotions configured, this feature must be invisible: the same
 * cart, on the same install, must produce EXACTLY (not approximately) the
 * totals the SERVER actually persists. testNoPromotionsEquivalence asserts
 * this against an INDEPENDENT reimplementation, not against the new code's
 * own internals.
 *
 * ROUND 3 CORRECTION (superseding the paragraph this replaces): this used to
 * say the reference formula summed `line_total * frac` at the INVOICE level
 * ("the manual rate on everything... rather than a per-line sum")
 * specifically to stay numerically IEEE754-identical to that old,
 * never-rounded formula, because a per-line sum could differ from it by a
 * fraction of a cent on ~33% of realistic fixtures. That was true, and it
 * is no longer the right goal: `_recalc()` now rounds each line's
 * discount/tax at the currency's minor unit BEFORE summing, matching what
 * `core/retail/pricing.calculate_line()` actually persists server-side for
 * every real sale (retail_p0_till_e2e.py's own item #8/I-2 money-identity
 * check proves this against the REAL backend). Matching the server's real,
 * rounded money is more important than matching a formula that was never
 * rounded to money at all -- so `testNoPromotionsEquivalence` now compares
 * against `perLineRoundedTotals()` (an independent mirror of
 * `calculate_line()`'s own rounding, proven against the real Python module
 * -- see that helper's own comment for the exact numbers), not against
 * `oldFormulaTotals()`, which is kept, unused, as a record of the formula
 * this superseded rather than deleted outright.
 *
 * Run: node products/retail/tests/retail_promotions_ui_test.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const FRONTEND_FILE = path.join(__dirname, '..', 'frontend', 'subsystem-retail.js');
const SOURCE = fs.readFileSync(FRONTEND_FILE, 'utf8');

function makeElementStub(overrides) {
  return Object.assign({
    innerHTML: '', outerHTML: '', textContent: '', value: '', id: '', disabled: false,
    style: {}, dataset: {},
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    appendChild() {}, getAttribute() { return null; }, setAttribute() {}, remove() {},
    querySelector() { return null; }, querySelectorAll() { return []; },
    addEventListener() {}, focus() {}, closest() { return null; },
  }, overrides);
}

/**
 * Loads a fresh RetailSystem from the REAL source, same isolation-per-test
 * discipline every other file in this directory uses (a shared instance
 * would let one test's _cart/_promotions leak into the next).
 *
 * `fetchImpl` defaults to "no network in this test" (matches
 * retail_surface_pos_test.js) -- tests that do not need a real fetch never
 * need to stub one; the ones that do (testFailedPromotionsFetch...,
 * testMutationProof helpers) pass their own.
 */
function loadRetailSystem(opts) {
  const o = opts || {};
  const els = Object.create(null);
  const toasts = [];
  const posts = [];
  const getEl = (id) => {
    if (!els[id]) els[id] = makeElementStub({ id });
    return els[id];
  };
  if (o.discPct != null) getEl('pos-disc').value = String(o.discPct);

  const sandbox = {
    console,
    t: (s) => s, // identity stub -- this file checks PRICING/WIRE behaviour, not translation coverage (that is retail_surface_i18n_test.js's job)
    fetch: o.fetchImpl || (() => Promise.reject(new Error('no network in this test'))),
    getComputedStyle: () => ({ getPropertyValue: () => '' }),
    navigator: { userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)' },
    localStorage: { getItem: () => null, setItem() {} },
    setTimeout, clearTimeout, setInterval, clearInterval,
    document: {
      activeElement: null,
      getElementById(id) { return getEl(id); },
      createElement() { return makeElementStub(); },
      querySelector() { return makeElementStub(); },
      querySelectorAll() { return []; },
      head: { appendChild() {} },
      body: { appendChild() {} },
      documentElement: { getAttribute: () => 'light', style: { setProperty() {} } },
      addEventListener() {},
      // The PAIR, not just the half. This stub had addEventListener alone,
      // which was enough while nothing ever tore a listener down; when
      // _showPromotionModal gained dialog semantics (2026-09-19) its close()
      // called removeEventListener and this fake died. A one-sided listener
      // API is not a document — and a fake that can register but never
      // unregister cannot model a leak either way.
      removeEventListener() {},
    },
    SubsystemApp: {
      active: 'retail',
      showToast(msg, type) { toasts.push({ msg, type }); },
      checkAuthAndSetup() {},
      hasCapability: () => true,
    },
  };
  sandbox.window = sandbox;
  // The real _post() JSON-stringifies its body over fetch -- captured here so
  // testCheckoutWireSendsManualDiscountNotPromoted can inspect exactly what
  // left the browser, not a paraphrase of it.
  const realFetch = sandbox.fetch;
  sandbox.fetch = (url, options) => {
    if (options && options.body) {
      try { posts.push({ url: String(url), body: JSON.parse(options.body) }); } catch (e) {}
    }
    return realFetch(url, options);
  };

  vm.createContext(sandbox);
  vm.runInContext(SOURCE, sandbox, { filename: FRONTEND_FILE });
  assert.ok(sandbox.RetailSystem, 'subsystem-retail.js did not expose window.RetailSystem');

  // Pin the currency this file formats in. These cases are about PRICING and
  // the WIRE -- which discount wins, what reaches _checkout -- and nothing
  // here is about currency. Before money became currency-aware the format was
  // hard-coded to dollars, so these assertions silently depended on that; when
  // the default became JOD (three decimals, the home market) every expected
  // string broke for a reason that had nothing to do with promotions.
  //
  // Pinning it makes the dependency explicit and keeps this file focused: a
  // future currency change cannot break promotion tests again, and the
  // currency behaviour itself is proved where it belongs, in
  // retail_currency_precision_test.py.
  sandbox.RetailSystem._currencySymbol = '$';
  sandbox.RetailSystem._currencyDecimals = 2;

  return { RetailSystem: sandbox.RetailSystem, els, getEl, toasts, posts };
}

/**
 * Tolerant equality for a totals figure that involves an ACTUAL promotion
 * percentage, as opposed to testNoPromotionsEquivalence's zero-promotion
 * case, which is proven bit-exact by construction (see _recalc's own
 * comment) and is asserted with plain assert.strictEqual instead.
 *
 * `lineFrac = Math.max(manualFrac, promoFrac)` mixed with the `promoExtra`
 * correction term is ordinary floating-point arithmetic once a real
 * promotion resolves (e.g. discount 60 / manual 5 on a $100 line measures
 * 59.99999999999999, not 60) -- IEEE754 reality, not a bug, and not
 * something `.toFixed(2)` display ever shows to a cashier. 1e-9 is far
 * tighter than a cent (an actual pricing-formula regression would be off by
 * many cents, not thirteen decimal places) so this still catches a real
 * defect while tolerating the noise a real defect would never produce.
 */
function assertClose(actual, expected, message) {
  assert.ok(
    Math.abs(actual - expected) < 1e-9,
    `${message} Got ${actual}, expected ${expected} (±1e-9).`
  );
}

/** Drain the microtask queue so an un-awaited async render finishes. */
async function settle() {
  for (let i = 0; i < 8; i++) await Promise.resolve();
  await new Promise((resolve) => setImmediate(resolve));
}

// ── Fixtures ─────────────────────────────────────────────────────────────

function widget(overrides) {
  return Object.assign({
    product_id: 'p1', name: 'Widget', quantity: 1,
    unit_price: 100, tax_rate: 0, line_total: 100, max_stock: 50,
    category_id: 'c1',
  }, overrides);
}

/** An independent reimplementation of the PRE-promotions formula this file
 *  guards against silently changing. Deliberately NOT calling into
 *  RetailSystem at all -- it exists to be compared against RetailSystem's
 *  real output, not to agree with it by construction. */
function oldFormulaTotals(cart, discPct, beforeDiscount) {
  const discFrac = discPct / 100;
  let subtotal = 0, tax = 0;
  cart.forEach((i) => {
    subtotal += i.line_total;
    const taxableBase = beforeDiscount ? i.line_total : (i.line_total * (1 - discFrac));
    tax += taxableBase * (i.tax_rate / 100);
  });
  const discAmt = subtotal * discFrac;
  const total = subtotal - discAmt + tax;
  return { subtotal, discount: discAmt, tax, total };
}

/** Round 3 (U2): mirrors core/retail/pricing.py's calculate_line() EXACTLY --
 *  gross/discount_amount/tax rounded to the currency's minor unit (2dp for
 *  the fixture below) ONE LINE AT A TIME, from full precision, then summed --
 *  never oldFormulaTotals()'s single invoice-level rounding (in fact no
 *  rounding at all, see that function's own body). This is the till's NEW
 *  reference for testNoPromotionsEquivalence: _recalc() was changed to sum
 *  per-line rounded discount/tax (matching what create_sale() actually
 *  persists, server-side, via pricing.calculate_line() for every real
 *  sale), and a multi-line cart with mixed tax rates is NOT numerically
 *  identical between "round once at the end" and "round every line" --
 *  algebraically equal, not IEEE754-equal, which is exactly this file's own
 *  header comment's warning about the OLD design, now superseded by a
 *  bigger one: matching what the server actually charges is more important
 *  than matching a formula that was never rounded to money at all. */
function perLineRoundedTotals(cart, discPct, beforeDiscount) {
  const round2 = (x) => Math.round((x + Number.EPSILON) * 100) / 100;
  const discFrac = discPct / 100;
  let subtotal = 0, discAmt = 0, tax = 0;
  cart.forEach((i) => {
    const gross = round2(i.line_total);
    const lineDisc = round2(gross * discFrac);
    const lineTax = beforeDiscount
      ? round2(gross * (i.tax_rate / 100))
      : round2((gross - lineDisc) * (i.tax_rate / 100));
    subtotal += gross;
    discAmt += lineDisc;
    tax += lineTax;
  });
  subtotal = round2(subtotal);
  discAmt = round2(discAmt);
  tax = round2(tax);
  const total = round2(beforeDiscount ? (subtotal - discAmt + tax) : ((subtotal - discAmt) + tax));
  return { subtotal, discount: discAmt, tax, total };
}

/** URL-routed fetch mock for POS mount, matching retail_pos_scale_test.js's
 *  own makeApi() shape. `promotionsHandler` lets a test override just the
 *  promotions/active response (resolve or reject) without re-stating the
 *  other four endpoints _loadPOSData fetches. */
function makePosApi(catalogue, promotionsHandler) {
  const json = (status, body) => Promise.resolve({ status, json: () => Promise.resolve(body) });
  return (url) => {
    const u = String(url);
    if (u.indexOf('/api/sub/retail/promotions/active') === 0) {
      return promotionsHandler ? promotionsHandler() : json(200, { status: 'success', data: [] });
    }
    if (u.indexOf('/api/sub/retail/products') === 0) return json(200, { status: 'success', data: catalogue });
    if (u.indexOf('/api/sub/retail/categories') === 0) return json(200, { status: 'success', data: [] });
    if (u.indexOf('/api/sub/retail/customers') === 0) return json(200, { status: 'success', data: [] });
    if (u.indexOf('/api/sub/retail/settings/tax') === 0) return json(200, { status: 'success', data: { tax_calculation_mode: 'after_discount' } });
    if (u.indexOf('/api/sub/retail/held-sales') === 0) return json(200, { status: 'success', data: [] });
    return Promise.reject(new Error('unhandled URL in test fetch mock: ' + u));
  };
}

// ═══════════════════════════════════════════════════════════════════════════
// (1) A line with a promotion previews the discounted price and shows the
//     promotion name
// ═══════════════════════════════════════════════════════════════════════════

function testPromotedLinePreviewsDiscountAndName() {
  const { RetailSystem, els } = loadRetailSystem();
  RetailSystem._cart = [widget()];
  RetailSystem._promotions = [{ id: 1, name: 'Weekend Sale', discount_pct: 20, product_id: 'p1', category_id: null }];
  RetailSystem._taxMode = 'after_discount';

  RetailSystem._renderCart();
  const cartHTML = els['pos-cart'].innerHTML;

  assert.ok(
    cartHTML.includes('$80.00'),
    'The promoted unit price ($100 - 20% = $80.00) must appear in the rendered cart line. Got:\n' + cartHTML
  );
  assert.ok(
    cartHTML.includes('$100.00'),
    'The ORIGINAL (pre-promotion) price must still be shown (struck through), so the customer can see the saving. Got:\n' + cartHTML
  );
  assert.ok(
    cartHTML.includes('Weekend Sale'),
    'The promotion\'s name must appear on the line so the cashier can tell the customer why it is cheaper. Got:\n' + cartHTML
  );
}

// ═══════════════════════════════════════════════════════════════════════════
// (2) BEST PRICE WINS: promotion 20% + manual 30% -> 30%, never 50%.
//     And promotion 40% + manual 10% -> 40%.
// ═══════════════════════════════════════════════════════════════════════════

function testBestPriceWinsNeverSums() {
  {
    // manual (30%) beats promo (20%)
    const { RetailSystem } = loadRetailSystem({ discPct: 30 });
    RetailSystem._cart = [widget()];
    RetailSystem._promotions = [{ id: 1, name: 'Small Promo', discount_pct: 20, product_id: 'p1', category_id: null }];
    RetailSystem._taxMode = 'after_discount';
    RetailSystem._recalc();
    assertClose(
      RetailSystem._currentTotals.discount, 30,
      `promo 20% + manual 30% must charge 30% ($30 off $100), never 50% (the sum).`
    );
    assertClose(RetailSystem._currentTotals.total, 70, `total must be $70.`);
  }
  {
    // promo (40%) beats manual (10%)
    const { RetailSystem } = loadRetailSystem({ discPct: 10 });
    RetailSystem._cart = [widget()];
    RetailSystem._promotions = [{ id: 2, name: 'Big Promo', discount_pct: 40, product_id: 'p1', category_id: null }];
    RetailSystem._taxMode = 'after_discount';
    RetailSystem._recalc();
    assertClose(
      RetailSystem._currentTotals.discount, 40,
      `promo 40% + manual 10% must charge 40% ($40 off $100), never 50% (the sum) and never 10% (the manual alone).`
    );
    assertClose(RetailSystem._currentTotals.total, 60, `total must be $60.`);
  }
}

// ═══════════════════════════════════════════════════════════════════════════
// (3) A product-specific promotion beats a category promotion on the same
//     product -- even when the category promotion's own percentage is higher.
// ═══════════════════════════════════════════════════════════════════════════

function testProductSpecificBeatsCategory() {
  const { RetailSystem } = loadRetailSystem();
  const item = widget(); // product_id 'p1', category_id 'c1'
  RetailSystem._cart = [item];
  RetailSystem._promotions = [
    { id: 1, name: 'Category Blowout', discount_pct: 50, product_id: null, category_id: 'c1' },
    { id: 2, name: 'Widget Special', discount_pct: 10, product_id: 'p1', category_id: null },
  ];
  RetailSystem._taxMode = 'after_discount';

  const resolved = RetailSystem._bestPromoFor(item);
  assert.ok(resolved, '_bestPromoFor must resolve a promotion for a product that matches both a product- and a category-scoped rule.');
  assert.strictEqual(
    resolved.name, 'Widget Special',
    `The product-specific promotion must win over the category one, REGARDLESS of which percentage is larger ` +
    `(the category one is 50%, the product one only 10%). Got: ${JSON.stringify(resolved)}`
  );

  RetailSystem._recalc();
  assertClose(
    RetailSystem._currentTotals.discount, 10,
    `The charged discount must follow the product-specific rule (10%, i.e. $10 off $100), not the higher ` +
    `category rate (50%).`
  );
}

// ═══════════════════════════════════════════════════════════════════════════
// (4) NO-PROMOTIONS EQUIVALENCE: with zero promotions and a manual discount,
//     totals are EXACTLY what the old invoice-level maths produced.
// ═══════════════════════════════════════════════════════════════════════════

function testNoPromotionsEquivalence() {
  const cart = [
    widget({ product_id: 'p1', name: 'Widget', quantity: 2, unit_price: 18.5, tax_rate: 16, line_total: 37.0 }),
    widget({ product_id: 'p2', name: 'Cups',   quantity: 3, unit_price: 4.25, tax_rate: 16, line_total: 12.75 }),
    widget({ product_id: 'p3', name: 'Milk',   quantity: 1, unit_price: 9.99, tax_rate: 5,  line_total: 9.99 }),
  ];
  const discPct = 12.5;

  for (const beforeDiscount of [false, true]) {
    const { RetailSystem } = loadRetailSystem({ discPct });
    RetailSystem._cart = cart.map((i) => Object.assign({}, i));
    RetailSystem._promotions = []; // THE point of this test
    RetailSystem._taxMode = beforeDiscount ? 'before_discount' : 'after_discount';
    RetailSystem._recalc();

    // Round 3 (U2): _recalc() now rounds each line's discount/tax at the
    // currency's minor unit BEFORE summing, the same way the server's
    // pricing.calculate_line() always has -- see perLineRoundedTotals()'s
    // own comment for why oldFormulaTotals() (which rounds nothing at all)
    // stopped being the right reference the moment this cart has more than
    // one line at different tax rates. PROVEN against the real backend, not
    // just this JS mirror: `python -c "... core.retail.pricing.
    // calculate_line(...) ..."` against this EXACT fixture (18.5x2/16%,
    // 4.25x3/16%, 9.99x1/5%, 12.5% discount) gave, per mode --
    //   after_discount:  subtotal=59.74 discount=7.47 tax=7.41 total=59.68
    //   before_discount: subtotal=59.74 discount=7.47 tax=8.46 total=60.73
    // -- identical to perLineRoundedTotals()'s own output below, which is
    // what this assertion now pins the till's preview against. The OLD,
    // unrounded reference gave discount=7.4675 (oldFormulaTotals is kept,
    // unused by this test now, as a record of what the pre-round-3 formula
    // computed -- deleting it would erase that history for no gain).
    //
    // What this change can no longer catch: a REGRESSION back to the old,
    // never-rounded invoice-level formula would now PASS this test if it
    // happened to also change perLineRoundedTotals() to match (it can't,
    // since that helper is this file's own, independent of the source under
    // test) -- the real protection against that regression is the identity
    // this file's OTHER tests already hold (Subtotal - Discount + Tax =
    // Total to the money epsilon) plus retail_p0_till_e2e.py's own
    // item #8/I-2 money-identity check against the REAL server.
    const expected = perLineRoundedTotals(RetailSystem._cart, discPct, beforeDiscount);
    assert.strictEqual(RetailSystem._currentTotals.subtotal, expected.subtotal, `subtotal mismatch (beforeDiscount=${beforeDiscount})`);
    assert.strictEqual(
      RetailSystem._currentTotals.discount, expected.discount,
      `discount mismatch (beforeDiscount=${beforeDiscount}): got ${RetailSystem._currentTotals.discount}, ` +
      `the server's own per-line-rounded formula gives ${expected.discount}. With zero promotions this ` +
      `must be EXACT, not merely close.`
    );
    assert.strictEqual(RetailSystem._currentTotals.tax, expected.tax, `tax mismatch (beforeDiscount=${beforeDiscount})`);
    // `total` alone uses assertClose, not strictEqual: subtotal/discount/tax
    // are each ROUNDED ONCE (clean 2dp values, so exact equality is the
    // right bar), but `total` is their plain JS-float sum/difference with no
    // library to keep it Decimal -- measured here at 59.68000000000001 vs
    // the mathematically-identical 59.68, the ordinary IEEE754 residue of
    // `59.74 - 7.47 + 7.41` in binary floating point, not a rounding
    // regression (pricing.py's own Decimal arithmetic on the server does not
    // have this artifact; _recalc()'s plain-float total does, and always
    // will without adopting a Decimal library here, which is out of this
    // crew's scope). _fmt()/_money() round it again for DISPLAY, so this
    // never reaches a screen; assertClose's 1e-9 tolerance is many orders of
    // magnitude tighter than a real money bug (the currency's own half-minor
    // -unit gate, MONEY_EPS elsewhere in this suite, is 0.0005) while still
    // absorbing this exact, harmless float artifact.
    assertClose(RetailSystem._currentTotals.total, expected.total, `total mismatch (beforeDiscount=${beforeDiscount}):`);
  }
}

// ═══════════════════════════════════════════════════════════════════════════
// (5) A failed promotions fetch leaves the till selling normally with no
//     promotions applied.
// ═══════════════════════════════════════════════════════════════════════════

async function testFailedPromotionsFetchDoesNotBlockSelling() {
  const catalogue = [
    { id: 'p1', name: 'Widget', sku: 'W1', barcode: '111', sell_price: 100, tax_rate: 0, total_stock: 10, category_id: 'c1' },
  ];
  const fetchImpl = makePosApi(catalogue, () => Promise.reject(new Error('simulated promotions outage')));
  const { RetailSystem, els } = loadRetailSystem({ fetchImpl });

  const host = makeElementStub();
  RetailSystem._renderPOS(host);
  await settle();

  // .length, not assert.deepStrictEqual(..., []) -- RetailSystem was loaded
  // into a separate vm realm, so its Array is not the outer Node process's
  // Array; comparing .length sidesteps that entirely rather than fighting it.
  assert.ok(
    Array.isArray(RetailSystem._promotions) && RetailSystem._promotions.length === 0,
    'A failed promotions fetch must leave this._promotions as an empty array, not undefined/stale, ' +
    `so nothing downstream mistakes it for real data. Got: ${JSON.stringify(RetailSystem._promotions)}`
  );
  assert.ok(
    Array.isArray(RetailSystem._products) && RetailSystem._products.length === 1,
    'The product catalogue must still load normally when ONLY the promotions fetch fails -- they are ' +
    `independent Promise.all entries with their own .catch(). Got _products=${JSON.stringify(RetailSystem._products)}`
  );

  RetailSystem._addToCart('p1');
  assert.strictEqual(RetailSystem._cart.length, 1, 'Selling must still work: _addToCart must succeed with the promotions endpoint down.');

  RetailSystem._recalc();
  assert.strictEqual(
    RetailSystem._currentTotals.total, 100,
    `With the promotions fetch down, the $100 item must total exactly $100 (no promotion silently applied, no crash). ` +
    `Got total=${RetailSystem._currentTotals.total}`
  );

  const cartHTML = els['pos-cart'].innerHTML;
  assert.ok(!/pos-promo-tag/.test(cartHTML), 'No promo tag must render when the promotions fetch failed. Got:\n' + cartHTML);
}

// ═══════════════════════════════════════════════════════════════════════════
// (6) THE WIRE IS UNCHANGED: _checkout sends the MANUAL discount_pct, never
//     the promoted one. The capability-gate protection -- the single most
//     important assertion in this file.
// ═══════════════════════════════════════════════════════════════════════════

async function testCheckoutWireSendsManualDiscountNotPromoted() {
  const fetchImpl = (url, opts) => {
    const u = String(url);
    if (u.indexOf('/api/sub/retail/sales') === 0 && opts && opts.method === 'POST') {
      return Promise.resolve({ status: 200, json: () => Promise.resolve({ status: 'success', data: { lines: [] } }) });
    }
    return Promise.reject(new Error('unhandled URL in test fetch mock: ' + u));
  };
  const { RetailSystem, posts } = loadRetailSystem({ fetchImpl, discPct: 5 });

  RetailSystem._cart = [widget({ line_total: 100 })];
  // A promotion resolves for this exact line at a MUCH higher rate (60%) than
  // the manual discount typed at the till (5%) -- the scenario where a
  // regression is most likely to leak the wrong number onto the wire.
  RetailSystem._promotions = [{ id: 9, name: 'Huge Promo', discount_pct: 60, product_id: 'p1', category_id: null }];
  RetailSystem._taxMode = 'after_discount';
  RetailSystem._paymentMethod = 'cash';
  RetailSystem._recalc(); // sanity: the ON-SCREEN total must reflect the 60% promo

  assertClose(RetailSystem._currentTotals.discount, 60, 'sanity: the preview must reflect the 60% promo, or this test proves nothing about what the wire diverges from.');

  await RetailSystem._checkout();

  const salesPost = posts.find((p) => p.url.indexOf('/api/sub/retail/sales') === 0);
  assert.ok(salesPost, 'No POST to /api/sub/retail/sales was captured -- _checkout did not run to completion. Posts seen: ' + JSON.stringify(posts));

  const line = salesPost.body.items && salesPost.body.items[0];
  assert.ok(line, 'The sales payload must carry at least one item. Got: ' + JSON.stringify(salesPost.body));
  assert.strictEqual(
    line.discount_pct, 5,
    `_checkout must send the MANUAL discount_pct (5, from #pos-disc) on every cart line, never the promoted ` +
    `one (60) and never a blend of the two. Sending anything else means a cashier without CAP_DISCOUNT could ` +
    `no longer sell a promoted item at all -- the shop's own weekend offer would lock its own till. ` +
    `Got discount_pct=${line.discount_pct}. Full item: ${JSON.stringify(line)}`
  );
}

// ─────────────────────────────────────────────────────────────────────────

// ═══════════════════════════════════════════════════════════════════════════
// (7) WHEN THE MANUAL DISCOUNT WINS, THE RENDERED LINE SHOWS ITS OWN GROSS,
//     UNTOUCHED -- never a per-line price with the manual % baked in.
//
//     ROUND 3 CORRECTION (rewritten, not merely re-tuned -- the old version's
//     CLAIM, not just its expected number, was wrong by the time this ran):
//     this used to assert the manual discount gets baked INTO the per-line
//     price when it beats a promotion (manual 30% > promo 20% -> $100.00 ->
//     $70.00 on the LINE). That was accurate for an EARLIER design. T2's
//     winner-takes-all-per-line redesign (subsystem-retail.js's own
//     `promoWins = promoFrac > manualFrac` in _renderCart, "AUDIT (Phase 0
//     #8)" comment on the line total branch) deliberately supersedes it: a
//     MANUAL discount is an INVOICE-LEVEL percentage (typed once, applies to
//     the whole cart), never a per-product promotional price, so it is
//     explained ONLY by the separate Discount($) row in the summary --
//     never baked into an individual line's own displayed price, even when
//     it is the larger of the two rates for that specific line. Baking it in
//     ($70.00) would ALSO have been "the cashier reads one number, the
//     drawer takes another" once accounting for the invoice-level Discount
//     row too: the fixed subsystem-retail.js prints the line at $100.00 and
//     the invoice Discount row separately explains the 30% off -- $50.00
//     (additive) was always wrong and still is.
//
//     What this rewrite can no longer catch: the OLD claim ("a promotion
//     that loses still gets baked into the line at its own reduced rate")
//     going red. That is not a regression any more -- it is the behaviour
//     this item now REQUIRES to be false. The money-safety invariant this
//     file still protects is retail_p0_till_e2e.py's own item
//     #11/interface-I-2 "winner-takes-all, both directions" E2E, proven
//     against the real running till, relay and receipt together.
// ═══════════════════════════════════════════════════════════════════════════

function testManualWinsRendersLineGrossUntouched() {
  const { RetailSystem, els } = loadRetailSystem({ discPct: 30 });
  RetailSystem._cart = [widget()];
  RetailSystem._promotions = [{ id: 1, name: 'Weekend Sale', discount_pct: 20, product_id: 'p1', category_id: null }];
  RetailSystem._taxMode = 'after_discount';

  RetailSystem._renderCart();
  const cartHTML = els['pos-cart'].innerHTML;

  assert.ok(
    cartHTML.includes('$100.00'),
    'When the manual discount (30%) beats the promotion (20%), the RENDERED line price must stay '
    + 'the line\'s own gross, UNTOUCHED ($100.00) -- the manual saving belongs only in the invoice-level '
    + 'Discount row, never baked into a per-line price. Got:\n' + cartHTML
  );
  assert.ok(
    !cartHTML.includes('$70.00'),
    'The rendered line must NOT show $70.00 (100 * (1 - 0.30)) -- that would mean the manual % got '
    + 'baked into this specific line\'s own price, which is no longer how a manual discount is '
    + 'represented (it is invoice-level only). Got:\n' + cartHTML
  );
  assert.ok(
    !cartHTML.includes('$50.00'),
    'The rendered line must NEVER show the SUM of the manual and promotional discounts '
    + '(30% + 20% = 50% -> $50.00). That would display a price the till does not charge. Got:\n'
    + cartHTML
  );
  assert.ok(
    !/pos-price-orig/.test(cartHTML),
    'No struck-through original price should render on this line -- that markup is reserved for a '
    + 'line where the PROMOTION wins, which is not this case (manual 30% > promo 20%). Got:\n' + cartHTML
  );
  assert.ok(
    !/pos-promo-tag/.test(cartHTML),
    'No "Promo" tag should render on this line -- the promotion did not win it. Got:\n' + cartHTML
  );
}

// ═══════════════════════════════════════════════════════════════════════════
// (8) PARENT TIER -- launch-readiness "product variants" follow-up (design
//     doc section 3.3 point 2). A promotion configured on a variant's PARENT
//     product ("20% off T-Shirts" set on the grouping product) must reach
//     the variant's cart line, mirroring core/retail/promotions.py's
//     resolve_line_discount_pct's new parent tier EXACTLY: exact-variant >
//     parent > category, specificity beats size at every step.
//
//     WHY THIS EXISTS AS ITS OWN CASE, SEPARATE FROM THE BACKEND'S OWN
//     COVERAGE (retail_promotions_test.py): _bestPromoFor is a SEPARATE
//     reimplementation of the server's tier order in JavaScript, for the
//     cart PREVIEW -- what the cashier and the customer see before Charge is
//     pressed. The server (create_sale) resolves independently and is what
//     actually gets charged. Nothing keeps the two in sync except both
//     being hand-maintained correctly; if this file skipped the parent tier
//     while the server had it, the till would preview one price and charge
//     another -- see mutation proof M5. And a parent tier that COMPILES but
//     never actually matches anything (M6) would pass every "not over-
//     discounted" assertion by construction, which is why
//     testParentPromotionAppliesToVariantLinePreview asserts a promotion
//     DOES resolve, not just that it never over-resolves.
// ═══════════════════════════════════════════════════════════════════════════

function testParentPromotionAppliesToVariantLinePreview() {
  const { RetailSystem, els } = loadRetailSystem();
  const item = widget({ product_id: 'variant1', parent_product_id: 'parent1', category_id: 'c1' });
  RetailSystem._cart = [item];
  RetailSystem._promotions = [
    { id: 1, name: 'Parent Promo', discount_pct: 15, product_id: 'parent1', category_id: null },
  ];
  RetailSystem._taxMode = 'after_discount';

  const resolved = RetailSystem._bestPromoFor(item);
  assert.ok(resolved, "_bestPromoFor must resolve a promotion configured on this line's PARENT product (parent_product_id), not just its own product_id or category_id.");
  assert.strictEqual(resolved.name, 'Parent Promo', `Got: ${JSON.stringify(resolved)}`);

  RetailSystem._renderCart();
  const cartHTML = els['pos-cart'].innerHTML;
  assert.ok(
    cartHTML.includes('$85.00'),
    `The parent-tier discount (15% off $100 = $85.00) must appear in the RENDERED cart line -- the exact ` +
    `preview-vs-charge defect a mutation caught during the promotions wave. Got:\n${cartHTML}`
  );

  RetailSystem._recalc();
  assertClose(RetailSystem._currentTotals.discount, 15, 'The previewed TOTAL must also reflect the 15% parent-tier discount.');
}

function testExactVariantPromotionBeatsParentPromotionInPreview() {
  const { RetailSystem } = loadRetailSystem();
  const item = widget({ product_id: 'variant1', parent_product_id: 'parent1', category_id: 'c1' });
  RetailSystem._cart = [item];
  RetailSystem._promotions = [
    // The parent's own rule is the BIGGER number -- specificity must still win.
    { id: 1, name: 'Parent Promo', discount_pct: 60, product_id: 'parent1', category_id: null },
    { id: 2, name: 'Variant Special', discount_pct: 5, product_id: 'variant1', category_id: null },
  ];
  RetailSystem._taxMode = 'after_discount';

  const resolved = RetailSystem._bestPromoFor(item);
  assert.strictEqual(
    resolved.name, 'Variant Special',
    `The exact-variant promotion (5%) must beat the parent promotion (60%), REGARDLESS of which percentage ` +
    `is larger -- mirrors core/retail/promotions.py's tier order exactly (mutation proof M3). Got: ${JSON.stringify(resolved)}`
  );

  RetailSystem._recalc();
  assertClose(RetailSystem._currentTotals.discount, 5, 'The previewed total must follow the exact-variant rule (5%), never the higher parent rate (60%).');
}

function testParentPromotionBeatsCategoryPromotionInPreview() {
  const { RetailSystem } = loadRetailSystem();
  const item = widget({ product_id: 'variant1', parent_product_id: 'parent1', category_id: 'c1' });
  RetailSystem._cart = [item];
  RetailSystem._promotions = [
    // The category rule is the BIGGER number -- specificity must still win.
    { id: 1, name: 'Category Blowout', discount_pct: 50, product_id: null, category_id: 'c1' },
    { id: 2, name: 'Parent Promo', discount_pct: 15, product_id: 'parent1', category_id: null },
  ];
  RetailSystem._taxMode = 'after_discount';

  const resolved = RetailSystem._bestPromoFor(item);
  assert.strictEqual(
    resolved.name, 'Parent Promo',
    `The parent-tier promotion (15%) must beat the category one (50%), REGARDLESS of which percentage is ` +
    `larger -- mirrors core/retail/promotions.py's tier order exactly (mutation proof M4). Got: ${JSON.stringify(resolved)}`
  );

  RetailSystem._recalc();
  assertClose(RetailSystem._currentTotals.discount, 15, 'The previewed total must follow the parent rule (15%), never the higher category rate (50%).');
}

const CASES = [
  ['a promoted line previews the discounted price and shows the promotion name', testPromotedLinePreviewsDiscountAndName],
  ['BEST PRICE WINS -- never summed', testBestPriceWinsNeverSums],
  ['a product-specific promotion beats a category promotion on the same product', testProductSpecificBeatsCategory],
  ['NO-PROMOTIONS EQUIVALENCE: totals match the server\'s own per-line-rounded maths exactly', testNoPromotionsEquivalence],
  ['a failed promotions fetch leaves the till selling normally', testFailedPromotionsFetchDoesNotBlockSelling],
  ['THE WIRE IS UNCHANGED: _checkout sends the manual discount_pct, not the promoted one', testCheckoutWireSendsManualDiscountNotPromoted],
  ['MANUAL WINS renders the line at its own gross, untouched (never baked-in, never summed)', testManualWinsRendersLineGrossUntouched],
  ['PARENT TIER: a promotion on the parent previews and charges the discount on a variant line', testParentPromotionAppliesToVariantLinePreview],
  ['PARENT TIER: an exact-variant promotion beats a parent promotion in the preview', testExactVariantPromotionBeatsParentPromotionInPreview],
  ['PARENT TIER: a parent promotion beats a category promotion in the preview', testParentPromotionBeatsCategoryPromotionInPreview],
];

async function main() {
  let failed = 0;
  for (const [name, fn] of CASES) {
    try {
      await fn();
      console.log('  ok   ' + name);
    } catch (err) {
      failed += 1;
      console.error('  FAIL ' + name);
      console.error('       ' + (err && err.message ? err.message : err));
    }
  }
  if (failed) {
    console.error(`FAIL: retail_promotions_ui_test.js — ${failed} of ${CASES.length} case(s) failed`);
    process.exitCode = 1;
  } else {
    console.log(`PASS: retail_promotions_ui_test.js — ${CASES.length} case(s)`);
  }
}

main();
