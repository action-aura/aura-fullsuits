/**
 * retail_einvoicing_settings_ui_test.js — the Jordan e-invoicing switch on
 * the Admin Center ("Settings") screen.
 *
 * WHY THIS EXISTS
 *
 * GET/POST /api/einvoicing/settings (commercial_runtime/einvoicing/
 * routes.py) have been complete since Phase 1 shipped -- a dedicated
 * key/value store, a fail-closed `tax_regime` gate, an outbox, a retry
 * worker -- and the only client that ever called them was einvoicing.html/
 * .js, a STANDALONE document nothing in this app links to (see that file's
 * own header comment: "no shared SubsystemApp shell exists to plug into").
 * app-shell.js's nav has no entry for it and no other frontend file
 * references "einvoicing.html" at all (confirmed by grep before writing this
 * file). The Admin Center's own Branding card already reads the shop's
 * e-invoicing seller identity and tells the owner it is "configured
 * separately under E-Invoicing" -- a promise this codebase could not keep,
 * because nothing anywhere actually linked there. So the shop's tax regime
 * -- the single fail-closed gate settings.py::is_enabled() checks FIRST --
 * could be read but never CHANGED by anyone who did not already know that
 * URL existed.
 *
 * THIS FILE'S JOB: prove the switch is real, prove the status line is
 * HONEST (turning it on does not mean anything is filed -- both
 * providers/direct_istd.py's submit_invoice and check_status raise
 * NotImplementedError today, and the shipped default provider is
 * 'unconfigured'), and prove both by running the real code, not by reading
 * the diff.
 *
 *   1. The control renders on the Admin Center screen.
 *   2. It reflects the CURRENT state from GET /settings, in both directions
 *      -- the off case and the Jordan case.
 *   3. Choosing Jordan POSTs tax_regime='jordan'.
 *   4. Choosing off POSTs a non-jordan value ('none').
 *   5. The honest "held, not filed" status text appears when the regime is
 *      Jordan...
 *   6. ...and does NOT appear when it is off -- the other direction of the
 *      same guard (ENGINEERING.md: "prove both directions of anything that
 *      both denies and allows").
 *   7. Every user-visible string on this card is a locale lookup (t() with
 *      a string literal), present with real, distinct Arabic in both
 *      catalogs -- not a hardcoded literal that would render English on an
 *      Arabic page.
 *
 * ── MUTATION-PROVED ─────────────────────────────────────────────────────────
 * Every behavioural claim above is re-run against a DELIBERATELY BROKEN copy
 * of subsystem-retail.js and is required to FAIL there. M1/M2 are the
 * required "both directions" pair on the honest-status guard; M3 is the
 * one that matters most (a mutation that keeps SOMETHING reassuring on
 * screen while silently dropping the "held, not filed" honesty -- the
 * exact failure shape the brief warns against); M4 proves the off-choice
 * POST assertion is not vacuous.
 *
 * No test framework is configured for this vanilla-JS, build-step-free
 * frontend (see CLAUDE.md), so this runs standalone on Node built-ins:
 *
 *   node products/retail/tests/retail_einvoicing_settings_ui_test.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const FRONTEND = path.join(__dirname, '..', 'frontend');
const RETAIL_JS = path.join(FRONTEND, 'subsystem-retail.js');

const SRC = fs.readFileSync(RETAIL_JS, 'utf8');
const EN = JSON.parse(fs.readFileSync(path.join(FRONTEND, 'locales', 'en.json'), 'utf8'));
const AR = JSON.parse(fs.readFileSync(path.join(FRONTEND, 'locales', 'ar.json'), 'utf8'));

/* Match the file's OWN line ending -- an anchor written with the wrong one
   would silently match nothing, making every mutation proof below pass
   while breaking nothing (see retail_branches_test.js / retail_email_
   notifications_test.js's identical comment on this exact hazard). */
function eolOf(src) { return src.indexOf('\r\n') !== -1 ? '\r\n' : '\n'; }
function nlFor(src) { const eol = eolOf(src); return (s) => s.replace(/\n/g, eol); }

const STATUS_URL = '/api/einvoicing/status';
const SETTINGS_URL = '/api/einvoicing/settings';

// ─────────────────────────────────────────────────────────────────────────────
// FIXTURES — shapes routes.py / settings.py actually put on the wire
// ─────────────────────────────────────────────────────────────────────────────

const COUNTS_ZERO = { QUEUED: 0, CLEARED: 0, FAILED_PERMANENT: 0, SUBMITTING_UNKNOWN: 0, AWAITING_CLEARANCE: 0 };
const COUNTS_HELD = { QUEUED: 7, CLEARED: 2, FAILED_PERMANENT: 0, SUBMITTING_UNKNOWN: 0, AWAITING_CLEARANCE: 0 };

const STATUS_OFF = {
  status: 'success',
  data: { enabled: false, provider: 'unconfigured', provider_in_use: 'unconfigured', provider_configured: false,
    killswitch: { disabled: false, reason: null }, counts_by_state: COUNTS_ZERO, oldest_pending_created_at: null },
};
const STATUS_JORDAN = {
  status: 'success',
  data: { enabled: true, provider: 'unconfigured', provider_in_use: 'unconfigured', provider_configured: false,
    killswitch: { disabled: false, reason: null }, counts_by_state: COUNTS_HELD, oldest_pending_created_at: '2026-09-20T10:00:00Z' },
};

const SETTINGS_OFF = {
  status: 'success',
  data: { settings: { tax_regime: 'none', enabled: '1', provider: 'unconfigured' }, credentials: { configured: false } },
};
const SETTINGS_JORDAN = {
  status: 'success',
  data: { settings: { tax_regime: 'jordan', enabled: '1', provider: 'unconfigured' }, credentials: { configured: false } },
};

const SAVE_OK = { status: 'success', data: { tax_regime: 'jordan', enabled: '1' } };
const SAVE_400 = { status: 'error', message: "Unknown e-invoicing setting: 'bogus'." };

// ─────────────────────────────────────────────────────────────────────────────
// THE SANDBOX — the real subsystem-retail.js, never a reimplementation
// ─────────────────────────────────────────────────────────────────────────────

function makeStub(over) {
  return Object.assign({
    innerHTML: '', outerHTML: '', textContent: '', value: '', id: '', disabled: false, checked: false,
    style: {}, dataset: {}, options: [],
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    appendChild() {}, getAttribute() { return null; }, setAttribute() {},
    querySelector() { return null; }, querySelectorAll() { return []; },
    addEventListener() {}, removeEventListener() {},
    focus() {}, blur() {}, remove() {}, closest() { return null; }, insertBefore() {},
  }, over || {});
}

/**
 * A fetch that answers /api/einvoicing/{status,settings} with configurable
 * payloads and every OTHER Admin Center card's own endpoint (branding,
 * device-branch, business-day, doc-series, reorder-requests -- all fired by
 * the SAME _renderAdminCenter() call) with a generic empty success, so this
 * file only has to know the shape of the card it actually tests. Matches
 * retail_business_day_settings_test.js's makeFetch() pattern exactly, for
 * the identical reason: it also tests one card inside the same shared
 * render function.
 */
function makeFetch(opts) {
  const o = opts || {};
  const calls = [];
  const fn = (url, init) => {
    const method = ((init && init.method) || 'GET').toUpperCase();
    let body = null;
    if (init && typeof init.body === 'string') {
      try { body = JSON.parse(init.body); } catch (e) { body = init.body; }
    }
    const u = String(url);
    calls.push({ method, url: u, body });
    let payload;
    if (u.indexOf(STATUS_URL) === 0 && method === 'GET') {
      payload = o.status === undefined ? STATUS_OFF : o.status;
    } else if (u.indexOf(SETTINGS_URL) === 0 && method === 'GET') {
      payload = o.settings === undefined ? SETTINGS_OFF : o.settings;
    } else if (u.indexOf(SETTINGS_URL) === 0 && method === 'POST') {
      payload = o.save === undefined ? SAVE_OK : o.save;
    } else {
      // A generic empty success for every OTHER Admin Center card's own
      // endpoint (branding, device-branch, business-day, doc-series,
      // reorder-requests -- all fired by the SAME _renderAdminCenter()
      // call). `data: []` rather than `{}`: _loadDeviceBranchForm calls
      // `branches.map(...)` directly on the response with no array default
      // of its own beyond `|| []` on a falsy value, so an empty OBJECT here
      // throws "branches.map is not a function" for every single render --
      // not a mutation, just this card's own generic-fixture default being
      // the wrong shape for a sibling card's route. An array satisfies both
      // `.map()` callers and the more common `(resp && resp.data) || {}`
      // object-style readers (property access on `[]` is simply undefined,
      // not a crash).
      payload = { status: 'success', data: [] };
    }
    if (payload instanceof Error) return Promise.reject(payload);
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(payload) });
  };
  fn.calls = calls;
  return fn;
}

/**
 * @param {object} opts
 *   source   — subsystem-retail.js text (a mutant, for the proofs below)
 *   status   — GET /api/einvoicing/status payload
 *   settings — GET /api/einvoicing/settings payload
 *   save     — POST /api/einvoicing/settings payload
 */
function loadRetailSystem(opts) {
  const o = opts || {};
  const els = Object.create(null);
  const toasts = [];
  const getEl = (id) => (els[id] || (els[id] = makeStub({ id })));
  const fetchFn = makeFetch(o);

  const sandbox = {
    console: { log() {}, warn() {}, error() {}, info() {} },
    t: (s) => s, // identity stub -- WIRE/gating/status-content behaviour is checked here; catalog coverage is proved separately by scraping the SOURCE.
    fetch: fetchFn,
    getComputedStyle: () => ({ getPropertyValue: () => '' }),
    navigator: { userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)' },
    localStorage: { getItem: () => null, setItem() {} },
    setTimeout: () => 0, clearTimeout() {}, setInterval: () => 0, clearInterval() {},
    document: {
      activeElement: null,
      getElementById(id) { if (id === 'ret-styles') return null; return getEl(id); },
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
  return { rs: sandbox.RetailSystem, calls: fetchFn.calls, toasts, els, getEl };
}

async function settle() {
  for (let i = 0; i < 8; i++) await Promise.resolve();
  await new Promise((resolve) => setImmediate(resolve));
}

/** Render the Admin Center screen the same way retail_business_day_
 *  settings_test.js does -- directly, since this card's reachability
 *  (it lives on the already-routed 'admin-center' section) is not the
 *  thing under test here; the card's own behaviour is. */
async function renderScreen(opts) {
  const ctx = loadRetailSystem(opts);
  const host = makeStub({ id: 'sub-content' });
  await ctx.rs._renderAdminCenter(host);
  await settle();
  ctx.frame = host.innerHTML;
  ctx.statusLine = () => ctx.els['einvoicing-status-line'];
  return ctx;
}

function postsTo(calls, url) {
  return calls.filter((c) => c.method === 'POST' && c.url.indexOf(url) === 0);
}
function getsTo(calls, url) {
  return calls.filter((c) => c.method === 'GET' && c.url.indexOf(url) === 0);
}

// ─────────────────────────────────────────────────────────────────────────────
// MUTATION HARNESS — same shape as retail_email_notifications_test.js /
// retail_branches_test.js
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

async function provesMutation(what, check) {
  let threw = null;
  try {
    await check();
  } catch (err) {
    threw = err;
  }
  assert.ok(
    threw,
    `MUTATION SURVIVED — ${what}\n` +
    'The guard for this passed against a build with the behaviour deliberately broken, so it is ' +
    'not actually watching it. Fix the check, not the mutation.'
  );
  return `${what}  [caught: ${String(threw.message || threw).split('\n')[0].slice(0, 120)}]`;
}

// ─────────────────────────────────────────────────────────────────────────────
// (1) THE CONTROL RENDERS
// ─────────────────────────────────────────────────────────────────────────────

async function testControlRenders(src) {
  const ctx = await renderScreen({ source: src, settings: SETTINGS_OFF, status: STATUS_OFF });
  const cardTag = ctx.frame.match(/<div class="sub-chart-card" id="einvoicing-card"[^>]*>/);
  assert.ok(cardTag, 'Admin Center must render the E-Invoicing card with exactly that id.');
  assert.ok(!/display\s*:\s*none/i.test(cardTag[0]),
    'the E-Invoicing card must not render hidden -- an unreachable control is the entire defect this card fixes.');
  for (const id of ['einvoicing-status-line', 'einvoicing-regime', 'einvoicing-save-btn']) {
    assert.ok(ctx.frame.indexOf('id="' + id + '"') !== -1,
      'Admin Center must render the E-Invoicing card; missing id: ' + id);
  }
  assert.ok(ctx.frame.indexOf('<option value="jordan">') !== -1, 'No Jordan option in the tax-regime select.');
  assert.ok(ctx.frame.indexOf('<option value="none">') !== -1, 'No off ("none") option in the tax-regime select.');
  assert.ok(ctx.frame.indexOf('RetailSystem._saveEinvoicingRegime()') !== -1, 'No Save control wired to _saveEinvoicingRegime.');
  console.log('PASS: the E-Invoicing card, its regime select and its Save control all render');
}

// ─────────────────────────────────────────────────────────────────────────────
// (2) REFLECTS CURRENT STATE — both directions
// ─────────────────────────────────────────────────────────────────────────────

async function testReflectsOffState(src) {
  const ctx = await renderScreen({ source: src, settings: SETTINGS_OFF, status: STATUS_OFF });
  assert.strictEqual(ctx.getEl('einvoicing-regime').value, 'none',
    "The regime select did not reflect GET /settings' tax_regime='none'.");
  assert.ok(getsTo(ctx.calls, SETTINGS_URL).length >= 1, 'Rendering the card must GET /api/einvoicing/settings.');
  console.log("PASS: the regime select reflects GET /settings' off state (tax_regime='none')");
}

async function testReflectsJordanState(src) {
  const ctx = await renderScreen({ source: src, settings: SETTINGS_JORDAN, status: STATUS_JORDAN });
  assert.strictEqual(ctx.getEl('einvoicing-regime').value, 'jordan',
    "The regime select did not reflect GET /settings' tax_regime='jordan'.");
  console.log("PASS: the regime select reflects GET /settings' Jordan state (tax_regime='jordan')");
}

// ─────────────────────────────────────────────────────────────────────────────
// (3)/(4) SAVE — POSTs the chosen regime, in both directions
// ─────────────────────────────────────────────────────────────────────────────

async function testChoosingJordanPostsTaxRegimeJordan(src) {
  const ctx = await renderScreen({ source: src, settings: SETTINGS_OFF, status: STATUS_OFF, save: SAVE_OK });
  ctx.getEl('einvoicing-regime').value = 'jordan';
  await ctx.rs._saveEinvoicingRegime();
  await settle();

  const posts = postsTo(ctx.calls, SETTINGS_URL);
  assert.strictEqual(posts.length, 1, `Expected exactly 1 POST to ${SETTINGS_URL}, saw ${posts.length}.`);
  assert.deepStrictEqual(posts[0].body, { tax_regime: 'jordan' },
    `Choosing Jordan must POST tax_regime='jordan'. Body: ${JSON.stringify(posts[0].body)}`);
  console.log("PASS: choosing Jordan POSTs { tax_regime: 'jordan' }");
}

async function testChoosingOffPostsNonJordanValue(src) {
  const ctx = await renderScreen({ source: src, settings: SETTINGS_JORDAN, status: STATUS_JORDAN, save: { status: 'success', data: { tax_regime: 'none' } } });
  ctx.getEl('einvoicing-regime').value = 'none';
  await ctx.rs._saveEinvoicingRegime();
  await settle();

  const posts = postsTo(ctx.calls, SETTINGS_URL);
  assert.strictEqual(posts.length, 1, `Expected exactly 1 POST to ${SETTINGS_URL}, saw ${posts.length}.`);
  assert.ok(posts[0].body && posts[0].body.tax_regime !== 'jordan',
    `Choosing off must POST a non-jordan tax_regime value. Body: ${JSON.stringify(posts[0].body)}`);
  assert.strictEqual(posts[0].body.tax_regime, 'none');
  console.log("PASS: choosing off POSTs a non-jordan tax_regime value ('none')");
}

// ─────────────────────────────────────────────────────────────────────────────
// (5)/(6) THE HONEST STATUS TEXT — appears on, absent off
// ─────────────────────────────────────────────────────────────────────────────

async function testHonestStatusTextAppearsWhenOn(src) {
  const ctx = await renderScreen({ source: src, settings: SETTINGS_JORDAN, status: STATUS_JORDAN });
  const html = ctx.statusLine().innerHTML || '';
  assert.ok(html.indexOf('it is held here') !== -1 && html.indexOf('not filed with the tax authority') !== -1,
    `The honest "prepared and held, not filed" status text did not appear with tax_regime='jordan'. ` +
    `Status line HTML:\n${html}`);
  // Sanity: the provider/queued line the brief asks for ("a number the
  // owner can watch grow") is also present, so this is not passing because
  // some unrelated HTML happens to contain those two substrings.
  assert.ok(html.indexOf('Provider') !== -1 && html.indexOf('Queued') !== -1 && html.indexOf('7') !== -1,
    `Sanity: the provider/queued-count line did not render. Status line HTML:\n${html}`);
  console.log('PASS: the honest "held, not filed" status text (plus provider/queued count) appears when the regime is Jordan');
}

async function testHonestStatusTextAbsentWhenOff(src) {
  const ctx = await renderScreen({ source: src, settings: SETTINGS_OFF, status: STATUS_OFF });
  const html = ctx.statusLine().innerHTML || '';
  const text = ctx.statusLine().textContent || '';
  const combined = html + text;
  assert.ok(combined.indexOf('held here') === -1,
    `The "held, not filed" status text appeared even though tax_regime='none' -- it must not claim ` +
    `anything is being prepared/held when the shop has no e-invoicing regime at all. Status line: ${combined}`);
  // Sanity: SOME status text renders in the off state -- a mutation that
  // renders nothing for everyone would otherwise satisfy "does not show the
  // wrong text" vacuously (ENGINEERING.md failure-shape #4/#5).
  assert.ok(text.indexOf('nothing with any tax authority') !== -1,
    `Sanity: the off-state status text did not render at all. Status line text: ${text}`);
  console.log('PASS: the "held, not filed" status text does NOT appear when the regime is off (and the off-state text does)');
}

// ─────────────────────────────────────────────────────────────────────────────
// (7) i18n — every user-visible string on this card is a real, catalogued
// t() call (single- or double-quoted -- "The shop's tax regime" needs
// double quotes since it contains an apostrophe, matching the production
// ratchet's own quote-agnostic regex in retail_attribution_i18n_test.py)
// ─────────────────────────────────────────────────────────────────────────────

function testAllStringsAreLocaleLookups() {
  // Two DISJOINT regions, not one span: the card's markup sits inside
  // _renderAdminCenter's giant template (immediately followed in-source by
  // the pre-existing WhatsApp Reports / Shop Network cards, which are not
  // this card's strings to answer for), and _loadEinvoicingCard/
  // _saveEinvoicingRegime sit AFTER _renderAdminCenter's closing brace. A
  // single start..."Branding" span would silently sweep in the unrelated
  // cards' copy too.
  const cardStart = SRC.indexOf('<div class="sub-chart-card" id="einvoicing-card">');
  const cardEnd = SRC.indexOf("<h3 style=\"color:var(--text-primary);margin:0 0 14px;font-size:15px\">${t('WhatsApp Reports')}", cardStart);
  assert.ok(cardStart !== -1 && cardEnd !== -1 && cardEnd > cardStart,
    'Could not locate the E-Invoicing card markup in subsystem-retail.js -- the scrape is broken.');

  const methodsStart = SRC.indexOf('  async _loadEinvoicingCard() {');
  const methodsEnd = SRC.indexOf('\n  // ── Branding (Admin Center)', methodsStart);
  assert.ok(methodsStart !== -1 && methodsEnd !== -1 && methodsEnd > methodsStart,
    'Could not locate _loadEinvoicingCard/_saveEinvoicingRegime in subsystem-retail.js -- the scrape is broken.');

  const region = SRC.slice(cardStart, cardEnd) + '\n' + SRC.slice(methodsStart, methodsEnd);

  const re = /\bt\(\s*(?:'((?:[^'\\]|\\.)*)'|"((?:[^"\\]|\\.)*)")\s*\)/g;
  const keys = new Set();
  let m;
  while ((m = re.exec(region)) !== null) keys.add(m[1] !== undefined ? m[1] : m[2]);

  assert.ok(keys.size >= 8,
    `Only ${keys.size} t() call(s) found on the E-Invoicing card -- this card carries more copy than ` +
    'that (heading, subtitle, status texts, regime label, two options, Save, Manage link), so treat this as a harness failure.');

  const missing = [...keys].filter((k) => !(k in EN) || !(k in AR) || !String(AR[k]).trim() || EN[k] === AR[k]);
  assert.deepStrictEqual(missing, [],
    'These strings are t()-called on the E-Invoicing card but have no real, distinct Arabic:\n  ' +
    missing.map((k) => `${JSON.stringify(k)} — en:${k in EN ? 'yes' : 'MISSING'} ` +
      `ar:${k in AR ? (String(AR[k]).trim() ? (EN[k] === AR[k] ? 'UNTRANSLATED' : 'yes') : 'EMPTY') : 'MISSING'}`)
      .join('\n  ') +
    '\n\nAdd each to BOTH locales/en.json and locales/ar.json with a real translation.');

  console.log(`PASS: ${keys.size} t() call(s) on the E-Invoicing card, all present with real, distinct Arabic in both catalogs`);
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

  // ── The required behaviours, against the REAL, unmutated build ────────────
  await run('testControlRenders', () => testControlRenders(SRC));
  await run('testReflectsOffState', () => testReflectsOffState(SRC));
  await run('testReflectsJordanState', () => testReflectsJordanState(SRC));
  await run('testChoosingJordanPostsTaxRegimeJordan', () => testChoosingJordanPostsTaxRegimeJordan(SRC));
  await run('testChoosingOffPostsNonJordanValue', () => testChoosingOffPostsNonJordanValue(SRC));
  await run('testHonestStatusTextAppearsWhenOn', () => testHonestStatusTextAppearsWhenOn(SRC));
  await run('testHonestStatusTextAbsentWhenOff', () => testHonestStatusTextAbsentWhenOff(SRC));
  await run('testAllStringsAreLocaleLookups', testAllStringsAreLocaleLookups);

  // ── Mutation proofs ─────────────────────────────────────────────────────

  // M1 (direction A): the "is this Jordan" guard always reads true =>
  // the OFF-state test (5)/(6)'s absence-check FAILS, because the held/not-
  // filed text would now wrongly appear for a shop with NO regime selected.
  await run("M1: isJordan forced to always-true => testHonestStatusTextAbsentWhenOff FAILS", async () => {
    const broken = mutate(SRC, [[
      "      const isJordan = regime === 'jordan';",
      "      const isJordan = true; // MUTATED: always treated as Jordan",
    ]]);
    return provesMutation('M1 isJordan forced true', () => testHonestStatusTextAbsentWhenOff(broken));
  });

  // M2 (direction B): the same guard always reads false => the ON-state
  // test (5) FAILS, because the held/not-filed text would now never appear
  // even though the shop's regime IS Jordan.
  await run("M2: isJordan forced to always-false => testHonestStatusTextAppearsWhenOn FAILS", async () => {
    const broken = mutate(SRC, [[
      "      const isJordan = regime === 'jordan';",
      "      const isJordan = false; // MUTATED: never treated as Jordan",
    ]]);
    return provesMutation('M2 isJordan forced false', () => testHonestStatusTextAppearsWhenOn(broken));
  });

  // M3 (the one that matters most): keep the status block reachable but
  // strip the HONEST wording, replacing it with something reassuring-
  // sounding instead. A mutation like M2 that removes the block entirely is
  // the easy case to catch; this is the dangerous one -- SOMETHING renders,
  // so a shallow "did a status line appear" check would stay green while
  // the actual claim being made to the shopkeeper silently flips from
  // "held, not filed" to "on" (which reads as "compliant").
  await run('M3: honest wording replaced with a bare "on" claim => testHonestStatusTextAppearsWhenOn FAILS', async () => {
    const broken = mutate(SRC, [[
      "            `<p style=\"margin:0 0 8px\">${t('E-invoicing is on. Every sale prepares an e-invoice, but it is held here — not filed with the tax authority — until this installation is connected to JoFotara and its credentials are entered.')}</p>` +",
      "            `<p style=\"margin:0 0 8px\">${t('E-invoicing is on.')}</p>` + // MUTATED: honest held/not-filed wording dropped",
    ]]);
    return provesMutation('M3 honest wording dropped', () => testHonestStatusTextAppearsWhenOn(broken));
  });

  // M4: save() always POSTs 'jordan' regardless of what the select holds =>
  // the off-choice POST test (4) FAILS. Proves that assertion is not
  // vacuously true against a save() that ignores the control entirely.
  await run("M4: save() always posts 'jordan' => testChoosingOffPostsNonJordanValue FAILS", async () => {
    const broken = mutate(SRC, [[
      "    const value = select ? select.value : 'none';",
      "    const value = 'jordan'; // MUTATED: ignores the select, always posts jordan",
    ]]);
    return provesMutation('M4 save always posts jordan', () => testChoosingOffPostsNonJordanValue(broken));
  });

  console.log(results.join('\n'));
  if (failed) {
    console.error(`\nFAIL: retail_einvoicing_settings_ui_test.js — ${failed} of ${results.length} check(s) failed`);
    process.exitCode = 1;
  } else {
    console.log(`\nPASS: retail_einvoicing_settings_ui_test.js — ${results.length} check(s)`);
  }
}

main().catch((err) => {
  console.error('FAIL: retail_einvoicing_settings_ui_test.js (runner)');
  console.error((err && err.stack) || err);
  process.exitCode = 1;
});
