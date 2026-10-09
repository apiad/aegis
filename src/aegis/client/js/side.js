// The session panel's behaviour: the card each compact row opens, the panel's
// width, and collapsing it. What the rows say is app.js's; this file only moves
// them.
//
// A row (.prow) holds its own card (.pcard) in the markup, so the code that
// fills the panel fills the card too, and a card stays current while it is
// open. On a desktop the card is fixed to the left of the panel, level with its
// row, and opens on hover or focus. In the phone's drawer there is no hover:
// a tap opens the card under its row.

export const MIN_W = 200;
export const MAX_W = 480;
export const DEFAULT_W = 260;
const NARROW_W = 230; // under this the action buttons drop their labels
const W_KEY = "aegis.side.w";
const CLOSED_KEY = "aegis.side.closed";

const drawer = matchMedia("(max-width: 760px)");
const hover = matchMedia("(hover: hover)");

let root, side, grip;
let openEl = null;
let openT = null;
let closeT = null;
let onOpen = () => {};

export const collapsed = () => localStorage.getItem(CLOSED_KEY) === "1";

function setWidth(w, save = true) {
  w = Math.max(MIN_W, Math.min(MAX_W, Math.round(w)));
  root.style.setProperty("--side-w", `${w}px`);
  if (save) localStorage.setItem(W_KEY, String(w));
  return w;
}

// The desktop panel: shown, or collapsed out of the grid. The phone's drawer
// is data-side=open, and app.js closes it back to this state.
export function restState() {
  if (collapsed()) root.dataset.side = "closed";
  else delete root.dataset.side;
}

export function toggleCollapsed() {
  localStorage.setItem(CLOSED_KEY, collapsed() ? "0" : "1");
  closeCard();
  restState();
}

function place() {
  if (!openEl || drawer.matches) return;
  const card = openEl.querySelector(":scope > .pcard");
  const r = openEl.getBoundingClientRect();
  const s = side.getBoundingClientRect();
  if (!r.height) return closeCard();
  card.style.left = `${Math.max(8, s.left - card.offsetWidth - 12)}px`;
  card.style.top = `${Math.max(12, Math.min(r.top - 8, innerHeight - card.offsetHeight - 12))}px`;
}

function open(el) {
  clearTimeout(closeT);
  clearTimeout(openT);
  if (openEl === el) return;
  if (openEl) openEl.classList.remove("open");
  openEl = el;
  el.classList.add("open");
  onOpen();
  place();
}

// True when a card was open, for the Esc chain.
export function closeCard() {
  clearTimeout(openT);
  clearTimeout(closeT);
  if (!openEl) return false;
  openEl.classList.remove("open");
  openEl = null;
  return true;
}

function soonClose() {
  clearTimeout(openT);
  clearTimeout(closeT);
  closeT = setTimeout(closeCard, 160);
}

function wire(el) {
  el.addEventListener("mouseenter", () => {
    if (!hover.matches || drawer.matches) return;
    clearTimeout(closeT);
    clearTimeout(openT);
    openT = setTimeout(() => open(el), openEl ? 0 : 140);
  });
  el.addEventListener("mouseleave", () => {
    if (hover.matches && !drawer.matches) soonClose();
  });
  // Keyboard: the row takes focus and its card follows it.
  // Only keyboard focus: a click or a tap focuses the row too, and the tap's
  // own click would close what its focus just opened.
  el.addEventListener("focusin", (ev) => {
    if (ev.target === el && el.matches(":focus-visible")) open(el);
  });
  el.addEventListener("focusout", (ev) => {
    if (!el.contains(ev.relatedTarget)) soonClose();
  });
  // Touch, or the drawer: a tap on the row toggles its card. A tap inside the
  // card, or on a name being renamed, is the card's own.
  el.addEventListener("click", (ev) => {
    if (hover.matches && !drawer.matches) return;
    if (ev.target.closest(".pcard, .editable, input")) return;
    if (openEl === el) closeCard();
    else open(el);
  });
}

export function initSide({ onCardOpen } = {}) {
  root = document.getElementById("a2");
  side = document.getElementById("side");
  grip = document.getElementById("side-grip");
  if (onCardOpen) onOpen = onCardOpen;
  for (const el of side.querySelectorAll(".prow")) wire(el);
  setWidth(Number(localStorage.getItem(W_KEY)) || DEFAULT_W, false);
  restState();

  new ResizeObserver(() => {
    side.classList.toggle("narrow", side.clientWidth > 0 && side.clientWidth < NARROW_W);
    place();
  }).observe(side);
  side.addEventListener("scroll", place);
  addEventListener("resize", place);
  // A card that grows (a quota reading lands) stays inside the window.
  const cards = new ResizeObserver(place);
  for (const c of side.querySelectorAll(".pcard")) cards.observe(c);

  grip.addEventListener("pointerdown", (ev) => {
    if (ev.button !== 0) return;
    ev.preventDefault();
    grip.setPointerCapture(ev.pointerId);
    root.dataset.resizing = "";
    closeCard();
    const right = side.getBoundingClientRect().right;
    const move = (e) => setWidth(right - e.clientX);
    const up = () => {
      delete root.dataset.resizing;
      grip.removeEventListener("pointermove", move);
      grip.removeEventListener("pointerup", up);
      grip.removeEventListener("pointercancel", up);
    };
    grip.addEventListener("pointermove", move);
    grip.addEventListener("pointerup", up);
    grip.addEventListener("pointercancel", up);
  });
  grip.addEventListener("dblclick", () => setWidth(DEFAULT_W));
}
