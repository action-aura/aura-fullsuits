// Runs the REAL owner/app/static/js/pwa.js against a minimal fake browser,
// once per case, and reports what the install hint ended up doing.
// Driven by tests/test_pwa.py -- not a standalone test.
//
// stdin:  {"jsPath": "...", "cases": [{id, ua, platform, maxTouchPoints, standaloneMedia,
//          iosStandalone, storage: "ok"|"throws", dismissedBefore, fireBeforeInstall,
//          click: null|"dismiss"|"install"}]}
// stdout: {id: {hintHidden, androidHidden, iosHidden, installHidden, prevented, prompted,
//          stored, registered, error}}
'use strict';
const fs = require('fs');
const vm = require('vm');

const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const source = fs.readFileSync(input.jsPath, 'utf8');
const DISMISS_KEY = 'aura-owner-install-hint-dismissed';

class FakeElement {
  constructor(attrs) { this.attrs = attrs; this.hidden = true; this.listeners = {}; this.children = []; }
  getAttribute(name) { return Object.prototype.hasOwnProperty.call(this.attrs, name) ? this.attrs[name] : null; }
  addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); }
  closest(selector) { return selector === '[data-install-action]' && this.attrs['data-install-action'] ? this : null; }
  querySelectorAll(selector) {
    if (selector === '[data-install-variant]') return this.children.filter((c) => c.attrs['data-install-variant']);
    throw new Error('unexpected selector ' + selector);
  }
  querySelector(selector) {
    const m = /^\[data-install-action='(\w+)'\]$/.exec(selector);
    if (!m) throw new Error('unexpected selector ' + selector);
    return this.children.find((c) => c.attrs['data-install-action'] === m[1]) || null;
  }
}

async function runCase(c) {
  const hint = new FakeElement({ id: 'aura-install-hint' });
  const android = new FakeElement({ 'data-install-variant': 'android' });
  const ios = new FakeElement({ 'data-install-variant': 'ios' });
  const install = new FakeElement({ 'data-install-action': 'install' });
  const dismiss = new FakeElement({ 'data-install-action': 'dismiss' });
  hint.children = [android, ios, install, dismiss];

  const store = new Map();
  if (c.dismissedBefore) store.set(DISMISS_KEY, '1');
  const localStorage = c.storage === 'throws'
    ? { getItem() { throw new Error('SecurityError'); }, setItem() { throw new Error('SecurityError'); } }
    : { getItem: (k) => (store.has(k) ? store.get(k) : null), setItem: (k, v) => store.set(k, String(v)) };

  const registered = [];
  const windowListeners = {};
  const documentListeners = {};
  const document = {
    readyState: 'loading',
    currentScript: new FakeElement({ 'data-sw-url': '/sw.js', 'data-sw-scope': '/' }),
    getElementById: (id) => (id === 'aura-install-hint' ? hint : null),
    addEventListener: (type, fn) => { (documentListeners[type] = documentListeners[type] || []).push(fn); },
  };
  const window = {
    matchMedia: (query) => ({ matches: Boolean(c.standaloneMedia) && query === '(display-mode: standalone)' }),
    navigator: {
      userAgent: c.ua || 'Mozilla/5.0 (Linux; Android 14) Chrome/130.0 Mobile',
      platform: c.platform || 'Linux armv8l',
      maxTouchPoints: c.maxTouchPoints || 5,
      serviceWorker: { register: (url, opts) => { registered.push({ url, scope: opts && opts.scope }); return Promise.resolve(); } },
    },
    localStorage,
    addEventListener: (type, fn) => { (windowListeners[type] = windowListeners[type] || []).push(fn); },
  };
  if (c.iosStandalone) window.navigator.standalone = true;

  const result = { prevented: false, prompted: false };
  try {
    vm.runInNewContext(source, { window, document });
    document.readyState = 'interactive';
    (documentListeners.DOMContentLoaded || []).forEach((fn) => fn());
    (windowListeners.load || []).forEach((fn) => fn());
    if (c.fireBeforeInstall) {
      const event = {
        preventDefault: () => { result.prevented = true; },
        prompt: () => { result.prompted = true; },
        userChoice: Promise.resolve({ outcome: 'accepted' }),
      };
      (windowListeners.beforeinstallprompt || []).forEach((fn) => fn(event));
    }
    if (c.click) {
      const target = c.click === 'dismiss' ? dismiss : install;
      (hint.listeners.click || []).forEach((fn) => fn({ target }));
    }
    await new Promise((resolve) => setImmediate(resolve));
  } catch (error) {
    result.error = String(error && error.stack || error);
  }
  return Object.assign(result, {
    hintHidden: hint.hidden,
    androidHidden: android.hidden,
    iosHidden: ios.hidden,
    installHidden: install.hidden,
    stored: store.has(DISMISS_KEY) ? store.get(DISMISS_KEY) : null,
    registered,
  });
}

(async () => {
  const out = {};
  for (const c of input.cases) out[c.id] = await runCase(c);
  process.stdout.write(JSON.stringify(out));
})().catch((error) => {
  process.stderr.write(String(error && error.stack || error));
  process.exit(1);
});
