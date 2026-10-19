// The one context menu (#479). A view attaches a provider to a root element;
// the provider turns the element pressed into a menu, {title?, groups}, or
// null to leave the browser its own. One menu is open at a time across the
// page, so opening one closes the other.
//
// It opens on a right-click (not with Shift, and not inside a text field, a
// terminal or an iframe, where spell-check, paste and inspect live), on a touch
// long press, and from the keyboard through openFor (keys.js's menuKey). It
// closes on a choice, Esc, a press outside it, focus leaving it and a change
// of view. A choice closes the menu before its run(), so an action that opens a
// dialog or moves focus is not fought by the menu's own focus handling.
//
// The menu's only key listener is on its own node: keys.js owns the page's.

import { icon } from "./glyphs.js";

export const LONG_PRESS = 500; // ms a touch holds before the menu opens
const SLOP = 8; // px a held touch may move and still open it
const NATIVE = "input, textarea, [contenteditable], iframe, .xterm";

const roots = new Map(); // root -> {provider, selector}
let cur = null; // {target, node, anchor, away}

// The element a press on `el` inside `root` is about: the nearest match of
// the root's selector, else the root; without a selector, `el` itself.
function targetOf(el, root, selector) {
  if (!selector) return el;
  return el.closest(selector) ?? root;
}

export function attach(root, provider, opts = {}) {
  const entry = { provider, selector: opts.selector };
  roots.set(root, entry);
  let timer = null;
  let at = null;
  let swallow = false; // a long press opened the menu: the mousedown and click after it do nothing
  const stop = () => {
    clearTimeout(timer);
    timer = null;
  };
  const show = (el, ev, x, y) => {
    const target = targetOf(el, root, entry.selector);
    const m = provider(target, ev);
    if (!m) return false;
    open(m, { x, y }, target);
    return true;
  };
  const onMenu = (ev) => {
    if (ev.shiftKey || ev.target.closest(NATIVE)) return;
    ev.preventDefault();
    // A touch long press can fire contextmenu too: the timer already opened it.
    if (swallow && cur) return;
    show(ev.target, ev, ev.clientX, ev.clientY);
  };
  const onDown = (ev) => {
    swallow = false;
    if (ev.pointerType === "mouse" || ev.target.closest(NATIVE)) return;
    at = [ev.clientX, ev.clientY];
    const el = ev.target;
    stop();
    timer = setTimeout(() => {
      timer = null;
      swallow = show(el, ev, at[0], at[1]);
    }, LONG_PRESS);
  };
  const onMove = (ev) => {
    if (timer && Math.hypot(ev.clientX - at[0], ev.clientY - at[1]) > SLOP) stop();
  };
  // The release's mousedown would focus what is under the finger, which
  // closes the menu through its focusout.
  const onMouseDown = (ev) => {
    if (swallow) ev.preventDefault();
  };
  const onClick = (ev) => {
    if (!swallow) return;
    swallow = false;
    ev.preventDefault();
    ev.stopPropagation();
  };
  root.addEventListener("contextmenu", onMenu);
  root.addEventListener("pointerdown", onDown);
  root.addEventListener("pointermove", onMove);
  root.addEventListener("pointerup", stop);
  root.addEventListener("pointercancel", stop);
  root.addEventListener("mousedown", onMouseDown, true);
  root.addEventListener("click", onClick, true);
  return () => {
    stop();
    roots.delete(root);
    root.removeEventListener("contextmenu", onMenu);
    root.removeEventListener("pointerdown", onDown);
    root.removeEventListener("pointermove", onMove);
    root.removeEventListener("pointerup", stop);
    root.removeEventListener("pointercancel", stop);
    root.removeEventListener("mousedown", onMouseDown, true);
    root.removeEventListener("click", onClick, true);
  };
}

// The keyboard's way in: the menu of the root `el` is in, placed under it.
export function openFor(el) {
  if (!el) return false;
  for (const [root, { provider, selector }] of roots) {
    if (!root.contains(el)) continue;
    const target = targetOf(el, root, selector);
    const m = provider(target, null);
    if (!m) return false;
    open(m, { anchor: target }, target);
    return true;
  }
  return false;
}

export function current() {
  return cur && { target: cur.target, node: cur.node };
}

// True when one was open (Esc closes it before it interrupts).
export function close() {
  if (!cur) return false;
  const { node, away } = cur;
  cur = null; // first: removing the focused node fires its focusout
  document.removeEventListener("pointerdown", away, true);
  removeEventListener("hashchange", close);
  node.remove();
  return true;
}

function refocus(target) {
  if (target?.isConnected) target.focus({ preventScroll: true });
}

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
}

// `at` is a point (a right-click, a long press) or an anchor to drop under.
export function open(menu, at, target) {
  close();
  const node = el("div", "cmenu");
  node.setAttribute("role", "menu");
  if (menu.title) node.append(el("div", "head", menu.title));
  menu.groups
    .filter((g) => g.length)
    .forEach((g, i) => {
      if (i) node.append(el("div", "grp"));
      for (const item of g) {
        const b = el("button", `btn${item.danger ? " danger" : ""}`);
        b.type = "button";
        b.dataset.id = item.id;
        b.setAttribute("role", "menuitem");
        b.append(icon(item.icon), el("span", "lb", item.label));
        if (item.key) b.append(el("span", "k", item.key));
        if (item.disabled) {
          b.disabled = true;
          b.title = item.disabled;
        }
        b.addEventListener("click", () => {
          close();
          refocus(target);
          item.run();
        });
        node.append(b);
      }
    });
  document.getElementById("a2").append(node);
  const w = node.offsetWidth;
  const h = node.offsetHeight;
  let x = at.x;
  let y = at.y;
  if (at.anchor) {
    const r = at.anchor.getBoundingClientRect();
    x = r.left;
    y = r.bottom + 4;
  }
  node.style.left = `${Math.max(4, Math.min(x, innerWidth - w - 4))}px`;
  node.style.top = `${Math.max(4, Math.min(y, innerHeight - h - 4))}px`;
  // A press anywhere else closes it; a press on its anchor is the anchor's
  // (a button that opened it closes it again).
  const away = (ev) => {
    if (node.contains(ev.target) || at.anchor?.contains(ev.target)) return;
    if (cur?.node === node) close();
  };
  document.addEventListener("pointerdown", away, true);
  addEventListener("hashchange", close);
  cur = { target, node, anchor: at.anchor ?? null, away };
  const items = () => [...node.querySelectorAll("[role=menuitem]:not(:disabled)")];
  node.addEventListener("keydown", (ev) => {
    const all = items();
    const i = all.indexOf(document.activeElement);
    let next = null;
    if (ev.key === "ArrowDown") next = all[(i + 1) % all.length];
    else if (ev.key === "ArrowUp") next = all[(i - 1 + all.length) % all.length];
    else if (ev.key === "Home") next = all[0];
    else if (ev.key === "End") next = all.at(-1);
    else if (ev.key === "Escape") {
      ev.preventDefault();
      ev.stopPropagation();
      close();
      refocus(target);
      return;
    } else return;
    ev.preventDefault();
    next?.focus();
  });
  // A press inside keeps the focus where it is: on the padding or a rule it
  // would go to the body and close the menu (a touch's release lands a
  // mousedown there when the menu opened under the finger).
  node.addEventListener("mousedown", (ev) => ev.preventDefault());
  node.addEventListener("focusout", (ev) => {
    if (!node.contains(ev.relatedTarget) && cur?.node === node) close();
  });
  items()[0]?.focus({ preventScroll: true });
}
