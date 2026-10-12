// The canvas: the session's docked pages and files beside the transcript, in
// columns of stacked panes (layout.py on the server says what may go where).
//
// Every pane of the shown window is a child of one container, placed with
// percentages computed from the layout. A rearrangement only changes those
// numbers: moving an iframe in the DOM reloads its page, so a pane's element is
// created once and never re-parented. A tab switch rebuilds them, as the viewer
// rebuilds its item, and each page starts again from the server's state.
//
// The transcript's pane is the session view's own column (#tr-pane), placed
// like the others and never recreated, so the transcript, the navigator, the
// composer and the viewer inside it keep working whatever the layout.
//
// A page runs in one place: a docked artifact's card holds a line
// (entries.hold), and the agent's state goes to the pane's frame.
//
// The person's moves are sent as what they did (layout.dock, layout.undock,
// layout.resize) when the pointer is released, never while it moves, and the
// server applies each to the layout as it stands then: a drop does not undo
// what the agent docked while the pointer was moving. The card that follows
// redraws every browser on the session, this one included.

import { drawArtifact, drawFile } from "./viewer.js";
import { DOCKED, fileUrl, hold } from "./entries.js";
import { icon } from "./glyphs.js";

const TRANSCRIPT = "transcript";
// The canvas with nothing docked: the transcript alone (layout.DEFAULT).
const DEFAULT = { windows: [{ n: 1, columns: [{ w: 1, panes: [{ id: TRANSCRIPT, h: 1 }] }] }] };
// layout.py's limits, and the spec's minimums a grip stops at.
const MAX_COLUMNS = 4;
const MAX_PANES = 3;
const MIN_W = 280;
const MIN_W_TRANSCRIPT = 360;
const MIN_H = 120;

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined && text !== null) n.textContent = text;
  return n;
}

const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
const minWidth = (col) => (col.panes.some((p) => p.id === TRANSCRIPT) ? MIN_W_TRANSCRIPT : MIN_W);

export class Canvas {
  constructor(root, { transcriptPane, entryOf, latestFile, call, onChange, refuse }) {
    this.root = root; // #canvas
    this.tr = transcriptPane; // #tr-pane
    this.entryOf = entryOf; // (id) -> the shown transcript's entry, or undefined
    this.latestFile = latestFile; // (key) -> the newest sent file with that key, or undefined
    this.call = call; // (op, params) -> Promise, on the shown session
    this.onChange = onChange; // (before, after): the docked ids changed
    this.refuse = refuse; // (message): a refusal the person should read
    this.key = null; // the session shown
    this.n = 1; // the window this tab draws
    this.layout = null; // the card's layout; null is the default
    this.writable = false;
    this.panes = new Map(); // pane id -> its element, for the shown session
    this.grips = new Map(); // "x<i>" or "y<i>.<j>" -> its element
    // The transcript's handle: a thin bar over its top edge, since its own
    // column has no bar to drag by. It has no ×: the transcript always stays.
    this.trBar = el("div", "pbar tr-handle");
    this.trBar.title = "Drag to move the transcript";
    this.target = el("div", "droptarget");
    root.addEventListener("pointerdown", (ev) => {
      if (ev.target.closest(".cgrip")) this.resizeFrom(ev);
      else if (ev.target.closest(".pbar")) this.grab(ev);
    });
  }

  // Draw window n of a session: on every tab switch, and on every card of the
  // shown session, so a change from the agent or another browser lands here.
  show(key, layout, { n = 1, writable = false } = {}) {
    const before = this.ids();
    if (key !== this.key || n !== this.n) this.clear();
    this.key = key;
    this.n = n;
    this.writable = writable;
    this.layout = layout || null;
    this.place();
    const after = this.ids();
    if (before.length !== after.length || before.some((id, i) => id !== after[i])) this.onChange(before, after);
  }

  window() {
    const ws = (this.layout || DEFAULT).windows;
    return ws.find((w) => w.n === this.n) || { n: this.n, columns: [] };
  }

  // What is docked in the shown window, the transcript left out.
  ids() {
    if (this.key === null) return [];
    return this.window()
      .columns.flatMap((c) => c.panes.map((p) => p.id))
      .filter((id) => id !== TRANSCRIPT);
  }

  has(id) {
    return this.panes.has(id);
  }

  // Each pane's box from the shares. Panes no longer in the window go, and
  // their pages go back to their cards.
  place() {
    const keep = new Set(this.ids());
    for (const [id, node] of this.panes) {
      if (keep.has(id)) continue;
      this.drop(id, node);
    }
    let x = 0;
    for (const col of this.window().columns) {
      let y = 0;
      for (const p of col.panes) {
        const node = p.id === TRANSCRIPT ? this.tr : this.pane(p.id);
        this.box(node, x, y, col.w, p.h);
        // A rule on the edges a neighbour shares, drawn by the pane right or below.
        node.classList.toggle("lx", x > 0.0005);
        node.classList.toggle("ty", y > 0.0005);
        if (p.id === TRANSCRIPT) this.box(this.trBar, x, y, col.w, null);
        y += p.h;
      }
      x += col.w;
    }
    this.root.dataset.docked = String(keep.size);
    if (this.writable) this.root.dataset.writable = "";
    else delete this.root.dataset.writable;
    // Handles only where there is something to arrange, for someone who may.
    if (keep.size && this.writable) {
      if (!this.trBar.isConnected) this.root.append(this.trBar);
    } else this.trBar.remove();
    this.placeGrips(keep.size && this.writable);
  }

  box(node, x, y, w, h) {
    Object.assign(node.style, {
      left: `${x * 100}%`,
      top: `${y * 100}%`,
      width: `${w * 100}%`,
      height: h === null ? "" : `${h * 100}%`,
      right: "auto",
      bottom: "auto",
    });
  }

  // One grip on each boundary between columns, and one between each two
  // stacked panes. Kept by key across redraws, so the grip being dragged keeps
  // its pointer while the panes around it move.
  placeGrips(on) {
    const want = new Map();
    if (on) {
      let x = 0;
      this.window().columns.forEach((c, i) => {
        if (i > 0) want.set(`x${i}`, { i, j: null, x, y: 0, w: null });
        let y = 0;
        c.panes.forEach((p, j) => {
          if (j > 0) want.set(`y${i}.${j}`, { i, j, x, y, w: c.w });
          y += p.h;
        });
        x += c.w;
      });
    }
    for (const [k, node] of this.grips) {
      if (want.has(k)) continue;
      node.remove();
      this.grips.delete(k);
    }
    for (const [k, g] of want) {
      let node = this.grips.get(k);
      if (!node) {
        node = el("div", `cgrip ${g.j === null ? "x" : "y"}`);
        node.title = "Drag to resize";
        this.root.append(node);
        this.grips.set(k, node);
      }
      node.dataset.i = String(g.i);
      node.dataset.j = g.j === null ? "" : String(g.j);
      node.style.left = `${g.x * 100}%`;
      node.style.top = `${g.y * 100}%`;
      if (g.w !== null) node.style.width = `${g.w * 100}%`;
    }
  }

  pane(id) {
    let node = this.panes.get(id);
    if (node) return node;
    node = el("section", "pane docked");
    node.dataset.pane = id;
    this.root.append(node);
    this.panes.set(id, node);
    this.draw(id);
    return node;
  }

  drop(id, node) {
    node.remove();
    this.panes.delete(id);
    if (id.startsWith("art-")) hold(id, null, this.entryOf(id));
  }

  // (Re)draw one pane's contents: on creation, when its entry first arrives,
  // and when it changes (a resend, a new status, a new file version). The bar
  // is the item's own (its glyph, title and buttons, as the viewer draws them)
  // and ×, and it is the handle the pane is dragged by. `e` is the artifact's
  // entry, or the file to show; by default the newest the browser has.
  draw(id, e) {
    const node = this.panes.get(id);
    if (!node) return;
    const drawn = (node._drawn = (node._drawn || 0) + 1); // a late fetch for an older draw draws nothing
    const bar = el("div", "fbar pbar");
    const body = el("div", "pbody");
    body.tabIndex = -1;
    const acts = el("span", "acts");
    node.replaceChildren(bar, body);
    delete node.dataset.loading;
    if (id.startsWith("art-")) {
      const entry = e || this.entryOf(id);
      if (!entry) return this.loading(node, bar, acts, "Loading the page…");
      drawArtifact(entry, { bar, body, acts });
      // An ended page leaves the layout with its submit; until that card
      // lands it shows read-only, and its card keeps its own line.
      if (entry.status === "live") hold(id, (next) => this.onEntry(next), entry, DOCKED);
      else hold(id, null, entry);
    } else {
      const f = e || this.latestFile(id);
      if (!f) return this.loading(node, bar, acts, "Loading the file…");
      drawFile(f, { remote: false, glyph: "file", bar, body, acts, isCurrent: () => node._drawn === drawn });
      bar.append(el("span", "ver", `v${f.version}`));
      node.dataset.version = String(f.version);
    }
    acts.append(this.undockButton(id));
    bar.append(acts);
  }

  undockButton(id) {
    const b = el("button", "btn icon undock");
    b.type = "button";
    b.append(icon("close"));
    b.title = "Undock: back to its card in the transcript";
    b.setAttribute("aria-label", "Undock");
    b.addEventListener("click", () => this.undock(id));
    return b;
  }

  // A pane whose entry this browser does not have yet: the layout came on the
  // sessions channel before the transcript's snapshot. refresh() fills it.
  loading(node, bar, acts, text) {
    node.dataset.loading = "";
    acts.append(this.undockButton(node.dataset.pane));
    bar.append(el("span", "fn", text), acts);
  }

  // The transcript's entries arrived or changed in bulk (a snapshot, a
  // restore): fill the panes that were waiting for theirs, and show a file's
  // newest version if one came with them.
  refresh() {
    for (const [id, node] of this.panes) {
      if ("loading" in node.dataset) this.draw(id);
      else if (!id.startsWith("art-")) {
        const f = this.latestFile(id);
        if (f && String(f.version) !== node.dataset.version) this.draw(id, f);
      }
    }
  }

  // An entry was drawn again. A pane redraws only when what it shows changed:
  // a new page (src) or a new status; a file pane, when a newer version of its
  // key lands, in place (a frame or an image gets the new URL, text is fetched
  // again).
  onEntry(e) {
    if (e.kind === "file") {
      for (const f of e.detail?.files || []) {
        const node = f.key && !f.replaced && this.panes.get(f.key);
        if (node && ("loading" in node.dataset || node.dataset.version !== String(f.version))) this.draw(f.key, f);
      }
      return;
    }
    const node = this.panes.get(e.id);
    if (!node || e.kind !== "artifact") return;
    if ("loading" in node.dataset) return this.draw(e.id, e);
    const frame = node.querySelector("iframe[data-artifact]");
    if (frame && frame.dataset.status === e.status && frame.getAttribute("src") === fileUrl(e.detail?.url)) return;
    this.draw(e.id, e);
  }

  // Outline a docked pane for a moment; false when it is not in this window.
  flash(id) {
    const node = this.panes.get(id);
    if (!node) return false;
    node.classList.remove("flash");
    void node.offsetWidth; // restart the outline when it is still on
    node.classList.add("flash");
    setTimeout(() => node.classList.remove("flash"), 900);
    return true;
  }

  // -- the person's moves --------------------------------------------------

  // A Dock button: a new column at the right of window 1, or under the last
  // pane once there are four columns. A full canvas says why and does nothing.
  dock(id) {
    if (this.flash(id)) return;
    const w1 = (this.layout || DEFAULT).windows.find((w) => w.n === 1);
    let where = "right";
    if (w1.columns.length >= MAX_COLUMNS) {
      const last = w1.columns[w1.columns.length - 1];
      if (last.panes.length >= MAX_PANES) {
        this.refuse(`The canvas is full: ${MAX_COLUMNS} columns, and the last holds ${MAX_PANES} panes. Undock one first.`);
        return;
      }
      where = `below:${last.panes[last.panes.length - 1].id}`;
    }
    this.move(id, where);
  }

  move(id, where) {
    return this.call("layout.dock", { pane: id, where }).catch((err) => this.refuse(err.message || String(err)));
  }

  undock(id) {
    return this.call("layout.undock", { pane: id }).catch((err) => this.refuse(err.message || String(err)));
  }

  // Drag a pane by its bar. While the pointer moves, the pane under it shows
  // where the drop goes: its left or right fifth makes a column on that side,
  // its top or bottom third stacks above or below it, its middle swaps the
  // two. Frames take no pointer meanwhile (.dragging), or they would swallow it.
  grab(ev) {
    if (!this.writable || ev.button !== 0 || ev.target.closest("button, a, input")) return;
    const bar = ev.target.closest(".pbar");
    const id = bar === this.trBar ? TRANSCRIPT : bar.closest(".pane.docked")?.dataset.pane;
    if (!id) return;
    ev.preventDefault();
    bar.setPointerCapture(ev.pointerId);
    this.root.classList.add("dragging");
    let where = null;
    const move = (e) => {
      where = this.aim(id, e.clientX, e.clientY);
    };
    const end = (e) => {
      for (const [t, f] of on) bar.removeEventListener(t, f);
      this.root.classList.remove("dragging");
      this.target.remove();
      if (e.type === "pointerup" && where) this.move(id, where);
    };
    const on = [
      ["pointermove", move],
      ["pointerup", end],
      ["pointercancel", end],
      ["lostpointercapture", end],
    ];
    for (const [t, f] of on) bar.addEventListener(t, f);
  }

  // The drop at a point while dragging `id`, drawn as .droptarget, or null.
  aim(id, x, y) {
    const box = this.root.getBoundingClientRect();
    for (const [pid, node] of [[TRANSCRIPT, this.tr], ...this.panes]) {
      if (pid === id) continue;
      const r = node.getBoundingClientRect();
      if (x < r.left || x >= r.right || y < r.top || y >= r.bottom) continue;
      const fx = (x - r.left) / r.width;
      const fy = (y - r.top) / r.height;
      // [where, and the part of the pane the drop would take]
      const [side, l, t, w, h] =
        fx < 0.2 ? ["left", 0, 0, 0.5, 1]
        : fx > 0.8 ? ["right", 0.5, 0, 0.5, 1]
        : fy < 1 / 3 ? ["above", 0, 0, 1, 0.5]
        : fy > 2 / 3 ? ["below", 0, 0.5, 1, 0.5]
        : ["swap", 0, 0, 1, 1];
      Object.assign(this.target.style, {
        left: `${r.left - box.left + l * r.width}px`,
        top: `${r.top - box.top + t * r.height}px`,
        width: `${w * r.width}px`,
        height: `${h * r.height}px`,
      });
      this.target.dataset.where = side;
      if (!this.target.isConnected) this.root.append(this.target);
      return `${side}:${pid}`;
    }
    this.target.remove();
    return null;
  }

  // Drag a grip: the boundary moves here at once, stopped at the minimums
  // (a column 280 px wide, the transcript's 360, a pane 120 px high), and the
  // shares are sent once, on release.
  resizeFrom(ev) {
    if (!this.writable || ev.button !== 0) return;
    const grip = ev.target.closest(".cgrip");
    ev.preventDefault();
    grip.setPointerCapture(ev.pointerId);
    grip.classList.add("on");
    this.root.classList.add("dragging");
    const lay = structuredClone(this.layout || DEFAULT);
    const win = lay.windows.find((w) => w.n === this.n);
    const i = Number(grip.dataset.i);
    const j = grip.dataset.j === "" ? null : Number(grip.dataset.j);
    const box = this.root.getBoundingClientRect();
    let moved = false;
    const move = (e) => {
      if (j === null) {
        const [a, b] = [win.columns[i - 1], win.columns[i]];
        const start = win.columns.slice(0, i - 1).reduce((s, c) => s + c.w, 0);
        const pair = a.w + b.w;
        const lo = minWidth(a) / box.width;
        const hi = pair - minWidth(b) / box.width;
        if (lo > hi) return; // too narrow for both minimums: leave it
        a.w = clamp((e.clientX - box.left) / box.width - start, lo, hi);
        b.w = pair - a.w;
      } else {
        const panes = win.columns[i].panes;
        const [a, b] = [panes[j - 1], panes[j]];
        const start = panes.slice(0, j - 1).reduce((s, p) => s + p.h, 0);
        const pair = a.h + b.h;
        const lo = MIN_H / box.height;
        const hi = pair - MIN_H / box.height;
        if (lo > hi) return;
        a.h = clamp((e.clientY - box.top) / box.height - start, lo, hi);
        b.h = pair - a.h;
      }
      moved = true;
      this.layout = lay;
      this.place();
    };
    const end = (e) => {
      for (const [t, f] of on) grip.removeEventListener(t, f);
      grip.classList.remove("on");
      this.root.classList.remove("dragging");
      if (e.type !== "pointerup" || !moved) return;
      const widths = win.columns.map((c) => c.w);
      const heights = win.columns.map((c) => c.panes.map((p) => p.h));
      this.call("layout.resize", { n: this.n, widths, heights }).catch((err) => this.refuse(err.message || String(err)));
    };
    const on = [
      ["pointermove", move],
      ["pointerup", end],
      ["pointercancel", end],
      ["lostpointercapture", end],
    ];
    for (const [t, f] of on) grip.addEventListener(t, f);
  }

  // No session, or another one: every pane goes and the transcript fills the
  // canvas again.
  clear() {
    for (const [id, node] of this.panes) this.drop(id, node);
    this.key = null;
    this.layout = null;
    for (const k of ["left", "top", "width", "height", "right", "bottom"]) this.tr.style[k] = "";
    this.tr.classList.remove("lx", "ty");
    this.trBar.remove();
    this.target.remove();
    this.placeGrips(false);
    this.root.dataset.docked = "0";
  }
}
