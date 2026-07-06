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
  // Credentialed + non-fatal: behind HTTP basic auth, the install-context
  // fetches must carry credentials, and one failure must not abort activation
  // (all-or-nothing addAll would leave the SW stuck installing → no install
  // prompt, no offline). Precache best-effort; activate regardless.
  e.waitUntil((async () => {
    const cache = await caches.open(CACHE);
    await Promise.allSettled(
      SHELL.map((u) => cache.add(new Request(u, { credentials: "include" })))
    );
    await self.skipWaiting();
  })());
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
