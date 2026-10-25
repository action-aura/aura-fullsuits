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
// Push notifications are the next Growth Plan item. Their "push" and
// "notificationclick" listeners belong here, below the fetch policy. They
// must not write to CACHE_NAME (activate() would not know about it) and must
// not put money/customer data into a notification body without a decision.
