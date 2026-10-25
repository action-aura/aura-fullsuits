// Runs the REAL rendered Owner service worker (GET /sw.js) inside a Node vm
// with in-memory caches and a fake network, and reports what it did.
// Driven by tests/test_pwa.py -- not a standalone test.
//
// stdin:  {"swPath": "...", "origin": "https://owner.test",
//          "scenarios": [{"id": "...", "url": "/x", "mode": "navigate"|"cors"|"no-cors", "method": "GET"}]}
// stdout: {"caches": {name: [absolute urls]}, "installRequests": [{url, credentials, cache}],
//          "skipWaiting": bool, "claimed": bool,
//          "online": {id: {intercepted, body}}, "offline": {id: {intercepted, body, error}}}
//
// The fake network answers every URL with body "NETWORK:<path+query>", so a
// body proves where a response came from, and anything the worker stores is
// visible in the dumped caches afterwards.
'use strict';
const fs = require('fs');
const vm = require('vm');

const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const ORIGIN = input.origin;
const abs = (u) => new URL(typeof u === 'string' ? u : u.url, ORIGIN + '/').href;

class MemoryCache {
  constructor() { this.entries = new Map(); }
  async put(request, response) {
    const body = await response.text();
    this.entries.set(abs(request), { body, status: response.status, headers: [...response.headers] });
  }
  async match(request) {
    const hit = this.entries.get(abs(request));
    if (!hit) return undefined;
    return new Response(hit.body, { status: hit.status, headers: hit.headers });
  }
  async keys() { return [...this.entries.keys()]; }
}

class MemoryCacheStorage {
  constructor() { this.store = new Map(); }
  async open(name) {
    if (!this.store.has(name)) this.store.set(name, new MemoryCache());
    return this.store.get(name);
  }
  async keys() { return [...this.store.keys()]; }
  async delete(name) { return this.store.delete(name); }
  async match(request, options) {
    const names = options && options.cacheName ? [options.cacheName] : [...this.store.keys()];
    for (const name of names) {
      const cache = this.store.get(name);
      const hit = cache && (await cache.match(request));
      if (hit) return hit;
    }
    return undefined;
  }
}

let networkUp = true;
const installRequests = [];
let phase = 'install';
async function fakeFetch(resource, init) {
  const request = resource instanceof Request ? resource : new Request(abs(resource), init);
  if (phase === 'install') {
    installRequests.push({ url: request.url, credentials: request.credentials, cache: request.cache });
  }
  if (!networkUp) throw new TypeError('Failed to fetch (offline)');
  const url = new URL(request.url);
  const response = new Response('NETWORK:' + url.pathname + url.search, {
    status: 200,
    headers: { 'content-type': 'text/plain' },
  });
  // A real same-origin fetch is type "basic"; a constructed Response is "default".
  Object.defineProperty(response, 'type', { value: 'basic' });
  return response;
}

const caches = new MemoryCacheStorage();
const listeners = {};
const state = { skipWaiting: false, claimed: false };
const self = {
  location: new URL(ORIGIN + '/sw.js'),
  registration: { navigationPreload: { enable: async () => {} } },
  clients: { claim: async () => { state.claimed = true; } },
  skipWaiting: async () => { state.skipWaiting = true; },
  addEventListener: (type, fn) => { (listeners[type] = listeners[type] || []).push(fn); },
};
// In a browser worker a relative URL resolves against the worker's own URL;
// Node's Request needs an absolute one, so resolve the same way here.
class WorkerRequest extends Request {
  constructor(resource, init) { super(typeof resource === 'string' ? abs(resource) : resource, init); }
}
const sandbox = { self, caches, fetch: fakeFetch, Request: WorkerRequest, Response, Headers, URL, console };
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(input.swPath, 'utf8'), sandbox, { filename: 'sw.js' });

async function lifecycle(type) {
  const pending = [];
  for (const fn of listeners[type] || []) fn({ waitUntil: (p) => pending.push(p) });
  await Promise.all(pending);
}

async function dispatchFetch(scenario) {
  const request = new Request(abs(scenario.url), { method: scenario.method || 'GET' });
  // Request.mode cannot be set to "navigate" through the constructor.
  Object.defineProperty(request, 'mode', { value: scenario.mode });
  let responded = null;
  const event = {
    request,
    preloadResponse: Promise.resolve(undefined),
    respondWith: (p) => { responded = Promise.resolve(p); },
    waitUntil: () => {},
  };
  for (const fn of listeners.fetch || []) fn(event);
  if (!responded) return { intercepted: false, body: null };
  try {
    const response = await responded;
    return { intercepted: true, body: response.type === 'error' ? null : await response.text(), status: response.status };
  } catch (error) {
    return { intercepted: true, body: null, error: String(error) };
  }
}

(async () => {
  // Leftovers a real phone could have: an older version of our cache (must be
  // deleted on activate) and somebody else's cache (must be left alone).
  await (await caches.open(input.cachePrefix + 'old-version')).put(abs('/static/old.css'), new Response('OLD'));
  await (await caches.open('unrelated-cache')).put(abs('/unrelated'), new Response('UNRELATED'));

  await lifecycle('install');
  phase = 'activate';
  await lifecycle('activate');
  phase = 'fetch';

  const online = {};
  for (const s of input.scenarios) online[s.id] = await dispatchFetch(s);
  networkUp = false;
  const offline = {};
  for (const s of input.scenarios) offline[s.id] = await dispatchFetch(s);

  const dump = {};
  for (const name of await caches.keys()) dump[name] = await (await caches.open(name)).keys();
  process.stdout.write(JSON.stringify({
    caches: dump, installRequests, skipWaiting: state.skipWaiting, claimed: state.claimed, online, offline,
  }));
})().catch((error) => {
  process.stderr.write(String(error && error.stack || error));
  process.exit(1);
});
