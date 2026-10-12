// aegis's service worker. Chrome on Android shows a notification only from a
// worker's registration (it refuses `new Notification()`), so ping.js raises
// them through this one, and a click on one comes back here to open its
// session, also when no aegis window is open. It caches nothing yet.

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  event.waitUntil(openSession(event.notification.data?.key || ""));
});

// Focus an aegis window and tell it which session to open, or open one on it.
async function openSession(key) {
  const wins = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
  const win = wins.find((w) => w.focused) || wins[0];
  if (win) {
    await win.focus();
    if (key) win.postMessage({ t: "open", key });
    return;
  }
  await self.clients.openWindow(key ? `/#s=${key}` : "/");
}
