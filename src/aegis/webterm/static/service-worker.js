// A tombstone, not a service worker.
//
// The retired aegis PWA registered a worker at scope "/" on this origin, and
// it stays registered in every browser that ever loaded that page — deleting
// the old client does not remove it. A registered worker intercepts every GET
// for the origin, so it would sit in front of the new terminal.
//
// A browser fetches this script on its next update check. Unregistering here
// is what actually removes it; the caches go with it so the old shell cannot
// be served from disk in the meantime. There is deliberately no fetch
// handler: this worker's whole job is to stop existing.

self.addEventListener("install", () => self.skipWaiting());

self.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys.map((k) => caches.delete(k)));
    await self.registration.unregister();
    for (const client of await self.clients.matchAll({ type: "window" })) {
      client.navigate(client.url);
    }
  })());
});
