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

export function updatePing(metas, { onOpen }) {
  const n = metas.filter((m) => URGENT.has(m.attention)).length;
  if (n !== count || seen === null) {
    count = n;
    setTitle(base);
    favicon(n);
  }
  const now = new Map(metas.map((m) => [m.key, m.attention]));
  if (seen !== null) {
    for (const m of metas) {
      if (!URGENT.has(m.attention) || seen.get(m.key) === m.attention) continue;
      notify(
        `${m.title || m.handle}: ${m.attention === "error" ? "error" : "needs you"}`,
        m.attention_line || "",
        `${m.key}:${m.attention}:${m.attention_line || ""}`,
        () => onOpen(m.key),
      );
    }
  }
  seen = now;
}

// An agent's toast (toast.js) while the page is hidden: the card would leave
// before anyone saw it, so it goes to the desktop as well. aegis's own toasts
// answer something done on the page, so they never do.
export function pingToast(t, onOpen) {
  if (!t.from) return;
  const { handle, id } = t.from;
  notify(t.kind === "error" ? `${handle}: error` : handle, t.text, `toast:${id}:${t.text}`, () => onOpen(id));
}

// A desktop notification, only while the page is hidden and the browser allows
// it; a click brings the page back on the session.
function notify(title, body, tag, onClick) {
  if (!document.hidden || !("Notification" in window) || Notification.permission !== "granted") return;
  const note = new Notification(title, { body, tag });
  note.onclick = () => {
    window.focus();
    onClick();
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
