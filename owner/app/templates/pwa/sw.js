/* Aura Owner -- service worker (rendered by app/pwa/routes.py, served at /sw.js).
 *
 * SAFETY POLICY -- read app/pwa/routes.py's docstring before changing this.
 * The ONLY thing ever written to the cache is PWA_CONFIG.precache (public
 * /static/ files + the offline page), at install. There is no runtime
 * caching anywhere in this file, on purpose: every Owner page and every
 * JSON route carries live money/customer/licence data behind a login, and a
 * copy left on the phone would be stale on screen and readable after logout.
 *
 *   navigation (a page)        -> network every time; offline page ONLY if
 *                                  the network itself fails. Never stored.
 *   GET of a precached file    -> from the cache (the hash-versioned copy),
 *                                  network if somehow missing. Never stored.
 *   anything else              -> not intercepted at all (API, JSON,
 *                                  downloads, POST, other origins).
 *
 * tests/test_pwa.py runs this exact rendered file (tests/pwa_sw_harness.js)
 * against every route the app has and fails if anything else gets cached.
 */
"use strict";

const PWA_CONFIG = {{ pwa_config|tojson }};
const CACHE_NAME = PWA_CONFIG.cachePrefix + PWA_CONFIG.version;
const PRECACHED_PATHS = new Set(PWA_CONFIG.precache);

// ---------------------------------------------------------------- install
// credentials "omit": the precache requests carry no cookie, so the server
// cannot personalise anything that ends up in the cache even by mistake.
// A redirected or non-OK answer fails the whole install (the previous worker
// stays in charge) instead of caching, say, a login page under a static URL.
async function precache() {
  const cache = await caches.open(CACHE_NAME);
  const responses = await Promise.all(PWA_CONFIG.precache.map(async (path) => {
    const response = await fetch(new Request(path, { cache: "reload", credentials: "omit" }));
    if (!response.ok || response.redirected || response.type !== "basic") {
      throw new Error("Aura Owner: refusing to precache " + path + " (" + response.status + ")");
    }
    return [path, response];
  }));
  await Promise.all(responses.map(([path, response]) => cache.put(path, response)));
}

self.addEventListener("install", (event) => {
  event.waitUntil(precache().then(() => self.skipWaiting()));
});

// --------------------------------------------------------------- activate
// Deletes older versions of OUR cache only (same prefix, different name).
async function activate() {
  const names = await caches.keys();
  await Promise.all(
    names
      .filter((name) => name.startsWith(PWA_CONFIG.cachePrefix) && name !== CACHE_NAME)
      .map((name) => caches.delete(name))
  );
  if (self.registration && self.registration.navigationPreload) {
    try { await self.registration.navigationPreload.enable(); } catch (error) { /* optional speed-up only */ }
  }
  await self.clients.claim();
}

self.addEventListener("activate", (event) => {
  event.waitUntil(activate());
});

// ------------------------------------------------------------------ fetch
function routeFor(request) {
  if (request.method !== "GET") return "passthrough";
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return "passthrough";
  if (request.mode === "navigate") return "navigate";
  if (url.search === "" && url.pathname !== PWA_CONFIG.offlineUrl && PRECACHED_PATHS.has(url.pathname)) {
    return "precached";
  }
  return "passthrough";
}

async function networkThenOfflinePage(event) {
  try {
    const preloaded = await event.preloadResponse;
    if (preloaded) return preloaded;
    return await fetch(event.request);
  } catch (error) {
    // Only a network failure lands here; an HTTP error page from the server
    // is a real answer and is shown as-is.
    const cache = await caches.open(CACHE_NAME);
    const offlinePage = await cache.match(PWA_CONFIG.offlineUrl);
    return offlinePage || Response.error();
  }
}

async function cachedThenNetwork(request) {
  const cache = await caches.open(CACHE_NAME);
  const cached = await cache.match(new URL(request.url).pathname, { ignoreVary: true });
  return cached || fetch(request);
}

self.addEventListener("fetch", (event) => {
  const route = routeFor(event.request);
  if (route === "navigate") {
    event.respondWith(networkThenOfflinePage(event));
  } else if (route === "precached") {
    event.respondWith(cachedThenNetwork(event.request));
  }
  // "passthrough": no respondWith -- the browser handles it exactly as if
  // there were no service worker, and nothing is stored.
});

// ------------------------------------------------------------------- push
// Manager push notifications (Step 1, app/push/). The server decides every
// word: title/body arrive already translated, and by the owner's rule they
// never carry money or a customer (app/push/worker.py::build_payload) --
// this worker only displays them. Nothing here touches the cache.
//
// One fixed tag (renotify: true): a newer message REPLACES the one on screen
// and still buzzes, so a burst of sales is one notification that says how
// many are waiting, not a pile. "userVisibleOnly" is a promise to the browser:
// every push shows a notification, even one with no readable data.
const PUSH_CONFIG = {{ push_config|tojson }};

function samePathOrFallback(raw) {
  // Only a non-empty string can name a page: new URL(undefined, origin) would
  // otherwise "resolve" to /undefined (caught by tests/pwa_push_harness.js).
  if (typeof raw !== "string" || raw === "") return PUSH_CONFIG.fallbackUrl;
  try {
    const url = new URL(raw, self.location.origin);
    if (url.origin === self.location.origin) return url.pathname + url.search + url.hash;
  } catch (error) { /* not a URL: use the fallback */ }
  return PUSH_CONFIG.fallbackUrl;
}

self.addEventListener("push", (event) => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch (error) { data = {}; }
  const text = (value) => (typeof value === "string" ? value : "");
  const title = text(data.title) || PUSH_CONFIG.fallbackTitle;
  event.waitUntil(self.registration.showNotification(title, {
    body: text(data.body),
    tag: text(data.tag) || PUSH_CONFIG.fallbackTag,
    renotify: true,
    lang: text(data.lang),
    dir: data.dir === "rtl" ? "rtl" : (data.dir === "ltr" ? "ltr" : "auto"),
    icon: PUSH_CONFIG.icon,
    badge: PUSH_CONFIG.badge,
    data: { url: samePathOrFallback(data.url) },
  }));
});

// Tapping opens the sales list (at the sale, when the message was about one):
// an Aura Owner window that is already open is reused, otherwise a new one.
// The page itself says "already confirmed" when another manager was first.
self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const target = samePathOrFallback(event.notification.data && event.notification.data.url);
  event.waitUntil((async () => {
    const windows = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    for (const client of windows) {
      if (new URL(client.url).origin !== self.location.origin) continue;
      if ("navigate" in client) {
        try {
          const navigated = await client.navigate(target);
          if (navigated) { await navigated.focus(); return; }
        } catch (error) { /* an uncontrolled window cannot be navigated: open one below */ }
      }
    }
    await self.clients.openWindow(target);
  })());
});
