/**
 * retail_pos_scale_test.js — regression tests for "the POS scale fix"
 * (ROADMAP.md 2026-08-29 v21; see also GET /products/lookup and the
 * `?q=`/`?limit=` additions to GET /products, both in retail_api.py).
 *
 * Bug: RetailSystem._loadPOSData() (products/retail/frontend/subsystem-retail.js)
 * fetched the ENTIRE product catalogue on every POS mount, and again after
 * every completed sale (_checkout calling _loadPOSData() a second time).
 * _findByCode() -- shared by the POS scan, the Products-screen scan, and the
 * PO scan -- did a client-side linear scan over that same array.
 * _renderPOSGrid() rendered every match with no cap, and the search box
 * re-filtered the local array on every keystroke with no debounce. At
 * 50,000 SKUs that is ~25MB per sale and a 50,000-button innerHTML rebuild
 * from one keystroke.
 *
 * Fix: the POS now loads a bounded first page (POS_GRID_PAGE_SIZE, 200),
 * searches the server instead of a local array (debounced), scans resolve
 * via GET /products/lookup instead of a linear scan, the grid caps its
 * render and says so when truncated, and a completed sale decrements the
 * cached stock locally instead of re-fetching the catalogue.
 *
 * This test loads the REAL products/retail/frontend/subsystem-retail.js (via
 * Node's vm module, never a reimplementation) into a sandboxed DOM with a
 * URL-routed fetch mock standing in for retail_api.py, and drives the real
 * _loadPOSData/_filterPOS/_findByCode/_posScan/_renderPOSGrid/_checkout code
 * paths.
 *
 * No test framework is configured for this vanilla-JS, build-step-free
 * frontend (see CLAUDE.md), so this runs standalone with only Node built-ins:
 *
 *   node products/retail/tests/retail_pos_scale_test.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const FRONTEND_FILE = path.join(__dirname, '..', 'frontend', 'subsystem-retail.js');
const SOURCE = fs.readFileSync(FRONTEND_FILE, 'utf8');

// ─────────────────────────────────────────────────────────────────────────────
// Harness
// ─────────────────────────────────────────────────────────────────────────────

function makeElementStub(overrides) {
  const classes = [];
  return Object.assign({
    innerHTML: '', textContent: '', value: '', id: '', disabled: false, style: {},
    classes,
    classList: {
      add(c) { if (!classes.includes(c)) classes.push(c); },
      remove(c) { const i = classes.indexOf(c); if (i !== -1) classes.splice(i, 1); },
      toggle(c, on) { if (on) this.add(c); else this.remove(c); },
      contains(c) { return classes.includes(c); },
    },
    appendChild() {}, getAttribute() { return null; }, setAttribute() {},
    querySelectorAll() { return []; }, addEventListener() {}, focus() {}, remove() {},
  }, overrides);
}

/**
 * Builds a synthetic catalogue plus a fetch mock that stands in for
 * retail_api.py's list_products/lookup_product routes -- URL-routed, and
 * honouring `limit`/`q`/`code` for real, so the client's own paging and
 * search logic is exercised against realistic responses, not a stub that
 * always returns everything. `calls` records every {method,url} pair, which
 * is what proves "no full-catalogue refetch" and "one search, not one per
 * keystroke".
 */
function makeApi(catalogue) {
  const calls = [];
  let salesHandler = () => Promise.reject(new Error('sales POST not stubbed for this test'));

  const json = (status, body) => Promise.resolve({ status, json: () => Promise.resolve(body) });

  const fetchImpl = (url, opts) => {
    const method = (opts && opts.method) || 'GET';
    calls.push({ method, url: String(url) });
    const u = new URL(String(url), 'http://till.local');
    const p = u.pathname;
    const q = u.searchParams;

    if (p === '/api/sub/retail/products/lookup') {
      const code = (q.get('code') || '').toLowerCase();
      const hit = catalogue.find(x =>
        (x.barcode || '').toLowerCase() === code || (x.sku || '').toLowerCase() === code);
      if (!hit) return json(404, { status: 'error', message: 'Product not found' });
      return json(200, { status: 'success', data: hit });
    }
    if (p === '/api/sub/retail/products') {
      let rows = catalogue;
      const term = q.get('q');
      if (term) {
        const needle = term.toLowerCase();
        rows = rows.filter(x =>
          x.name.toLowerCase().includes(needle) ||
          x.sku.toLowerCase().includes(needle) ||
          (x.barcode || '').toLowerCase().includes(needle));
      }
      const rawLimit = q.get('limit');
      if (rawLimit) rows = rows.slice(0, parseInt(rawLimit, 10));
      return json(200, { status: 'success', data: rows });
    }
    if (p === '/api/sub/retail/categories') return json(200, { status: 'success', data: [] });
    if (p === '/api/sub/retail/customers') return json(200, { status: 'success', data: [] });
    if (p === '/api/sub/retail/settings/tax') return json(200, { status: 'success', data: { tax_calculation_mode: 'after_discount' } });
    if (p === '/api/sub/retail/held-sales') return json(200, { status: 'success', data: [] });
    if (p === '/api/sub/retail/sales' && method === 'POST') return salesHandler();
    return Promise.reject(new Error('unhandled URL in test fetch mock: ' + method + ' ' + url));
  };

  return {
    calls,
    fetchImpl,
    setSalesHandler(fn) { salesHandler = fn; },
    productGets() { return calls.filter(c => c.method === 'GET' && c.url.indexOf('/api/sub/retail/products') === 0); },
    unfilteredProductGets() {
      // A "full catalogue" request the way the OLD code made it: the
      // products endpoint with no query string at all.
      return calls.filter(c => c.method === 'GET' && c.url === '/api/sub/retail/products');
    },
    searchGets() {
      return calls.filter(c => c.method === 'GET' && /\/api\/sub\/retail\/products\?.*q=/.test(c.url));
    },
  };
}

function loadRetailSystem(fetchImpl) {
  const els = Object.create(null);
  const toasts = [];
  const getEl = (id) => {
    if (!els[id]) els[id] = makeElementStub({ id });
    return els[id];
  };

  const sandbox = {
    console,
    t: (s) => s,
    fetch: fetchImpl,
    setTimeout, clearTimeout,               // real timers -- the debounce genuinely waits
    getComputedStyle: () => ({ getPropertyValue: () => '' }),
    navigator: { userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)' },
    localStorage: { getItem: () => null, setItem() {} },
    document: {
      activeElement: null,
      getElementById(id) { if (id === 'ret-styles') return null; return getEl(id); },
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

  vm.createContext(sandbox);
  vm.runInContext(SOURCE, sandbox, { filename: FRONTEND_FILE });
  assert.ok(sandbox.RetailSystem, 'subsystem-retail.js did not expose window.RetailSystem');

  return { RetailSystem: sandbox.RetailSystem, els, getEl, toasts };
}

/** A catalogue larger than POS_GRID_PAGE_SIZE (200), so page 1 genuinely
 *  excludes some real products -- the entire point of this fix. Indices
 *  200-249 are the ones that can ONLY be reached through the server (scan
 *  lookup or search), never through the client's own loaded page. */
function buildCatalogue(size) {
  const rows = [];
  for (let i = 0; i < size; i++) {
    rows.push({
      id: `p${i}`, name: `Product ${i}`, sku: `SKU-${i}`, barcode: `BC-${i}`,
      category_id: null, category_name: null,
      total_stock: 50, reorder_level: 5, sell_price: 9.99, unit: 'pc',
    });
  }
  return rows;
}

// ─────────────────────────────────────────────────────────────────────────────
// 1 — bounded first page, not the whole catalogue
// ─────────────────────────────────────────────────────────────────────────────

async function testMountRequestsABoundedPage() {
  const api = makeApi(buildCatalogue(250));
  const { RetailSystem } = loadRetailSystem(api.fetchImpl);

  await RetailSystem._loadPOSData();

  const productGets = api.productGets();
  assert.ok(productGets.length >= 1, 'No GET to /products was made at all.');
  assert.strictEqual(
    api.unfilteredProductGets().length, 0,
    'The POS mount fetched /api/sub/retail/products with NO query string -- ' +
    'that is the whole-catalogue request this fix removes. Calls: ' + JSON.stringify(productGets)
  );
  const withLimit = productGets.filter(c => /[?&]limit=\d+/.test(c.url));
  assert.strictEqual(withLimit.length, 1, 'Expected exactly one bounded (limit=) products request on mount.');
  // The client asks for POS_GRID_PAGE_SIZE+1 (never exactly the cap) so
  // _renderPOSGrid can tell "more exist" apart from "that's everything" with
  // no truncation flag from the backend -- see that method's own comment.
  // Bounded either way: nowhere near the 250-row catalogue this fixture holds.
  assert.ok(
    RetailSystem._products.length <= RetailSystem.POS_GRID_PAGE_SIZE + 1,
    `Page 1 held ${RetailSystem._products.length} products, more than the bounded page this fix requires.`
  );
  assert.ok(
    RetailSystem._products.length < 250,
    'Page 1 held the WHOLE 250-product catalogue -- the bounded request was not honoured.'
  );

  console.log('PASS: POS mount requests a bounded page, not the whole catalogue');
}

// ─────────────────────────────────────────────────────────────────────────────
// 2 — a scan for a code OUTSIDE the loaded page still resolves, via the endpoint
// ─────────────────────────────────────────────────────────────────────────────

async function testScanResolvesAProductOutsideTheLoadedPage() {
  const api = makeApi(buildCatalogue(250));
  const { RetailSystem } = loadRetailSystem(api.fetchImpl);

  await RetailSystem._loadPOSData();
  assert.ok(
    !RetailSystem._products.find(x => x.id === 'p249'),
    'Fixture is broken: p249 must NOT be in the loaded page, or this test proves nothing.'
  );

  await RetailSystem._posScan('BC-249');   // barcode of a product outside page 1

  const line = RetailSystem._cart.find(i => i.product_id === 'p249');
  assert.ok(
    line,
    'Scanning a barcode for a product outside the loaded page did not add it to the cart -- ' +
    'a local-array lookup can never find a product it never loaded. Cart: ' + JSON.stringify(RetailSystem._cart)
  );

  console.log('PASS: a scan for a code outside the loaded page resolves via the lookup endpoint');
}

// ─────────────────────────────────────────────────────────────────────────────
// 3 — "not found" vs "network failure" are told apart
// ─────────────────────────────────────────────────────────────────────────────

async function testNotFoundAndTransportFailureAreDistinguished() {
  // (a) genuinely unknown code -> the not-found prompt, no error toast.
  {
    const api = makeApi(buildCatalogue(5));
    const { RetailSystem, toasts } = loadRetailSystem(api.fetchImpl);
    await RetailSystem._loadPOSData();
    const notFoundCalls = [];
    RetailSystem._showScanNotFound = (code) => notFoundCalls.push(code);

    await RetailSystem._posScan('NOPE-DOES-NOT-EXIST');

    assert.deepStrictEqual(notFoundCalls, ['NOPE-DOES-NOT-EXIST'],
      'An unknown code must trigger the not-found prompt exactly once.');
    assert.strictEqual(toasts.filter(x => x.type === 'error').length, 0,
      'An unknown code must not also raise an error toast. Toasts: ' + JSON.stringify(toasts));
  }

  // (b) the lookup call itself fails (offline / non-401 server error) -> an
  //     error toast, and the not-found prompt must NOT fire (that would be
  //     telling the cashier the product doesn't exist, which is not what happened).
  {
    const failing = () => Promise.reject(new Error('simulated network failure'));
    const { RetailSystem, toasts } = loadRetailSystem(failing);
    RetailSystem._products = [];
    RetailSystem._productsById = Object.create(null);
    const notFoundCalls = [];
    RetailSystem._showScanNotFound = (code) => notFoundCalls.push(code);

    await RetailSystem._posScan('ANY-CODE');

    assert.deepStrictEqual(notFoundCalls, [],
      'A transport failure must NOT show the not-found prompt.');
    assert.ok(
      toasts.some(x => x.type === 'error'),
      'A transport failure during a scan must show an error toast. Toasts: ' + JSON.stringify(toasts)
    );
  }

  console.log('PASS: "not found" and "network failure" are distinguished on the scan path');
}

// ─────────────────────────────────────────────────────────────────────────────
// 4 — the grid caps its render and says so when truncated
// ─────────────────────────────────────────────────────────────────────────────

function testGridCapsAndAnnouncesTruncation() {
  const { RetailSystem, els } = loadRetailSystem(() => Promise.reject(new Error('no network in this test')));

  RetailSystem._products = buildCatalogue(250);
  RetailSystem._categories = [];
  RetailSystem._activeCat = null;

  RetailSystem._renderPOSGrid();

  const html = els['pos-product-grid'].innerHTML;
  // Count TILES, not every "pos-card*" class occurrence -- each tile's own
  // markup also carries .pos-card-icon/.pos-card-name/.pos-card-price/
  // .pos-card-stock, all of which share the "pos-card" prefix. The tile
  // itself is the only element opened with this exact button tag.
  const tileCount = (html.match(/<button type="button" class="pos-card/g) || []).length;
  assert.strictEqual(tileCount, 200, `Expected exactly 200 tiles rendered, got ${tileCount}.`);
  assert.ok(
    /Showing the first 200 matches/.test(html),
    'The grid did not say results were truncated. HTML head: ' + html.slice(0, 300)
  );

  // And the un-truncated case: no notice when everything fits.
  RetailSystem._products = buildCatalogue(5);
  RetailSystem._renderPOSGrid();
  const smallHtml = els['pos-product-grid'].innerHTML;
  assert.ok(
    !/Showing the first 200 matches/.test(smallHtml),
    'The truncation notice appeared even though every match fit on screen.'
  );

  console.log('PASS: the grid renders at most the cap, and announces truncation only when true');
}

// ─────────────────────────────────────────────────────────────────────────────
// 5 — typing several characters quickly issues ONE search
// ─────────────────────────────────────────────────────────────────────────────

async function testTypingQuicklyIssuesOneSearch() {
  const api = makeApi(buildCatalogue(5));
  const { RetailSystem, getEl } = loadRetailSystem(api.fetchImpl);
  await RetailSystem._loadPOSData();

  const search = getEl('pos-search');
  const term = 'SKU-';
  for (let i = 1; i <= term.length; i++) {
    search.value = term.slice(0, i);
    RetailSystem._filterPOS();   // fired on every keystroke, exactly like the real oninput
  }

  // Wait past the debounce window for the trailing search to actually fire.
  await new Promise((resolve) => setTimeout(resolve, RetailSystem.POS_SEARCH_DEBOUNCE_MS + 80));

  const searches = api.searchGets();
  assert.strictEqual(
    searches.length, 1,
    `Expected exactly ONE search request for ${term.length} keystrokes, got ${searches.length}: ` +
    JSON.stringify(searches)
  );
  assert.ok(
    searches[0].url.indexOf('q=' + encodeURIComponent(term)) !== -1,
    'The single search request did not carry the FINAL typed term. Got: ' + searches[0].url
  );

  console.log('PASS: typing several characters quickly issues one search, not one per keystroke');
}

// ─────────────────────────────────────────────────────────────────────────────
// 6 — no full-catalogue refetch after checkout, and cached stock is decremented
// ─────────────────────────────────────────────────────────────────────────────

async function testCheckoutDoesNotRefetchAndDecrementsCachedStock() {
  const api = makeApi(buildCatalogue(5));
  const { RetailSystem } = loadRetailSystem(api.fetchImpl);
  await RetailSystem._loadPOSData();

  const target = RetailSystem._products.find(x => x.id === 'p0');
  target.total_stock = 10;

  RetailSystem._cart = [{
    product_id: 'p0', name: target.name, quantity: 3,
    unit_price: 9.99, tax_rate: 0, line_total: 29.97, max_stock: 10,
  }];
  RetailSystem._currentTotals = { subtotal: 29.97, discount: 0, tax: 0, total: 29.97 };
  RetailSystem._paymentMethod = 'cash';

  api.setSalesHandler(() => Promise.resolve({
    status: 200,
    json: () => Promise.resolve({
      status: 'success',
      data: {
        id: 1, sale_number: 'S-0001', total: 29.97, change: 0,
        lines: [{ product_id: 'p0', quantity: 3 }],
      },
    }),
  }));

  api.calls.length = 0;   // isolate: only calls made DURING checkout matter here

  await RetailSystem._checkout();

  assert.strictEqual(
    api.unfilteredProductGets().length + api.productGets().filter(c => !/limit=|q=/.test(c.url)).length,
    0,
    'Checkout issued a GET to /products after a successful sale -- the full-catalogue ' +
    'refetch this fix removes. Calls made during checkout: ' + JSON.stringify(api.calls)
  );
  // Also assert directly: _loadPOSData's own products call shape (limit=)
  // never appears among the post-checkout calls at all.
  assert.strictEqual(
    api.calls.filter(c => c.url.indexOf('/api/sub/retail/products') === 0).length, 0,
    'Checkout made a request to the products endpoint at all -- expected only the sales POST.'
  );

  assert.strictEqual(target.total_stock, 7, 'Cached stock was not decremented by the quantity sold.');

  console.log('PASS: checkout issues no catalogue refetch and decrements cached stock locally');
}

// ─────────────────────────────────────────────────────────────────────────────
// 7 — _addToCart works for a product that arrived by scan, never in page 1
// ─────────────────────────────────────────────────────────────────────────────

async function testAddToCartWorksForAScanArrivedProduct() {
  const api = makeApi(buildCatalogue(250));
  const { RetailSystem } = loadRetailSystem(api.fetchImpl);
  await RetailSystem._loadPOSData();
  assert.ok(!RetailSystem._products.find(x => x.id === 'p230'), 'Fixture broken: p230 must be outside page 1.');

  // "Arrived by scan": the lookup path populates the by-id cache, same as a
  // real scan would, without going through the whole _posScan UI plumbing.
  const { product } = await RetailSystem._findByCode('BC-230');
  assert.ok(product, 'Setup failed: the lookup itself did not resolve p230.');

  RetailSystem._cart = [];
  RetailSystem._addToCart('p230');

  const line = RetailSystem._cart.find(i => i.product_id === 'p230');
  assert.ok(
    line,
    '_addToCart() could not find a product that arrived via scan and was never on the ' +
    'loaded page -- the by-id cache fallback is missing or not being read.'
  );

  console.log('PASS: _addToCart works for a product that arrived by scan and was never in page 1');
}

// ─────────────────────────────────────────────────────────────────────────────

async function main() {
  await testMountRequestsABoundedPage();
  await testScanResolvesAProductOutsideTheLoadedPage();
  await testNotFoundAndTransportFailureAreDistinguished();
  testGridCapsAndAnnouncesTruncation();
  await testTypingQuicklyIssuesOneSearch();
  await testCheckoutDoesNotRefetchAndDecrementsCachedStock();
  await testAddToCartWorksForAScanArrivedProduct();
  console.log('PASS: retail_pos_scale_test.js');
  // _showReceipt() (exercised in test 6) schedules a real 8s setTimeout to
  // auto-dismiss its overlay; this test's sandbox wires in the real timer
  // functions (needed for the debounce test), so without an explicit exit
  // here the process would sit idle until that timer fires.
  process.exit(0);
}

main().catch((err) => {
  console.error('FAIL: retail_pos_scale_test.js');
  console.error(err);
  process.exitCode = 1;
});
