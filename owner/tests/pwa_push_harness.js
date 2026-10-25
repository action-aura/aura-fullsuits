// Runs the REAL rendered Owner service worker (GET /sw.js) in a Node vm and
// fires its push / notificationclick listeners. Driven by
// tests/test_step1_push.py -- not a standalone test.
//
// stdin:  {"swPath": "...", "origin": "https://owner.test",
//          "pushes": [{"id": "...", "data": <string|null>}],
//          "clicks": [{"id": "...", "url": "...", "windows": ["https://owner.test/x", ...]}]}
// stdout: {"notifications": {id: {title, options}}, "pushErrors": {id: "..."},
//          "clicks": {id: {navigated: [...], focused: [...], opened: [...], closed: bool}},
//          "cacheNamesBefore": [...], "cacheNamesAfter": [...]}
//
// `data` is the raw push payload text (as the browser hands it over after
// decrypting): null = a push with no data at all.
'use strict';
const fs = require('fs');
const vm = require('vm');

const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const ORIGIN = input.origin;
const listeners = {};
const cacheNames = new Set(['aura-owner-static-preexisting']);
let shown = [];
let clickLog = null;
let currentWindows = [];

const caches = {
  open: async (name) => { cacheNames.add(name); return { put: async () => {}, match: async () => undefined, keys: async () => [] }; },
  keys: async () => [...cacheNames],
  delete: async (name) => cacheNames.delete(name),
  match: async () => undefined,
};

function makeWindow(url) {
  return {
    url,
    navigate: async (target) => { clickLog.navigated.push(target); return makeFocusable(new URL(target, url).href); },
    focus: async () => { clickLog.focused.push(url); },
  };
}
function makeFocusable(url) {
  return { url, focus: async () => { clickLog.focused.push(url); } };
}

const self = {
  location: new URL(ORIGIN + '/sw.js'),
  registration: {
    navigationPreload: { enable: async () => {} },
    showNotification: async (title, options) => { shown.push({ title, options }); },
  },
  clients: {
    claim: async () => {},
    matchAll: async () => currentWindows.map(makeWindow),
    openWindow: async (url) => { clickLog.opened.push(url); return makeFocusable(url); },
  },
  skipWaiting: async () => {},
  addEventListener: (type, fn) => { (listeners[type] = listeners[type] || []).push(fn); },
};
const sandbox = { self, caches, fetch: async () => { throw new Error('no network in this harness'); },
  Request, Response, Headers, URL, console };
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(input.swPath, 'utf8'), sandbox, { filename: 'sw.js' });

async function fire(type, event) {
  const pending = [];
  event.waitUntil = (p) => pending.push(Promise.resolve(p));
  for (const fn of listeners[type] || []) fn(event);
  await Promise.all(pending);
}

(async () => {
  const cacheNamesBefore = [...cacheNames].sort();
  const notifications = {};
  const pushErrors = {};
  for (const p of input.pushes) {
    shown = [];
    const data = p.data === null ? null : {
      json: () => JSON.parse(p.data),
      text: () => p.data,
    };
    try {
      await fire('push', { data });
    } catch (error) {
      pushErrors[p.id] = String(error);
    }
    notifications[p.id] = shown.length === 1 ? shown[0] : { count: shown.length };
  }
  const clicks = {};
  for (const c of input.clicks) {
    clickLog = { navigated: [], focused: [], opened: [], closed: false };
    currentWindows = c.windows;
    const notification = { data: { url: c.url }, close: () => { clickLog.closed = true; } };
    await fire('notificationclick', { notification });
    clicks[c.id] = clickLog;
  }
  process.stdout.write(JSON.stringify({
    notifications, pushErrors, clicks, cacheNamesBefore, cacheNamesAfter: [...cacheNames].sort(),
  }));
})().catch((error) => {
  process.stderr.write(String(error && error.stack || error));
  process.exit(1);
});
