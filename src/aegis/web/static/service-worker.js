// aegis PWA service worker: precache the app shell, serve it cache-first so
// the app launches instantly and works installed offline. Live WS traffic is
// never cached (it isn't a fetch event). The cache name carries the server
// version — a new deploy changes these bytes, so the SW reinstalls and busts
// the old cache.
const VERSION = "__SW_VERSION__";
const CACHE = `aegis-shell-${VERSION}`;
const SHELL = [
  "/",
  "/static/js/app.js",
  "/static/js/ws.js",
  "/static/js/coalesce.js",
  "/static/js/markdown.js",
  "/static/js/renderEvent.js",
  "/static/js/tabs.js",
  "/static/js/queues.js",
  "/theme.css",
  "/manifest.webmanifest",
  "/static/icons/icon.svg",
  "/static/icons/icon-192.png",
  "/static/icons/icon-512.png",
];

self.addEventListener("install", (e) => {
  e.waitUntil(
    caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
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
  // Cache-first for static assets.
  e.respondWith(caches.match(req).then((hit) => hit || fetch(req)));
});
