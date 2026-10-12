// aegis's service worker. It keeps one build of the client, the files
// /client-files.json lists, in a cache named after the build, and serves the
// page and its files from there, so aegis opens with no connection. It also
// raises the notifications ping.js asks for (Chrome on Android shows none any
// other way), shows the pushes aegis's sender sends (push.py), and opens a
// session when one is clicked.

const BUILD = "__AEGIS_BUILD__"; // web.py writes the build id here
const CACHE = `aegis-${BUILD}`;
const DONE = "/__aegis/build.json"; // written last: the build is complete
const PARALLEL = 4;

// A new worker downloads its build next to the old one and then waits: taking
// over a page the old build loaded would mix two builds' files. The page asks
// it to take over (a "skip" message) when the person reloads.
//
// A failed download never fails the install. A worker without a complete
// build serves everything from the network and still raises notifications;
// it downloads again whenever a page asks ("keep"), which every page does
// once the worker is active and on every reconnect.
self.addEventListener("install", (event) => event.waitUntil(ensure()));
self.addEventListener("activate", (event) => event.waitUntil(activate()));

self.addEventListener("message", (event) => {
  const msg = event.data || {};
  if (msg.t === "skip") self.skipWaiting();
  if (msg.t === "keep") event.waitUntil(ensure());
  if (msg.t === "status" && event.ports[0]) event.waitUntil(status().then((s) => event.ports[0].postMessage(s)));
  if (msg.t === "view" && event.source) views.set(event.source.id, { key: msg.key || "", visible: Boolean(msg.visible), window: Boolean(msg.window) });
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

// A push from aegis's sender (push.py): shown unless a window of this device
// shows that session in front of the person. Chrome allows a silent push while
// a page of the origin is in front. A worker restarted since the page last said
// what it shows has an empty map, and shows the notification: telling the
// person twice is better than not at all.
self.addEventListener("push", (event) => {
  let p = {};
  try {
    p = event.data?.json() ?? {};
  } catch {
    p = { title: "aegis", body: event.data?.text() ?? "" };
  }
  event.waitUntil(
    (async () => {
      if (p.key && (await inFront(p.key))) return;
      await self.registration.showNotification(p.title || "aegis", {
        body: p.body || "",
        tag: p.tag || p.key || "aegis",
        renotify: p.kind === "needs_you",
        icon: "/static/icons/aegis-192.png",
        badge: "/static/icons/aegis-badge-96.png",
        data: { key: p.key || "" },
      });
    })(),
  );
});

const views = new Map(); // client id -> {key, visible, window}

// A window that closed says nothing; its view goes the next time one is asked.
async function inFront(key) {
  const wins = await self.clients.matchAll({ type: "window" });
  const open = new Set(wins.map((w) => w.id));
  for (const id of views.keys()) if (!open.has(id)) views.delete(id);
  return wins.some((w) => views.get(w.id)?.key === key && views.get(w.id)?.visible && w.visibilityState === "visible");
}

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  event.waitUntil(openSession(event.notification.data?.key || ""));
});

let complete = null; // whether CACHE holds the whole build; null until looked at
let keeping = null; // the download under way, if any

// Looked at again whenever the cache may have gone (another build's
// activation, the browser evicting it): has() is cheap, match() only then.
async function isComplete() {
  if (complete && (await caches.has(CACHE))) return true;
  complete = Boolean(await caches.match(DONE, { cacheName: CACHE }));
  return complete;
}

// Only a complete build is served: half of one would mix with the network's.
async function fromBuild(req, key) {
  const hit = (await isComplete()) && (await caches.match(key, { cacheName: CACHE }));
  return hit || fetch(req);
}

// Download the build unless it is here or already coming. Never rejects.
async function ensure() {
  if (await isComplete()) return;
  if (!keeping)
    keeping = keep()
      .catch((e) => console.warn("aegis: keeping the client failed", e))
      .finally(() => (keeping = null));
  await keeping;
}

// Download every listed file, PARALLEL at a time, telling every window how far
// it got. Any failure drops what was kept, so a device never serves half a
// client; ensure() tries again when a page next asks.
async function keep() {
  let failed = false;
  try {
    const list = await (await fetch("/client-files.json", { cache: "no-store" })).json();
    if (list.build !== BUILD) throw new Error(`the file list is build ${list.build}, this worker is ${BUILD}`);
    const cache = await caches.open(CACHE);
    const todo = [...list.files];
    const total = todo.length;
    let done = 0;
    let bytes = 0;
    const lane = async () => {
      for (let f = todo.shift(); f && !failed; f = todo.shift()) {
        const res = await fetch(f.url, { cache: "no-cache" });
        if (!res.ok) throw new Error(`${f.url}: ${res.status}`);
        await cache.put(f.url, res);
        done += 1;
        bytes += f.bytes;
        tell({ t: "progress", build: BUILD, done, total, bytes, of: list.bytes });
      }
    };
    await Promise.all(Array.from({ length: PARALLEL }, () => lane().catch((e) => ((failed = true), Promise.reject(e)))));
    const marker = { build: BUILD, version: list.version, bytes: list.bytes, files: total };
    await cache.put(DONE, new Response(JSON.stringify(marker), { headers: { "Content-Type": "application/json" } }));
    // Another build's activation deletes every cache but its own, this one
    // included if it was still filling: then this build is not here at all.
    if (!(await caches.has(CACHE))) throw new Error("the cache was deleted while it filled");
    complete = true;
  } catch (e) {
    failed = true;
    await caches.delete(CACHE);
    throw e;
  }
}

async function activate() {
  for (const name of await caches.keys()) if (name.startsWith("aegis-") && name !== CACHE) await caches.delete(name);
  await self.clients.claim();
}

async function status() {
  const hit = (await isComplete()) && (await caches.match(DONE, { cacheName: CACHE }));
  return hit ? { complete: true, ...(await hit.json()) } : { build: BUILD, complete: false, downloading: Boolean(keeping) };
}

async function tell(msg) {
  for (const c of await self.clients.matchAll({ type: "window", includeUncontrolled: true })) c.postMessage(msg);
}

// Bring an aegis page forward and tell it which session to open, or open one
// on it. Only the page at / is aegis: a sent file or a dictation model open in
// a tab is in this scope too, and has nobody listening. A window tab (one
// window of a session's canvas) never turns into another view.
async function openSession(key) {
  const wins = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
  const pages = wins.filter((w) => new URL(w.url).pathname === "/" && !views.get(w.id)?.window);
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
