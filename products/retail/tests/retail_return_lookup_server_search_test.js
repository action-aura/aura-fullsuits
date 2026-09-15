/**
 * Regression test: a receipt older than the till's recent page must still be
 * refundable.
 *
 * ── THE SHIPPED BUG ──────────────────────────────────────────────────────────
 *
 * RetailSystem._findSaleForReturn() (products/retail/frontend/subsystem-retail.js)
 * resolved a typed receipt number by fetching
 *
 *     GET /api/sub/retail/sales/recent?limit=200
 *
 * and then running `.find()` over the result IN JAVASCRIPT. The server was
 * never told what was being looked for, so the search space was "the newest
 * 200 sales in the shop" and nothing else. A receipt older than that came back
 * as `Sale not found` -- about a sale that plainly exists, on the one screen
 * whose entire job is to refund it. On a busy till 200 sales is a few days.
 *
 * ── THE FIX, AND WHY IT NEEDED NO BACKEND WORK ───────────────────────────────
 *
 * `recent_sales` (products/retail/backend/api/retail_api.py) already accepts
 * `q`, and already does the right thing with it:
 *
 *     if q:
 *         conditions.append("(s.sale_number LIKE ? OR c.name LIKE ?)")
 *         params.extend([f'%{q}%', f'%{q}%'])
 *
 * -- a company-scoped (`s.company_id=?`) LIKE added to the WHERE clause, i.e.
 * applied BEFORE `ORDER BY s.created_at DESC LIMIT ?`, so the age of the
 * receipt stops mattering. That route's own docstring says it was extended for
 * exactly this problem ("the only gap was a way to FIND a past sale beyond the
 * last `limit` rows"), names THIS function as the caller that constrained its
 * gating, and keeps `q` open at any capability on the grounds that "a receipt
 * number is a single-sale question" -- because retail.refund is a cashier
 * default. So the whole defect was one client-side URL.
 *
 * ── WHAT THIS TEST ACTUALLY RUNS ─────────────────────────────────────────────
 *
 * The REAL, unmodified products/retail/frontend/subsystem-retail.js, loaded
 * through Node's vm module into a minimal sandboxed DOM (the harness style
 * every other *_test.js in this directory uses -- see
 * retail_checkout_error_toast_test.js). `fetch` is replaced by a FAKE SERVER
 * that models `recent_sales`'s query the way SQLite executes it:
 *
 *     filter by the LIKE (sale_number OR customer name, case-insensitive --
 *     SQLite's LIKE is case-insensitive for ASCII)   then
 *     ORDER BY created_at DESC                        then
 *     LIMIT ?
 *
 * That ordering is the whole point. A model that filtered AFTER the limit
 * would make the buggy client pass, so the model is asserted against itself in
 * case 3 below rather than trusted.
 *
 * No test framework is configured for this vanilla-JS, build-step-free
 * frontend (see CLAUDE.md), so this runs standalone on Node built-ins:
 *
 *   node products/retail/tests/retail_return_lookup_server_search_test.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const FRONTEND_FILE = path.join(__dirname, '..', 'frontend', 'subsystem-retail.js');

// ─────────────────────────────────────────────────────────────────────────────
// Fixture: one shop's sales book, deliberately deeper than the till's page.
// ─────────────────────────────────────────────────────────────────────────────

// The client asks for `limit=200`; the shop has more sales than that, and the
// receipt being refunded is the OLDEST one. Nothing about the numbers below is
// arbitrary -- case 3 asserts this fixture really does put the target outside
// the unfiltered window, because a fixture that quietly stopped doing so would
// make case 1 pass with the bug restored.
const DEVICE_A_SALES = 250;
const TILL_PAGE = 200;

// Real sale numbers are `SALE-<6-digit per-device counter>-<company8>-<device8>`
// (_next_ref + _device_doc_discriminator, retail_api.py). Both suffixes matter
// to case 2, so they are modelled rather than simplified away.
const COMPANY_FRAGMENT = 'a1b2c3d4';
const DEVICE_A_FRAGMENT = '11111111';

function saleNumberA(counter) {
  return `SALE-${String(counter).padStart(6, '0')}-${COMPANY_FRAGMENT}-${DEVICE_A_FRAGMENT}`;
}

const TARGET_SALE_ID = 1;
const TARGET_SALE_NUMBER = saleNumberA(1);
const TARGET_PRODUCT_NAME = 'Vintage Brass Lamp';

// A SECOND till that joined the shop recently. Its per-device counter is still
// low (doc_sequences is per-device and never synced -- AUDIT-032B) while its
// sales are the newest in the book, and its 8-hex device fragment happens to
// contain the digit run `000001`. That is the collision case 2 needs, and it
// is a real shape, not a contrivance: the fragment is uuid4-derived hex.
const DEVICE_B_SALE_NUMBER = `SALE-000003-${COMPANY_FRAGMENT}-c0000017`;

function buildBook() {
  const sales = [];
  for (let i = 1; i <= DEVICE_A_SALES; i++) {
    sales.push({
      id: i,
      sale_number: saleNumberA(i),
      customer_name: i === TARGET_SALE_ID ? 'Nadia Haddad' : 'Walk-in',
      // Minute-spaced so `ORDER BY created_at DESC` is unambiguous and id order
      // and time order agree (higher id == newer), the way an append-only
      // sales table behaves.
      created_at: new Date(Date.UTC(2026, 0, 1, 8, 0, 0) + i * 60_000)
        .toISOString().slice(0, 19).replace('T', ' '),
      total: 12.5,
    });
  }
  // Newest row in the shop, from the other till.
  sales.push({
    id: 9001,
    sale_number: DEVICE_B_SALE_NUMBER,
    customer_name: 'Walk-in',
    created_at: '2026-06-01 09:00:00',
    total: 400,
  });
  return sales;
}

const SALE_ITEMS = {
  [TARGET_SALE_ID]: [
    { product_id: 'p-lamp', product_name: TARGET_PRODUCT_NAME, quantity: 1, unit_price: 12.5 },
  ],
  9001: [
    { product_id: 'p-fridge', product_name: 'Display Fridge', quantity: 1, unit_price: 400 },
  ],
};

// ─────────────────────────────────────────────────────────────────────────────
// Fake server: a model of recent_sales + get_sale, WHERE-then-ORDER-then-LIMIT.
// ─────────────────────────────────────────────────────────────────────────────

function makeServer(book) {
  const requests = [];

  function queryRecent(search) {
    const q = (search.get('q') || '').trim();
    const limit = parseInt(search.get('limit') || '50', 10);
    const needle = q.toLowerCase();
    // WHERE ... (s.sale_number LIKE ? OR c.name LIKE ?) -- retail_api.py's
    // `conditions` block. SQLite's LIKE is case-insensitive for ASCII, which
    // is why the client can get away with comparing lowercased afterwards.
    const matched = q
      ? book.filter(s => s.sale_number.toLowerCase().includes(needle)
                      || (s.customer_name || '').toLowerCase().includes(needle))
      : book.slice();
    // ORDER BY s.created_at DESC LIMIT ? -- applied AFTER the filter, which is
    // the property the whole fix rests on.
    matched.sort((a, b) => (a.created_at < b.created_at ? 1 : a.created_at > b.created_at ? -1 : 0));
    return matched.slice(0, limit);
  }

  function fetchImpl(url) {
    requests.push(url);
    const u = new URL(url, 'http://till.local');
    if (u.pathname === '/api/sub/retail/sales/recent') {
      return jsonResponse({ status: 'success', data: queryRecent(u.searchParams) });
    }
    const m = u.pathname.match(/^\/api\/sub\/retail\/sales\/(\d+)$/);
    if (m) {
      const id = Number(m[1]);
      const sale = book.find(s => s.id === id);
      if (!sale) return jsonResponse({ status: 'error', message: 'Not found' }, 404);
      return jsonResponse({ status: 'success', data: { sale, items: SALE_ITEMS[id] || [] } });
    }
    // Anything else this screen incidentally asks for (settings, etc.) gets an
    // empty success rather than a throw, so an unrelated fetch cannot be
    // mistaken for the failure under test.
    return jsonResponse({ status: 'success', data: [] });
  }

  function jsonResponse(payload, httpStatus = 200) {
    return Promise.resolve({ status: httpStatus, json: async () => payload });
  }

  return { requests, fetchImpl, queryRecent };
}

// ─────────────────────────────────────────────────────────────────────────────
// Sandbox
// ─────────────────────────────────────────────────────────────────────────────

function makeElementStub(overrides) {
  return Object.assign({
    innerHTML: '',
    textContent: '',
    value: '',
    id: '',
    disabled: false,
    style: {},
    appendChild() {},
    getAttribute() { return null; },
    setAttribute() {},
    querySelectorAll() { return []; },
    addEventListener() {},
    focus() {},
    remove() {},
  }, overrides);
}

function loadRetailSystem({ fetchImpl, toasts, typedReceipt }) {
  const code = fs.readFileSync(FRONTEND_FILE, 'utf8');

  const searchBox = makeElementStub({ id: 'ret-sale-search', value: typedReceipt });
  const itemsBox = makeElementStub({ id: 'ret-sale-items' });
  const els = {
    'ret-sale-search': searchBox,
    'ret-sale-items': itemsBox,
  };

  const sandbox = {
    console,
    URL,
    URLSearchParams,
    t: (s) => s, // stand-in for i18n.js's global `t()` shorthand
    fetch: fetchImpl,
    getComputedStyle: () => ({ getPropertyValue: () => '' }),
    document: {
      getElementById(id) { return els[id] || makeElementStub({ id }); },
      createElement() { return makeElementStub(); },
      querySelector() { return makeElementStub(); },
      querySelectorAll() { return []; },
      head: { appendChild() {} },
      body: { appendChild() {} },
      documentElement: { getAttribute() { return null; } },
      addEventListener() {},
    },
    SubsystemApp: {
      showToast(msg, type) { toasts.push({ msg, type }); },
      checkAuthAndSetup() {},
    },
  };
  sandbox.window = sandbox;

  vm.createContext(sandbox);
  vm.runInContext(code, sandbox, { filename: FRONTEND_FILE });

  assert.ok(sandbox.RetailSystem, 'subsystem-retail.js did not expose window.RetailSystem');
  return { RetailSystem: sandbox.RetailSystem, itemsBox };
}

// ─────────────────────────────────────────────────────────────────────────────

async function main() {
  const book = buildBook();

  // ── Case 3 (run FIRST: it is the harness self-check) ──────────────────────
  // ENGINEERING.md failure shape #2 -- "the fixture manufactures the exact
  // state that hides the bug" -- and #5, "mutation-prove the harness too".
  // Case 1 below only means anything if the receipt it looks up is genuinely
  // out of reach of the old `?limit=200` fetch. Assert that against the same
  // model the client will talk to, rather than assuming it from the row count:
  // if someone later shrinks the fixture, or the model starts limiting before
  // filtering, THIS goes red instead of case 1 going quietly vacuous.
  {
    const probe = makeServer(book);
    const unfilteredPage = probe.queryRecent(new URLSearchParams({ limit: String(TILL_PAGE) }));
    assert.strictEqual(unfilteredPage.length, TILL_PAGE,
      'Harness self-check: the fixture must be deeper than one till page.');
    assert.ok(
      !unfilteredPage.some(s => s.sale_number === TARGET_SALE_NUMBER),
      'Harness self-check FAILED: the target receipt is inside the unfiltered ' +
      '?limit=200 window, so case 1 would pass even with the bug restored. ' +
      'Fixture/model is not load-bearing.'
    );
    const filteredPage = probe.queryRecent(
      new URLSearchParams({ q: TARGET_SALE_NUMBER, limit: String(TILL_PAGE) }));
    assert.deepStrictEqual(
      filteredPage.map(s => s.sale_number), [TARGET_SALE_NUMBER],
      'Harness self-check FAILED: the modelled server must return the old ' +
      'receipt when `q` names it -- otherwise case 1 proves nothing about the client.'
    );
  }

  // ── Case 1: the shipped bug. An old receipt must resolve. ─────────────────
  {
    const toasts = [];
    const server = makeServer(book);
    const { RetailSystem, itemsBox } = loadRetailSystem({
      fetchImpl: server.fetchImpl, toasts, typedReceipt: TARGET_SALE_NUMBER,
    });
    // _openCreateReturn() clears this when the modal opens (:8892); the Lookup
    // button is only reachable from inside that modal, so start from the state
    // the real screen is actually in rather than from `undefined`.
    RetailSystem._returnSaleId = null;

    await RetailSystem._findSaleForReturn();

    assert.strictEqual(
      RetailSystem._returnSaleId, TARGET_SALE_ID,
      'A receipt older than the last ' + TILL_PAGE + ' sales must still resolve, so a ' +
      'refund can be taken against it. _findSaleForReturn() did not select it. ' +
      'Toasts: ' + JSON.stringify(toasts) + ' | Requests: ' + JSON.stringify(server.requests)
    );
    assert.ok(
      itemsBox.innerHTML.includes(TARGET_PRODUCT_NAME),
      'The lines of the located sale must be rendered for the cashier to pick from. ' +
      'Rendered: ' + JSON.stringify(itemsBox.innerHTML.slice(0, 400))
    );
    assert.ok(
      !toasts.some(x => x.type === 'error'),
      'No error toast expected on a successful lookup. Got: ' + JSON.stringify(toasts)
    );

    // "Assert THE CHECK RAN, not just the outcome" (ENGINEERING.md shape #1).
    // The two assertions above would also hold if the client had simply
    // fetched the entire book and filtered in JS -- which is the same defect
    // with a bigger number. This pins that the SERVER was asked the question:
    // the receipt number has to travel in `q`.
    const recentCalls = server.requests
      .filter(u => u.startsWith('/api/sub/retail/sales/recent'))
      .map(u => new URL(u, 'http://till.local').searchParams);
    assert.strictEqual(recentCalls.length, 1,
      'Expected exactly one /sales/recent call. Got: ' + JSON.stringify(server.requests));
    assert.strictEqual(
      recentCalls[0].get('q'), TARGET_SALE_NUMBER,
      'The typed receipt number must be sent to the server as `q` -- that is the ' +
      'company-scoped LIKE that makes the age of the receipt irrelevant ' +
      '(recent_sales, retail_api.py). Requests: ' + JSON.stringify(server.requests)
    );
  }

  // ── Case 2: the allow-half's other half -- `q` is a LIKE, so the exact
  // match must stay on the client. ──────────────────────────────────────────
  // A cashier reading a smudged slip types the fragment `000001`. The server's
  // `%000001%` legitimately matches TWO rows: the target, and the OTHER till's
  // newest sale, whose uuid4-derived device fragment `c0000017` contains that
  // digit run. Neither is an exact receipt number. The correct answer is "not
  // found" -- refunding against whichever row came back first would be a
  // refund against an unrelated sale, for an unrelated amount.
  {
    const toasts = [];
    const server = makeServer(book);
    const matches = server.queryRecent(new URLSearchParams({ q: '000001', limit: String(TILL_PAGE) }));
    // Self-check for THIS case: the ambiguity has to be real, and the wrong
    // row has to be the one a naive `recent[0]` would take.
    assert.ok(matches.length >= 2,
      'Case-2 self-check FAILED: the fragment must match more than one sale, ' +
      'or "the client still exact-matches" is untested. Matched: ' +
      JSON.stringify(matches.map(s => s.sale_number)));
    assert.strictEqual(matches[0].sale_number, DEVICE_B_SALE_NUMBER,
      'Case-2 self-check FAILED: the newest LIKE match must NOT be the target, ' +
      'otherwise taking recent[0] would look correct.');

    const { RetailSystem } = loadRetailSystem({
      fetchImpl: server.fetchImpl, toasts, typedReceipt: '000001',
    });
    RetailSystem._returnSaleId = null; // as _openCreateReturn() leaves it (:8892)

    await RetailSystem._findSaleForReturn();

    assert.strictEqual(
      RetailSystem._returnSaleId, null,
      'A partial receipt fragment that LIKE-matches several sales must select NONE. ' +
      'It selected sale id ' + RetailSystem._returnSaleId + ' -- a refund would have been ' +
      'taken against a sale the cashier never named.'
    );
    assert.ok(
      toasts.some(x => x.type === 'error'),
      'The cashier must be told the receipt was not found rather than left with a ' +
      'silently empty item table. Toasts: ' + JSON.stringify(toasts)
    );
  }

  console.log('PASS: retail_return_lookup_server_search_test.js');
}

main().catch((err) => {
  console.error('FAIL: retail_return_lookup_server_search_test.js');
  console.error(err);
  process.exitCode = 1;
});
