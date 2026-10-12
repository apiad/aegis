// Toasts (#329): a short message that leaves on its own, as a card in the
// bottom-right, full width above the message box on a phone. Anything in the
// client calls toast(); the server's arrive on the `toasts` channel (app.js).
// An event is a toast; a state that stays true while you look stays next to
// what it is about. At most three show, the newest at the bottom; hover holds
// one, × dismisses it, and the same text again restarts the one on screen.

import { icon } from "./glyphs.js";

const MAX = 3;
const MS = { info: 4000, error: 6000, from: 8000 };

let stack = null;
let opener = () => {};

// How a click on an agent's toast opens its session; app.js sets it.
export function setToastOpener(fn) {
  opener = fn;
}

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
}

// In a session the stack sits over the transcript's column, above the message
// box and the navigator; elsewhere it keeps its corner (base.css).
function place() {
  const at = (id) => {
    const n = document.getElementById(id);
    const r = n && !n.hidden && n.getClientRects().length ? n.getBoundingClientRect() : null;
    return r && r.width ? r : null;
  };
  const box = document.getElementById("a2").dataset.view === "session" && at("composer");
  const nav = box && at("nav");
  stack.style.right = box ? `${Math.max(8, innerWidth - box.right)}px` : "";
  stack.style.bottom = box ? `${innerHeight - Math.min(box.top, nav ? nav.top : box.top) + 8}px` : "";
}

// kind: "info" | "error"; from: {handle, id} for an agent's toast.
export function toast(text, { kind = "info", from = null, ms } = {}) {
  if (!stack) {
    stack = el("div", "toasts");
    stack.setAttribute("role", "status");
    stack.setAttribute("aria-live", "polite");
    document.getElementById("a2").append(stack);
  }
  place();
  const live = () => [...stack.children].filter((n) => !n.classList.contains("out"));
  const same = live().find((n) => n.dataset.text === text);
  if (same) return same.restart();
  while (live().length >= MAX) live()[0].remove();

  const n = el("div", `toast ${kind === "error" ? "error" : "info"}${from ? " from" : ""}`);
  n.dataset.text = text;
  if (kind === "error") n.setAttribute("role", "alert");
  const hd = el("div", "hd");
  hd.append(
    icon(from ? "arrow" : kind === "error" ? "err" : "dot"),
    el("span", "nm", from ? from.handle : kind === "error" ? "aegis · error" : "aegis"),
  );
  if (from) hd.append(el("span", "go", "· open"));
  const x = el("button", "x");
  x.type = "button";
  x.title = "Dismiss";
  x.setAttribute("aria-label", "Dismiss");
  x.append(icon("close"));
  const life = el("i", "life");
  n.append(hd, el("div", "msg", text), x, life);

  const full = ms ?? (from ? MS.from : MS[kind] || MS.info);
  let left = full;
  let started = 0;
  let timer = null;
  const leave = () => {
    clearTimeout(timer);
    n.classList.add("out");
    setTimeout(() => n.remove(), 200);
  };
  const run = () => {
    started = Date.now();
    timer = setTimeout(leave, left);
  };
  const drain = () => {
    life.style.animation = "none";
    void life.offsetWidth; // restart the line from full
    life.style.animation = "";
    life.style.animationDuration = `${full}ms`;
  };
  n.restart = () => {
    clearTimeout(timer);
    left = full;
    drain();
    run();
  };
  n.addEventListener("mouseenter", () => {
    clearTimeout(timer);
    left -= Date.now() - started;
  });
  n.addEventListener("mouseleave", run);
  x.addEventListener("click", (ev) => {
    ev.stopPropagation();
    leave();
  });
  if (from)
    n.addEventListener("click", () => {
      leave();
      opener(from.id);
    });
  drain();
  stack.append(n);
  run();
}
