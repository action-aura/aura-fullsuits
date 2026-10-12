/**
 * Shared loader for the UI-2 node tests (retail_ui2_*_test.js). Not itself a
 * test (the leading underscore keeps products/run_all_tests.py's *_test.js glob
 * from running it).
 *
 * loadRetail() evaluates the REAL subsystem-retail.js (and, on request,
 * cash-drawer.js) in a vm sandbox with a small fake DOM:
 *
 *   - getElementById(id) answers ONLY for ids that exist in the markup a screen
 *     rendered (the overlays appended to document.body, plus any id registered
 *     with seed()). A real DOM returns null for an id that is not on the page;
 *     an auto-creating stub would answer for every id and make "this element is
 *     not in the DOM" assertions pass for the wrong reason.
 *   - allText() is everything a person could read on screen: every overlay's
 *     markup plus whatever the code wrote into the elements afterwards.
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const FRONTEND = path.join(__dirname, '..', 'frontend');

function makeEl(id) {
  const el = {
    id, value: '', textContent: '', hidden: false, style: {}, dataset: {}, attrs: {}, children: [], className: '',
    _html: '', _cls: new Set(), _listeners: {}, disabled: false, checked: false,
    classList: {
      add(c) { el._cls.add(c); }, remove(c) { el._cls.delete(c); },
      toggle(c, f) { if (f === undefined ? !el._cls.has(c) : f) el._cls.add(c); else el._cls.delete(c); },
      contains(c) { return el._cls.has(c); },
    },
    setAttribute(k, v) { el.attrs[k] = v; }, getAttribute(k) { return el.attrs[k] == null ? null : el.attrs[k]; },
    appendChild(c) { el.children.push(c); return c; }, focus() {}, select() {}, remove() {},
    querySelector() { return null; }, querySelectorAll() { return []; }, closest() { return null; },
    addEventListener(type, fn) { (el._listeners[type] = el._listeners[type] || []).push(fn); },
    removeEventListener() {},
    scrollWidth: 0, clientWidth: 0, scrollLeft: 0,
  };
  Object.defineProperty(el, 'innerHTML', { get() { return el._html; }, set(v) { el._html = v; el.children = []; } });
  return el;
}

function loadRetail(opts) {
  opts = opts || {};
  const live = Object.create(null);      // id -> element, created lazily FROM MARKUP
  const overlays = [];
  const markup = () => overlays.map((o) => o.innerHTML).join('\n') + '\n' + Object.keys(seeded).map((k) => seeded[k] ? '' : '').join('');
  const seeded = Object.create(null);
  const toasts = [];

  const inMarkup = (id) => {
    if (seeded[id]) return true;
    const re = new RegExp('\\bid="' + id.replace(/[-/\\^$*+?.()|[\]{}]/g, '\\$&') + '"');
    return overlays.some((o) => re.test(o.innerHTML)) || Object.keys(live).some((k) => k !== id && live[k]._html && re.test(live[k]._html));
  };
  const document = {
    activeElement: null,
    getElementById(id) {
      if (id === 'ret-styles') return makeEl('ret-styles');
      if (live[id] && overlays.includes(live[id])) return live[id];          // an overlay is addressable by its own id
      if (live[id] && (seeded[id] || inMarkup(id))) return live[id];
      if (seeded[id] || inMarkup(id)) return (live[id] = makeEl(id));
      return null;
    },
    createElement(tag) { const e = makeEl('::' + tag); e.tagName = String(tag).toUpperCase(); return e; },
    querySelector() { return null; }, querySelectorAll() { return []; },
    head: { appendChild() {} },
    body: {
      appendChild(o) { overlays.push(o); if (o.id) live[o.id] = o; return o; }, classList: { add() {}, remove() {} },
    },
    documentElement: { getAttribute: () => 'light', style: { setProperty() {} } }, addEventListener() {},
  };
  const SubsystemApp = {
    active: 'retail', role: opts.role === undefined ? 'admin' : opts.role,
    capabilities: opts.capabilities === undefined ? null : opts.capabilities,
    showToast(msg, kind) { toasts.push({ msg, kind }); }, _navigate() {},
    hasCapability(code) { return !Array.isArray(this.capabilities) ? true : this.capabilities.includes(code); },
  };
  const sandbox = {
    console, t: (s) => s,
    fetch: () => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ status: 'success', data: {} }) }),
    getComputedStyle: () => ({ getPropertyValue: () => '' }),
    navigator: { userAgent: 'Mozilla/5.0 (test)' },
    localStorage: { getItem: () => null, setItem() {} },
    setTimeout: () => 0, clearTimeout() {}, setInterval: () => 0, clearInterval() {}, URLSearchParams, document, SubsystemApp,
    Option: function Option(text, value) { this.text = text; this.value = value; },
  };
  sandbox.window = sandbox;
  sandbox.Chart = function ChartStub() { return { destroy() {} }; };
  vm.createContext(sandbox);
  const run = (file) => vm.runInContext(fs.readFileSync(path.join(FRONTEND, file), 'utf8'), sandbox, { filename: path.join(FRONTEND, file) });
  if (opts.code128 !== false) run('code128.js');
  run('subsystem-retail.js');
  if (opts.drawer) run('cash-drawer.js');
  const R = sandbox.RetailSystem;
  R._currencyDecimals = opts.dp == null ? 3 : opts.dp; R._currencySymbol = 'JD';
  R._refocusScan = () => {};
  R._wireModalA11y = () => () => {};
  R._closeModalOverlay = (x) => { const i = overlays.indexOf(x && x.id ? x : live['ret-return-modal']); if (i >= 0) overlays.splice(i, 1); };
  return {
    R, sandbox, toasts, document, SubsystemApp, CashDrawer: sandbox.CashDrawer,
    overlays, live,
    seed(id, extra) { seeded[id] = true; live[id] = Object.assign(makeEl(id), extra || {}); return live[id]; },
    el(id) { return document.getElementById(id); },
    allText() {
      return overlays.map((o) => o.innerHTML).join('\n') + '\n' +
        Object.keys(live).map((k) => (live[k]._html || '') + ' ' + (live[k].textContent || '') + ' ' + (live[k].value || '')).join('\n');
    },
    closeAll() { overlays.length = 0; Object.keys(live).forEach((k) => { if (!seeded[k]) delete live[k]; }); },
  };
}

module.exports = { loadRetail, makeEl, assert };
