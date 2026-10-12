// aegis's service worker. It keeps one build of the client, the files
// /client-files.json lists, in a cache named after the build, and serves the
// page and its files from there, so aegis opens with no connection. It also
// raises the notifications ping.js asks for (Chrome on Android shows none any
// other way) and opens a session when one is clicked.

const BUILD = "__AEGIS_BUILD__"; // web.py writes the build id here
const CACHE = `aegis-${BUILD}`;
const DONE = "/__aegis/build.json"; // written last: the build is complete
const PARALLEL = 4;

// A new worker downloads its build next to the old one and then waits: taking
// over a page the old build loaded would mix two builds' files. The page asks
// it to take over (a "skip" message) when the person reloads.
self.addEventListener("install", (event) => event.waitUntil(keep()));
self.addEventListener("activate", (event) => event.waitUntil(activate()));

self.addEventListener("message", (event) => {
  const msg = event.data || {};
  if (msg.t === "skip") self.skipWaiting();
  if (msg.t === "status" && event.ports[0]) event.waitUntil(status().then((s) => event.ports[0].postMessage(s)));
});

// Only what belongs to the build: the page at / with no query (a query is a
// sign-in, /?token=, which must reach the server), its static files and the
// manifest. Everything else (the socket, sent files, dictation models, /mcp,
// /login, /sw.js itself) goes to the network untouched.
self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;
  const page = req.mode === "navigate" && url.pathname === "/" && !url.search;
  const file = req.mode !== "navigate" && (url.pathname.startsWith("/static/") || url.pathname === "/manifest.webmanifest");
  if (page || file) event.respondWith(fromBuild(req, page ? "/" : url.pathname));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  event.waitUntil(openSession(event.notification.data?.key || ""));
});

async function fromBuild(req, key) {
  const hit = await caches.match(key, { cacheName: CACHE });
  return hit || fetch(req);
}

// Download every listed file, PARALLEL at a time, telling every window how far
// it got. Any failure fails the install as a whole and drops what was kept:
// the browser tries again at its next update check, and a device never holds
// half a client.
async function keep() {
  try {
    const list = await (await fetch("/client-files.json", { cache: "no-store" })).json();
    if (list.build !== BUILD) throw new Error(`the file list is build ${list.build}, this worker is ${BUILD}`);
    const cache = await caches.open(CACHE);
    const todo = [...list.files];
    const total = todo.length;
    let done = 0;
    let bytes = 0;
    const lane = async () => {
      for (let f = todo.shift(); f; f = todo.shift()) {
        const res = await fetch(f.url, { cache: "no-cache" });
        if (!res.ok) throw new Error(`${f.url}: ${res.status}`);
        await cache.put(f.url, res);
        done += 1;
        bytes += f.bytes;
        tell({ t: "progress", build: BUILD, done, total, bytes, of: list.bytes });
      }
    };
    await Promise.all(Array.from({ length: PARALLEL }, lane));
    const marker = { build: BUILD, version: list.version, bytes: list.bytes, files: total };
    await cache.put(DONE, new Response(JSON.stringify(marker), { headers: { "Content-Type": "application/json" } }));
  } catch (e) {
    await caches.delete(CACHE);
    throw e;
  }
}

async function activate() {
  for (const name of await caches.keys()) if (name.startsWith("aegis-") && name !== CACHE) await caches.delete(name);
  await self.clients.claim();
}

async function status() {
  const hit = await caches.match(DONE, { cacheName: CACHE });
  return hit ? { complete: true, ...(await hit.json()) } : { build: BUILD, complete: false };
}

async function tell(msg) {
  for (const c of await self.clients.matchAll({ type: "window", includeUncontrolled: true })) c.postMessage(msg);
}

// Bring an aegis page forward and tell it which session to open, or open one
// on it. Only the page at / is aegis: a sent file or a dictation model open in
// a tab is in this scope too, and has nobody listening.
async function openSession(key) {
  const wins = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
  const pages = wins.filter((w) => new URL(w.url).pathname === "/");
  const win = pages.find((w) => w.focused) || pages[0];
  if (win) {
    // The browser may refuse to raise a window (a discarded tab on Android);
    // the page still opens the session for when it comes back.
    await win.focus().catch(() => {});
    if (key) win.postMessage({ t: "open", key });
    return;
  }
  await self.clients.openWindow(key ? `/#s=${key}` : "/");
}
