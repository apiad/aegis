// The ping outside the page: the count of sessions that need you or broke, in
// the document title and on the favicon, and a desktop notification when a
// session enters one of those states while the page is hidden, or when an agent
// raises a toast then. The states are decided in Python; this only counts them
// and notices transitions.

import { MARK } from "./mark.js";

const URGENT = new Set(["needs_you", "error"]);
let count = 0;
let base = "aegis";
let seen = null; // session key -> attention, after the first draw

export function setTitle(text) {
  base = text;
  document.title = count ? `(${count}) ${base}` : base;
}

function favicon(n) {
  const accent = getComputedStyle(document.getElementById("a2")).getPropertyValue("--accent").trim();
  const dot = n ? `<circle id="dot" cx="50" cy="14" r="12" fill="${accent}" stroke="#11100e" stroke-width="4"/>` : "";
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" rx="14" fill="#11100e"/><g transform="translate(32 32) scale(.94) translate(-32 -32)">${MARK}</g>${dot}</svg>`;
  document.getElementById("favicon").href = `data:image/svg+xml,${encodeURIComponent(svg)}`;
}

// The dot takes the theme's accent, so a theme switch redraws it.
export function redrawFavicon() {
  favicon(count);
}

const ICON = "/static/icons/aegis-192.png";
const BADGE = "/static/icons/aegis-badge-96.png";

export function updatePing(metas, { onOpen }) {
  const n = metas.filter((m) => URGENT.has(m.attention)).length;
  if (n !== count || seen === null) {
    count = n;
    setTitle(base);
    favicon(n);
  }
  // `seen` moves first, so a notification the browser refuses is not tried
  // again on every update while the page stays hidden.
  const before = seen;
  seen = new Map(metas.map((m) => [m.key, m.attention]));
  if (before === null) return;
  for (const [key, was] of before) if (URGENT.has(was) && seen.get(key) !== was) withdraw(`${key}:${was}`);
  for (const m of metas) {
    if (!URGENT.has(m.attention) || before.get(m.key) === m.attention) continue;
    const title = `${m.title || m.handle}: ${m.attention === "error" ? "error" : "needs you"}`;
    notify(title, m.attention_line || "", `${m.key}:${m.attention}`, m.key, onOpen).catch(refused);
  }
}

// An agent's toast (toast.js) while the page is hidden: the card would leave
// before anyone saw it, so it goes to the desktop as well. aegis's own toasts
// answer something done on the page, so they never do.
export function pingToast(t, onOpen) {
  if (!t.from) return;
  const { handle, id } = t.from;
  const title = t.kind === "error" ? `${handle}: error` : handle;
  notify(title, t.text, `toast:${id}:${t.text}`, id, onOpen).catch(refused);
}

// A click on a notification the worker showed: sw.js focuses this page and
// posts which session to open.
export function listenForOpen(onOpen) {
  navigator.serviceWorker?.addEventListener("message", (ev) => {
    if (ev.data?.t === "open" && typeof ev.data.key === "string") onOpen(ev.data.key);
  });
}

const refused = (e) => console.warn("aegis: notification refused", e);

// A session answered, here or anywhere, or closed: its notification would only
// open a question nobody asks any more.
function withdraw(tag) {
  navigator.serviceWorker
    ?.getRegistration()
    .then((reg) => reg?.getNotifications({ tag }))
    .then((notes) => notes?.forEach((n) => n.close()))
    .catch(() => {});
}

// A notification, only while the page is hidden and the browser allows it. It
// comes from the service worker's registration when one is active: Chrome on
// Android refuses `new Notification()` ("Illegal constructor"), and a
// registration's notification still opens its session after the page is gone.
// Without an active worker (plain http on another host, or the first load
// before the worker activates) the constructor still works on a desktop.
async function notify(title, body, tag, key, onOpen) {
  if (!document.hidden || !("Notification" in window) || Notification.permission !== "granted") return;
  const reg = await navigator.serviceWorker?.getRegistration();
  if (reg?.active) {
    await reg.showNotification(title, { body, tag, renotify: true, icon: ICON, badge: BADGE, data: { key } });
    return;
  }
  const note = new Notification(title, { body, tag });
  note.onclick = () => {
    window.focus();
    onOpen(key);
  };
}

export function installBell(button) {
  const draw = () => {
    const p = "Notification" in window ? Notification.permission : "denied";
    button.dataset.state = p;
    button.title = p === "granted" ? "Desktop notifications are on" : p === "denied" ? "Notifications are blocked in this browser" : "Turn on desktop notifications";
  };
  draw();
  button.addEventListener("click", async () => {
    if ("Notification" in window && Notification.permission === "default") await Notification.requestPermission();
    draw();
  });
}
