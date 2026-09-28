/**
 * Phase 0 item #16 -- short document numbers on screen.
 *
 * RetailSystem._docNo(n) strips the two uniqueness fragments a legacy
 * document number carries (SALE-000016-<company8>-<device8> --
 * _device_doc_discriminator's docstring in retail_api.py explains why both
 * exist: AUDIT-032B, cross-till uniqueness) down to the human part
 * ("SALE-000016") plus a short device tag, while leaving a per-till SERIES
 * number (schema v34, doc_series.allocate -- "<code>-<n>", no fragments at
 * all) and anything else it does not recognise completely untouched.
 *
 * This loads the REAL products/retail/frontend/subsystem-retail.js (via
 * Node's vm module, the same technique retail_products_table_name_xss_test.js
 * and retail_pos_keyboard_shortcuts_test.js already use) and drives the
 * actual _docNo()/_docNoHtml() functions -- not a reimplementation of the
 * regex here.
 *
 * Run: node products/retail/tests/retail_p0_docno_test.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const SRC_PATH = path.join(__dirname, '..', 'frontend', 'subsystem-retail.js');
const source = fs.readFileSync(SRC_PATH, 'utf8');

function loadRetailSystem() {
  const sandbox = {
    console,
    t: (s) => s, // i18n.js's global shorthand
    document: { addEventListener() {} },
  };
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(source, sandbox, { filename: SRC_PATH });
  assert.ok(sandbox.RetailSystem, 'subsystem-retail.js did not expose window.RetailSystem');
  return sandbox.RetailSystem;
}

function run() {
  const RetailSystem = loadRetailSystem();

  // ── 1. Legacy sale number: prefix+digits kept, both fragments dropped ────
  {
    const r = RetailSystem._docNo('SALE-000016-a1b2c3d4-e5f6a7b8');
    assert.strictEqual(r.main, 'SALE-000016', `legacy sale number: expected main 'SALE-000016', got '${r.main}'`);
    assert.strictEqual(r.deviceTag, 'e5f6', `legacy sale number: expected a 4-char device tag 'e5f6', got '${r.deviceTag}'`);
    assert.strictEqual(r.full, 'SALE-000016-a1b2c3d4-e5f6a7b8', 'legacy sale number: full string must be preserved untouched');
    console.log('PASS: a legacy SALE number is shortened to SALE-000016 with a 4-char device tag, full string kept');
  }

  // ── 2. Legacy return / PO numbers use the SAME shape ─────────────────────
  {
    const rReturn = RetailSystem._docNo('RET-000003-11112222-33334444');
    assert.strictEqual(rReturn.main, 'RET-000003');
    assert.strictEqual(rReturn.deviceTag, '3333');

    const rPO = RetailSystem._docNo('PO-000099-deadbeef-cafebabe');
    assert.strictEqual(rPO.main, 'PO-000099');
    assert.strictEqual(rPO.deviceTag, 'cafe');
    console.log('PASS: legacy return and PO numbers shorten the same way sale numbers do');
  }

  // ── 3. Per-till SERIES numbers (schema v34) pass through UNTOUCHED ───────
  //    ("A-000123", doc_series.allocate's own shape -- no company/device
  //    fragments to strip at all.)
  {
    const r = RetailSystem._docNo('A-000123');
    assert.strictEqual(r.main, 'A-000123', `series number must pass through untouched, got main='${r.main}'`);
    assert.strictEqual(r.deviceTag, null, 'series number must carry no device tag');
    assert.strictEqual(r.full, 'A-000123');
    console.log('PASS: a per-till series number passes through untouched (no fragments to strip)');
  }

  // ── 4. Malformed / edge inputs never throw and never guess ───────────────
  {
    const cases = [
      [null, ''],
      [undefined, ''],
      ['', ''],
      // 3 hyphen-separated parts, not 4 -- must not be mistaken for legacy.
      ['SALE-000016-a1b2c3d4', 'SALE-000016-a1b2c3d4'],
      // Right shape, but the suffixes are not lowercase hex -- must not match.
      ['SALE-000016-notahex12-alsobadxx', 'SALE-000016-notahex12-alsobadxx'],
      // A hand-typed test fixture used elsewhere in this test suite.
      ['TEST-0000', 'TEST-0000'],
    ];
    for (const [input, expectedMain] of cases) {
      const r = RetailSystem._docNo(input);
      assert.strictEqual(r.main, expectedMain,
        `malformed input ${JSON.stringify(input)}: expected main ${JSON.stringify(expectedMain)}, got ${JSON.stringify(r.main)}`);
      assert.strictEqual(r.deviceTag, null,
        `malformed input ${JSON.stringify(input)}: expected no device tag, got ${JSON.stringify(r.deviceTag)}`);
    }
    console.log('PASS: malformed/empty/unrecognised inputs pass through as-is, never guessed at');
  }

  // ── 5. _docNoHtml: the PROMINENT text is separate from the device tag ────
  //    (the receipt's own regex check, /^SALE-\d{6}$/, reads exactly the
  //    <bdi> text -- a device tag concatenated into the same text node
  //    would break that check the same way it would break this one).
  {
    const html = RetailSystem._docNoHtml('SALE-000016-a1b2c3d4-e5f6a7b8');
    const bdiMatch = /<bdi[^>]*>([^<]*)<\/bdi>/.exec(html);
    assert.ok(bdiMatch, `_docNoHtml did not render a <bdi> element. Got: ${html}`);
    assert.strictEqual(bdiMatch[1], 'SALE-000016',
      `_docNoHtml's <bdi> text must be exactly the short number with nothing appended, got '${bdiMatch[1]}'`);
    assert.ok(html.includes('title="SALE-000016-a1b2c3d4-e5f6a7b8"'),
      `_docNoHtml must carry the FULL number in a title attribute for lookup. Got: ${html}`);
    assert.ok(html.includes('e5f6') && !bdiMatch[1].includes('e5f6'),
      'the device tag must render OUTSIDE the <bdi> text node, in its own element');

    // A series number has no fragment to strip, so _bdi's own rule applies:
    // no title attribute at all when full === main (nothing extra to show).
    const seriesHtml = RetailSystem._docNoHtml('A-000123');
    assert.ok(!seriesHtml.includes('title='), `a series number needs no title (nothing was stripped). Got: ${seriesHtml}`);
    console.log('PASS: _docNoHtml keeps the short number and the device tag in separate nodes, full number in title');
  }

  console.log('\nAll retail_p0_docno_test.js checks passed.');
}

try {
  run();
} catch (err) {
  console.error('FAIL: retail_p0_docno_test.js');
  console.error(err);
  process.exitCode = 1;
}
