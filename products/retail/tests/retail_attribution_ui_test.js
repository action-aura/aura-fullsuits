/**
 * Retail — surfacing v13 attribution (who rang it, on which till) in the UI.
 *
 * Schema v13 (_migrate_add_identity_and_attribution_columns, backend/database/
 * schema.py) added `actor_user_uid` / `terminal_id` / `created_at_utc` to
 * sales, returns, inventory_movements, cash_sessions and cash_movements. Two
 * properties of that migration decide everything this file asserts:
 *
 *   1. Existing rows were left NULL on purpose. The migration's docstring is
 *      explicit that a wrong name on a sale is worse than no name, and that
 *      this device cannot prove it is the terminal that rang a sale from
 *      before the column existed. So a large, permanent slice of every real
 *      install's history has no attribution at all, and the UI's job is to
 *      say so — not to guess, and not to render a blank cell that a manager
 *      chasing a discrepancy will read as a rendering bug.
 *
 *   2. The pre-existing free-text `cashier` column was KEPT and is never
 *      rewritten or used to derive `actor_user_uid`. It is the only surviving
 *      evidence of who the shop believed rang a transaction. Today it holds
 *      `_uid()` — `session['mt_user_id']`, a raw UUID — because
 *      `create_sale` defaults it that way and the POS never sends a name
 *      (retail_api.py). So "the recorded value" is frequently an opaque id,
 *      and the honest thing is to show it verbatim rather than pretty it up
 *      into something that looks like a person.
 *
 * ── The bidi hazard, which is the reason half these assertions exist ───────
 *
 * This product ships Arabic with `dir="rtl"` on <html> (i18n.js `apply()`).
 * Employee names and till identifiers sit directly beside numbers and dates,
 * which is exactly where the Unicode bidirectional algorithm goes wrong: a
 * run of neutral or Latin characters embedded in an RTL paragraph takes its
 * direction from the first STRONG character, so an ASCII id placed next to
 * Arabic words reorders the line and, worse, can visually split a UUID around
 * its own hyphens. `<bdi>` is the element the HTML spec added for precisely
 * this — it isolates a span of unknown-directionality text from its
 * surroundings — and needs no library, which matters in a frontend with no
 * build step (CLAUDE.md).
 *
 * The same hazard has a second form these tests pin: building one string out
 * of a translatable word and an id (`Invoice ${sale.sale_number}`). That is
 * bad twice over. In Arabic the leading Latin word makes the whole heading
 * resolve left-to-right, AND i18n.js's dictionary sweep matches a text node's
 * FULL trimmed text against the catalog, so a concatenated heading can never
 * match a key and stays permanently English on an Arabic page. Splitting the
 * word into its own text node fixes both at once.
 *
 * Standalone Node, no framework — this frontend has no build step:
 *
 *   node products/retail/tests/retail_attribution_ui_test.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const FRONTEND_FILE = path.join(__dirname, '..', 'frontend', 'subsystem-retail.js');

// A canonical uuid4, the shape both `terminal_id` (from
// schema.local_terminal_id() -> peek_local_device_uuid()) and today's
// `cashier` free text (mt_user_id) actually carry.
const TILL_UUID = '9f1c2f2a-6b1e-4a0d-9c3e-77f0a2b41d55';
const ACTOR_UUID = '3a7d4e10-5c22-4f9b-8e61-0d4c9a1b2e33';

// Breaks out of a double-quoted attribute and injects a tag. `users` is a
// shared, cross-device table (design §4) and an employee name can arrive from
// another device over the sync relay, so this is a real trust boundary, not a
// hypothetical one -- the same one the supplier/category modals escape for.
const MALICIOUS_NAME = `Sara" onmouseover="alert(document.cookie)"><img src=x onerror=alert(1)>`;

function makeElementStub(id) {
  return {
    innerHTML: '',
    textContent: '',
    value: '',
    id: id || '',
    className: '',
    style: {},
    dataset: {},
    parentElement: null,
    appendChild() {},
    addEventListener(type, fn) { this['_on' + type] = fn; },
    remove() {},
    getAttribute() { return null; },
    setAttribute() {},
    querySelector() { return makeElementStub(); },
    querySelectorAll() { return []; },
  };
}

/**
 * @param {object} opts
 *   responses -- map of url-substring -> parsed JSON body
 *   elements  -- map of id/selector -> element stub, for the panels under test
 * Returns { RetailSystem, calls, appended }
 */
function loadRetail(opts) {
  const options = opts || {};
  const calls = [];
  const appended = [];
  const elements = options.elements || {};

  const code = fs.readFileSync(FRONTEND_FILE, 'utf8');

  // A mapped value can be: an Error (the request itself fails -- offline, DNS,
  // connection reset), a function returning a Response-shaped object (for
  // cases where the HTTP STATUS is what is under test, e.g. a 404 from a
  // backend build that has no such route), or a plain body (200 + JSON).
  // Those three are genuinely different failures with different honest
  // messages, so the stub has to be able to express all three -- a stub that
  // could only reject would leave the status-dependent branches untested,
  // which is exactly the gap mutation testing surfaced here.
  const fetchImpl = (url) => {
    calls.push(url);
    for (const key of Object.keys(options.responses || {})) {
      if (url.indexOf(key) !== -1) {
        const r = options.responses[key];
        if (r instanceof Error) return Promise.reject(r);
        if (typeof r === 'function') return Promise.resolve(r());
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(r) });
      }
    }
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ status: 'success', data: [] }) });
  };

  const sandbox = {
    console,
    t: (s) => s,
    setTimeout, clearTimeout,
    // WHATWG globals, not ECMAScript ones -- a bare vm context does not carry
    // them, but every browser this ships to does.
    URLSearchParams,
    fetch: fetchImpl,
    getComputedStyle: () => ({ getPropertyValue: () => '' }),
    document: {
      getElementById(id) { return elements[id] || makeElementStub(id); },
      createElement() { return makeElementStub(); },
      querySelector(sel) { return elements[sel] || makeElementStub(); },
      head: { appendChild() {} },
      body: { appendChild(node) { appended.push(node); } },
      documentElement: { getAttribute() { return null; } },
    },
    SubsystemApp: {
      capabilities: null,               // fail open; capability gating is the other file's subject
      hasCapability() { return true; },
      showToast() {},
    },
  };
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(code, sandbox, { filename: FRONTEND_FILE });
  assert.ok(sandbox.RetailSystem, 'subsystem-retail.js did not expose window.RetailSystem');
  return { RetailSystem: sandbox.RetailSystem, calls, appended };
}

async function viewSale(sale, items) {
  const env = loadRetail({
    responses: { '/sales/': { status: 'success', data: { sale: sale, items: items || [] } } },
  });
  await env.RetailSystem._viewSale(1);
  assert.strictEqual(env.appended.length, 1, 'Expected the sale-detail modal to be appended to the document body.');
  return env.appended[0].innerHTML;
}

// ═══════════════════════════════════════════════════════════════════════════
// Sale detail — the one place a "who rang this, at which till" dispute lands
// ═══════════════════════════════════════════════════════════════════════════

async function testAttributedSaleShowsEmployeeAndTill() {
  const html = await viewSale({
    id: 1, sale_number: 'SALE-000012-ab12cd34', created_at: '2026-08-19T14:30:00',
    total: 42.5, status: 'completed', payment_method: 'cash',
    cashier: ACTOR_UUID, actor_user_uid: ACTOR_UUID, employee_name: 'Sara Haddad',
    terminal_id: TILL_UUID, created_at_utc: '2026-08-19T11:30:00Z',
  });

  assert.ok(
    html.includes('Sara Haddad'),
    'A sale carrying a resolved employee name must show the name. Got: ' + html.slice(0, 600)
  );
  assert.ok(
    /Till/.test(html),
    'The sale detail must label which till rang the sale -- `terminal_id` is on ' +
    'the row and this is the view where a discrepancy is investigated.'
  );
  // Not the whole 36-char uuid inline: it is unreadable next to a name and a
  // total, and on a phone it forces the header grid to wrap. A stable
  // leading fragment is enough to tell two tills apart, and the full value
  // stays available.
  assert.ok(
    html.includes(TILL_UUID.slice(0, 8)),
    'Expected a recognisable fragment of the till id to be rendered. Got: ' + html.slice(0, 800)
  );
  assert.ok(
    html.includes(TILL_UUID),
    'The FULL till id must remain in the markup (as a title/tooltip) -- a ' +
    'truncated-only id cannot be matched against device_registry.devices when ' +
    'someone actually needs to identify the terminal.'
  );
}

// The load-bearing honesty case. Every row rung before v13 looks like this.
async function testPreV13SaleSaysNotRecordedRatherThanBlank() {
  const html = await viewSale({
    id: 2, sale_number: 'SALE-000003-ab12cd34', created_at: '2025-11-02T09:05:00',
    total: 12, status: 'completed', payment_method: 'cash',
    cashier: null, actor_user_uid: null, employee_name: null,
    terminal_id: null, created_at_utc: null,
  });

  const matches = html.match(/Not recorded/g) || [];
  assert.ok(
    matches.length >= 2,
    'A sale predating v13 has neither an actor nor a terminal, and BOTH must ' +
    'say so in words. Blank cells read as a rendering bug to the person ' +
    'chasing a discrepancy, and an em dash alone does not distinguish "nobody ' +
    'recorded this" from "the page failed to load". Found ' + matches.length +
    ' honest markers in: ' + html.slice(0, 900)
  );
  assert.ok(
    !/undefined|null/.test(html.replace(/null"/g, '')),
    'A missing attribution value leaked into the rendered markup as the string ' +
    '"undefined" or "null". Got: ' + html.slice(0, 900)
  );
}

// v13's central rule, enforced at the display layer: never turn a recorded
// identifier into something that looks like a person's name.
async function testUnresolvedActorIsShownVerbatimNotGuessed() {
  const html = await viewSale({
    id: 3, sale_number: 'SALE-000009-ab12cd34', created_at: '2026-08-19T14:30:00',
    total: 20, status: 'completed', payment_method: 'cash',
    cashier: ACTOR_UUID, actor_user_uid: ACTOR_UUID, employee_name: null,
    terminal_id: TILL_UUID,
  });

  assert.ok(
    html.includes(ACTOR_UUID),
    'With no resolved name available, the recorded actor id must be shown as ' +
    'it stands. It is the only surviving evidence of who rang the sale; ' +
    'hiding it in favour of "Not recorded" would destroy real information, ' +
    'and inventing a name from it is exactly what v13 forbids.'
  );
  assert.ok(
    !/Not recorded/.test(html.split('Till')[0]),
    'The actor is on record here -- claiming it was "Not recorded" would be a ' +
    'different lie from guessing a name, but a lie either way.'
  );
}

// ── Bidirectional text ─────────────────────────────────────────────────────

async function testIdentifiersAreBidiIsolated() {
  const html = await viewSale({
    id: 4, sale_number: 'SALE-000012-ab12cd34', created_at: '2026-08-19T14:30:00',
    total: 42.5, status: 'completed', payment_method: 'cash',
    cashier: ACTOR_UUID, actor_user_uid: ACTOR_UUID, employee_name: 'سارة حداد',
    terminal_id: TILL_UUID,
  });

  const bdiCount = (html.match(/<bdi[\s>]/g) || []).length;
  assert.ok(
    bdiCount >= 2,
    'Identifiers rendered next to Arabic text must be wrapped in <bdi> (or an ' +
    'explicit dir="ltr" isolate). With dir="rtl" on <html>, an unisolated ' +
    'ASCII id takes its direction from the surrounding paragraph and a ' +
    'hyphenated uuid visually reorders around its own hyphens. Found ' +
    bdiCount + ' isolate(s) in: ' + html.slice(0, 900)
  );
}

// The named open example of this bug class: a heading that concatenates a
// translatable word with an ASCII id resolves left-to-right in Arabic because
// the first strong character is Latin -- and can never be translated at all,
// because i18n.js matches a text node's full trimmed text against the catalog.
async function testInvoiceHeadingDoesNotConcatenateWordAndNumber() {
  const html = await viewSale({
    id: 5, sale_number: 'SALE-000012-ab12cd34', created_at: '2026-08-19T14:30:00',
    total: 42.5, status: 'completed', payment_method: 'cash',
  });

  assert.ok(
    !/Invoice\s+SALE-/.test(html),
    'The modal heading still renders the single string "Invoice SALE-...". ' +
    'That text node can never match a catalog key, so the word stays English ' +
    'forever on an Arabic page, and its leading Latin character forces the ' +
    'whole heading left-to-right under dir="rtl". Got: ' + html.slice(0, 400)
  );
  assert.ok(
    /<bdi[^>]*>\s*SALE-000012-ab12cd34\s*<\/bdi>/.test(html),
    'The sale number must be bidi-isolated in its own <bdi>, leaving the word ' +
    'beside it as a standalone, translatable text node. Got: ' + html.slice(0, 400)
  );
}

async function testEmployeeNameIsEscaped() {
  const html = await viewSale({
    id: 6, sale_number: 'SALE-000012-ab12cd34', created_at: '2026-08-19T14:30:00',
    total: 42.5, status: 'completed', payment_method: 'cash',
    actor_user_uid: ACTOR_UUID, employee_name: MALICIOUS_NAME, terminal_id: TILL_UUID,
  });

  assert.ok(
    !html.includes('<img src=x'),
    'An employee name reached innerHTML unescaped. `users` is a shared, ' +
    'cross-device table -- this name is not guaranteed to be locally-typed ' +
    'text. Got: ' + html.slice(0, 900)
  );
  assert.ok(
    html.includes('&lt;img') || html.includes('&quot;'),
    'Expected the injected name to survive as escaped entities, proving it was ' +
    'rendered rather than silently dropped. Got: ' + html.slice(0, 900)
  );
}

// ═══════════════════════════════════════════════════════════════════════════
// Reports — takings and transaction count per employee
// ═══════════════════════════════════════════════════════════════════════════

function employeePanelEnv(body) {
  const tbody = makeElementStub();
  const note = makeElementStub();
  const days = makeElementStub('rep-days'); days.value = '30';
  const branch = makeElementStub('rep-branch'); branch.value = '';
  const env = loadRetail({
    responses: { '/reports/by-employee': body },
    elements: { '#rep-emp-table tbody': tbody, 'rep-emp-note': note, 'rep-days': days, 'rep-branch': branch },
  });
  return { env, tbody, note };
}

// Row shape copied from core/retail/metrics.py's revenue_by_employee(), not
// invented: {actor_user_uid, transactions, gross_sales, refunds, revenue,
// avg_ticket}, highest revenue first, with `employee_name` joined on by the
// route (the metrics module holds no connection to registry.db and says so).
async function testEmployeePanelRendersTakingsAndCount() {
  const { env, tbody } = employeePanelEnv({
    status: 'success',
    data: [
      { actor_user_uid: ACTOR_UUID, employee_name: 'Sara Haddad', transactions: 31,
        gross_sales: 1300.75, refunds: 50, revenue: 1250.75, avg_ticket: 40.35 },
      { actor_user_uid: null, employee_name: null, transactions: 12,
        gross_sales: 480, refunds: 0, revenue: 480, avg_ticket: 40 },
    ],
  });

  await env.RetailSystem._loadEmployeeSales();

  assert.ok(
    env.calls.some(u => /\/reports\/by-employee\?/.test(u) && /days=30/.test(u)),
    'The per-employee panel must query the period the Reports page is showing. ' +
    'Requests seen: ' + JSON.stringify(env.calls)
  );
  assert.ok(tbody.innerHTML.includes('Sara Haddad'), 'Expected the employee name in the table. Got: ' + tbody.innerHTML);
  assert.ok(/31/.test(tbody.innerHTML), 'Expected the transaction count. Got: ' + tbody.innerHTML);
  assert.ok(/1,?250\.75/.test(tbody.innerHTML), 'Expected the takings figure. Got: ' + tbody.innerHTML);
  assert.ok(
    /Not recorded/.test(tbody.innerHTML),
    'The unattributed bucket -- every sale rung before v13 -- must be labelled ' +
    'honestly rather than shown as an empty employee cell or folded silently ' +
    'into another row. Got: ' + tbody.innerHTML
  );
}

// The route genuinely absent from this backend build. Flask answers 404 with
// an HTML body, so .json() rejects too -- both halves are stubbed. This is the
// state the panel ships in until retail_api.py grows the route, so it is the
// state most installs will actually see first.
async function testMissingRouteSaysUnavailableNotZeroSales() {
  const { env, tbody } = employeePanelEnv(() => ({
    ok: false,
    status: 404,
    json: () => Promise.reject(new SyntaxError('Unexpected token < in JSON at position 0')),
  }));
  await env.RetailSystem._loadEmployeeSales();

  assert.ok(
    /not available on this version/i.test(tbody.innerHTML),
    'A 404 means this backend build has no per-employee route -- a specific, ' +
    'actionable fact. Reporting it as anything vaguer (or as "no sales") throws ' +
    'away the one piece of information that tells an owner to update rather ' +
    'than to go and ask their staff what happened. Got: ' + tbody.innerHTML
  );
  assert.ok(
    !/No sales in this period/.test(tbody.innerHTML),
    'A missing route was reported as "no sales in this period" -- a claim about ' +
    'the shop that nothing established. Got: ' + tbody.innerHTML
  );
}

// The request itself failing -- offline, connection reset, a proxy in the way.
// Deliberately NOT reported as "not available on this version": that would be
// a specific claim about the build, and a dropped connection establishes
// nothing about which routes exist.
async function testNetworkFailureIsNotReportedAsZeroSales() {
  const { env, tbody } = employeePanelEnv(new Error('network down'));
  await env.RetailSystem._loadEmployeeSales();

  assert.ok(
    tbody.innerHTML.length > 0,
    'The per-employee table was left empty when its endpoint failed. An empty ' +
    'table is indistinguishable from "nobody sold anything".'
  );
  assert.ok(
    !/No sales in this period/.test(tbody.innerHTML),
    'A failed request must not be reported as "no sales in this period" -- that ' +
    'states a fact about the shop that was never established. Got: ' + tbody.innerHTML
  );
  assert.ok(
    /Could not load/i.test(tbody.innerHTML),
    'Expected the honest "could not load" message for a request that never ' +
    'completed. Got: ' + tbody.innerHTML
  );
  assert.ok(
    !/not available on this version/i.test(tbody.innerHTML),
    'A dropped request was blamed on the backend version. Nothing here ' +
    'established which routes this build has. Got: ' + tbody.innerHTML
  );
}

async function testGenuinelyEmptyPeriodSaysSo() {
  const { env, tbody } = employeePanelEnv({ status: 'success', data: [] });
  await env.RetailSystem._loadEmployeeSales();
  assert.ok(
    /No sales in this period/.test(tbody.innerHTML),
    'A successful response with no rows is a real "no sales" answer and must ' +
    'say so. Got: ' + tbody.innerHTML
  );
}

// core/retail/metrics.py exists so that "revenue" is spelled out ONCE -- its
// docstring says the last time it was spelled twice, this product shipped
// screens that contradicted each other, and it carries a test
// (test_average_ticket_is_the_module_definition_not_a_local_one) whose entire
// job is to stop a second definition appearing. A division written in this
// file is that second definition, one layer up and out of that test's reach:
// it agrees today and silently stops agreeing the first time the module's
// definition changes (it already nets refunds out of revenue while NOT
// netting them out of transactions -- #1 and #2 -- which is not a rule a
// frontend would rediscover).
//
// The fixture below is deliberately self-inconsistent: 90 over 3 transactions
// is 30, but the payload states 12.50. Production can never produce that (the
// module computes avg_ticket from the very same two numbers it reports), and
// that is exactly why it discriminates -- it is the only way to tell "renders
// the value the server sent" apart from "recomputes it and happens to match".
async function testAvgTicketIsTheServersFigureNotARecomputedOne() {
  const { env, tbody } = employeePanelEnv({
    status: 'success',
    data: [{ actor_user_uid: ACTOR_UUID, employee_name: 'Sara Haddad', transactions: 3,
             gross_sales: 90, refunds: 0, revenue: 90, avg_ticket: 12.5 }],
  });
  await env.RetailSystem._loadEmployeeSales();

  assert.ok(
    tbody.innerHTML.includes('$12.50'),
    'The Avg Ticket cell must render the avg_ticket the server sent. Got: ' + tbody.innerHTML
  );
  assert.ok(
    !tbody.innerHTML.includes('$30.00'),
    'The Avg Ticket cell was recomputed on the client as revenue/transactions. ' +
    'That is a second definition of a figure core/retail/metrics.py exists to ' +
    'define exactly once. Got: ' + tbody.innerHTML
  );
}

// The module's docstring flags this as one of "three things that look like
// bugs and are not": a refund is charged to whoever processed it, so somebody
// who spent the shift on the returns desk reports negative takings and zero
// transactions -- and that is precisely what keeps the buckets summing back
// to the Revenue KPI above them. The UI must render it, not swallow or
// absolutise it.
async function testRefundOnlyEmployeeRendersNegativeTakings() {
  const { env, tbody } = employeePanelEnv({
    status: 'success',
    data: [{ actor_user_uid: ACTOR_UUID, employee_name: 'Omar Nasser', transactions: 0,
             gross_sales: 0, refunds: 80, revenue: -80, avg_ticket: 0 }],
  });
  await env.RetailSystem._loadEmployeeSales();

  assert.ok(
    tbody.innerHTML.includes('-$80.00'),
    'A refund-only employee reports negative takings and the table must show ' +
    'the sign. Hiding it breaks the reconciliation with the Revenue KPI on the ' +
    'same page. Got: ' + tbody.innerHTML
  );
  assert.ok(
    !/\$80\.00(?!<)/.test(tbody.innerHTML.replace('-$80.00', '')),
    'The negative was rendered as a positive somewhere. Got: ' + tbody.innerHTML
  );
}

// A row the server sent without the figure at all (an older backend, a route
// that forgot the field). Printing $0.00 would state an average that was
// never computed; the honest answer is the same one the rest of this work
// gives for an absent value.
async function testAbsentAvgTicketIsNotPrintedAsZero() {
  const { env, tbody } = employeePanelEnv({
    status: 'success',
    data: [{ actor_user_uid: ACTOR_UUID, employee_name: 'Sara Haddad',
             transactions: 3, revenue: 90 }],
  });
  await env.RetailSystem._loadEmployeeSales();

  assert.ok(
    !tbody.innerHTML.includes('$0.00'),
    'A missing avg_ticket was printed as $0.00 -- a figure nobody computed, ' +
    'shown beside two that were. Got: ' + tbody.innerHTML
  );
  assert.ok(
    tbody.innerHTML.includes('$90.00'),
    'Sanity: the figures that ARE present must still render. Got: ' + tbody.innerHTML
  );
}

async function testEmployeeNameInReportIsEscaped() {
  const { env, tbody } = employeePanelEnv({
    status: 'success',
    data: [{ actor_user_uid: ACTOR_UUID, employee_name: MALICIOUS_NAME, revenue: 10, transactions: 1 }],
  });
  await env.RetailSystem._loadEmployeeSales();
  assert.ok(!tbody.innerHTML.includes('<img src=x'), 'Employee name reached innerHTML unescaped in the report table. Got: ' + tbody.innerHTML);
}

async function testReportsPageWiresTheEmployeePanel() {
  const env = loadRetail({ responses: {} });
  const content = makeElementStub();
  await env.RetailSystem._renderReports(content);
  assert.ok(
    env.calls.some(u => /\/reports\/by-employee/.test(u)),
    'Opening Reports must load the per-employee view alongside the other ' +
    'panels. Requests seen: ' + JSON.stringify(env.calls)
  );
  assert.ok(
    /Sales by Employee/.test(content.innerHTML),
    'Expected the per-employee section heading on the Reports page. Got: ' + content.innerHTML.slice(0, 600)
  );
}

// ═══════════════════════════════════════════════════════════════════════════
// Audit Log — the one "who did it" surface that already existed
// ═══════════════════════════════════════════════════════════════════════════
//
// inventory_movements has no viewer anywhere in this frontend (see this
// file's companion report), so the Audit Log's User column is the nearest
// live equivalent: it renders `audit_log.user_id` -- a raw UUID -- in a
// monospace cell, inside a table that flips to RTL in Arabic. Same isolate,
// same reason.
async function testAuditLogUserIdIsBidiIsolated() {
  const tbody = makeElementStub();
  const env = loadRetail({
    responses: {
      '/audit-log': {
        status: 'success',
        data: [{ id: 1, user_id: ACTOR_UUID, action: 'VOID_PAYMENT', entity: 'PAYMENT', entity_id: 7, details: '', timestamp: '2026-08-19T14:30:00' }],
        meta: { total: 1, page: 1, limit: 50, actions: [], entities: [] },
      },
    },
    elements: { '#aud-table tbody': tbody },
  });
  env.RetailSystem._auditLog = { page: 1, limit: 50, date_from: '', date_to: '', action: '', entity: '', totalPages: 1 };
  await env.RetailSystem._loadAuditLog();

  assert.ok(tbody.innerHTML.includes(ACTOR_UUID), 'Sanity: the audit row must render its user id. Got: ' + tbody.innerHTML);
  assert.ok(
    /<bdi[^>]*>[^<]*3a7d4e10/.test(tbody.innerHTML),
    'The Audit Log user id must be bidi-isolated: it is an ASCII uuid in a ' +
    'monospace cell inside a table that renders right-to-left in Arabic. Got: ' +
    tbody.innerHTML
  );
}

// See the note in retail_reports_capability_gate_test.js: every case runs
// even after one fails, so a red run shows the whole surface rather than only
// its first casualty.
const CASES = [
  testAttributedSaleShowsEmployeeAndTill,
  testPreV13SaleSaysNotRecordedRatherThanBlank,
  testUnresolvedActorIsShownVerbatimNotGuessed,
  testIdentifiersAreBidiIsolated,
  testInvoiceHeadingDoesNotConcatenateWordAndNumber,
  testEmployeeNameIsEscaped,
  testEmployeePanelRendersTakingsAndCount,
  testMissingRouteSaysUnavailableNotZeroSales,
  testNetworkFailureIsNotReportedAsZeroSales,
  testGenuinelyEmptyPeriodSaysSo,
  testAvgTicketIsTheServersFigureNotARecomputedOne,
  testRefundOnlyEmployeeRendersNegativeTakings,
  testAbsentAvgTicketIsNotPrintedAsZero,
  testEmployeeNameInReportIsEscaped,
  testReportsPageWiresTheEmployeePanel,
  testAuditLogUserIdIsBidiIsolated,
];

async function main() {
  let failed = 0;
  for (const fn of CASES) {
    try {
      await fn();
      console.log('  ok   ' + fn.name);
    } catch (err) {
      failed += 1;
      console.error('  FAIL ' + fn.name);
      console.error('       ' + (err && err.message ? err.message : err));
    }
  }
  if (failed) {
    console.error(`FAIL: retail_attribution_ui_test.js — ${failed} of ${CASES.length} case(s) failed`);
    process.exitCode = 1;
  } else {
    console.log(`PASS: retail_attribution_ui_test.js — ${CASES.length} case(s)`);
  }
}

main();
