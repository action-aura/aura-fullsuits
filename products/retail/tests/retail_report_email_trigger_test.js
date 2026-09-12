/**
 * retail_report_email_trigger_test.js — the on-demand "queue a summary
 * report" control on the Email Notifications screen.
 *
 * WHY THIS EXISTS
 *
 * POST /api/sub/retail/reports/email shipped complete, with its own test
 * file (retail_email_outbox_test.py), and no client ever called it. The
 * email channel even gained a settings screen — recipients, retry policy, an
 * outbox drainer — and still nothing anywhere could QUEUE a report, so the
 * feature was dead on every install while its WhatsApp sibling
 * (POST /reports/whatsapp, wired in whatsapp.js) worked fine.
 *
 * That is the same complete-backend-no-doorway shape this codebase has now
 * produced six times, which is why the doorway ships with a guard rather
 * than on its own.
 *
 * THE ASSERTIONS THAT MATTER
 *
 *  * An EMPTY override box must leave `recipient` out of the payload, so the
 *    route falls back to the configured reports_recipient. This is the whole
 *    point of the optional field: a shop that has set a Reports Recipient
 *    must not have to retype it to send a report.
 *  * A refusal must be shown in the SERVER's words. The route returns 400
 *    (no recipient anywhere) and 409 (email notifications not enabled for
 *    this company — which also covers "SMTP was never configured", because
 *    is_enabled() folds that in). Those need different actions from
 *    different people, so a generic "Error" toast would be a downgrade.
 *
 * Loads the REAL products/retail/frontend/subsystem-retail.js through Node's
 * vm module. No framework is configured for this vanilla-JS frontend:
 *
 *   node products/retail/tests/retail_report_email_trigger_test.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const FRONTEND_FILE = path.join(__dirname, '..', 'frontend', 'subsystem-retail.js');

function makeElementStub(id) {
  const el = {
    id: id || '',
    _innerHTML: '',
    textContent: '',
    value: '',
    checked: false,
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

/**
 * @param responder (url, method) -> {status, message?, data?} for the routes
 *        this screen touches. Every request is recorded in `calls`.
 */
function loadRetailSystem(responder, calls) {
  const code = fs.readFileSync(FRONTEND_FILE, 'utf8');
  const els = new Map();
  const toasts = [];

  const sandbox = {
    console: { error() {}, warn() {}, log() {} },
    t: (s) => s,
    setTimeout,
    clearTimeout,
    getComputedStyle: () => ({ getPropertyValue: () => '' }),
    fetch: (url, init) => {
      const method = (init && init.method) || 'GET';
      calls.push({ url: String(url), method, body: init && init.body });
      const payload = responder(String(url), method);
      return Promise.resolve({
        ok: true, status: 200, json: () => Promise.resolve(payload),
      });
    },
    SubsystemApp: {
      // The screen is ownerOnly and enforces it itself, not just via the nav.
      role: 'admin',
      showToast(msg, kind) { toasts.push({ msg, kind }); },
      hasCapability() { return true; },
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
  // getEl mirrors document.getElementById's create-on-demand behaviour, so a
  // control the loader never touched is still addressable here -- exactly as
  // it would be in a real document, where the element exists because the
  // template rendered it.
  const getEl = (id) => sandbox.document.getElementById(id);
  return { RetailSystem: sandbox.RetailSystem, els, toasts, getEl };
}

/** A healthy, email-enabled shop with a configured reports recipient. */
function healthyResponder(sendResult) {
  return (url, method) => {
    if (url.includes('/reports/email')) {
      return sendResult || { status: 'success', data: { queued: true, recipient: 'owner@shop.test' } };
    }
    if (url.includes('/notifications/status')) {
      return { status: 'success', data: { smtp_configured: true, enabled: true } };
    }
    if (url.includes('/notifications/settings')) {
      return { status: 'success', data: { settings: {
        enabled: '1', reports_recipient: 'owner@shop.test',
        low_stock_recipient: '', max_attempts: 5, submit_interval_seconds: 60,
      } } };
    }
    return { status: 'success', data: {} };
  };
}

async function renderScreen(responder) {
  const calls = [];
  const ctx = loadRetailSystem(responder, calls);
  // Held by reference rather than looked up afterwards: this container is the
  // argument the renderer writes into, not an element it ever fetches by id,
  // so it never enters the sandbox's element map.
  const container = makeElementStub('content');
  await ctx.RetailSystem._renderEmailNotifications(container);
  return Object.assign(ctx, { calls, container });
}

async function testTheControlIsOnTheScreen() {
  const { container: c, calls } = await renderScreen(healthyResponder());
  // Matched as a whole id ATTRIBUTE. A loose substring check would be
  // satisfied by 'email-send-report-card-REMOVED', which is exactly how the
  // sibling business-day guard first went green against a hidden card.
  const cardTag = c.innerHTML.match(/<div class="sub-chart-card" id="email-send-report-card"[^>]*>/);
  assert.ok(cardTag, 'the Email Notifications screen must carry the send-report card');
  assert.ok(!/display\s*:\s*none/i.test(cardTag[0]),
    'the send-report card must not render hidden');

  for (const id of ['email-report-days', 'email-report-recipient', 'email-send-report-btn']) {
    assert.ok(c.innerHTML.includes('id="' + id + '"'), 'missing control: ' + id);
  }
  assert.ok(c.innerHTML.includes('RetailSystem._sendReportEmail()'),
    'the button must be wired to _sendReportEmail');
  assert.ok(calls.length > 0, 'the screen must load its settings');
}

async function testEmptyOverrideFallsBackToTheConfiguredRecipient() {
  const { RetailSystem, getEl, calls } = await renderScreen(healthyResponder());

  getEl('email-report-recipient').value = '   ';  // owner typed nothing
  getEl('email-report-days').value = '7';
  calls.length = 0;
  await RetailSystem._sendReportEmail();

  const post = calls.find(c => c.url.includes('/reports/email') && c.method === 'POST');
  assert.ok(post, 'the button must POST to /reports/email');
  const body = JSON.parse(post.body);

  assert.ok(!('recipient' in body),
    'an empty override box must omit `recipient` entirely, so the route ' +
    'falls back to the configured reports_recipient. A shop that has set a ' +
    'Reports Recipient must not have to retype it to send a report.');
  assert.strictEqual(body.days, 7, 'the period must be sent as a number');
}

async function testATypedOverrideIsSentTrimmed() {
  const { RetailSystem, getEl, calls } = await renderScreen(healthyResponder());

  getEl('email-report-recipient').value = '  accountant@shop.test  ';
  getEl('email-report-days').value = '30';
  calls.length = 0;
  await RetailSystem._sendReportEmail();

  const post = calls.find(c => c.url.includes('/reports/email') && c.method === 'POST');
  const body = JSON.parse(post.body);
  assert.strictEqual(body.recipient, 'accountant@shop.test',
    'a typed address overrides the configured one, trimmed');
  assert.strictEqual(body.days, 30);
}

async function testSuccessIsReportedAndTheOverrideIsCleared() {
  const { RetailSystem, getEl, toasts } = await renderScreen(healthyResponder());

  getEl('email-report-recipient').value = 'accountant@shop.test';
  toasts.length = 0;
  await RetailSystem._sendReportEmail();

  assert.ok(toasts.some(x => x.kind === 'success'),
    'a queued report must be confirmed -- the outbox is asynchronous, so ' +
    'this toast is the only immediate evidence anything happened');
  assert.strictEqual(getEl('email-report-recipient').value, '',
    'the one-off override must be cleared after a successful send, so the ' +
    'next report does not silently go to a stale address');
}

async function testARefusalIsShownInTheServersOwnWords() {
  const { RetailSystem, getEl, toasts } = await renderScreen(healthyResponder(
    { status: 'error', message: 'Email notifications are not enabled for this company' }));

  getEl('email-report-recipient').value = 'accountant@shop.test';
  toasts.length = 0;
  await RetailSystem._sendReportEmail();

  const errors = toasts.filter(x => x.kind === 'error');
  assert.strictEqual(errors.length, 1, 'a refused send must be reported');
  assert.ok(/not enabled for this company/.test(errors[0].msg),
    'the route\'s 400 (no recipient) and 409 (not enabled, which also covers ' +
    'unconfigured SMTP) need different actions from different people, so its ' +
    'own message must reach the owner. Got: ' + errors[0].msg);
  assert.ok(!toasts.some(x => x.kind === 'success'),
    'a refused send must never also claim success');
  assert.strictEqual(getEl('email-report-recipient').value, 'accountant@shop.test',
    'a FAILED send must keep what was typed -- clearing it would make the ' +
    'owner retype the address to retry the thing that just failed');
}

async function main() {
  await testTheControlIsOnTheScreen();
  await testEmptyOverrideFallsBackToTheConfiguredRecipient();
  await testATypedOverrideIsSentTrimmed();
  await testSuccessIsReportedAndTheOverrideIsCleared();
  await testARefusalIsShownInTheServersOwnWords();
  console.log('PASS: retail_report_email_trigger_test.js (5 cases)');
}

main().catch((err) => {
  console.error('FAIL: retail_report_email_trigger_test.js');
  console.error(err);
  process.exitCode = 1;
});
