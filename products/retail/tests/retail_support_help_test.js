/**
 * retail_support_help_test.js -- SUPPORT-1 items 2 and 4 in the Help dialog:
 * "Send diagnostics to support" and "Let support see my screen" (Quick Assist).
 * Runs the real help.js in a node vm. Mutation-proved at the bottom.
 *
 *   node products/retail/tests/retail_support_help_test.js
 */
'use strict';
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const HELP = fs.readFileSync(path.join(__dirname, '..', 'frontend', 'help.js'), 'utf8');

function mutate(src, find, replace) {
  const hits = src.split(find).length - 1;
  assert.strictEqual(hits, 1, `Mutation anchor occurs ${hits} time(s): ${JSON.stringify(find)}`);
  return src.split(find).join(replace);
}

function load(src, { platform = 'Win32', ua = 'Mozilla/5.0 (Windows NT 10.0)', role = 'admin', fetchImpl } = {}) {
  const calls = [];
  const anchors = [];
  const el = () => ({
    innerHTML: '', textContent: '', style: {}, children: [], hidden: false, disabled: false,
    setAttribute() {}, appendChild() {}, addEventListener() {}, remove() {}, focus() {}, click() {},
    querySelector() { return el(); }, querySelectorAll() { return []; },
  });
  const overlay = el();
  const sandbox = {
    console, setTimeout, clearTimeout,
    navigator: { platform, userAgent: ua },
    t: (s) => s,
    SubsystemApp: { role, currentSection: 'sales' },
    document: {
      getElementById: (id) => (id === 'aura-help-overlay' ? null : null),
      createElement: (tag) => { const e = el(); if (tag === 'a') anchors.push(e); if (tag === 'div') Object.assign(overlay, e, { _o: true }); return tag === 'div' ? overlay : e; },
      body: el(), querySelector: () => null,
    },
    fetch: (url, opts) => { calls.push({ url, opts }); return (fetchImpl || (() => Promise.resolve({ ok: false })))(url, opts); },
  };
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(src + '\nthis.AuraHelp = AuraHelp;', sandbox);
  return { help: sandbox.AuraHelp, calls, anchors, overlay, sandbox };
}

const plain = (o) => JSON.parse(JSON.stringify(o));
const json = (body, ok = true) => Promise.resolve({ ok, json: async () => body });
const EXPORT = { status: 'success', data: { app_version: '1', row_counts: { sales: 3 }, log_tail: ['Ahmad 0791234567'] } };
const good = (url) => (String(url).includes('diagnostics/export') ? json(EXPORT)
  : json({ status: 'success', data: { reference_code: 'K7PX2M', sending_enabled: true } }));

(async () => {
  // ---- item 2: sending ----
  let m = load(HELP, { fetchImpl: good });
  const r = await m.help.sendDiagnostics();
  assert.deepStrictEqual(plain(r), { ok: true, code: 'K7PX2M', sendingEnabled: true });
  const post = m.calls.find((c) => c.url === '/api/crash-reports/diagnostics');
  assert.ok(post && post.opts.method === 'POST');
  assert.ok(!post.opts.body.includes('Ahmad') && !post.opts.body.includes('log_tail'), 'the log tail must stay on this computer');
  assert.ok(post.opts.body.includes('row_counts'), 'positive control: the facts are sent');

  m = load(HELP, { fetchImpl: () => json(EXPORT, false) });
  assert.deepStrictEqual(plain(await m.help.sendDiagnostics()), { ok: false, reason: 'export' });
  assert.strictEqual(m.calls.filter((c) => c.url.includes('crash-reports')).length, 0, 'no export -> nothing may be posted');
  m = load(HELP, { fetchImpl: (u) => (String(u).includes('export') ? json(EXPORT) : json({}, false)) });
  assert.deepStrictEqual(plain(await m.help.sendDiagnostics()), { ok: false, reason: 'send' });
  m = load(HELP, { fetchImpl: () => Promise.reject(new Error('offline')) });
  assert.strictEqual((await m.help.sendDiagnostics()).ok, false, 'a network error resolves, never rejects');

  // the WhatsApp message carries the code
  m = load(HELP);
  assert.ok(decodeURIComponent(m.help.supportUrl(null, 'K7PX2M')).includes('Diagnostics code: K7PX2M'));
  assert.ok(!decodeURIComponent(m.help.supportUrl(null)).includes('Diagnostics code'));

  // ---- item 4: Quick Assist only on Windows desktop ----
  for (const [platform, ua, expected] of [
    ['Win32', 'Mozilla/5.0 (Windows NT 10.0)', true],
    ['', 'Mozilla/5.0 (Windows NT 10.0; Win64)', true],
    ['Linux armv8l', 'Mozilla/5.0 (Linux; Android 12; Sunmi V2s)', false],
    ['Linux x86_64', 'Mozilla/5.0 (X11; Linux x86_64)', false],
    ['MacIntel', 'Mozilla/5.0 (Macintosh)', false],
    ['', 'Mozilla/5.0 (Linux; Android 11; Windows-like)', false],
  ]) {
    const x = load(HELP, { platform, ua });
    assert.strictEqual(x.help.isWindowsDesktop(), expected, `${platform} / ${ua}`);
    assert.strictEqual(x.help.openQuickAssist(), expected);
    assert.strictEqual(x.anchors.length, expected ? 1 : 0, 'non-Windows must not even create the launcher');
    if (expected) assert.strictEqual(x.anchors[0].href, 'ms-quick-assist:');
  }

  // ---- the dialog offers the right buttons to the right people ----
  const dialog = (opts) => { const x = load(HELP, opts); x.help.open(); return x.overlay.innerHTML; };
  const admin = dialog({});
  assert.ok(admin.includes('data-help="diagnostics"') && admin.includes('data-help="quick-assist"'));
  assert.ok(admin.includes('+962 78 006 9485'), 'the number is shown next to the remote-help entry');
  assert.ok(!dialog({ role: 'cashier' }).includes('data-help="diagnostics"'), 'a cashier would only get a 403');
  assert.ok(dialog({ role: 'cashier' }).includes('data-help="quick-assist"'));
  const phone = dialog({ platform: 'Linux armv8l', ua: 'Android' });
  assert.ok(!phone.includes('data-help="quick-assist"'), 'hidden off Windows');

  // ---- mutation proofs: each broken copy must make the matching check above fail ----
  { // log tail not stripped -> it reaches the POST body
    const x = load(mutate(HELP, 'delete safe.log_tail;', ''), { fetchImpl: good });
    await x.help.sendDiagnostics();
    assert.ok(x.calls.find((c) => c.url.includes('crash-reports')).opts.body.includes('Ahmad'), 'MUTATION SURVIVED: log tail stripping');
  }
  { // android guard removed -> an Android user agent is offered Quick Assist
    const x = load(mutate(HELP, 'if (/android/i.test(ua) || /android/i.test(platform)) return false;', ''),
      { platform: '', ua: 'Mozilla/5.0 (Linux; Android 11; Windows-like)' });
    assert.strictEqual(x.help.isWindowsDesktop(), true, 'MUTATION SURVIVED: android guard');
  }
  { // export failure no longer stops the post
    const x = load(mutate(HELP, "if (!r.ok) return { ok: false, reason: 'export' };", ''), { fetchImpl: () => json(EXPORT, false) });
    await x.help.sendDiagnostics();
    assert.ok(x.calls.some((c) => c.url.includes('crash-reports')), 'MUTATION SURVIVED: export failure guard');
  }
  { // Quick Assist always shown
    const x = load(mutate(HELP, 'const showQuickAssist = this.isWindowsDesktop();', 'const showQuickAssist = true;'), { platform: 'Linux armv8l', ua: 'Android' });
    x.help.open();
    assert.ok(x.overlay.innerHTML.includes('data-help="quick-assist"'), 'MUTATION SURVIVED: quick-assist visibility');
  }
  console.log('retail_support_help_test: OK (4 mutation proofs)');
})().catch((e) => { console.error(e); process.exit(1); });