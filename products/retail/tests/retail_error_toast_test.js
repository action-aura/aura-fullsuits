/**
 * retail_error_toast_test.js -- SUPPORT-1 item 3: the quiet "a report was saved
 * (code ABC123)" toast. Runs the real error-toast.js. Mutation-proved below.
 *
 *   node products/retail/tests/retail_error_toast_test.js
 */
'use strict';
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const SRC = fs.readFileSync(path.join(__dirname, '..', 'frontend', 'error-toast.js'), 'utf8');
const INDEX = fs.readFileSync(path.join(__dirname, '..', 'frontend', 'index.html'), 'utf8');

function mutate(src, find, replace) {
  const hits = src.split(find).length - 1;
  assert.strictEqual(hits, 1, `Mutation anchor occurs ${hits} time(s): ${JSON.stringify(find)}`);
  return src.split(find).join(replace);
}

/** Loads a copy of the source as a CommonJS module and returns its api. */
function loadApi(src) {
  const sandbox = { module: { exports: {} }, console };
  sandbox.module.exports = {};
  vm.createContext(sandbox);
  vm.runInContext(src, sandbox);
  return sandbox.module.exports;
}

function world(src, { toasts = [], hasShell = true } = {}) {
  const clock = { t: 1000000 };
  const win = {
    t: (s) => s,
    SubsystemApp: hasShell ? { showToast: (msg, type) => toasts.push({ msg, type }) } : undefined,
    fetch: null,
  };
  const api = loadApi(src);
  const handle = api.create(win, () => clock.t);
  return { win, handle, clock, toasts };
}
const resp = (code) => ({ status: code ? 500 : 204, headers: { get: (k) => (k === 'X-Aura-Report-Code' ? code : null) } });

(async () => {
  // 1. code in the header -> one toast, with that code, non-blocking info type
  let w = world(SRC);
  assert.strictEqual(w.handle.observe(resp('K7PX2M')), true);
  assert.strictEqual(w.toasts.length, 1);
  assert.strictEqual(w.toasts[0].msg, 'Something went wrong. A report was saved (code K7PX2M).');
  assert.strictEqual(w.toasts[0].type, 'info');

  // 2. NO header (capture failed / rate-limited / anonymous) -> no toast
  w = world(SRC);
  assert.strictEqual(w.handle.observe(resp(null)), false);
  assert.strictEqual(w.handle.observe({ status: 500, headers: { get: () => '' } }), false);
  assert.strictEqual(w.handle.observe(undefined), false);
  assert.strictEqual(w.toasts.length, 0);

  // 3. a header that is not a real code is never rendered
  for (const bad of ['K7PX2', 'K7PX2MM', 'k7px2m', 'K7PX0M', '<b>hi</b>', 'K7PXIM']) {
    w = world(SRC);
    assert.strictEqual(w.handle.observe(resp(bad)), false, bad);
    assert.strictEqual(w.toasts.length, 0, bad);
  }

  // 4. rate limit: once per 60 s, then again after
  w = world(SRC);
  w.handle.observe(resp('K7PX2M'));
  w.clock.t += 59000;
  assert.strictEqual(w.handle.observe(resp('AAAAAA')), false);
  assert.strictEqual(w.toasts.length, 1, 'second error inside 60 s must not toast');
  w.clock.t += 1001;
  assert.strictEqual(w.handle.observe(resp('BBBBBB')), true);
  assert.strictEqual(w.toasts.length, 2);

  // 5. no shell yet -> nothing happens and the rate limit is not burned
  w = world(SRC, { hasShell: false });
  assert.strictEqual(w.handle.observe(resp('K7PX2M')), false);
  w.win.SubsystemApp = { showToast: (m, t) => w.toasts.push({ m, t }) };
  assert.strictEqual(w.handle.observe(resp('K7PX2M')), true, 'a toast that never showed must not use up the minute');

  // 6. the fetch wrapper returns the SAME promise and never changes the outcome
  w = world(SRC);
  const reply = resp('K7PX2M');
  w.win.fetch = () => Promise.resolve(reply);
  const original = w.win.fetch;
  assert.strictEqual(w.handle.install(), true);
  assert.notStrictEqual(w.win.fetch, original);
  assert.strictEqual(await w.win.fetch('/api/sub/retail/x'), reply, 'caller must get the original response');
  await new Promise((r) => setTimeout(r, 5));
  assert.strictEqual(w.toasts.length, 1, 'a 500 with a code seen through fetch shows the toast');
  const same = Promise.resolve(reply); w.win.fetch = () => same; w.win.fetch.__auraErrorToast = false; w.handle.install();
  assert.strictEqual(w.win.fetch('/x'), same, 'the wrapper must hand back the very promise fetch returned');
  assert.strictEqual(w.handle.install(), false, 'installing twice must not double-wrap');
  w.win.fetch = () => Promise.reject(new Error('offline'));
  w.handle.install();
  await assert.rejects(w.win.fetch('/x'), /offline/, 'a network failure still reaches the caller');

  // 7. wired into the page with exactly one script line
  assert.strictEqual(INDEX.split('/static/error-toast.js').length - 1, 1);

  // ---- mutation proofs ----
  const mut = (find, repl) => world(mutate(SRC, find, repl));
  { const x = mut('if (typeof code !== \'string\' || !CODE_RE.test(code)) return false;', 'if (!code) return false;');
    assert.strictEqual(x.handle.observe(resp('<b>hi</b>')), true, 'MUTATION SURVIVED: code validation'); }
  { const x = mut('if (lastShown !== null && clock() - lastShown < MIN_GAP_MS) return false;', '');
    x.handle.observe(resp('K7PX2M')); x.handle.observe(resp('AAAAAA'));
    assert.strictEqual(x.toasts.length, 2, 'MUTATION SURVIVED: rate limit'); }
  { const x = mut('lastShown = clock();\n      app.showToast', 'app.showToast');
    x.handle.observe(resp('K7PX2M')); x.handle.observe(resp('AAAAAA'));
    assert.strictEqual(x.toasts.length, 2, 'MUTATION SURVIVED: rate limit stamp'); }
  { const x = mut('return promise;', 'return promise.then((r) => r);');
    const p = Promise.resolve(reply); x.win.fetch = () => p; x.handle.install();
    assert.notStrictEqual(x.win.fetch('/x'), p, 'MUTATION SURVIVED: same-promise guarantee'); }  console.log('retail_error_toast_test: OK (7 checks, 4 mutation proofs)');
})().catch((e) => { console.error(e); process.exit(1); });
