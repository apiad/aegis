// aegis's service worker. Chrome on Android shows a notification only from a
// worker's registration (it refuses `new Notification()`), so ping.js raises
// them through this one, and a click on one comes back here to open its
// session, also when no aegis window is open. It caches nothing yet.

// Taking over at once is safe only while nothing is cached: once the worker
// serves a build's files, a new one must wait until the page reloads, or a
// page of the old build would load files of the new one.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  event.waitUntil(openSession(event.notification.data?.key || ""));
});

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
