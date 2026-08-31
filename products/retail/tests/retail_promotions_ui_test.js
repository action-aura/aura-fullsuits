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
 * totals the old single invoice-level-percentage math produced.
 * testNoPromotionsEquivalence asserts this against an INDEPENDENT
 * reimplementation of the pre-promotions formula, not against the new code's
 * own internals -- see that function's own comment for why the aggregate
 * discount is computed as "the manual rate on everything, plus whatever a
 * promotion adds on top", rather than a per-line sum, and why that is not
 * merely a style choice: summing `line_total * frac` per line is
 * ALGEBRAICALLY equal to `subtotal * frac` but not numerically identical in
 * IEEE754 (measured divergence on ~33% of realistic fixtures at full
 * precision), which would have made this exact test flaky-red on an
 * unrelated refactor.
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

    const expected = oldFormulaTotals(RetailSystem._cart, discPct, beforeDiscount);
    assert.strictEqual(RetailSystem._currentTotals.subtotal, expected.subtotal, `subtotal mismatch (beforeDiscount=${beforeDiscount})`);
    assert.strictEqual(
      RetailSystem._currentTotals.discount, expected.discount,
      `discount mismatch (beforeDiscount=${beforeDiscount}): got ${RetailSystem._currentTotals.discount}, ` +
      `old formula gave ${expected.discount}. With zero promotions this must be EXACT, not merely close.`
    );
    assert.strictEqual(RetailSystem._currentTotals.tax, expected.tax, `tax mismatch (beforeDiscount=${beforeDiscount})`);
    assert.strictEqual(RetailSystem._currentTotals.total, expected.total, `total mismatch (beforeDiscount=${beforeDiscount})`);
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
// (7) BEST PRICE WINS *IN THE RENDERED LINE*, not just in the totals.
//
//     WHY THIS EXISTS AS A SEPARATE CASE FROM (2): there are TWO places that
//     resolve a line's effective discount -- _renderCart (what the cashier and
//     the customer SEE on the line) and _recalc (what the totals say). Case (2)
//     drives _recalc only, and case (1) renders with NO manual discount, where
//     max(0, promo) and 0 + promo are the same number. So a stacking bug in the
//     RENDER path passed the entire suite: mutating _renderCart's
//     `Math.max(manualFrac, promoFrac)` to `manualFrac + promoFrac` left every
//     case green.
//
//     What that would have shipped: a $100 line with a 30% manual discount and a
//     20% promotion displaying $50.00 on the line while the total charged $70.00.
//     The cashier reads one number to the customer and the drawer takes another.
// ═══════════════════════════════════════════════════════════════════════════

function testRenderedLinePriceAlsoTakesBestPriceNotSum() {
  const { RetailSystem, els } = loadRetailSystem({ discPct: 30 });
  RetailSystem._cart = [widget()];
  RetailSystem._promotions = [{ id: 1, name: 'Weekend Sale', discount_pct: 20, product_id: 'p1', category_id: null }];
  RetailSystem._taxMode = 'after_discount';

  RetailSystem._renderCart();
  const cartHTML = els['pos-cart'].innerHTML;

  assert.ok(
    cartHTML.includes('$70.00'),
    'The RENDERED line price must use the best single discount (manual 30% beats promo 20%, '
    + 'so $100 -> $70.00). Got:\n' + cartHTML
  );
  assert.ok(
    !cartHTML.includes('$50.00'),
    'The rendered line must NEVER show the SUM of the manual and promotional discounts '
    + '(30% + 20% = 50% -> $50.00). That would display a price the till does not charge. Got:\n'
    + cartHTML
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
  ['NO-PROMOTIONS EQUIVALENCE: totals match the old invoice-level maths exactly', testNoPromotionsEquivalence],
  ['a failed promotions fetch leaves the till selling normally', testFailedPromotionsFetchDoesNotBlockSelling],
  ['THE WIRE IS UNCHANGED: _checkout sends the manual discount_pct, not the promoted one', testCheckoutWireSendsManualDiscountNotPromoted],
  ['BEST PRICE WINS IN THE RENDERED LINE, not just the totals', testRenderedLinePriceAlsoTakesBestPriceNotSum],
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
