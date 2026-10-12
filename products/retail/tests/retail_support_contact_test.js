/**
 * retail_support_contact_test.js -- SUPPORT-1 item 1: the licence page offers a
 * real way to reach Action Aura, and it is the SAME number the Help dialog uses.
 *
 * Before: licensing.html said "Contact Action Aura support" and gave no contact.
 * The guards below are MUTATION-PROVED at the bottom: break the thing on purpose
 * and the matching assertion must go red, else the file fails MUTATION SURVIVED.
 *
 *   node products/retail/tests/retail_support_contact_test.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const FRONT = path.join(__dirname, '..', 'frontend');
const HTML = fs.readFileSync(path.join(FRONT, 'licensing.html'), 'utf8');
const LIC_JS = fs.readFileSync(path.join(FRONT, 'licensing.js'), 'utf8');
const HELP_JS = fs.readFileSync(path.join(FRONT, 'help.js'), 'utf8');

function mutate(src, find, replace) {
  const hits = src.split(find).length - 1;
  assert.strictEqual(hits, 1, `Mutation anchor occurs ${hits} time(s), expected 1:\n  ${JSON.stringify(find)}`);
  return src.split(find).join(replace);
}

/** Runs help.js then licensing.js in one sandbox; resolves with the whatsapp <a>'s href after the lookups. */
async function runPage(html, licJs, helpJs) {
  const link = { attrs: {}, setAttribute(k, v) { this.attrs[k] = v; }, getAttribute(k) { return this.attrs[k]; } };
  const stubEl = () => ({
    appendChild() {}, setAttribute() {}, addEventListener() {}, classList: { add() {}, remove() {}, toggle() {} },
    style: {}, textContent: '', innerHTML: '', children: [], remove() {},
  });
  const sandbox = {
    console, setTimeout, clearTimeout, setInterval: () => 1, clearInterval() {},
    location: { search: '', href: 'http://127.0.0.1:5000/licensing.html' },
    localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
    document: {
      getElementById: (id) => (id === 'support-whatsapp' ? link : stubEl()),
      createElement: stubEl, body: stubEl(), addEventListener() {}, querySelector: () => null, querySelectorAll: () => [],
    },
    fetch: (url) => Promise.resolve({
      json: async () => (String(url).includes('/api/version')
        ? { app_version: '1.2.3-test' }
        : { data: { installation_id: 'INST-ABC-123' } }),
    }),
  };
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(helpJs, sandbox);
  try { vm.runInContext(licJs, sandbox); } catch (e) { /* the page's own boot may need more DOM; only our wiring is under test */ }
  await new Promise((r) => setTimeout(r, 30));
  return { link, sandbox };
}

(async () => {
  // 1. static contact: phone, WhatsApp, e-mail are all on the page
  assert.ok(HTML.includes('tel:+962780069485'), 'licence page has no tel: link');
  assert.ok(HTML.includes('+962 78 006 9485'), 'licence page does not show the number');
  assert.ok(HTML.includes('mailto:info@actionaura.me'), 'licence page has no support e-mail');
  assert.ok(/id="support-whatsapp"\s+href="https:\/\/wa\.me\/962780069485"/.test(HTML), 'no static WhatsApp fallback link');
  assert.ok(HTML.indexOf('src="help.js"') > -1 && HTML.indexOf('src="help.js"') < HTML.indexOf('src="licensing.js"'),
    'help.js must load before licensing.js');

  // 2. the page's number IS help.js's number (cannot drift)
  const helpNumber = /ACTION_AURA_SUPPORT_WHATSAPP:\s*'(\d+)'/.exec(HELP_JS)[1];
  assert.strictEqual(helpNumber, '962780069485');
  assert.ok(HTML.includes('wa.me/' + helpNumber) && HTML.includes('tel:+' + helpNumber));

  // 3. the link is upgraded with installation id + version (the same prefill as Help)
  const ok = await runPage(HTML, LIC_JS, HELP_JS);
  const href = decodeURIComponent(ok.link.attrs.href || '');
  assert.ok(href.startsWith('https://wa.me/962780069485?text='), 'link not upgraded: ' + href);
  assert.ok(href.includes('Installation: INST-ABC-123'), 'installation id missing from the prefill');
  assert.ok(href.includes('Version: 1.2.3-test'), 'version missing from the prefill');

  // 4. deny direction: with no help.js the page must still work and keep the static link
  const noHelp = await runPage(HTML, LIC_JS, '/* help.js missing */');
  assert.strictEqual(noHelp.link.attrs.href, undefined, 'without help.js nothing may rewrite the static link');

  // ---- mutation proofs ----
 // (a) wiring removed -> the link is not upgraded
  const mutWiring = mutate(LIC_JS, "if (url) link.setAttribute('href', url);", '/* mutated */');
  const m1 = await runPage(HTML, mutWiring, HELP_JS);
  assert.strictEqual(m1.link.attrs.href, undefined, 'MUTATION SURVIVED: wiring removed but link still upgraded');
  // (b) wrong prefill -> version dropped from help.js's message is caught by (3)
  const mutHelp = mutate(HELP_JS, "if (c.appVersion) lines.push('Version: ' + c.appVersion);", '');
  const m2 = await runPage(HTML, LIC_JS, mutHelp);
  assert.ok(!decodeURIComponent(m2.link.attrs.href || '').includes('Version: 1.2.3-test'), 'MUTATION SURVIVED: version still present');
  // (c) a page whose number drifted from help.js would fail check 2
  const driftHtml = HTML.split('962780069485').join('962700000000');
  assert.ok(!(driftHtml.includes('wa.me/' + helpNumber)), 'MUTATION SURVIVED: drift undetected');
  console.log('retail_support_contact_test: OK (5 checks, 3 mutation proofs)');
})().catch((e) => { console.error(e); process.exit(1); });
