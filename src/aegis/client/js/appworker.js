// The page's side of the service worker (sw.js): registering it, the progress
// of a build's download, what this device keeps, and updates. A new build
// downloads beside the one the page runs and waits; startWorker's onUpdate
// says so, and applyUpdate tells the waiting worker to take over and reloads
// once it has. A worker exists only in a secure context (https or
// localhost); elsewhere every function here does nothing.

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
  sw.addEventListener("message", (ev) => {
    if (ev.data?.t === "progress") for (const fn of progress) fn(ev.data);
  });
  sw.addEventListener("controllerchange", () => {
    if (reloading) location.reload();
  });
  sw.register("/sw.js")
    .then((r) => {
      reg = r;
      if (r.waiting && sw.controller) announce();
      keepUnderPressure();
      r.addEventListener("updatefound", () => {
        const w = r.installing;
        w?.addEventListener("statechange", () => {
          // Installed with a controller in place: a new build is waiting. With
          // none, this is the first install, which takes over by itself.
          if (w.state === "installed" && sw.controller) announce();
        });
      });
    })
    .catch((e) => console.warn("aegis: service worker", e));
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
  return reg?.update().catch(() => {});
}

export const hasUpdate = () => Boolean(reg?.waiting);

export function applyUpdate() {
  if (!reg?.waiting) return;
  reloading = true;
  reg.waiting.postMessage({ t: "skip" });
}

// The server speaks a protocol this build cannot (the socket closed with
// 4400): take the new build as soon as it is ready, without asking.
export function setAutoApply() {
  autoApply = true;
  if (hasUpdate()) applyUpdate();
  else checkForUpdate();
}
