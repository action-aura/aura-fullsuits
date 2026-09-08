/**
 * clinic_patient_table_xss_test.js — the clinic tables escape operator-entered
 * data before it reaches innerHTML, and before an onclick attribute.
 *
 * WHY THIS EXISTS, AND WHY NOW
 *
 * products/clinic/frontend/subsystem-clinic.js wrote patient and doctor names
 * straight into innerHTML (`<td style="font-weight:600">${p.name}</td>`) and
 * built its row buttons' onclick attributes with only an apostrophe-doubling
 * pass (`'${p.name.replace(/'/g,"\\'")}'`), which covers neither `"` nor `<`.
 * `git grep -n "_esc" products/clinic/frontend/subsystem-clinic.js` returned
 * NOTHING, while retail defines the identical helper at
 * subsystem-retail.js:795 and pins the same boundary with three dedicated
 * tests (retail_pos_name_xss_test.js, retail_products_table_name_xss_test.js,
 * retail_supplier_name_apostrophe_onclick_test.js).
 *
 * This is NOT a live sink today: products/clinic/frontend has no index.html
 * and no app-shell.js, so nothing loads subsystem-clinic.js and neither table
 * can be reached. That is precisely why it was worth fixing now — it is a
 * hazard the clinic-shell decision would otherwise inherit, and it is far
 * cheaper to close before the shell exists than after. This file is the guard
 * that keeps it closed while the shell is being built.
 *
 * TWO NESTED CONTEXTS, TWO ESCAPES. The onclick sites need both, in order:
 * `.replace(/'/g,"\\'")` makes the name safe inside the single-quoted JS
 * STRING, and `_esc()` then makes the result safe inside the double-quoted
 * HTML ATTRIBUTE. Either one alone leaves a hole, so both directions are
 * asserted below rather than just "the raw payload is absent".
 *
 * No test framework is configured for this vanilla-JS, build-step-free
 * frontend (see CLAUDE.md), so this runs standalone on Node built-ins:
 *
 *   node products/clinic/tests/clinic_patient_table_xss_test.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const FRONTEND_FILE = path.join(__dirname, '..', 'frontend', 'subsystem-clinic.js');
const SRC = fs.readFileSync(FRONTEND_FILE, 'utf8');

// Dangerous in THREE contexts at once, which is the point: it closes a
// double-quoted HTML attribute, opens a tag, and carries a bare apostrophe
// that the old single-quote pass was the only thing handling.
const MALICIOUS_NAME = `"><img src=x onerror=alert(document.cookie)>O'Brien`;

function makeElementStub() {
  const el = {
    innerHTML: '',
    textContent: '',
    id: '',
    value: '',
    style: { setProperty() {} },
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    appendChild() {},
    remove() {},
    focus() {},
    getAttribute() { return null; },
    setAttribute() {},
    querySelector() { return null; },
    querySelectorAll() { return []; },
    addEventListener() {},
  };
  return el;
}

/**
 * Load the REAL subsystem-clinic.js and hand back the <tbody> stubs the two
 * table renderers write into. `fetch` is stubbed per-URL rather than rejected,
 * because _loadPatients/_loadDoctors swallow their own errors -- a rejecting
 * fetch would leave both tables EMPTY and this file would then report a clean
 * bill of health having read no markup at all.
 */
function loadClinicSystem(rows) {
  const tbodies = Object.create(null);
  const named = Object.create(null);

  const sandbox = {
    console,
    t: (s) => s,
    fetch: (url) => Promise.resolve({
      ok: true,
      status: 200,
      json: () => Promise.resolve({ status: 'success', data: rows }),
    }),
    getComputedStyle: () => ({ getPropertyValue: () => '' }),
    setTimeout: (fn) => { if (typeof fn === 'function') fn(); return 0; },
    clearTimeout: () => {},
    localStorage: { getItem: () => null, setItem() {} },
    document: {
      getElementById: () => makeElementStub(),
      createElement: () => makeElementStub(),
      querySelector(sel) {
        if (!named[sel]) named[sel] = makeElementStub();
        if (/tbody/.test(sel)) tbodies[sel] = named[sel];
        return named[sel];
      },
      querySelectorAll: () => [],
      head: { appendChild() {} },
      body: { appendChild() {} },
      documentElement: { getAttribute: () => 'light', style: { setProperty() {} } },
      addEventListener() {},
    },
    SubsystemApp: { showToast() {} },
  };
  sandbox.window = sandbox;

  vm.createContext(sandbox);
  vm.runInContext(SRC, sandbox, { filename: FRONTEND_FILE });
  assert.ok(sandbox.ClinicSystem, 'subsystem-clinic.js did not expose window.ClinicSystem');
  return { ClinicSystem: sandbox.ClinicSystem, tbodies };
}

function assertEscaped(html, label) {
  // ANTI-VACUITY: a renderer that produced nothing would satisfy every
  // "payload absent" assertion below while proving nothing at all.
  assert.ok(html && html.length > 60,
    `${label}: rendered only ${JSON.stringify(html)} -- there is no markup here to make a claim about.`);
  assert.ok(!html.includes(MALICIOUS_NAME),
    `${label}: the raw, unescaped name reached innerHTML -- a stored-XSS injection point. Got: ${html}`);
  assert.ok(!html.includes('"><img'),
    `${label}: the attribute-breakout half of the payload survived unescaped. Got: ${html}`);
  assert.ok(html.includes('&lt;img') && html.includes('&quot;&gt;'),
    `${label}: expected the name HTML-escaped through _esc(), but found no escaped form. Got: ${html}`);
}

async function testPatientTableEscapesName() {
  const { ClinicSystem, tbodies } = loadClinicSystem([{
    id: 7, patient_code: 'P-0007', name: MALICIOUS_NAME,
    gender: 'f', dob: '1990-01-01', phone: '0790000000',
    blood_type: 'O+', status: 'active',
  }]);
  await ClinicSystem._loadPatients();
  const html = (tbodies['#cl-pt-table tbody'] || {}).innerHTML || '';
  assertEscaped(html, 'patient table');

  // The onclick half. The name is inside a single-quoted JS string inside a
  // double-quoted HTML attribute; the apostrophe must survive as an ESCAPED
  // JS apostrophe (\&#39; -- the browser decodes the entity, leaving \'),
  // never as a bare one that would terminate the string early.
  assert.ok(!/_deletePatient\(\d+, '[^']*'[^)]*O'Brien/.test(html),
    `patient table: an apostrophe in the name broke out of the onclick's JS string. Got: ${html}`);
  assert.ok(html.includes('\\&#39;'),
    `patient table: the apostrophe was not escaped for BOTH the JS-string and the HTML-attribute ` +
    `context. Expected the "\\\\&#39;" pair. Got: ${html}`);
  console.log('PASS: the patient table escapes a hostile name in both the cell and the onclick');
}

async function testDoctorTableEscapesName() {
  const { ClinicSystem, tbodies } = loadClinicSystem([{
    id: 3, name: MALICIOUS_NAME, specialty: 'Cardiology',
    phone: '0791111111', email: 'a@b.c', status: 'active',
  }]);
  await ClinicSystem._loadDoctors();
  assertEscaped((tbodies['#cl-doc-table tbody'] || {}).innerHTML || '', 'doctor table');
  console.log('PASS: the doctor table escapes a hostile name');
}

// ─────────────────────────────────────────────────────────────────────────────
// MUTATION PROOF — remove the escape and both checks must go red.
//
// Without this, a _esc() that returned its argument unchanged, or a renderer
// that stopped calling it, would leave every assertion above passing on a
// technicality. The mutation is the EXACT pre-fix source line.
// ─────────────────────────────────────────────────────────────────────────────

async function testEscapingIsMutationProved() {
  const find = '<td style="font-weight:600">${this._esc(p.name)}</td>';
  const hits = SRC.split(find).length - 1;
  assert.strictEqual(hits, 1,
    `Mutation anchor occurs ${hits} time(s), expected exactly 1: ${JSON.stringify(find)}\n` +
    'A mutation that no longer applies would let this proof pass while proving nothing.');
  const mutated = SRC.split(find).join('<td style="font-weight:600">${p.name}</td>');

  const tbodies = Object.create(null);
  const named = Object.create(null);
  const sandbox = {
    console,
    t: (s) => s,
    fetch: () => Promise.resolve({
      ok: true, status: 200,
      json: () => Promise.resolve({
        status: 'success',
        data: [{ id: 7, patient_code: 'P-0007', name: MALICIOUS_NAME, status: 'active' }],
      }),
    }),
    getComputedStyle: () => ({ getPropertyValue: () => '' }),
    setTimeout: (fn) => { if (typeof fn === 'function') fn(); return 0; },
    clearTimeout: () => {},
    localStorage: { getItem: () => null, setItem() {} },
    document: {
      getElementById: () => makeElementStub(),
      createElement: () => makeElementStub(),
      querySelector(sel) {
        if (!named[sel]) named[sel] = makeElementStub();
        if (/tbody/.test(sel)) tbodies[sel] = named[sel];
        return named[sel];
      },
      querySelectorAll: () => [],
      head: { appendChild() {} },
      body: { appendChild() {} },
      documentElement: { getAttribute: () => 'light', style: { setProperty() {} } },
      addEventListener() {},
    },
    SubsystemApp: { showToast() {} },
  };
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(mutated, sandbox, { filename: FRONTEND_FILE + ' (mutated)' });
  await sandbox.ClinicSystem._loadPatients();

  let threw = null;
  try {
    assertEscaped((tbodies['#cl-pt-table tbody'] || {}).innerHTML || '', 'patient table (mutated)');
  } catch (err) {
    threw = err;
  }
  assert.ok(threw,
    'MUTATION SURVIVED — the escaping check passed against a build with the original, unescaped ' +
    '`${p.name}` name cell deliberately reintroduced, so it is not actually watching for it. ' +
    'Fix the check, not the mutation.');
  console.log(`PASS: the escaping check is mutation-proved  [caught: ${String(threw.message).split('\n')[0].slice(0, 110)}]`);
}

const CHECKS = [
  ['the patient table escapes a hostile name', testPatientTableEscapesName],
  ['the doctor table escapes a hostile name', testDoctorTableEscapesName],
  ['the escaping check is mutation-proved', testEscapingIsMutationProved],
];

async function main() {
  const failures = [];
  for (const [name, fn] of CHECKS) {
    try {
      await fn();
    } catch (err) {
      failures.push(name);
      console.error(`FAIL: ${name}`);
      console.error('      ' + String((err && err.message) || err).replace(/\n/g, '\n      '));
    }
  }
  if (failures.length) {
    console.error(`\nFAIL: clinic_patient_table_xss_test.js — ${failures.length} of ${CHECKS.length} checks failed:`);
    for (const name of failures) console.error(`  - ${name}`);
    process.exitCode = 1;
    return;
  }
  console.log(`PASS: clinic_patient_table_xss_test.js — ${CHECKS.length} checks`);
}

main().catch((err) => {
  console.error('FAIL: clinic_patient_table_xss_test.js (runner)');
  console.error(err);
  process.exitCode = 1;
});
