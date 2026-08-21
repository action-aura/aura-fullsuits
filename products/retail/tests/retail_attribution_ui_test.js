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
    // Identity by default. Pass `t: markingT` to prove a label is actually
    // wrapped in t() at the point it is rendered -- an unwrapped literal
    // reaches innerHTML as bare English and is then invisible to i18n.js's
    // catalog sweep only when it is NOT also a catalog key, which is exactly
    // the failure a source grep cannot tell apart from a translated one.
    t: options.t || ((s) => s),
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

async function viewSale(sale, items, extra) {
  const env = loadRetail(Object.assign({
    responses: { '/sales/': { status: 'success', data: { sale: sale, items: items || [] } } },
  }, extra || {}));
  await env.RetailSystem._viewSale(1);
  assert.strictEqual(env.appended.length, 1, 'Expected the sale-detail modal to be appended to the document body.');
  return env.appended[0].innerHTML;
}

// A t() that proves it ran. Any label rendered through t() comes back
// wrapped; a hardcoded English literal in the template does not.
const markingT = (s) => '‹' + s + '›';

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

// ── The sale detail names a person the same way the report does ────────────
//
// GET /sales/<id> resolves `actor_user_uid` through the same helper the
// by-employee route uses, and ships three fields: `employee_name` (the shared
// display string this file reads) plus `actor_employee_id` / `actor_email`
// (the raw pair Android reads). Prefixed on this route, unprefixed on the
// report -- the same values under two spellings, which is precisely the kind
// of detail that silently renders `undefined` if a client assumes one shape.
async function testSaleDetailShowsTheResolvedNameFromItsOwnFieldSpelling() {
  const html = await viewSale({
    id: 7, sale_number: 'SALE-000021-ab12cd34', created_at: '2026-08-19T14:30:00',
    total: 20, status: 'completed', payment_method: 'cash',
    cashier: ACTOR_UUID, actor_user_uid: ACTOR_UUID, terminal_id: TILL_UUID,
    employee_name: 'sam@shop.test', actor_email: 'sam@shop.test', actor_employee_id: 'EMP-0002',
  });

  assert.ok(
    html.includes('sam@shop.test'),
    'The sale detail must show the resolved identity GET /sales/<id> now sends. ' +
    'Got: ' + html.slice(0, 900)
  );
  assert.ok(
    !/Account removed|Not recorded/.test(html.split('Till')[0]),
    'A resolved cashier must not also carry an unresolved marker. Got: ' +
    html.slice(0, 900)
  );
}

// The version-skew direction that actually happens: the Android app ships its
// own embedded Python server, so a newer frontend regularly talks to an older
// backend. Such a build answers with `s.*` and nothing else -- no identity
// fields at all. "The route sent employee_name: null" and "the route has no
// such field" are different facts, and only the first licenses "the account
// was deleted". Inferring deletion from a field nobody sent would be a fresh
// lie of exactly the shape v13 refused when it left old rows NULL rather than
// stamping them with this machine's identity.
async function testSaleDetailDoesNotInferDeletionFromAFieldNobodySent() {
  const html = await viewSale({
    id: 10, sale_number: 'SALE-000022-ab12cd34', created_at: '2026-08-19T14:30:00',
    total: 20, status: 'completed', payment_method: 'cash',
    cashier: ACTOR_UUID, actor_user_uid: ACTOR_UUID, terminal_id: TILL_UUID,
  });

  assert.ok(
    !/Account removed/i.test(html),
    'A backend that never carried identity fields was reported as having ' +
    'deleted the cashier\'s account. Nothing here established that anyone was ' +
    'ever looked up. Got: ' + html.slice(0, 900)
  );
  assert.ok(
    !/undefined/.test(html),
    'A field the older backend never sent leaked into the markup as ' +
    '"undefined". Got: ' + html.slice(0, 900)
  );
  assert.ok(
    html.includes(ACTOR_UUID),
    'Sanity: the recorded actor uid must still be shown. Got: ' + html.slice(0, 900)
  );
}

// The state that IS available on this route now: it looked, and found nobody.
// All three identity fields present and null together is the route's own
// documented signal for that, and it means something specific and useful --
// the account that rang this sale has been deleted since.
async function testSaleDetailReportsADeletedAccountWhenTheRouteLookedAndFoundNobody() {
  const html = await viewSale({
    id: 11, sale_number: 'SALE-000023-ab12cd34', created_at: '2026-08-19T14:30:00',
    total: 20, status: 'completed', payment_method: 'cash',
    cashier: ACTOR_UUID, actor_user_uid: ACTOR_UUID, terminal_id: TILL_UUID,
    employee_name: null, actor_email: null, actor_employee_id: null,
  });

  assert.ok(
    /Account removed/.test(html),
    'The route resolved this uid and found nobody -- that is a real fact about ' +
    'the shop (an employee record is gone) and the same words Android prints ' +
    'for it. Got: ' + html.slice(0, 900)
  );
  assert.ok(
    !/Not recorded/.test(html.split('Till')[0]),
    'A recorded-but-unresolvable actor is not the pre-v13 bucket. Got: ' +
    html.slice(0, 900)
  );
}

// `sales.cashier` is free text -- `data.get('cashier', _uid())` in
// create_sale, i.e. session['mt_user_id'], which is registry `users.ID`.
// `actor_user_uid` is `users.UID`. Both are uuid4 strings, they are different
// identity spaces, and metrics.py's rule 8 refuses to read `cashier` at all
// ("money grouped by free text would look authoritative and be worthless").
// Rendering it in the same cell, in the same shape, under the same label meant
// a manager copying the shown fragment to look someone up searched the wrong
// column and found nobody -- while the screen looked entirely correct. The two
// screens must also agree: the by-employee report never reads this column, so
// a sale that report calls unattributed must not read as attributed here.
async function testLegacyCashierFreeTextIsNotPresentedAsTheActor() {
  const html = await viewSale({
    id: 8, sale_number: 'SALE-000004-ab12cd34', created_at: '2025-11-02T09:05:00',
    total: 12, status: 'completed', payment_method: 'cash',
    cashier: ACTOR_UUID, actor_user_uid: null, terminal_id: null,
  });

  const beforeTill = html.split('Till')[0];
  assert.ok(
    /Not recorded/.test(beforeTill),
    'A sale with no `actor_user_uid` is unattributed -- that is exactly the ' +
    'bucket revenue_by_employee() reports under a NULL key -- and this cell ' +
    'must say so. Falling back to the free-text `cashier` column makes an ' +
    'unattributed sale read as attributed, on the one screen a dispute lands ' +
    'on. Got: ' + beforeTill.slice(0, 900)
  );
  assert.ok(
    !new RegExp(ACTOR_UUID.slice(0, 8) + '\\s*…?\\s*</bdi>').test(beforeTill),
    'The legacy free-text `cashier` value was rendered as the actor identity. ' +
    'It is a users.ID, not the users.UID this label now means. Got: ' +
    beforeTill.slice(0, 900)
  );
  assert.ok(
    html.includes(ACTOR_UUID),
    'The recorded free-text value must not be DESTROYED either -- v13 kept the ' +
    'column because it is the only surviving evidence of who the shop believed ' +
    'rang the sale. Keep it reachable (a title/tooltip), just not presented as ' +
    'a resolved identity. Got: ' + html.slice(0, 900)
  );
}

// Consistency nit with real consequences in Arabic. Five of the six labels in
// the sale-detail grid were hardcoded English while `Till` alone went through
// t(). Both render identically in English, so review sees nothing; on an
// Arabic page the row reads as five English words and one Arabic one.
//
// i18n.js's DOM sweep would in fact catch a bare `Status`/`Total`/`Cashier`
// (they are catalog keys), which is precisely why a source grep is the wrong
// instrument here -- it cannot tell a label that is wrapped from one that is
// merely lucky, and `Customer`/`Payment` were in NEITHER catalog, so they were
// not lucky. This drives the real render with a t() that marks what passed
// through it.
async function testSaleDetailGridLabelsAllGoThroughT() {
  const html = await viewSale(
    { id: 9, sale_number: 'SALE-000031-ab12cd34', created_at: '2026-08-19T14:30:00',
      total: 42.5, status: 'completed', payment_method: 'cash',
      actor_user_uid: ACTOR_UUID, terminal_id: TILL_UUID },
    [], { t: markingT }
  );

  const missing = ['Customer', 'Cashier', 'Till', 'Payment', 'Status', 'Total']
    .filter(label => html.indexOf('‹' + label + '›') === -1);
  assert.deepStrictEqual(
    missing, [],
    'These sale-detail grid labels never went through t(): ' + JSON.stringify(missing) +
    '. They are siblings in one six-cell grid; wrapping some and not others is ' +
    'how a row ends up half-Arabic. (They must also exist in BOTH catalogs -- ' +
    'retail_attribution_i18n_test.py asserts that half.)'
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

// ── The three identity states of a by-employee row ─────────────────────────
//
// The route contract (settled before implementation; see the wave's contract
// document, §2.5-2.6) gives every row four identity fields, all four null
// together for the unattributed bucket:
//
//   {"actor_user_uid": "<uuid>|null", "employee_id": "<EMP-000n>|null",
//    "email": "<users.email>|null",   "employee_name": "<display>|null",
//    "transactions", "gross_sales", "refunds", "revenue", "avg_ticket"}
//
// `employee_name` is the server's single pre-formatted display string, derived
// from the SAME two columns Android receives, so the two clients cannot
// disagree about who a row is. These cases pin the three states the pair of
// clients must render identically, and the fourth field set (blank strings)
// that neither may accept as an identity.

// Android's order, and it is not arbitrary: EmployeesScreen shows the email in
// bold with the assigned id underneath, so a report that ordered them the other
// way reads as being about different people. The desktop follows rather than
// invents, and it reads `employee_name` first so that when the server sends its
// pre-formatted string the two clients are literally reading the same value.
async function testResolvedNameUsesEmailBeforeEmployeeId() {
  const { env, tbody } = employeePanelEnv({
    status: 'success', success: true,
    data: [{ actor_user_uid: ACTOR_UUID, employee_id: 'EMP-0002', email: 'sam@shop.test',
             employee_name: null, transactions: 4, gross_sales: 60, refunds: 0,
             revenue: 60, avg_ticket: 15 }],
  });
  await env.RetailSystem._loadEmployeeSales();

  assert.ok(
    tbody.innerHTML.includes('sam@shop.test'),
    'With both an email and an employee id available the email is the identity ' +
    'shown, matching EmployeesScreen and the Android report. Got: ' + tbody.innerHTML
  );
  assert.ok(
    !/Account removed|Not recorded/.test(tbody.innerHTML),
    'A row that resolved to a person must not also carry an unresolved marker. ' +
    'Got: ' + tbody.innerHTML
  );
}

// The contract names this case explicitly as a server bug to defend against:
// `"email": ""` for a row that could not be resolved. A blank string is not an
// identity, and treating one as a name produces a nameless name -- a row that
// looks resolved, points at nobody, and cannot be told apart from a rendering
// fault.
async function testBlankIdentityStringsAreNotIdentities() {
  const { env, tbody } = employeePanelEnv({
    status: 'success', success: true,
    data: [{ actor_user_uid: ACTOR_UUID, employee_id: '   ', email: '',
             employee_name: '', transactions: 2, gross_sales: 30, refunds: 0,
             revenue: 30, avg_ticket: 15 }],
  });
  await env.RetailSystem._loadEmployeeSales();

  assert.ok(
    /Account removed/.test(tbody.innerHTML),
    'Blank identity strings must be treated as absent, leaving this row in the ' +
    'unresolved-uid state rather than rendering an empty employee cell. Got: ' +
    tbody.innerHTML
  );
}

// State 2 of 3, and the one with no desktop precedent. The route DID try to
// resolve this uid (it sent the identity keys) and came back with nothing, so
// the account that rang these sales is gone. That is a real, specific fact and
// the row must say it in words -- it is neither a person (there is no name to
// print) nor the unattributed bucket (there IS a recorded actor, and calling it
// "not recorded" would erase the only evidence the sale carries).
async function testUnresolvableActorIsNamedAsSuchNotAsAPersonNorAsUnrecorded() {
  const { env, tbody } = employeePanelEnv({
    status: 'success', success: true,
    data: [{ actor_user_uid: ACTOR_UUID, employee_id: null, email: null,
             employee_name: null, transactions: 9, gross_sales: 300, refunds: 0,
             revenue: 300, avg_ticket: 33.33 }],
  });
  await env.RetailSystem._loadEmployeeSales();

  assert.ok(
    /Account removed/.test(tbody.innerHTML),
    'A uid the route tried and failed to resolve must be labelled in words. ' +
    'Android calls this state ACCOUNT_GONE and prints "Account removed"; the ' +
    'two clients naming the same row differently is the drift this contract ' +
    'was settled to prevent. Got: ' + tbody.innerHTML
  );
  assert.ok(
    !/Not recorded/.test(tbody.innerHTML),
    'An unresolvable actor was reported as "Not recorded". There IS a recorded ' +
    'actor on these sales -- that is a different fact from the pre-v13 bucket, ' +
    'and collapsing the two hides that the shop lost an employee record. Got: ' +
    tbody.innerHTML
  );
  assert.ok(
    tbody.innerHTML.includes(ACTOR_UUID),
    'The uid itself must survive as evidence (a fragment on screen, the whole ' +
    'value one hover away) -- it is what makes the row traceable at all. Got: ' +
    tbody.innerHTML
  );
}

// State 3 of 3. This bucket holds most of a real shop's money on the day the
// feature ships (every row written before v13), it sorts by revenue like every
// other row so it usually lands FIRST, and there is exactly one of it. Nothing
// about it may read as a person.
//
// ── Why the fixture carries a NAME on the null-uid row ─────────────────────
//
// This case used to feed `{actor_user_uid: null, employee_id: null, email:
// null, employee_name: null}` -- all four fields null together, the shape the
// route contract documents. Every assertion below passed on it, and every one
// of them would have passed on a classifier that never looked at
// `actor_user_uid` at all, because with no name on the row there is no name to
// wrongly print. The test named for this exact concern was never asking the
// classifier the question it exists to answer.
//
// It was not answering it correctly either. `_attributionState` tested the
// NAME first and returned 'named' without ever consulting the uid, so
// `{actor_user_uid: null, employee_name: "Sara Haddad"}` resolved 'named' on
// the desktop while Android's `attributionOf` -- which checks the uid FIRST
// (EmployeeSalesScreen.kt) -- resolved NOT_RECORDED for the same row. Two
// clients, one row, two different people named.
//
// The desktop's answer is the dangerous one, and specifically here rather than
// anywhere else: on THIS report the null-uid row is not one sale, it is the
// aggregate of every sale rung before v13 added the column. A name landing on
// it attributes the shop's entire pre-v13 history -- 120 transactions and
// 5,400 of takings in this fixture -- to one person, which is precisely the
// fabrication v13 refused to commit when it left `actor_user_uid` NULL rather
// than backfilling it from the free-text `cashier` column.
//
// The name does not have to be malice or a server bug to arrive: a joined-in
// default, a "POS" placeholder, or a well-meaning display string computed
// before the uid was checked all produce it. So the settled rule is the one
// Android already implements -- a name is only ever shown when a NON-BLANK
// ACTOR UID resolved to it -- and the fixture below is the one that can tell
// the difference. The second row is not padding: it is the control that keeps
// this case honest in the other direction, since a classifier that answered
// 'unattributed' for everything would satisfy the first row's assertions
// perfectly.
async function testUnattributedBucketNeverLooksLikeAPerson() {
  const { env, tbody, note } = employeePanelEnv({
    status: 'success', success: true,
    data: [
      // The pre-v13 aggregate, arriving with a name attached to it.
      { actor_user_uid: null, employee_id: 'EMP-0001', email: 'sara@shop.test',
        employee_name: 'Sara Haddad', transactions: 120, gross_sales: 5400,
        refunds: 0, revenue: 5400, avg_ticket: 45 },
      // A genuinely attributed row, in the same response.
      { actor_user_uid: ACTOR_UUID, employee_id: 'EMP-0002', email: 'sam@shop.test',
        employee_name: 'sam@shop.test', transactions: 4, gross_sales: 60,
        refunds: 0, revenue: 60, avg_ticket: 15 },
    ],
  });
  await env.RetailSystem._loadEmployeeSales();

  // The classifier, asked directly. The rendered assertions below are the
  // consequence; this one is the decision, and it is the decision that has to
  // match Android's. Asserting only the markup would let a future refactor
  // reach the right cell text for the wrong reason.
  assert.strictEqual(
    env.RetailSystem._attributionState({ actor_user_uid: null, employee_name: 'Sara Haddad' }),
    'unattributed',
    'A row with NO recorded actor uid was classified as a named person purely ' +
    'because a name field happened to be populated. Android\'s attributionOf() ' +
    'checks the uid first and calls this NOT_RECORDED; the two clients must not ' +
    'name the same row differently, and on this report that row is every ' +
    'pre-v13 sale in the shop.'
  );
  assert.strictEqual(
    env.RetailSystem._attributionState({ actor_user_uid: '   ', employee_name: 'Sara Haddad' }),
    'unattributed',
    'A whitespace-only uid is not a recorded actor. Blank is treated as absent ' +
    'everywhere else in this classifier (_identityOf) and Android uses ' +
    'isNullOrBlank(); a name must not ride in on a uid made of spaces.'
  );
  assert.strictEqual(
    env.RetailSystem._attributionState({ actor_user_uid: ACTOR_UUID, employee_name: 'Sara Haddad' }),
    'named',
    'A uid that WAS recorded and DID resolve to a name must still be named. ' +
    'Without this the case above is satisfied by a classifier that simply ' +
    'never names anyone.'
  );

  assert.ok(
    !/Sara Haddad/.test(tbody.innerHTML),
    'The unattributed bucket rendered a person\'s name. There is no actor on ' +
    'these rows -- naming one puts every sale the shop rang before v13 on a ' +
    'single employee, which is the fabrication v13 was written to refuse. ' +
    'Got: ' + tbody.innerHTML
  );
  assert.ok(
    /Not recorded/.test(tbody.innerHTML),
    'The unattributed bucket must be labelled in words. Got: ' + tbody.innerHTML
  );
  assert.ok(
    !/Account removed/.test(tbody.innerHTML),
    'The unattributed bucket was reported as a deleted account. Nothing was ' +
    'ever recorded for these sales; no account was removed. Got: ' + tbody.innerHTML
  );
  assert.ok(
    !/<bdi/.test(tbody.innerHTML.split('</td>')[0]),
    'The unattributed employee cell rendered an identifier. There is no ' +
    'identity on these rows -- an isolate around nothing is an empty box where ' +
    'a name should be. Got: ' + tbody.innerHTML
  );
  assert.strictEqual(
    note.style.display, 'block',
    'The footnote explaining the unattributed bucket must be shown when such a ' +
    'row is actually on screen.'
  );
  assert.ok(
    /5,?400/.test(tbody.innerHTML),
    'Sanity: the bucket\'s money must still be reported. Dropping it would omit ' +
    'most of a real shop\'s takings from a report whose columns still added up. ' +
    'Got: ' + tbody.innerHTML
  );
  assert.ok(
    /sam@shop\.test/.test(tbody.innerHTML),
    'Control row: an actor that WAS recorded and DID resolve must still be ' +
    'named. Suppressing every name would satisfy the assertions above and ' +
    'break the report instead of fixing it. Got: ' + tbody.innerHTML
  );
}

// Both attribution words sit in a table cell beside a uuid fragment and beside
// three number columns, on a page that flips to dir="rtl" in Arabic. i18n.js's
// sweep matches a text node's FULL trimmed text against the catalog, so a word
// concatenated into the same text node as an id can never be translated -- and
// its leading Latin character would drag the cell left-to-right anyway. The
// word gets its own element; the id gets its own <bdi>.
async function testAttributionWordsAreTheirOwnTextNode() {
  const { env, tbody } = employeePanelEnv({
    status: 'success', success: true,
    data: [{ actor_user_uid: ACTOR_UUID, employee_id: null, email: null,
             employee_name: null, transactions: 1, gross_sales: 5, refunds: 0,
             revenue: 5, avg_ticket: 5 }],
  });
  await env.RetailSystem._loadEmployeeSales();

  assert.ok(
    />\s*Account removed\s*</.test(tbody.innerHTML),
    'The words "Account removed" must be the entire trimmed text of their own ' +
    'node, or i18n.js cannot match them against the catalog and the label stays ' +
    'English on an Arabic page forever. Got: ' + tbody.innerHTML
  );
  assert.ok(
    !/Account removed[^<]*[0-9a-f]{8}/.test(tbody.innerHTML),
    'The label and the uid share one text node. Got: ' + tbody.innerHTML
  );
}

// The settled envelope carries BOTH discriminators -- `status` for this client,
// `success` for Android and for the five sibling /reports/* routes, which all
// answer {"success": true, ...} and carry no `status` key at all. Reading one
// must not be disturbed by the presence of the other.
async function testDualDiscriminatorEnvelopeIsAccepted() {
  const { env, tbody } = employeePanelEnv({
    status: 'success', success: true,
    data: [{ actor_user_uid: ACTOR_UUID, employee_id: 'EMP-0002', email: 'sam@shop.test',
             employee_name: 'sam@shop.test', transactions: 4, gross_sales: 60,
             refunds: 0, revenue: 60, avg_ticket: 15 }],
  });
  await env.RetailSystem._loadEmployeeSales();
  assert.ok(
    tbody.innerHTML.includes('sam@shop.test'),
    'The panel rejected the settled {"status":"success","success":true,...} ' +
    'envelope. Got: ' + tbody.innerHTML
  );
}

// Version skew, in the direction that actually happens: a desktop build newer
// than the embedded backend it is talking to (the Android app ships its own
// Python server; a shop updates one and not the other). Such a route answers
// with metrics.py's raw rows and NO identity fields whatsoever. Not one key
// present is different from "present and null", and only the second licenses
// the "the account was deleted" inference.
async function testRouteThatResolvedNothingIsNotReportedAsDeletedAccounts() {
  const { env, tbody } = employeePanelEnv({
    status: 'success',
    data: [{ actor_user_uid: ACTOR_UUID, transactions: 9, gross_sales: 300,
             refunds: 0, revenue: 300, avg_ticket: 33.33 }],
  });
  await env.RetailSystem._loadEmployeeSales();

  assert.ok(
    !/Account removed/.test(tbody.innerHTML),
    'A backend that never carried identity fields was reported as having ' +
    'deleted every employee. Nothing here established that anyone was ever ' +
    'looked up. Got: ' + tbody.innerHTML
  );
  assert.ok(
    tbody.innerHTML.includes(ACTOR_UUID.slice(0, 8)),
    'The recorded uid must still be shown as the identifier it is. Got: ' +
    tbody.innerHTML
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
  testSaleDetailShowsTheResolvedNameFromItsOwnFieldSpelling,
  testSaleDetailDoesNotInferDeletionFromAFieldNobodySent,
  testSaleDetailReportsADeletedAccountWhenTheRouteLookedAndFoundNobody,
  testLegacyCashierFreeTextIsNotPresentedAsTheActor,
  testSaleDetailGridLabelsAllGoThroughT,
  testEmployeePanelRendersTakingsAndCount,
  testMissingRouteSaysUnavailableNotZeroSales,
  testNetworkFailureIsNotReportedAsZeroSales,
  testGenuinelyEmptyPeriodSaysSo,
  testAvgTicketIsTheServersFigureNotARecomputedOne,
  testRefundOnlyEmployeeRendersNegativeTakings,
  testAbsentAvgTicketIsNotPrintedAsZero,
  testResolvedNameUsesEmailBeforeEmployeeId,
  testBlankIdentityStringsAreNotIdentities,
  testUnresolvableActorIsNamedAsSuchNotAsAPersonNorAsUnrecorded,
  testUnattributedBucketNeverLooksLikeAPerson,
  testAttributionWordsAreTheirOwnTextNode,
  testDualDiscriminatorEnvelopeIsAccepted,
  testRouteThatResolvedNothingIsNotReportedAsDeletedAccounts,
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
