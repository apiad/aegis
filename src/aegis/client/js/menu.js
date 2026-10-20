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
import { clampToRow, rangeText } from "./quote.js";

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
  const show = (el, ev, x, y, touch = false) => {
    const target = targetOf(el, root, entry.selector);
    const m = provider(target, ev);
    if (!m) return false;
    open(m, { x, y, touch }, target);
    return true;
  };
  const onMenu = (ev) => {
    if (ev.shiftKey || ev.target.closest(NATIVE)) return;
    ev.preventDefault();
    // A touch long press fires contextmenu too, late: the timer already
    // opened the menu, or the bar took its place (bar's check).
    if (swallow) return;
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
      swallow = show(el, ev, at[0], at[1], true);
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

// The open menu's row was redrawn as a new node (a streaming reply's patch):
// the menu stays, now about that node.
export function retarget(target) {
  if (cur) cur.target = target;
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
  unbar(); // one at a time: the bar's items go on top of a row's menu instead
  cur = { target, node, anchor: at.anchor ?? null, away, top: target.getBoundingClientRect().top, touch: !!at.touch };
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

// The selection bar: a non-empty selection inside one row's body (clampToRow)
// shows the provider's items over its first line, or under its last on a
// coarse pointer, where the system's handles and callout sit above. The text
// is the provider's to capture when the bar opens: a touch can clear the
// selection before a button's click lands. Its buttons keep the selection by
// cancelling their pointerdown. One bar at a time, and a menu opening closes
// it.
//
// `unit` says what a selection may span: by default one transcript row's body.
// A file's or a page's view passes its own, which answers {range, row} with
// `row` the element the range stays in, or null.
let bar_ = null; // {node, range, row, root}

function rowUnit(range, root) {
  const n = range.startContainer;
  const row = (n.nodeType === 1 ? n : n.parentElement)?.closest(".row[data-id]");
  const r = row && root.contains(row) ? clampToRow(range, row) : null;
  return r && { range: r, row };
}

const coarse = matchMedia("(pointer: coarse)");

export function bar(root, provider, { unit = rowUnit } = {}) {
  let down = false;
  let timer = null;
  // Several views have a bar: each closes only its own.
  const drop = () => bar_?.root === root && unbar();
  const check = () => {
    clearTimeout(timer);
    const sel = getSelection();
    const range = sel.rangeCount && !sel.isCollapsed ? sel.getRangeAt(0) : null;
    const start = range?.startContainer;
    if (!range || !root.contains(start)) return drop();
    const u = unit(range, root);
    const row = u?.row;
    // A right-click inside the selection: its menu carries the bar's items.
    // A touch's long press both opens the row's menu and selects a word; the
    // selection is what the person was after, so the bar takes its place.
    if (cur && !(cur.touch && row && cur.target.contains(row))) return;
    const r = u?.range;
    if (!r || !rangeText(r)) return drop();
    if (bar_ && bar_.range.toString() === r.toString() && bar_.row === row) {
      bar_.at = ends(range);
      return place();
    }
    const items = provider(r, row);
    if (!items?.length) return drop();
    showBar(items, r, row, root);
    bar_.at = ends(range);
  };
  // A key that moved the selection (Shift and arrows). Esc's keyup comes
  // after its keydown closed the bar: checking then would open it again.
  const onKey = (ev) => ev.key !== "Escape" && check();
  const onDown = () => (down = true);
  // A press that began in the root ends wherever it is let go: a drag that
  // selected a line and went on out of the transcript gets its bar too. The
  // check waits a task: a click inside a selection collapses it after pointerup.
  const onUp = () => {
    if (!down) return;
    down = false;
    setTimeout(check);
  };
  // While a finger or the mouse is down the selection is still being made;
  // a touch's handles change it with no pointerup on the root.
  const onChange = () => {
    // A selection that moved (a touch handle dragged) is not the one the bar
    // quotes: the bar goes at once, and only its reopening waits.
    if (bar_ && !same(getSelection(), bar_.at)) unbar();
    if (!getSelection().isCollapsed && !down) {
      clearTimeout(timer);
      timer = setTimeout(check, 200);
    } else if (getSelection().isCollapsed) drop();
  };
  root.addEventListener("pointerdown", onDown);
  document.addEventListener("pointerup", onUp, true);
  root.addEventListener("pointercancel", onUp); // a native long press cancels the pointer
  root.addEventListener("keyup", onKey);
  document.addEventListener("selectionchange", onChange);
  return () => {
    drop();
    root.removeEventListener("pointerdown", onDown);
    document.removeEventListener("pointerup", onUp, true);
    root.removeEventListener("pointercancel", onUp);
    root.removeEventListener("keyup", onKey);
    document.removeEventListener("selectionchange", onChange);
  };
}

// What a selection is, as values (getRangeAt's range is live): its text and
// its row's id. A row a streaming delta redraws gets new nodes but keeps both.
function ends(r) {
  const n = r.startContainer;
  return { text: r.toString(), row: (n.nodeType === 1 ? n : n.parentElement)?.closest(".row[data-id]")?.dataset.id };
}
function same(sel, at) {
  if (!sel.rangeCount) return false;
  const now = ends(sel.getRangeAt(0));
  return now.text === at.text && now.row === at.row;
}

function showBar(items, range, row, root) {
  close();
  unbar();
  const node = el("div", "sbar");
  node.setAttribute("role", "toolbar");
  for (const item of items) {
    const b = el("button", "btn");
    b.type = "button";
    b.dataset.id = item.id;
    b.title = item.title ?? item.label;
    b.setAttribute("aria-label", item.label);
    b.append(icon(item.icon), el("span", "lb", item.label));
    b.addEventListener("pointerdown", (ev) => ev.preventDefault());
    b.addEventListener("mousedown", (ev) => ev.preventDefault());
    b.addEventListener("click", (ev) => {
      unbar();
      item.run(ev);
    });
    node.append(b);
  }
  document.getElementById("a2").append(node);
  bar_ = { node, range, row, root };
  addEventListener("hashchange", unbar);
  place();
}

// Over the first line, or under the last on a coarse pointer, clamped to the
// viewport.
function place() {
  if (!bar_) return;
  const { node, range } = bar_;
  const rects = range.getClientRects();
  if (!rects.length) return unbar();
  const first = rects[0];
  const last = rects[rects.length - 1];
  const h = node.offsetHeight;
  const w = node.offsetWidth;
  const x = coarse.matches ? last.left : first.left;
  const y = coarse.matches ? last.bottom + 6 : first.top - h - 6;
  node.style.left = `${Math.max(4, Math.min(x, innerWidth - w - 4))}px`;
  node.style.top = `${Math.max(4, Math.min(y, innerHeight - h - 4))}px`;
}

// The bar of a row no longer drawn (its pane went behind another canvas tab):
// no scroll tells follow(), so the canvas calls this when it lays out.
export function unbarHidden() {
  // A tabbed canvas hides a pane with visibility, not display (base.css).
  // The row by its id: a streaming delta redraws it as a new node.
  if (!bar_) return;
  const row = bar_.row.isConnected ? bar_.row : document.querySelector(`.row[data-id="${CSS.escape(bar_.row.dataset.id)}"]`);
  if (!row?.checkVisibility({ visibilityProperty: true })) unbar();
}

// True when one was shown.
export function unbar() {
  if (!bar_) return false;
  bar_.node.remove();
  bar_ = null;
  removeEventListener("hashchange", unbar);
  return true;
}

// A scroll of `scroller`: one it made itself (ownScroll() true, a transcript
// following its bottom) moves the bar and an open menu with their anchor; one
// by the person closes them. A wheel, a touch's move and the scroll keys are
// the person's for sure.
const SCROLL_KEYS = new Set(["PageUp", "PageDown", "Home", "End", "ArrowUp", "ArrowDown", " "]);
export function follow(scroller, ownScroll) {
  const away = () => {
    unbar();
    if (cur && scroller.contains(cur.target)) close();
  };
  // An anchor the scroll took out of the scroller's view closes what it
  // anchors: a bar pinned over the tabs would point at nothing.
  scroller.addEventListener("scroll", () => {
    if (!ownScroll()) return away();
    const box = scroller.getBoundingClientRect();
    const out = (r) => !r || r.bottom <= box.top || r.top >= box.bottom;
    if (bar_ && scroller.contains(bar_.row)) {
      const rs = bar_.range.getClientRects();
      if (out(coarse.matches ? rs[rs.length - 1] : rs[0])) unbar();
      else place();
    }
    if (cur && scroller.contains(cur.target) && cur.target.isConnected) {
      const r = cur.target.getBoundingClientRect();
      if (out(r)) return close();
      cur.node.style.top = `${parseFloat(cur.node.style.top) + r.top - cur.top}px`;
      cur.top = r.top;
    }
  });
  scroller.addEventListener("wheel", away, { passive: true });
  scroller.addEventListener("touchmove", away, { passive: true });
  scroller.addEventListener("keydown", (ev) => SCROLL_KEYS.has(ev.key) && away());
}
