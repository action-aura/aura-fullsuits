/**
 * retail_crash_report_client_test.js -- the browser half of crash reports, run
 * straight against the real app-shell.js in a node vm.
 *
 * The hook must be QUIET and SAFE: it reports errors that are ours, once each,
 * at most 5 per page load, sends the section id (never the page address), and
 * can never throw into the page. Every guard is MUTATION-PROVED at the bottom:
 * the source is broken on purpose and the matching assertion must go red, else
 * the file fails with MUTATION SURVIVED.
 *
 *   node products/retail/tests/retail_crash_report_client_test.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const SHELL_SRC = fs.readFileSync(path.join(__dirname, '..', 'frontend', 'app-shell.js'), 'utf8');
const ORIGIN = 'http://127.0.0.1:5000';

function mutate(src, find, replace) {
  const hits = src.split(find).length - 1;
  assert.strictEqual(hits, 1, `Mutation anchor occurs ${hits} time(s), expected 1:\n  ${JSON.stringify(find)}`);
  return src.split(find).join(replace);
}

function stub() {
  const el = {
    innerHTML: '', textContent: '', value: '', id: '', className: '', style: {}, dataset: {}, children: [],
    classList: { toggle() {}, add() {}, remove() {} },
    appendChild() {}, addEventListener() {}, removeEventListener() {}, remove() {},
    insertBefore() {}, getAttribute() { return null; }, setAttribute() {},
    querySelector() { return null; }, querySelectorAll() { return []; },
  };
  return el;
}

/** Loads the shell with a window that records its listeners and a fetch that records its calls. */
function load(src, { fetchImpl, section = 'sales', withAddEventListener = true } = {}) {
  const listeners = {};
  const fetches = [];
  const sandbox = {
    console, t: (s) => s, setTimeout, clearTimeout, setInterval: () => 1, clearInterval() {},
    navigator: { userAgent: 'x' },
    location: { hash: '', href: ORIGIN + '/?customer=Ahmad#/sales', origin: ORIGIN },
    localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
    AuraI18n: { current: 'en' },
    document: {
      readyState: 'complete', body: stub(), head: { appendChild() {} },
      documentElement: { getAttribute: () => null, setAttribute() {}, style: { setProperty() {} } },
      getElementById: () => null, createElement: () => stub(),
      querySelector: () => null, querySelectorAll: () => [], addEventListener() {},
    },
  };
  sandbox.fetch = fetchImpl || ((url, opts) => { fetches.push({ url, opts }); return Promise.resolve({ ok: true }); });
  if (withAddEventListener) sandbox.addEventListener = (type, fn) => { (listeners[type] = listeners[type] || []).push(fn); };
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(src, sandbox, { filename: 'app-shell.js' });
  if (sandbox.SubsystemApp) sandbox.SubsystemApp.currentSection = section;
  const fire = (type, ev) => (listeners[type] || []).forEach((fn) => fn(ev));
  return { sandbox, listeners, fetches, fire };
}

const OURS = `TypeError: x is not a function\n    at renderRow (${ORIGIN}/static/subsystem-retail.js:812:19)`;
const EXTENSION = 'Error: boom\n    at inject (chrome-extension://abcdef/content.js:1:1)';

function runChecks(src, label) {
  const results = {};

  // 1. an error from our own script is reported once, to the exact endpoint,
  //    carrying the section id and NOT the page address
  {
    const h = load(src);
    h.fire('error', { message: 'x is not a function', filename: ORIGIN + '/static/subsystem-retail.js', error: { stack: OURS } });
    results.reportsOurs = h.fetches.length === 1 && h.fetches[0].url === '/api/crash-reports/client'
      && h.fetches[0].opts.method === 'POST' && h.fetches[0].opts.keepalive === true;
    const body = h.fetches[0] ? JSON.parse(h.fetches[0].opts.body) : {};
    results.sendsSectionOnly = body.screen === 'sales' && !JSON.stringify(body).includes('customer=Ahmad')
      && !JSON.stringify(body).includes('location');
  }
  // 2. an extension's error (stack from another origin) is ignored
  {
    const h = load(src);
    h.fire('error', { message: 'boom', filename: 'chrome-extension://abcdef/content.js', error: { stack: EXTENSION } });
    h.fire('unhandledrejection', { reason: { message: 'ext', stack: EXTENSION } });
    results.ignoresExtensions = h.fetches.length === 0;
  }
  // 3. the same error twice is sent once
  {
    const h = load(src);
    const ev = { message: 'same', filename: ORIGIN + '/static/a.js', error: { stack: OURS } };
    h.fire('error', ev); h.fire('error', ev);
    results.dedupes = h.fetches.length === 1;
  }
  // 4. at most 5 per page load
  {
    const h = load(src);
    for (let i = 0; i < 12; i++) {
      h.fire('error', { message: 'distinct ' + i, filename: ORIGIN + '/static/a.js', error: { stack: OURS } });
    }
    results.capsAtFive = h.fetches.length === 5;
  }
  // 5. unhandled rejections: reported when they carry our stack, silent without a stack
  {
    const h = load(src);
    h.fire('unhandledrejection', { reason: { message: 'rejected', stack: OURS } });
    h.fire('unhandledrejection', { reason: 'a bare string' });
    results.rejections = h.fetches.length === 1;
  }
  // 6. never throws into the page: a throwing fetch, a rejecting fetch, junk events
  {
    const h = load(src, { fetchImpl: () => { throw new Error('offline'); } });
    let threw = false;
    try {
      h.fire('error', { message: 'm', filename: ORIGIN + '/a.js', error: { stack: OURS } });
      h.fire('error', null);
      h.fire('unhandledrejection', undefined);
    } catch (e) { threw = true; }
    const r = load(src, { fetchImpl: () => Promise.reject(new Error('offline')) });
    r.fire('error', { message: 'm2', filename: ORIGIN + '/a.js', error: { stack: OURS } });
    results.neverThrows = !threw;
  }
  // 7. a window without addEventListener (the other vm-based tests) still loads
  {
    let ok = true;
    try { load(src, { withAddEventListener: false }); } catch (e) { ok = false; }
    results.loadsWithoutWindowEvents = ok;
  }
  // 8. both listeners are registered
  {
    const h = load(src);
    results.registersBoth = (h.listeners.error || []).length === 1 && (h.listeners.unhandledrejection || []).length === 1;
  }
  return results;
}

const REAL = runChecks(SHELL_SRC, 'real');
for (const [name, ok] of Object.entries(REAL)) assert.strictEqual(ok, true, `${name} failed against the real source`);
console.log(`ok - ${Object.keys(REAL).length} browser-hook checks pass on the real source`);

// ── mutation proofs: break the guard, the matching check must go red ─────────
const MUTATIONS = [
  ['ignoresExtensions', "if (!ours(stack, filename)) return;", ''],
  ['dedupes', "if (seen.has(key)) return;", ''],
  ['capsAtFive', "if (sentCount >= MAX_PER_PAGE) return;", ''],
  ['sendsSectionOnly', "screen: String(section)", "screen: String(window.location.href)"],
  // Both listeners must be registered: drop the rejection one and "rejections" goes red.
  ['rejections', "window.addEventListener('unhandledrejection',", "window.addEventListener('unhandledrejection-disabled',"],
];
for (const [check, find, replace] of MUTATIONS) {
  const mutated = runChecks(mutate(SHELL_SRC, find, replace), 'mutant');
  assert.strictEqual(mutated[check], false, `MUTATION SURVIVED: breaking "${find}" did not fail the "${check}" check`);
  console.log(`ok - mutation red: ${check}`);
}
console.log('all crash-report client checks passed');
