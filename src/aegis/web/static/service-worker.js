// aegis PWA service worker. Runtime-cache-first (not install-time precache):
// the app sits behind HTTP basic auth, where install-context precache fetches
// can 401 and abort activation. Empty install → the SW always activates
// (satisfying installability); the fetch handler caches each shell asset on
// first load (those requests carry the page's auth). Live WS traffic isn't a
// fetch event, so it's never touched. Cache name carries the server version so
// a deploy busts the old cache.
const VERSION = "__SW_VERSION__";
const CACHE = `aegis-shell-${VERSION}`;

self.addEventListener("install", (e) => {
  e.waitUntil(self.skipWaiting());
});

self.addEventListener("activate", (e) => {
  e.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)));
    await self.clients.claim();
  })());
});

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== location.origin) return;

  if (req.mode === "navigate") {
    // Network-first for navigations; fall back to the cached shell offline.
    e.respondWith(fetch(req).catch(() => caches.match("/")));
    return;
  }

  // Runtime cache-first: serve from cache, else fetch and populate. The fetch
  // reuses the original request, so it carries the page's basic-auth creds.
  e.respondWith((async () => {
    const cached = await caches.match(req);
    if (cached) return cached;
    try {
      const resp = await fetch(req);
      if (resp && resp.ok) {
        const cache = await caches.open(CACHE);
        cache.put(req, resp.clone());
      }
      return resp;
    } catch (err) {
      return cached || Response.error();
    }
  })());
});
