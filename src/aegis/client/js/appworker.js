// The page's side of the service worker (sw.js): registering it, the progress
// of a build's download, what this device keeps, and updates. A new build
// downloads beside the one the page runs and waits; startWorker's onUpdate
// says so, and applyUpdate tells the waiting worker to take over and reloads
// once it has. A worker exists only in a secure context (https or
// localhost); elsewhere every function here does nothing but takeBuild.

const sw = navigator.serviceWorker;
let reg = null;
let reloading = false;
let autoApply = false;
let announce = () => {};
let persisted = false;
const progress = new Set();

export function startWorker({ onUpdate }) {
  if (!sw) return;
  announce = () => (autoApply ? applyUpdate() : onUpdate());
  // A page that had a worker when it loaded reloads whenever another takes
  // over, whichever tab asked: the new worker serves its own build's files,
  // and this page's scripts are the old build's.
  const hadController = Boolean(sw.controller);
  sw.addEventListener("message", (ev) => {
    if (ev.data?.t === "progress") for (const fn of progress) fn(ev.data);
  });
  sw.addEventListener("controllerchange", () => {
    if (reloading || hadController) location.reload();
  });
  // Installed with a controller in place: a new build is waiting. With none,
  // this is the first install, which takes over by itself.
  const watch = (w) =>
    w?.addEventListener("statechange", () => {
      if (w.state === "installed" && sw.controller) announce();
    });
  sw.register("/sw.js")
    .then((r) => {
      reg = r;
      if (r.waiting && sw.controller) announce();
      watch(r.installing); // a download that began before this page loaded
      r.addEventListener("updatefound", () => watch(r.installing));
      keep();
      keepUnderPressure();
    })
    .catch((e) => console.warn("aegis: service worker", e));
}

// Ask the active worker to download its build if it lacks it: a download that
// failed (a flaky network, a deploy mid-way) is tried again from here.
async function keep() {
  (await sw?.ready)?.active?.postMessage({ t: "keep" });
}

// Once this device holds a complete build, ask the browser not to evict it
// under storage pressure. Asked once; the answer changes nothing here.
async function keepUnderPressure() {
  await sw.ready;
  if (persisted || !(await deviceCopy())?.complete) return;
  persisted = true;
  navigator.storage?.persist?.();
}

export function onProgress(fn) {
  progress.add(fn);
  return () => progress.delete(fn);
}

// What the active worker keeps on this device, or null with no worker.
export async function deviceCopy() {
  const active = sw && (await sw.getRegistration())?.active;
  if (!active) return null;
  return new Promise((resolve) => {
    const ch = new MessageChannel();
    const timer = setTimeout(() => resolve(null), 2000);
    ch.port1.onmessage = (ev) => {
      clearTimeout(timer);
      resolve(ev.data);
    };
    active.postMessage({ t: "status" }, [ch.port2]);
  });
}

export function checkForUpdate() {
  keep();
  return reg?.update().catch(() => {});
}

export const hasUpdate = () => Boolean(reg?.waiting);

export function applyUpdate() {
  // Another tab took the update already: this page only has to catch up.
  if (!reg?.waiting) return location.reload();
  reloading = true;
  reg.waiting.postMessage({ t: "skip" });
}

// The server speaks a protocol this build cannot (the socket closed with
// 4400): take the new build as soon as it is ready, without asking.
export function setAutoApply() {
  if (autoApply) return;
  autoApply = true;
  if (hasUpdate()) return applyUpdate();
  // The new build may fail to install (a file fetch on a flaky network): ask
  // again until one is waiting, which announce() then applies.
  checkForUpdate();
  setInterval(checkForUpdate, 10000);
}

// The server serves a newer client than this page (#499): an upgrade restarted
// it, and every open tab reconnects into this. With a worker, the new build
// downloads and takes over by itself; without one, a reload fetches it, once
// per build, so a server that keeps answering another build cannot loop it.
export function takeBuild(build) {
  if (sw?.controller) return setAutoApply();
  if (sessionStorage.getItem("aegis.reloadedFor") === build) return;
  sessionStorage.setItem("aegis.reloadedFor", build);
  location.reload();
}
