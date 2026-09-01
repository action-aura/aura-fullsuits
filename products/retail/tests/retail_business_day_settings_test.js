/**
 * retail_business_day_settings_test.js — the Business Day card in Admin
 * Center, and the one thing about it that is easy to get quietly wrong.
 *
 * WHY THIS EXISTS
 *
 * GET/POST /settings/business-day shipped complete — validated, audited,
 * capability-gated — and no client could reach either. So every install ran
 * on the unconfigured default, which buckets a row on the clock of whichever
 * DEVICE wrote it, at midnight. That is defensible for a single till and
 * wrong the moment a second device syncs: two tills whose clocks differ file
 * the same evening under different days, and every report, the dashboard and
 * the shift Z-report group by that boundary (core/retail/metrics.py).
 *
 * It is the sixth complete-backend-no-doorway defect found in this codebase,
 * so the card is guarded rather than merely added. retail_route_reachability_
 * test.py already proves SOME client calls the route; this file proves the
 * control is real and that its semantics survive.
 *
 * THE ASSERTION THAT MATTERS MOST
 *
 * An empty timezone box must POST `business_timezone: null`, which the route
 * treats as CLEARING the key. Not the empty string, and above all not 'UTC':
 * the route's own docstring is explicit that UTC is a REAL zone, so a screen
 * that wrote it to mean "unset" would silently re-file the trading history of
 * every shop that merely stopped declaring one. That is a data-corruption
 * bug with a cheerful success toast, and it is exactly the kind a render-only
 * test never sees.
 *
 * Loads the REAL products/retail/frontend/subsystem-retail.js through Node's
 * vm module — no reimplementation — following the same harness shape as
 * retail_dashboard_error_propagation_test.js. No test framework is configured
 * for this vanilla-JS, build-step-free frontend (see CLAUDE.md):
 *
 *   node products/retail/tests/retail_business_day_settings_test.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const FRONTEND_FILE = path.join(__dirname, '..', 'frontend', 'subsystem-retail.js');

/**
 * An element stub that remembers what was assigned to it.
 *
 * `innerHTML` is a real accessor that re-derives `options` from any
 * `<option value="...">` it is given, because _loadBusinessDayForm guards on
 * `hourSelect.options.length` to avoid rebuilding the 24 hour entries on
 * every visit. A stub with a permanently empty `options` array would make
 * that guard untestable in the direction that matters.
 */
function makeElementStub(id) {
  const el = {
    id: id || '',
    _innerHTML: '',
    textContent: '',
    value: '',
    disabled: false,
    style: {},
    options: [],
    appendChild() {},
    getAttribute() { return null; },
    setAttribute() {},
    querySelectorAll() { return []; },
    addEventListener() {},
  };
  Object.defineProperty(el, 'innerHTML', {
    get() { return el._innerHTML; },
    set(v) {
      el._innerHTML = String(v);
      const found = el._innerHTML.match(/<option /g);
      el.options = found ? found.map(() => ({})) : [];
    },
  });
  return el;
}

/** Load the real frontend with a controllable fetch and a per-id DOM. */
function loadRetailSystem(fetchImpl) {
  const code = fs.readFileSync(FRONTEND_FILE, 'utf8');
  const els = new Map();
  const toasts = [];

  const sandbox = {
    console: { error() {}, warn() {}, log() {} },
    t: (s) => s,
    fetch: fetchImpl,
    setTimeout,
    clearTimeout,
    getComputedStyle: () => ({ getPropertyValue: () => '' }),
    SubsystemApp: {
      showToast(msg, kind) { toasts.push({ msg, kind }); },
    },
    document: {
      getElementById(id) {
        if (!els.has(id)) els.set(id, makeElementStub(id));
        return els.get(id);
      },
      createElement() { return makeElementStub(); },
      querySelector() { return makeElementStub(); },
      querySelectorAll() { return []; },
      head: { appendChild() {} },
      body: { appendChild() {} },
      documentElement: { getAttribute() { return null; } },
    },
  };
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(code, sandbox, { filename: FRONTEND_FILE });
  assert.ok(sandbox.RetailSystem, 'subsystem-retail.js did not expose window.RetailSystem');
  return { RetailSystem: sandbox.RetailSystem, els, toasts };
}

/** A fetch that answers the business-day GET with `payload`, everything else
 *  with an empty success, and records every request it is given. */
function makeFetch(payload, calls, postResult) {
  return (url, init) => {
    const method = (init && init.method) || 'GET';
    calls.push({ url: String(url), method, body: init && init.body });
    let data = {};
    if (String(url).includes('business-day')) {
      if (method === 'POST') {
        return Promise.resolve({
          ok: true, status: 200,
          json: () => Promise.resolve(postResult || { status: 'success', data: payload }),
        });
      }
      data = payload;
    }
    return Promise.resolve({
      ok: true, status: 200,
      json: () => Promise.resolve({ status: 'success', data }),
    });
  };
}

async function testCardIsRenderedIntoAdminCenter() {
  const calls = [];
  const { RetailSystem, els } = loadRetailSystem(
    makeFetch({ business_timezone: null, business_day_start_hour: 0,
                timezone_database_available: true }, calls));

  const c = makeElementStub('content');
  await RetailSystem._renderAdminCenter(c);

  const html = c.innerHTML;

  // Matched as a whole id ATTRIBUTE, not as a loose substring. A bare
  // includes('business-day-card') is satisfied by 'business-day-card-REMOVED',
  // so the version of this check that shipped first went green against a card
  // that had been renamed and hidden -- caught only by mutating it. The
  // pass condition has to be able to tell "present" from "present-ish".
  const cardTag = html.match(/<div class="sub-chart-card" id="business-day-card"[^>]*>/);
  assert.ok(cardTag,
    'Admin Center must render the Business Day card with exactly that id');
  assert.ok(!/display\s*:\s*none/i.test(cardTag[0]),
    'the Business Day card must not render hidden -- an unreachable control ' +
    'is the entire defect this card exists to fix');

  for (const id of [
    'business-day-tz',
    'business-day-hour',
    'business-day-tzdata-warning',
    'business-day-undeclared-warning',
  ]) {
    assert.ok(html.includes('id="' + id + '"'),
      'Admin Center must render the Business Day card; missing id: ' + id);
  }
  assert.ok(html.includes('RetailSystem._saveBusinessDay()'),
    'the card must have a Save button wired to _saveBusinessDay');

  // The 24 hour entries are built by the loader, not the template.
  assert.strictEqual(els.get('business-day-hour').options.length, 24,
    'the hour select must offer all 24 hours of the day');

  // And the loader must actually have asked the server.
  assert.ok(calls.some(c2 => c2.url.includes('/settings/business-day') && c2.method === 'GET'),
    'rendering Admin Center must load the current business-day setting');
}

async function testUndeclaredTimezoneIsCalledOut() {
  const calls = [];
  const { RetailSystem, els } = loadRetailSystem(
    makeFetch({ business_timezone: null, business_day_start_hour: 0,
                timezone_database_available: true }, calls));
  await RetailSystem._renderAdminCenter(makeElementStub('content'));

  assert.strictEqual(els.get('business-day-undeclared-warning').style.display, '',
    'a shop with no declared timezone must be told so -- that is the state ' +
    'in which two tills can file the same evening on different days');
  assert.strictEqual(els.get('business-day-tzdata-warning').style.display, 'none',
    'the tzdata warning is about the INSTALL and must not appear merely ' +
    'because nobody has declared a zone yet');
  assert.strictEqual(els.get('business-day-tz').disabled, false,
    'the field must stay editable when the install can validate a zone');
}

async function testMissingTimezoneDatabaseIsReportedAsAnInstallProblem() {
  const calls = [];
  const { RetailSystem, els } = loadRetailSystem(
    makeFetch({ business_timezone: null, business_day_start_hour: 0,
                timezone_database_available: false }, calls));
  await RetailSystem._renderAdminCenter(makeElementStub('content'));

  // The two causes of "no timezone" are never conflated: the route returns
  // timezone_database_available precisely so the screen can name the real
  // one instead of offering a field that silently cannot save.
  assert.strictEqual(els.get('business-day-tzdata-warning').style.display, '',
    'an install with no timezone database must say so');
  assert.strictEqual(els.get('business-day-tz').disabled, true,
    'the timezone field must be disabled when nothing can be validated -- ' +
    'offering a box that cannot save sends the owner to fix the wrong thing');
  assert.strictEqual(els.get('business-day-undeclared-warning').style.display, 'none',
    '"you have not declared one" is the wrong advice when the install ' +
    'cannot accept one at all');
}

async function testDeclaredValuesArePopulatedAndWarningsCleared() {
  const calls = [];
  const { RetailSystem, els } = loadRetailSystem(
    makeFetch({ business_timezone: 'Asia/Amman', business_day_start_hour: 6,
                timezone_database_available: true }, calls));
  await RetailSystem._renderAdminCenter(makeElementStub('content'));

  assert.strictEqual(els.get('business-day-tz').value, 'Asia/Amman');
  assert.strictEqual(els.get('business-day-hour').value, '6');
  assert.strictEqual(els.get('business-day-undeclared-warning').style.display, 'none',
    'a shop that HAS declared a zone must not be nagged that it has not');
}

async function testEmptyTimezoneClearsRatherThanWritingUTC() {
  const calls = [];
  const { RetailSystem, els } = loadRetailSystem(
    makeFetch({ business_timezone: 'Asia/Amman', business_day_start_hour: 3,
                timezone_database_available: true }, calls));
  await RetailSystem._renderAdminCenter(makeElementStub('content'));

  // The owner clears the box and saves.
  els.get('business-day-tz').value = '   ';
  els.get('business-day-hour').value = '3';
  calls.length = 0;
  await RetailSystem._saveBusinessDay();

  const post = calls.find(c => c.url.includes('/settings/business-day') && c.method === 'POST');
  assert.ok(post, 'saving must POST to /settings/business-day');
  const body = JSON.parse(post.body);

  assert.ok('business_timezone' in body,
    'the key must be present, otherwise the route cannot tell "clear this" ' +
    'from "leave it alone"');
  assert.strictEqual(body.business_timezone, null,
    'an empty timezone box must send null, which CLEARS the setting. Sending ' +
    '"" or "UTC" instead is the bug this assertion exists for: UTC is a real ' +
    'zone, so writing it to mean "unset" silently re-files the trading ' +
    'history of every shop that merely stopped declaring one.');
  assert.notStrictEqual(body.business_timezone, 'UTC');
  assert.notStrictEqual(body.business_timezone, '');
  assert.strictEqual(body.business_day_start_hour, 3,
    'the hour must be sent as a number, not the select\'s string');
}

async function testATypedTimezoneIsSentVerbatim() {
  const calls = [];
  const { RetailSystem, els } = loadRetailSystem(
    makeFetch({ business_timezone: null, business_day_start_hour: 0,
                timezone_database_available: true }, calls));
  await RetailSystem._renderAdminCenter(makeElementStub('content'));

  els.get('business-day-tz').value = '  Asia/Amman  ';
  els.get('business-day-hour').value = '2';
  calls.length = 0;
  await RetailSystem._saveBusinessDay();

  const post = calls.find(c => c.url.includes('/settings/business-day') && c.method === 'POST');
  const body = JSON.parse(post.body);
  assert.strictEqual(body.business_timezone, 'Asia/Amman',
    'surrounding whitespace is trimmed, the zone name itself is untouched -- ' +
    'the server is the only validator of what a zone name means');
  assert.strictEqual(body.business_day_start_hour, 2);
}

async function testAServerRefusalIsShownInTheServersOwnWords() {
  const calls = [];
  const { RetailSystem, els, toasts } = loadRetailSystem(
    makeFetch({ business_timezone: null, business_day_start_hour: 0,
                timezone_database_available: true },
              calls,
              { status: 'error', message: 'This installation has no timezone database' }));
  await RetailSystem._renderAdminCenter(makeElementStub('content'));

  els.get('business-day-tz').value = 'Asia/Amman';
  toasts.length = 0;
  await RetailSystem._saveBusinessDay();

  const errors = toasts.filter(x => x.kind === 'error');
  assert.strictEqual(errors.length, 1, 'a refused save must be reported, not swallowed');
  // A 400 (bad zone name) and a 503 (install has no tz database) need
  // different actions from different people, so the route's own message wins
  // over a generic one.
  assert.ok(/no timezone database/.test(errors[0].msg),
    'the server\'s own message must reach the owner, got: ' + errors[0].msg);
  assert.ok(!toasts.some(x => x.kind === 'success'),
    'a refused save must never also claim success');
}

async function main() {
  await testCardIsRenderedIntoAdminCenter();
  await testUndeclaredTimezoneIsCalledOut();
  await testMissingTimezoneDatabaseIsReportedAsAnInstallProblem();
  await testDeclaredValuesArePopulatedAndWarningsCleared();
  await testEmptyTimezoneClearsRatherThanWritingUTC();
  await testATypedTimezoneIsSentVerbatim();
  await testAServerRefusalIsShownInTheServersOwnWords();
  console.log('PASS: retail_business_day_settings_test.js (7 cases)');
}

main().catch((err) => {
  console.error('FAIL: retail_business_day_settings_test.js');
  console.error(err);
  process.exitCode = 1;
});
