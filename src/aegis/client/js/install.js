// Installing aegis as an app. Chrome fires beforeinstallprompt when it judges
// the site installable; aegis keeps the event instead of Chrome's own banner
// and offers it in a card at the top of the Fleet and in Settings. The dialog
// shows the three steps with their real state: the worker's download of this
// build (appworker.js), the browser's install (which reports no progress, so
// its bar only says it is under way), and notifications.

import { deviceCopy, onProgress } from "./appworker.js";
import { refreshBell } from "./ping.js";

const LATER = "aegis.install.later";
const MONTH_MS = 30 * 24 * 3600 * 1000;
let deferred = null;
let installed = matchMedia("(display-mode: standalone)").matches;
let card = null;
let closeDialog = null;
const watchers = new Set();

export const installState = () => ({ canPrompt: Boolean(deferred), installed });

export function onInstallState(fn) {
  watchers.add(fn);
  return () => watchers.delete(fn);
}

function changed() {
  drawCard();
  for (const fn of watchers) fn(installState());
}

function drawCard() {
  if (!card) return;
  const later = Date.now() - Number(localStorage.getItem(LATER) || 0) < MONTH_MS;
  card.hidden = !deferred || installed || later;
}

export function startInstall(node) {
  card = node;
  window.addEventListener("beforeinstallprompt", (e) => {
    e.preventDefault();
    deferred = e;
    changed();
  });
  window.addEventListener("appinstalled", () => {
    installed = true;
    deferred = null;
    changed();
  });
  card.querySelector("[data-act=later]").addEventListener("click", () => {
    localStorage.setItem(LATER, String(Date.now()));
    drawCard();
  });
  card.querySelector("[data-act=install]").addEventListener("click", openInstallDialog);
  drawCard();
}

// Esc reaches this through keys.js's escape action (app.js); true if it closed one.
export function closeInstallDialog() {
  if (!closeDialog) return false;
  closeDialog();
  return true;
}

const kb = (n) => `${Math.round(n / 1024).toLocaleString()} KB`;

function h(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
}

function row(step, num, title) {
  const r = h("div", "irow");
  r.dataset.step = step;
  const body = h("div");
  body.append(h("h5", null, title), h("div", "sub"));
  r.append(h("span", "num", String(num)), body);
  return r;
}

function bar(r, indeterminate = false) {
  let b = r.querySelector(".pbar");
  if (!b) {
    b = h("div", "pbar");
    b.append(h("i"));
    r.append(b);
  }
  b.classList.toggle("ind", indeterminate);
  return b;
}

export function openInstallDialog() {
  closeInstallDialog();
  const d = h("div", "dialog");
  d.id = "install-dialog";
  const panel = h("div", "panel");
  panel.setAttribute("role", "dialog");
  panel.setAttribute("aria-modal", "true");
  const files = row("files", 1, "Files for offline use");
  const app = row("app", 2, "Add to this device");
  const notify = row("notify", 3, "Notifications");
  const done = h("button", "btn", "Done");
  done.type = "button";
  done.dataset.act = "done";
  const acts = h("div", "acts");
  acts.append(done);
  panel.append(h("p", "q", "Install aegis"), files, app, notify, acts);
  d.append(panel);
  document.getElementById("a2").append(d);

  // 1. The worker's download of this build.
  const filesSub = files.querySelector(".sub");
  const fill = (pct) => (bar(files).querySelector("i").style.width = `${pct}%`);
  const ready = (c) => {
    files.classList.add("done");
    fill(100);
    filesSub.textContent = `ready · build ${c.build} · ${kb(c.bytes)}`;
  };
  // On a first visit no worker is active until its install, the download
  // included, has finished, so ask what the device keeps once one is.
  const kept = () => navigator.serviceWorker?.ready.then(() => deviceCopy()).then((c) => c?.complete && ready(c));
  const stop = onProgress((p) => {
    fill((100 * p.done) / p.total);
    filesSub.textContent = `${p.done} of ${p.total} files · ${kb(p.bytes)} of ${kb(p.of)}`;
    if (p.done === p.total) kept();
  });
  filesSub.textContent = navigator.serviceWorker ? "waiting for the download" : "needs https: this device keeps no copy";
  kept();

  // 2. The browser's install.
  const appSub = app.querySelector(".sub");
  const drawApp = () => {
    app.querySelector(".ctl")?.remove();
    const { canPrompt, installed: done2 } = installState();
    app.classList.toggle("done", done2);
    if (done2) {
      bar(app).hidden = true;
      appSub.textContent = "installed · open it from the home screen";
      return;
    }
    if (!canPrompt) {
      appSub.textContent = "use your browser's menu: Install app";
      return;
    }
    appSub.textContent = "opens your browser's confirmation";
    const go = h("button", "btn primary", "Install");
    go.type = "button";
    go.addEventListener("click", async () => {
      const e = deferred;
      e.prompt();
      const { outcome } = await e.userChoice;
      // An event prompts once; Chrome fires a new one when it will offer again.
      deferred = null;
      if (outcome !== "accepted") return changed();
      go.remove();
      bar(app, true).hidden = false;
      appSub.textContent = "your browser is adding aegis to the home screen";
    });
    const ctl = h("div", "ctl");
    ctl.append(go);
    app.append(ctl);
  };
  const unwatch = onInstallState(drawApp);
  drawApp();

  // 3. Notifications (push, from slice 5, subscribes here too).
  const notifySub = notify.querySelector(".sub");
  const drawNotify = () => {
    notify.querySelector(".ctl")?.remove();
    const p = "Notification" in window ? Notification.permission : "denied";
    notify.classList.toggle("done", p === "granted");
    notifySub.textContent =
      p === "granted" ? "on · needs you, errors and agents' toasts" : p === "denied" ? "blocked in this browser's settings" : "needs you, errors and agents' toasts";
    if (p !== "default") return;
    const on = h("button", "btn", "Turn on");
    on.type = "button";
    on.addEventListener("click", async () => {
      await Notification.requestPermission();
      refreshBell();
      drawNotify();
    });
    const ctl = h("div", "ctl");
    ctl.append(on);
    notify.append(ctl);
  };
  drawNotify();

  const close = () => {
    stop();
    unwatch();
    d.remove();
    closeDialog = null;
  };
  closeDialog = close;
  done.addEventListener("click", close);
  d.addEventListener("click", (ev) => ev.target === d && close());
}
