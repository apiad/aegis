// Web Push for this device (push.py): the bell's panel subscribes it, picks
// the sources it hears about, sends a test and turns it off. The browser's
// subscription goes to the server with push.subscribe; the id the server
// answers and the sources are kept in localStorage, so ping.js knows which
// events push now carries and stands down for them, and the next load can
// tell whether the browser rotated its subscription (checkRotation).

import { who } from "./me.js";
import { refreshBell } from "./ping.js";

const ID = "aegis.push.id";
const SOURCES_KEY = "aegis.push.sources";
export const SOURCES = [
  ["needs_you", "Needs you"],
  ["error", "Errors"],
  ["toast", "Agents' toasts"],
  ["review", "Something to read"],
  ["done", "Finished work"],
];
export const DEFAULT_SOURCES = ["needs_you", "error", "toast"];

let call = () => Promise.reject(new Error("push is not set up"));
let panel = null;
let anchorNode = null;
const watchers = new Set();

export function setupPush(fn) {
  call = fn;
}

// Push needs a worker, a push service and a secure page; without them the
// bell keeps asking for permission only (ping.js).
export const pushSupported = () => window.isSecureContext && "serviceWorker" in navigator && "PushManager" in window;

function storedSources() {
  try {
    const s = JSON.parse(localStorage.getItem(SOURCES_KEY) || "null");
    return Array.isArray(s) ? s : null;
  } catch {
    return null;
  }
}

export function pushState() {
  const id = localStorage.getItem(ID);
  return { supported: pushSupported(), subscribed: Boolean(id), id, sources: id ? storedSources() || [] : [] };
}

// Settings and the install dialog redraw when this device's push changes.
export function onPushState(fn) {
  watchers.add(fn);
  return () => watchers.delete(fn);
}

function changed() {
  refreshBell();
  for (const fn of watchers) fn(pushState());
  if (panel) drawPanel();
}

function remember(id, sources) {
  if (id) {
    localStorage.setItem(ID, id);
    localStorage.setItem(SOURCES_KEY, JSON.stringify(sources));
  } else {
    localStorage.removeItem(ID);
    localStorage.removeItem(SOURCES_KEY);
  }
}

// "Chrome on Android", from the client hints where the browser gives them.
function deviceName() {
  const d = navigator.userAgentData;
  const ua = navigator.userAgent;
  const browser =
    d?.brands?.map((b) => b.brand).find((b) => !/not.?a.?brand|chromium/i.test(b)) ||
    (/Edg\//.test(ua) ? "Edge" : /Firefox\//.test(ua) ? "Firefox" : /Chrome\//.test(ua) ? "Chrome" : /Safari\//.test(ua) ? "Safari" : "A browser");
  const platform =
    d?.platform ||
    (/Android/.test(ua) ? "Android" : /iPhone|iPad/.test(ua) ? "iOS" : /Mac OS/.test(ua) ? "macOS" : /Windows/.test(ua) ? "Windows" : /Linux/.test(ua) ? "Linux" : "");
  return (platform ? `${browser} on ${platform}` : browser).slice(0, 80);
}

const bytes = (b64) => Uint8Array.from(atob(b64.replace(/-/g, "+").replace(/_/g, "/")), (c) => c.charCodeAt(0));

// The rows of the token signed in here; an admin's push.list has everyone's.
export async function listMine() {
  const { subscriptions } = await call("push.list");
  return subscriptions.filter((r) => r.owner === who.name);
}

// Ask permission if needed, subscribe the browser with this server's key and
// store the subscription. ``id`` (this device's, by default) is replaced, not
// added to, so a rotated subscription keeps its row.
export async function subscribePush(sources = null, id = localStorage.getItem(ID)) {
  if (!pushSupported()) return false;
  if (Notification.permission === "default") await Notification.requestPermission();
  if (Notification.permission !== "granted") {
    changed();
    return false;
  }
  const { key } = await call("push.key");
  const reg = await navigator.serviceWorker.ready;
  let sub = await reg.pushManager.getSubscription();
  // One made with another key (another server here, before) takes no pushes
  // signed with this one, and blocks subscribing with it.
  const made = sub?.options?.applicationServerKey;
  if (sub && made && btoa(String.fromCharCode(...new Uint8Array(made))) !== btoa(String.fromCharCode(...bytes(key)))) {
    await sub.unsubscribe();
    sub = null;
  }
  sub = sub || (await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: bytes(key) }));
  const chosen = sources || storedSources() || DEFAULT_SOURCES;
  const params = { subscription: sub.toJSON(), device: deviceName(), sources: chosen };
  let r;
  try {
    r = await call("push.subscribe", id ? { ...params, id } : params);
  } catch (e) {
    // Another token's row (someone else signed in here before): a new one.
    if (e.code !== "not_owner") throw e;
    r = await call("push.subscribe", params);
  }
  remember(r.id, chosen);
  changed();
  return true;
}

export async function setSources(sources) {
  const { id } = pushState();
  if (!id) return;
  await call("push.update", { id, sources });
  remember(id, sources);
  changed();
}

export async function unsubscribePush() {
  const { id } = pushState();
  const reg = await navigator.serviceWorker?.getRegistration();
  const sub = await reg?.pushManager.getSubscription();
  await sub?.unsubscribe();
  if (id) await call("push.remove", { id }).catch((e) => e.code === "unknown_subscription" || Promise.reject(e));
  remember(null);
  changed();
}

// Settings removed a row: if it was this device's, this browser forgets it too.
export async function forget(id) {
  if (id === localStorage.getItem(ID)) await unsubscribePush();
}

// On load: the server's row for this device still matches the browser's
// subscription, or the browser rotated it (a new endpoint, or none) and it
// is sent again under the same id. A row removed from Settings, or by a
// revoked token, turns push off here rather than coming back.
export async function checkRotation() {
  const { id } = pushState();
  if (!id || !pushSupported()) return;
  const row = (await listMine()).find((r) => r.id === id);
  if (!row) {
    remember(null);
    return changed();
  }
  remember(id, row.sources);
  const reg = await navigator.serviceWorker.ready;
  const sub = await reg.pushManager.getSubscription();
  if (sub?.endpoint === row.endpoint) return changed();
  await subscribePush(row.sources, id);
}

// -- the panel -------------------------------------------------------------------

function h(tag, props = {}, ...kids) {
  const n = Object.assign(document.createElement(tag), props);
  n.append(...kids.filter((k) => k != null && k !== false));
  return n;
}

function ago(t) {
  const s = Math.max(0, Date.now() / 1000 - t);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} days ago`;
}

let row = null; // this device's row as the server last answered
let status = ""; // the line beside the buttons

async function refreshRow() {
  const { id } = pushState();
  row = id ? (await listMine().catch(() => [])).find((r) => r.id === id) || null : null;
  if (panel) drawPanel();
}

function drawPanel() {
  const st = pushState();
  const perm = "Notification" in window ? Notification.permission : "denied";
  const parts = [st.subscribed ? row?.device || deviceName() : deviceName()];
  if (st.subscribed) {
    parts.push("push on");
    if (row?.last_ok_at) parts.push(`last delivered ${ago(row.last_ok_at)}`);
    if (row?.failures) parts.push(`${row.failures} failed`);
  } else parts.push(perm === "denied" ? "blocked in this browser's settings" : "push off");
  const boxes = SOURCES.map(([src, label]) => {
    const box = h("input", { type: "checkbox", checked: st.sources.includes(src), disabled: !st.subscribed });
    box.dataset.src = src;
    box.addEventListener("change", () => {
      const next = SOURCES.map(([s]) => s).filter((s) => panel.querySelector(`input[data-src=${s}]`).checked);
      act(() => setSources(next), `${next.length} sources`);
    });
    return h("label", {}, box, ` ${label} `, h("span", { textContent: src }));
  });
  const btn = (text, name, run, primary = false) => {
    const b = h("button", { type: "button", className: `btn sm${primary ? " primary" : ""}`, textContent: text });
    b.dataset.act = name;
    b.addEventListener("click", run);
    return b;
  };
  const acts = h(
    "div",
    { className: "acts" },
    h("span", { className: "st", textContent: status || (st.subscribed ? `${st.sources.length} sources` : "") }),
    st.subscribed && btn("Send a test", "test", sendTest),
    st.subscribed
      ? btn("Turn off", "off", () => act(unsubscribePush, "off"))
      : perm !== "denied" && btn("Turn on", "on", () => act(() => subscribePush(), ""), true),
  );
  panel.replaceChildren(h("h5", { textContent: "Notifications on this device" }), h("div", { className: "dev", textContent: parts.join(" · ") }), ...boxes, acts);
}

async function act(run, done) {
  try {
    await run();
    status = done;
    await refreshRow();
  } catch (e) {
    status = e.message || String(e);
    if (panel) drawPanel();
  }
}

async function sendTest() {
  status = "sending…";
  drawPanel();
  try {
    const r = await call("push.test", { id: pushState().id });
    status = r.status == null ? "the push service gave no answer" : r.status < 300 ? "test sent" : `the push service answered ${r.status}`;
  } catch (e) {
    status = e.message || String(e);
  }
  await refreshRow();
}

function outside(ev) {
  if (panel && !panel.contains(ev.target) && !anchorNode?.contains(ev.target)) closePushPanel();
}

// Esc reaches this through keys.js's escape action (app.js); true if it closed it.
export function closePushPanel() {
  if (!panel) return false;
  panel.remove();
  panel = null;
  document.removeEventListener("pointerdown", outside, true);
  return true;
}

export function openPushPanel(anchor) {
  if (closePushPanel()) return; // the bell toggles it
  anchorNode = anchor;
  status = "";
  panel = h("div", { id: "push-panel", className: "pop" });
  panel.setAttribute("role", "dialog");
  panel.setAttribute("aria-label", "Notifications on this device");
  const r = anchor.getBoundingClientRect();
  panel.style.top = `${Math.round(r.bottom + 6)}px`;
  document.getElementById("a2").append(panel);
  drawPanel();
  refreshRow();
  document.addEventListener("pointerdown", outside, true);
}
