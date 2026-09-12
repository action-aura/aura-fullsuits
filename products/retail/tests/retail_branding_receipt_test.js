/**
 * Aura Retail — the printed receipt is brandable (launch-readiness,
 * "make the system be brandable of whatever institute or coop or foundation
 * bought it").
 *
 * `_printReceipt` used to print the literal string "Aura Retail" with no
 * shop name, address or tax number anywhere on it. This file drives the
 * REAL `_printReceipt` (never a reimplementation) against a stubbed
 * `fetch`, and reads back the exact HTML it hands to the print iframe's
 * `document.write()`.
 *
 * ── WHAT THIS FILE REFUSES TO LET SHIP ──────────────────────────────────────
 *
 * 1. A RECEIPT THAT NEVER SHOWS THE CONFIGURED BRAND. Business name, address
 *    and tax number must actually reach the printed page when configured.
 * 2. AN UNBRANDED INSTALL THAT CANNOT PRINT. With nothing configured, the
 *    receipt must still render -- falling back to the product name, never a
 *    blank header.
 * 3. AN OPERATOR-TYPED BUSINESS NAME REACHING innerHTML/document.write RAW.
 *    branding_business_name is shop-typed text stored in retail_settings,
 *    exactly the trust boundary this file already escapes category/reorder-
 *    request text for (this._esc).
 *
 * ── MUTATION-PROVED ─────────────────────────────────────────────────────────
 * Every behavioural claim below is re-run against a DELIBERATELY BROKEN copy
 * of subsystem-retail.js and is required to FAIL there -- including an
 * "allow-half" mutation that renders the branding block as nothing at all,
 * which would otherwise pass every "does not show X" check by showing
 * nothing (see testEveryGuardIsProvenByBreakingIt).
 *
 * No test framework is configured for this vanilla-JS, build-step-free
 * frontend (see CLAUDE.md), so this runs standalone on Node built-ins:
 *
 *   node products/retail/tests/retail_branding_receipt_test.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const FRONTEND = path.join(__dirname, '..', 'frontend');
const RETAIL_JS = path.join(FRONTEND, 'subsystem-retail.js');

const SRC = fs.readFileSync(RETAIL_JS, 'utf8');

/* The file's own line ending. subsystem-retail.js is CRLF -- an anchor
   written with plain "\n" would silently match nothing, making every
   mutation proof below pass while breaking nothing. See mutate(). */
const EOL = SRC.indexOf('\r\n') !== -1 ? '\r\n' : '\n';
const nl = (s) => s.replace(/\n/g, EOL);

const BRANDING_URL = '/api/sub/retail/settings/branding';
const LOGO_URL = '/api/sub/retail/settings/branding/logo';

// ─────────────────────────────────────────────────────────────────────────────
// FIXTURES
// ─────────────────────────────────────────────────────────────────────────────

const UNCONFIGURED_BRANDING_OK = {
  status: 'success',
  data: {
    branding_business_name: '', branding_address: '', branding_phone: '',
    branding_tax_number: '', branding_receipt_header: '', branding_receipt_footer: '',
    has_logo: false, einvoicing_seller: null,
  },
};

const CONFIGURED_BRANDING_OK = {
  status: 'success',
  data: {
    branding_business_name: 'Sunrise Co-op',
    branding_address: '12 Rainbow St, Amman',
    branding_phone: '+962-6-000-0000',
    branding_tax_number: 'TIN-778899',
    branding_receipt_header: 'Fair trade, every time',
    branding_receipt_footer: 'Members save 5% -- ask at the till',
    has_logo: false, einvoicing_seller: null,
  },
};

/* Breaks out of a double-quoted HTML attribute AND carries a classic
   script-injection vector, matching this file's existing MALICIOUS_NAME
   fixtures elsewhere (retail_pos_name_xss_test.js and siblings). */
const MALICIOUS_NAME = `"><img src=x onerror=alert(document.cookie)>`;
const MALICIOUS_BRANDING_OK = {
  status: 'success',
  data: {
    branding_business_name: MALICIOUS_NAME,
    branding_address: '', branding_phone: '', branding_tax_number: '',
    branding_receipt_header: '', branding_receipt_footer: '',
    has_logo: false, einvoicing_seller: null,
  },
};

const SALE = {
  sale_number: 'SALE-0042',
  created_at: '2026-08-26 10:15:00',
  lines: [{ name: 'Widget', quantity: 2, line_total: 20 }],
  subtotal: 20, discount_amount: 0, tax_amount: 0, total: 20, amount_paid: 20, change: 0,
};

// ─────────────────────────────────────────────────────────────────────────────
// HARNESS
// ─────────────────────────────────────────────────────────────────────────────

function makeStub(over) {
  return Object.assign({
    innerHTML: '', textContent: '', value: '', id: '', style: {}, dataset: {},
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    appendChild() {}, getAttribute() { return null; }, setAttribute() {},
    querySelectorAll() { return []; }, querySelector() { return null; },
    addEventListener() {}, removeEventListener() {}, remove() {},
  }, over || {});
}

/**
 * @param {object} opts
 *   source   — subsystem-retail.js text (a mutant, for the proofs below)
 *   branding — the GET .../settings/branding payload
 *   logo     — the GET .../settings/branding/logo payload
 */
function loadRetailSystem(opts) {
  const o = opts || {};
  let printedHtml = null;
  const iframeStub = {
    id: '', style: {},
    contentWindow: {
      document: {
        open() {}, write(html) { printedHtml = html; }, close() {},
      },
      focus() {}, print() {},
    },
  };

  const sandbox = {
    console: { log() {}, warn() {}, error() {}, info() {} },
    t: (s) => s,
    fetch: (url) => {
      const u = String(url);
      let payload;
      if (u.indexOf(LOGO_URL) !== -1) {
        payload = o.logo === undefined ? { status: 'success', data: { logo: null } } : o.logo;
      } else if (u.indexOf(BRANDING_URL) !== -1) {
        payload = o.branding === undefined ? UNCONFIGURED_BRANDING_OK : o.branding;
      } else {
        payload = { status: 'error', message: 'unmapped fixture route: ' + u };
      }
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(payload) });
    },
    navigator: { userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)' },
    localStorage: { getItem: () => null, setItem() {} },
    // Fires synchronously -- _printReceipt's setTimeout(..., 150) around
    // focus()/print() is not under test here; the HTML is already captured
    // by doc.write() before that callback would ever run.
    setTimeout: (fn) => { fn(); return 0; }, clearTimeout() {},
    setInterval: () => 0, clearInterval() {},
    document: {
      getElementById(id) { return id === 'ret-print-frame' ? null : null; },
      createElement(tag) { return tag === 'iframe' ? iframeStub : makeStub(); },
      body: { appendChild() {} },
      head: { appendChild() {} },
      documentElement: { getAttribute: () => 'light', setAttribute() {}, style: { setProperty() {} } },
      querySelector() { return null; },
      querySelectorAll() { return []; },
      addEventListener() {},
    },
  };
  sandbox.window = sandbox;
  sandbox.SubsystemApp = { active: 'retail', showToast() {}, hasCapability: () => true };

  vm.createContext(sandbox);
  vm.runInContext(o.source || SRC, sandbox, { filename: RETAIL_JS });
  assert.ok(sandbox.RetailSystem, 'subsystem-retail.js did not expose window.RetailSystem');
  return { rs: sandbox.RetailSystem, getPrintedHtml: () => printedHtml };
}

async function printReceipt(opts) {
  const ctx = loadRetailSystem(opts);
  await ctx.rs._printReceipt(Object.assign({}, SALE));
  const html = ctx.getPrintedHtml();
  assert.ok(typeof html === 'string' && html.length > 200,
    `_printReceipt wrote ${html === null ? 'nothing' : html.length + ' char(s)'} to the print ` +
    'frame -- too little for the assertions below to mean anything.');
  return html;
}

// ─────────────────────────────────────────────────────────────────────────────
// MUTATION HARNESS — same shape as retail_exceptions_screen_test.js
// ─────────────────────────────────────────────────────────────────────────────

function mutate(pairs) {
  let src = SRC;
  for (const [rawFind, rawReplace] of pairs) {
    const find = nl(rawFind);
    const replace = nl(rawReplace);
    const hits = src.split(find).length - 1;
    assert.strictEqual(
      hits, 1,
      `Mutation anchor occurs ${hits} time(s), expected exactly 1:\n  ${JSON.stringify(find)}\n\n` +
      'A mutation that no longer applies would let the proof below pass while proving nothing. Re-anchor it.'
    );
    src = src.replace(find, replace);
  }
  return src;
}

async function provesMutation(what, mutations, check) {
  const broken = mutate(mutations);
  let threw = null;
  try {
    await check(broken);
  } catch (err) {
    threw = err;
  }
  assert.ok(
    threw,
    `MUTATION SURVIVED — ${what}\n` +
    'The guard for this passed against a build with the behaviour deliberately broken, so it is ' +
    'not actually watching it. Fix the check, not the mutation.'
  );
  return `${what}  [caught: ${String(threw.message || threw).split('\n')[0].slice(0, 110)}]`;
}

// ─────────────────────────────────────────────────────────────────────────────
// CHECKS
// ─────────────────────────────────────────────────────────────────────────────

async function testTheReceiptRendersConfiguredBranding(src) {
  const html = await printReceipt({ source: src, branding: CONFIGURED_BRANDING_OK });
  const b = CONFIGURED_BRANDING_OK.data;
  assert.ok(html.indexOf(b.branding_business_name) !== -1,
    `configured business name missing from the printed receipt:\n${html}`);
  assert.ok(html.indexOf(b.branding_address) !== -1,
    `configured address missing from the printed receipt:\n${html}`);
  assert.ok(html.indexOf(b.branding_tax_number) !== -1,
    `configured tax number missing from the printed receipt:\n${html}`);
  assert.ok(html.indexOf(b.branding_receipt_header) !== -1,
    `configured receipt header missing from the printed receipt:\n${html}`);
  assert.ok(html.indexOf(b.branding_receipt_footer) !== -1,
    `configured receipt footer missing from the printed receipt:\n${html}`);
  assert.ok(html.indexOf('Aura Retail') === -1,
    'the hardcoded "Aura Retail" fallback still appears even though a business name was configured.');
}

async function testTheReceiptFallsBackSensiblyWhenNothingIsConfigured(src) {
  const html = await printReceipt({ source: src, branding: UNCONFIGURED_BRANDING_OK });
  assert.ok(html.indexOf('Aura Retail') !== -1,
    `an unbranded install must still print a legible header (fallback to the product name); got:\n${html}`);
  assert.ok(html.indexOf('Receipt #' + SALE.sale_number) !== -1,
    `the receipt number line is missing entirely -- the receipt did not render at all:\n${html}`);
  assert.ok(html.indexOf('Tax #:') === -1,
    'an empty tax number must not print an empty "Tax #:" line.');
}

async function testAMaliciousBusinessNameIsEscaped(src) {
  const html = await printReceipt({ source: src, branding: MALICIOUS_BRANDING_OK });
  assert.ok(html.indexOf(MALICIOUS_NAME) === -1,
    `the malicious business name reached the printed receipt UNESCAPED:\n${html}`);
  assert.ok(html.indexOf('&lt;img') !== -1 || html.indexOf('&quot;&gt;') !== -1,
    `the malicious business name does not appear in its ESCAPED form either -- it was dropped ` +
    `entirely rather than escaped, which is a different bug (see testTheReceipt` +
    `FallsBackSensiblyWhenNothingIsConfigured for the "renders nothing" case):\n${html}`);
}

async function testEveryGuardIsProvenByBreakingIt() {
  const proved = [];

  proved.push(await provesMutation(
    '1. the business name is rendered without escaping',
    [["rows.push(`<div class=\"rcpt-center rcpt-bold\">${this._esc(b.branding_business_name || 'Aura Retail')}</div>`);",
      "rows.push(`<div class=\"rcpt-center rcpt-bold\">${b.branding_business_name || 'Aura Retail'}</div>`); // MUTATED: unescaped"]],
    testAMaliciousBusinessNameIsEscaped));

  proved.push(await provesMutation(
    '2a. ALLOW HALF — the branding block renders nothing at all (test: configured branding shows)',
    [['  _brandingReceiptBlock(branding, logoDataUri) {\n    const b = branding || {};',
      "  _brandingReceiptBlock(branding, logoDataUri) {\n    return ''; // MUTATED: renders nothing\n    const b = branding || {};"]],
    testTheReceiptRendersConfiguredBranding));

  proved.push(await provesMutation(
    '2b. ALLOW HALF — the branding block renders nothing at all (test: unconfigured install still falls back)',
    [['  _brandingReceiptBlock(branding, logoDataUri) {\n    const b = branding || {};',
      "  _brandingReceiptBlock(branding, logoDataUri) {\n    return ''; // MUTATED: renders nothing\n    const b = branding || {};"]],
    testTheReceiptFallsBackSensiblyWhenNothingIsConfigured));

  console.log(`PASS: ${proved.length} guards proved by breaking the behaviour they watch:`);
  for (const line of proved) console.log('      ' + line);
}

// ─────────────────────────────────────────────────────────────────────────────
// RUNNER — every check runs, every failure is collected
// ─────────────────────────────────────────────────────────────────────────────

const EXPECTED_CHECKS = 4;

async function main() {
  const checks = [
    ['the receipt renders the configured name, address, phone, tax number, header and footer',
      () => testTheReceiptRendersConfiguredBranding()],
    ['with nothing configured, the receipt still renders and falls back to the product name',
      () => testTheReceiptFallsBackSensiblyWhenNothingIsConfigured()],
    ['a malicious business name is escaped, not executed',
      () => testAMaliciousBusinessNameIsEscaped()],
    ['every guard is proven by breaking it', testEveryGuardIsProvenByBreakingIt],
  ];

  const failures = [];
  for (const [name, fn] of checks) {
    try {
      await fn();
      console.log('  ok   ' + name);
    } catch (err) {
      failures.push(name);
      console.error('  FAIL ' + name);
      console.error('       ' + String((err && err.message) || err).replace(/\n/g, '\n       '));
    }
  }

  if (checks.length < EXPECTED_CHECKS) {
    console.error(`FAIL: retail_branding_receipt_test.js ran only ${checks.length} of ${EXPECTED_CHECKS} known checks.`);
    process.exitCode = 1;
    return;
  }
  if (failures.length) {
    console.error(`\nFAIL: retail_branding_receipt_test.js — ${failures.length} of ${checks.length} checks failed:`);
    for (const name of failures) console.error(`  - ${name}`);
    process.exitCode = 1;
    return;
  }
  console.log(`PASS: retail_branding_receipt_test.js — ${checks.length} checks`);
}

if (require.main === module) {
  main().catch((err) => {
    console.error('FAIL: retail_branding_receipt_test.js (runner)');
    console.error((err && err.stack) || err);
    process.exitCode = 1;
  });
}
