/**
 * retail_email_notifications_test.js — Email Notifications screen
 * (ci-hardening-w0.3 continuation, "the doorway", second one on this branch).
 *
 * GET/POST /api/notifications/{status,settings,outbox,outbox/run-once}
 * (commercial_runtime/notifications/routes.py) have been complete since the
 * outbox/worker/SMTP client shipped -- an outbox, a retry worker, an SMTP
 * client and a real trigger (low-stock alerts) all real, all tested, and
 * reachable by nothing. WhatsApp got a settings page; email never did. So
 * SMTP recipients could not be configured by any user, ever, and the channel
 * was inert in practice.
 *
 * THIS FILE'S JOB: prove the doorway actually opens, and prove it by running
 * the real code, not by reading the diff.
 *
 *   1. The screen renders current settings from GET /settings.
 *   2. Saving POSTs the changed keys -- assert the REQUEST, since "the call
 *      never happens" is the exact class of defect being fixed here.
 *   3. `enabled` on + `smtp_configured` false shows the "nothing will send"
 *      warning -- THE most important behaviour on this screen. SMTP
 *      configuration is an env var (AURA_SMTP_HOST) set at INSTALL time; no
 *      UI can change it, so a shop can switch email on, save successfully,
 *      and have nothing ever send. Silence about that is worse than the
 *      screen not existing.
 *   4. `smtp_configured` true does NOT show it -- do not nag once it's fine.
 *   5. A malformed email address is refused client-side, with no POST issued.
 *   6. A 400 from the server is surfaced to the user, verbatim, not swallowed.
 *   7. THE NAV ENTRY EXISTS and points at the screen.
 *   8. A non-admin does not see it -- every mutating notifications route
 *      requires session role 'admin' (_require_admin in routes.py).
 *
 * ── MUTATION-PROVED ─────────────────────────────────────────────────────────
 * Every behavioural claim below is re-run against a DELIBERATELY BROKEN copy
 * of subsystem-retail.js (or app-shell.js, for the nav entry) and is required
 * to FAIL there -- including an "allow-half" mutation that renders nothing at
 * all, which would otherwise pass every "does not show the wrong thing" check
 * by showing nothing (ENGINEERING.md's failure-shape #4/#5).
 *
 * No test framework is configured for this vanilla-JS, build-step-free
 * frontend (see CLAUDE.md), so this runs standalone on Node built-ins:
 *
 *   node products/retail/tests/retail_email_notifications_test.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const FRONTEND = path.join(__dirname, '..', 'frontend');
const RETAIL_JS = path.join(FRONTEND, 'subsystem-retail.js');
const APP_SHELL_JS = path.join(FRONTEND, 'app-shell.js');

const SRC = fs.readFileSync(RETAIL_JS, 'utf8');
const SHELL_SRC = fs.readFileSync(APP_SHELL_JS, 'utf8');
const EN = JSON.parse(fs.readFileSync(path.join(FRONTEND, 'locales', 'en.json'), 'utf8'));
const AR = JSON.parse(fs.readFileSync(path.join(FRONTEND, 'locales', 'ar.json'), 'utf8'));

/* Match each file's OWN line ending -- an anchor written with the wrong one
   would silently match nothing, making every mutation proof below pass
   while breaking nothing (see retail_branches_test.js's identical comment
   on this exact hazard). */
function eolOf(src) { return src.indexOf('\r\n') !== -1 ? '\r\n' : '\n'; }
function nlFor(src) { const eol = eolOf(src); return (s) => s.replace(/\n/g, eol); }

const SECTION_ID = 'email-notifications';
const STATUS_URL = '/api/notifications/status';
const SETTINGS_URL = '/api/notifications/settings';
const RUN_ONCE_URL = '/api/notifications/outbox/run-once';

// ─────────────────────────────────────────────────────────────────────────────
// FIXTURES — shapes routes.py actually puts on the wire
// ─────────────────────────────────────────────────────────────────────────────

const COUNTS = { QUEUED: 2, SENDING: 0, SENT: 5, FAILED_PERMANENT: 1, CANCELLED: 0 };

const STATUS_UNCONFIGURED = { status: 'success', data: { enabled: false, smtp_configured: false, counts_by_state: COUNTS } };
const STATUS_CONFIGURED = { status: 'success', data: { enabled: true, smtp_configured: true, counts_by_state: COUNTS } };

const SETTINGS_ENABLED = {
  status: 'success',
  data: { settings: { enabled: '1', low_stock_recipient: 'owner@example.com', reports_recipient: '', max_attempts: '8', submit_interval_seconds: '60' } },
};
const SETTINGS_DISABLED = {
  status: 'success',
  data: { settings: { enabled: '0', low_stock_recipient: '', reports_recipient: '', max_attempts: '8', submit_interval_seconds: '60' } },
};

const SAVE_OK = { status: 'success', data: { enabled: '1', low_stock_recipient: 'new@shop.com', reports_recipient: '', max_attempts: '8', submit_interval_seconds: '60' } };
const SAVE_400 = { status: 'error', message: "'max_attempts' must be a positive integer, got '0'." };
const RUN_ONCE_OK = { status: 'success', data: { ran: true, claimed: 1, reclaimed: 0, outcomes: { sent: 1, retry: 0, failed_permanent: 0 } } };

// ─────────────────────────────────────────────────────────────────────────────
// THE SANDBOX — the real subsystem-retail.js, never a reimplementation
// ─────────────────────────────────────────────────────────────────────────────

function makeStub(over) {
  return Object.assign({
    innerHTML: '', outerHTML: '', textContent: '', value: '', id: '', disabled: false, checked: false,
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
 *   source   — subsystem-retail.js text (a mutant, for the proofs below)
 *   role     — SubsystemApp.role ('admin' by default)
 *   status   — GET /api/notifications/status payload
 *   settings — GET /api/notifications/settings payload
 *   save     — POST /api/notifications/settings payload
 *   runOnce  — POST /api/notifications/outbox/run-once payload
 */
function loadRetailSystem(opts) {
  const o = opts || {};
  const calls = [];
  const toasts = [];
  const els = Object.create(null);
  const getEl = (id) => (els[id] || (els[id] = makeStub({ id })));

  const sandbox = {
    console: { log() {}, warn() {}, error() {}, info() {} },
    t: (s) => s, // identity stub -- this file checks WIRE/gating/warning-content behaviour; catalog coverage is proved separately below by scraping the SOURCE.
    fetch: (url, init) => {
      const method = ((init && init.method) || 'GET').toUpperCase();
      let body = null;
      if (init && typeof init.body === 'string') {
        try { body = JSON.parse(init.body); } catch (e) { body = init.body; }
      }
      const u = String(url);
      calls.push({ method, url: u, body });
      let payload;
      if (u.indexOf(STATUS_URL) === 0 && method === 'GET') {
        payload = o.status === undefined ? STATUS_UNCONFIGURED : o.status;
      } else if (u.indexOf(SETTINGS_URL) === 0 && method === 'GET') {
        payload = o.settings === undefined ? SETTINGS_DISABLED : o.settings;
      } else if (u.indexOf(SETTINGS_URL) === 0 && method === 'POST') {
        payload = o.save === undefined ? SAVE_OK : o.save;
      } else if (u.indexOf(RUN_ONCE_URL) === 0 && method === 'POST') {
        payload = o.runOnce === undefined ? RUN_ONCE_OK : o.runOnce;
      } else {
        payload = { status: 'error', message: 'unmapped fixture route: ' + method + ' ' + u };
      }
      if (payload instanceof Error) return Promise.reject(payload);
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(payload) });
    },
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
    role: o.role || 'admin',
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

async function settle() {
  for (let i = 0; i < 8; i++) await Promise.resolve();
  await new Promise((resolve) => setImmediate(resolve));
}

/** Render the screen through the ROUTER, so the route id is load-bearing --
 *  matches retail_branches_test.js's renderScreen() exactly. */
async function renderScreen(opts) {
  const ctx = loadRetailSystem(opts);
  const host = makeStub({ id: 'sub-content' });
  ctx.els['sub-content'] = host;
  await ctx.rs.render(SECTION_ID);
  await settle();
  ctx.frame = host.innerHTML;
  ctx.warningHtml = () => (ctx.els['email-smtp-warning'] ? ctx.els['email-smtp-warning'].innerHTML : '');
  ctx.noteText = () => (ctx.els['email-smtp-note'] ? ctx.els['email-smtp-note'].textContent : '');
  return ctx;
}

function postsTo(calls, url) {
  return calls.filter((c) => c.method === 'POST' && c.url.indexOf(url) === 0);
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

async function provesMutation(what, mutatedSrcOrShell, check) {
  let threw = null;
  try {
    await check(mutatedSrcOrShell);
  } catch (err) {
    threw = err;
  }
  assert.ok(
    threw,
    `MUTATION SURVIVED — ${what}\n` +
    'The guard for this passed against a build with the behaviour deliberately broken, so it is ' +
    'not actually watching it. Fix the check, not the mutation.'
  );
  return `${what}  [caught: ${String(threw.message || threw).split('\n')[0].slice(0, 100)}]`;
}

// ─────────────────────────────────────────────────────────────────────────────
// (7) THE NAV ENTRY — static check against app-shell.js + the router case
// ─────────────────────────────────────────────────────────────────────────────

function assertNavEntryAndRouteOk(shellSrc, retailSrc) {
  const entry = (shellSrc || SHELL_SRC).split('\n').find((l) => l.indexOf(`id: '${SECTION_ID}'`) !== -1);
  assert.ok(entry, `app-shell.js has no nav entry for '${SECTION_ID}'.`);
  assert.ok(/ownerOnly:\s*true/.test(entry),
    `The '${SECTION_ID}' nav entry does not gate on ownerOnly, but every mutating notifications route ` +
    `requires session role 'admin' (_require_admin, routes.py -- the USER axis, not the DEVICE axis ` +
    `'adminOnly' actually means). Entry:\n  ${entry.trim()}`);

  const start = (retailSrc || SRC).indexOf('  render(sectionId) {');
  assert.ok(start !== -1, 'Could not find RetailSystem.render(sectionId).');
  const body = (retailSrc || SRC).slice(start, (retailSrc || SRC).indexOf('\n  },', start));
  assert.ok(
    new RegExp(`case\\s+'${SECTION_ID}'\\s*:`).test(body),
    `RetailSystem.render() has no case for '${SECTION_ID}', so the screen is unreachable through the router.`
  );
}

function testNavEntryExistsAndPointsAtScreen() {
  assertNavEntryAndRouteOk(SHELL_SRC, SRC);
  // Sanity: the group config lists it too, so it actually renders in the
  // sidebar (retail_nav_groups_test.js pins the full arrangement; this is
  // just "did the id get lost between nav[] and navGroups[]").
  assert.ok(/items:\s*\[[^\]]*'email-notifications'[^\]]*\]/.test(SHELL_SRC),
    "'email-notifications' is in nav[] but not listed in any navGroups[] entry -- it would render nowhere in the sidebar.");
  console.log(`PASS: '${SECTION_ID}' is routed, listed in a nav group, and gates on ownerOnly`);
}

// ─────────────────────────────────────────────────────────────────────────────
// (8) ROLE GATE — a non-admin viewer sees no screen
// ─────────────────────────────────────────────────────────────────────────────

async function testNonAdminDoesNotSeeScreen(src) {
  const ctx = await renderScreen({ source: src, role: 'manager', settings: SETTINGS_ENABLED, status: STATUS_CONFIGURED });
  // POSITIVE assertion first: the restricted panel actually rendered, not
  // just "the form is missing", which an empty-render mutation would also
  // satisfy vacuously.
  assert.ok(
    ctx.frame.indexOf('Email notification settings are limited to the store owner. Open the till to start ringing sales.') !== -1,
    `The role-restricted message did not render for a non-admin viewer. Frame:\n${ctx.frame.slice(0, 300)}`
  );
  assert.ok(!/id="email-enabled"/.test(ctx.frame), 'The enable toggle rendered for a non-admin viewer.');
  assert.ok(!/_saveEmailNotifications\(\)/.test(ctx.frame), 'The Save control rendered for a non-admin viewer.');
  console.log('PASS: a non-admin viewer sees the restricted panel, not the email notifications screen');
}

// ─────────────────────────────────────────────────────────────────────────────
// (1) LOAD — the screen renders current settings from GET /settings
// ─────────────────────────────────────────────────────────────────────────────

async function testScreenRendersCurrentSettings(src) {
  const ctx = await renderScreen({ source: src, settings: SETTINGS_ENABLED, status: STATUS_CONFIGURED });
  assert.ok(/_saveEmailNotifications\(\)/.test(ctx.frame), 'No Save control found in the rendered screen.');
  assert.strictEqual(ctx.getEl('email-enabled').checked, true,
    "The enable toggle did not reflect settings.enabled === '1'.");
  assert.strictEqual(ctx.getEl('email-low-stock').value, 'owner@example.com',
    'The low-stock recipient field did not load the value GET /settings returned.');
  assert.strictEqual(ctx.getEl('email-max-attempts').value, '8',
    'The max-attempts field did not load the value GET /settings returned.');
  assert.strictEqual(ctx.getEl('email-submit-interval').value, '60',
    'The submit-interval field did not load the value GET /settings returned.');
  console.log('PASS: the screen renders the current settings returned by GET /settings');
}

// ─────────────────────────────────────────────────────────────────────────────
// (2) SAVE — POSTs the changed keys to /api/notifications/settings
// ─────────────────────────────────────────────────────────────────────────────

async function testSavePostsChangedKeys(src) {
  const ctx = await renderScreen({ source: src, settings: SETTINGS_DISABLED, status: STATUS_UNCONFIGURED, save: SAVE_OK });
  // Structural half first -- see M5 below: this is what makes the "render
  // nothing for everyone" mutation also fail THIS test, not just the load
  // test.
  assert.ok(/_saveEmailNotifications\(\)/.test(ctx.frame), 'No Save control found in the rendered screen.');

  ctx.getEl('email-enabled').checked = true;
  ctx.getEl('email-low-stock').value = 'newlow@shop.com';
  ctx.getEl('email-reports').value = 'newreports@shop.com';
  ctx.getEl('email-max-attempts').value = '5';
  ctx.getEl('email-submit-interval').value = '120';
  await ctx.rs._saveEmailNotifications();
  await settle();

  const posts = postsTo(ctx.calls, SETTINGS_URL);
  assert.strictEqual(posts.length, 1,
    `Expected exactly 1 POST to ${SETTINGS_URL}, saw ${posts.length}. Calls:\n` + JSON.stringify(ctx.calls, null, 2));
  assert.deepStrictEqual(posts[0].body, {
    enabled: '1',
    low_stock_recipient: 'newlow@shop.com',
    reports_recipient: 'newreports@shop.com',
    max_attempts: '5',
    submit_interval_seconds: '120',
  }, `The POST body did not carry the typed/toggled values. Body: ${JSON.stringify(posts[0].body)}`);
  console.log('PASS: saving POSTs the changed keys to /api/notifications/settings');
}

// ─────────────────────────────────────────────────────────────────────────────
// (3) THE SMTP WARNING — enabled on + smtp_configured false shows it
// ─────────────────────────────────────────────────────────────────────────────

async function testEnabledWithoutSmtpShowsWarning(src) {
  const ctx = await renderScreen({ source: src, settings: SETTINGS_ENABLED, status: STATUS_UNCONFIGURED });
  assert.ok(
    ctx.warningHtml().indexOf('nothing will actually send') !== -1,
    `The SMTP-not-configured warning did not appear with enabled='1' and smtp_configured=false. ` +
    `Warning HTML:\n${ctx.warningHtml()}`
  );
  console.log('PASS: enabled on + smtp_configured false shows the "nothing will send" warning');
}

// ─────────────────────────────────────────────────────────────────────────────
// (4) THE SMTP WARNING — smtp_configured true does NOT show it
// ─────────────────────────────────────────────────────────────────────────────

async function testSmtpConfiguredDoesNotShowWarning(src) {
  const ctx = await renderScreen({ source: src, settings: SETTINGS_ENABLED, status: STATUS_CONFIGURED });
  assert.strictEqual(ctx.warningHtml(), '',
    `The SMTP warning appeared even though smtp_configured is true -- do not nag once the install can ` +
    `actually send. Warning HTML:\n${ctx.warningHtml()}`);
  // Sanity: the calm, once-only note IS shown in this state, so this test
  // is not passing because the whole status card failed to render.
  assert.ok(ctx.noteText().indexOf('can send email') !== -1,
    `Sanity: the calm "can send email" note did not render when smtp_configured is true. Note: ${ctx.noteText()}`);
  console.log('PASS: smtp_configured true does not show the warning (and shows the calm note instead)');
}

// ─────────────────────────────────────────────────────────────────────────────
// (5) MALFORMED EMAIL — refused client-side, no network round trip
// ─────────────────────────────────────────────────────────────────────────────

async function testMalformedEmailRefusedClientSide(src) {
  const ctx = await renderScreen({ source: src, settings: SETTINGS_DISABLED, status: STATUS_UNCONFIGURED, save: SAVE_OK });
  ctx.getEl('email-low-stock').value = 'not-an-email';
  await ctx.rs._saveEmailNotifications();
  await settle();

  const posts = postsTo(ctx.calls, SETTINGS_URL);
  assert.strictEqual(posts.length, 0,
    `A malformed recipient reached the network -- POST /settings would 400 on this instead of the ` +
    `client refusing it up front. Calls:\n${JSON.stringify(ctx.calls, null, 2)}`);
  assert.ok(ctx.toasts.some((t) => t.type === 'error'),
    'No error toast was shown when the recipient field held a malformed address.');
  console.log('PASS: a malformed email address is refused client-side with no network call');
}

// ─────────────────────────────────────────────────────────────────────────────
// (6) A 400 FROM THE SERVER — surfaced verbatim, not swallowed
// ─────────────────────────────────────────────────────────────────────────────

async function test400IsSurfacedToTheUser(src) {
  const ctx = await renderScreen({ source: src, settings: SETTINGS_DISABLED, status: STATUS_UNCONFIGURED, save: SAVE_400 });
  ctx.getEl('email-max-attempts').value = '0'; // the value the fixture's message describes
  await ctx.rs._saveEmailNotifications();
  await settle();

  assert.ok(ctx.toasts.some((t) => t.type === 'error' && t.msg === SAVE_400.message),
    `The server's 400 message was not surfaced verbatim. Toasts:\n${JSON.stringify(ctx.toasts, null, 2)}`);
  console.log('PASS: a 400 from the server is surfaced to the user verbatim');
}

// ─────────────────────────────────────────────────────────────────────────────
// i18n — every user-visible string on this screen is a real, single-quoted
// t() call, present with distinct Arabic in both catalogs (ci-hardening-w0.3
// brief: "the ratchet's regex matches only single-quoted calls and a
// double-quoted one silently dodged it earlier in this project")
// ─────────────────────────────────────────────────────────────────────────────

function testAllStringsAreSingleQuotedAndInBothCatalogs() {
  const start = SRC.indexOf('  async _renderEmailNotifications(c) {');
  const end = SRC.indexOf('\n  // ── AUDIT LOG', start);
  assert.ok(start !== -1 && end !== -1 && end > start,
    'Could not locate the Email Notifications section boundaries in subsystem-retail.js -- the scrape is broken.');
  const region = SRC.slice(start, end);

  const re = /\bt\('((?:[^'\\]|\\.)*)'\)/g;
  const keys = new Set();
  let m;
  while ((m = re.exec(region)) !== null) keys.add(m[1]);

  assert.ok(keys.size >= 20,
    `Only ${keys.size} single-quoted t() call(s) found in the Email Notifications section -- this screen ` +
    'carries far more copy than that, so treat it as a harness failure.');

  const missing = [...keys].filter((k) => !(k in EN) || !(k in AR) || !String(AR[k]).trim() || EN[k] === AR[k]);
  assert.deepStrictEqual(missing, [],
    'These strings are t()-called on the Email Notifications screen but have no real, distinct Arabic:\n  ' +
    missing.map((k) => `${JSON.stringify(k)} — en:${k in EN ? 'yes' : 'MISSING'} ` +
      `ar:${k in AR ? (String(AR[k]).trim() ? (EN[k] === AR[k] ? 'UNTRANSLATED' : 'yes') : 'EMPTY') : 'MISSING'}`)
      .join('\n  ') +
    '\n\nAdd each to BOTH locales/en.json and locales/ar.json with a real translation.');

  console.log(`PASS: ${keys.size} single-quoted t() call(s) on the Email Notifications screen, all present ` +
    'with real, distinct Arabic in both catalogs');
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

  // ── The eight required behaviours, against the REAL, unmutated build ──────
  await run('testNavEntryExistsAndPointsAtScreen', testNavEntryExistsAndPointsAtScreen);
  await run('testNonAdminDoesNotSeeScreen', () => testNonAdminDoesNotSeeScreen(SRC));
  await run('testScreenRendersCurrentSettings', () => testScreenRendersCurrentSettings(SRC));
  await run('testSavePostsChangedKeys', () => testSavePostsChangedKeys(SRC));
  await run('testEnabledWithoutSmtpShowsWarning', () => testEnabledWithoutSmtpShowsWarning(SRC));
  await run('testSmtpConfiguredDoesNotShowWarning', () => testSmtpConfiguredDoesNotShowWarning(SRC));
  await run('testMalformedEmailRefusedClientSide', () => testMalformedEmailRefusedClientSide(SRC));
  await run('test400IsSurfacedToTheUser', () => test400IsSurfacedToTheUser(SRC));
  await run('testAllStringsAreSingleQuotedAndInBothCatalogs', testAllStringsAreSingleQuotedAndInBothCatalogs);

  // ── Mutation proofs — M1-M5 from the ci-hardening-w0.3 brief, plus M6-M8
  //    (ENGINEERING.md: mutation-prove every guard, not only the four named
  //    ones) ─────────────────────────────────────────────────────────────────

  // M1: remove the nav entry => test (7) FAILS.
  await run('M1: nav entry removed => testNavEntryExistsAndPointsAtScreen FAILS', async () => {
    const brokenShell = mutate(SHELL_SRC, [[
      "        { id: 'email-notifications', label: 'Email Notifications', icon: '📧', ownerOnly: true },\n",
      '',
    ]]);
    return provesMutation('M1 nav entry removed', null, () => assertNavEntryAndRouteOk(brokenShell, SRC));
  });

  // M2: never show the SMTP warning => test (3) FAILS.
  await run('M2: SMTP warning never shown => testEnabledWithoutSmtpShowsWarning FAILS', async () => {
    const broken = mutate(SRC, [[
      "        warning.innerHTML = (enabledOn && !this._emailSmtpConfigured)\n" +
      "          ? `<p style=\"color:var(--state-warning-text);background:var(--state-warning-surface);border:1px solid var(--border-default);border-radius:8px;padding:10px 12px;font-size:13px;margin:0 0 16px\">${t('Email is switched on, but this installation has no mail server configured -- nothing will actually send. Ask whoever installed this system to set AURA_SMTP_HOST.')}</p>`\n" +
      "          : '';",
      "        warning.innerHTML = ''; // MUTATED: SMTP warning never shown",
    ]]);
    return provesMutation('M2 SMTP warning never shown', broken, (b) => testEnabledWithoutSmtpShowsWarning(b));
  });

  // M3: show the warning even when SMTP IS configured => test (4) FAILS.
  await run('M3: warning shown even when SMTP is configured => testSmtpConfiguredDoesNotShowWarning FAILS', async () => {
    const broken = mutate(SRC, [[
      '        warning.innerHTML = (enabledOn && !this._emailSmtpConfigured)',
      '        warning.innerHTML = (enabledOn) // MUTATED: shows even when SMTP is configured',
    ]]);
    return provesMutation('M3 warning shown regardless of smtp_configured', broken, (b) => testSmtpConfiguredDoesNotShowWarning(b));
  });

  // M4: make save() not POST => test (2) FAILS.
  await run('M4: save() stops POSTing => testSavePostsChangedKeys FAILS', async () => {
    const broken = mutate(SRC, [[
      "      const d = await this._post('/api/notifications/settings', payload);",
      "      const d = { status: 'success', data: payload }; // MUTATED: no network call",
    ]]);
    return provesMutation('M4 save() does not POST', broken, (b) => testSavePostsChangedKeys(b));
  });

  // M5: ALLOW HALF — render the screen empty for everyone. Tests (1) and (2)
  // must FAIL. A screen that shows nothing passes every "does not show the
  // wrong thing" assertion (the role-gate test would even look green).
  await run('M5: screen renders empty for everyone => tests (1) and (2) FAIL', async () => {
    const broken = mutate(SRC, [[
      '  async _renderEmailNotifications(c) {\n    this._injectStyles();',
      "  async _renderEmailNotifications(c) {\n    this._injectStyles();\n    c.innerHTML = ''; return; // MUTATED: allow-half, empty for everyone",
    ]]);
    const a = await provesMutation('M5a load test fails on an empty screen', broken, (b) => testScreenRendersCurrentSettings(b));
    const b2 = await provesMutation('M5b save test fails on an empty screen', broken, (b) => testSavePostsChangedKeys(b));
    return `${a}\n       ${b2}`;
  });

  // M6 (bonus): role gate short-circuited => test (8) FAILS.
  await run('M6 (bonus): role gate disabled => testNonAdminDoesNotSeeScreen FAILS', async () => {
    const broken = mutate(SRC, [[
      "    // enforcement; this guard is the real one.\n" +
      "    if (window.SubsystemApp && SubsystemApp.role && SubsystemApp.role !== 'admin') {",
      "    // enforcement; this guard is the real one.\n" +
      '    if (false) { // MUTATED: role gate disabled',
    ]]);
    return provesMutation('M6 role gate disabled', broken, (b) => testNonAdminDoesNotSeeScreen(b));
  });

  // M7 (bonus): the low-stock recipient's client-side validation guard is
  // removed => test (5) FAILS.
  await run('M7 (bonus): email validation guard removed => testMalformedEmailRefusedClientSide FAILS', async () => {
    const broken = mutate(SRC, [[
      "    if (lowStock && !this._looksLikeEmail(lowStock)) {\n" +
      "      SubsystemApp.showToast(t('That does not look like an email address.'), 'error');\n" +
      '      return;\n' +
      '    }',
      '    // MUTATED: low-stock recipient validation guard removed',
    ]]);
    return provesMutation('M7 email validation guard removed', broken, (b) => testMalformedEmailRefusedClientSide(b));
  });

  // M8 (bonus): the 400 message is swallowed => test (6) FAILS.
  await run('M8 (bonus): 400 message swallowed => test400IsSurfacedToTheUser FAILS', async () => {
    const broken = mutate(SRC, [[
      "        // Surfaced VERBATIM -- routes.py's 400 message (e.g. a bad\n" +
      "        // max_attempts value) is written to be read, not replaced with a\n" +
      "        // generic failure toast. See requirement (6) / M-proof in\n" +
      "        // retail_email_notifications_test.js.\n" +
      "        SubsystemApp.showToast((d && d.message) || t('Error'), 'error');",
      "        SubsystemApp.showToast(t('Email settings saved.'), 'success'); // MUTATED: 400 message swallowed",
    ]]);
    return provesMutation('M8 400 message swallowed', broken, (b) => test400IsSurfacedToTheUser(b));
  });

  console.log(results.join('\n'));
  if (failed) {
    console.error(`\nFAIL: retail_email_notifications_test.js — ${failed} of ${results.length} check(s) failed`);
    process.exitCode = 1;
  } else {
    console.log(`\nPASS: retail_email_notifications_test.js — ${results.length} check(s)`);
  }
}

main().catch((err) => {
  console.error('FAIL: retail_email_notifications_test.js (runner)');
  console.error(err && err.stack || err);
  process.exitCode = 1;
});
