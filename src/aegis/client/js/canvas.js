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

import { drawArtifact, drawFile } from "./viewer.js";
import { fileUrl, hold } from "./entries.js";

const TRANSCRIPT = "transcript";
// The canvas with nothing docked: the transcript alone (layout.DEFAULT).
const DEFAULT = { windows: [{ n: 1, columns: [{ w: 1, panes: [{ id: TRANSCRIPT, h: 1 }] }] }] };

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined && text !== null) n.textContent = text;
  return n;
}

export class Canvas {
  constructor(root, { transcriptPane, entryOf, latestFile, call, onChange }) {
    this.root = root; // #canvas
    this.tr = transcriptPane; // #tr-pane
    this.entryOf = entryOf; // (id) -> the shown transcript's entry, or undefined
    this.latestFile = latestFile; // (key) -> the newest sent file with that key, or undefined
    this.call = call; // (op, params) -> Promise, on the shown session
    this.onChange = onChange; // (before, after): the docked ids changed
    this.key = null; // the session shown
    this.n = 1; // the window this tab draws
    this.layout = null; // the card's layout; null is the default
    this.writable = false;
    this.panes = new Map(); // pane id -> its element, for the shown session
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
        Object.assign(node.style, {
          left: `${x * 100}%`,
          top: `${y * 100}%`,
          width: `${col.w * 100}%`,
          height: `${p.h * 100}%`,
          right: "auto",
          bottom: "auto",
        });
        // A rule on the edges a neighbour shares, drawn by the pane right or below.
        node.classList.toggle("lx", x > 0.0005);
        node.classList.toggle("ty", y > 0.0005);
        y += p.h;
      }
      x += col.w;
    }
    this.root.dataset.docked = String(keep.size);
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
  // and when it changes (a resend, a new status, a new file version).
  draw(id, e) {
    const node = this.panes.get(id);
    if (!node) return;
    const bar = el("div", "fbar pbar");
    const body = el("div", "pbody");
    body.tabIndex = -1;
    const acts = el("span", "acts");
    node.replaceChildren(bar, body);
    delete node.dataset.loading;
    if (id.startsWith("art-")) {
      const entry = e || this.entryOf(id);
      if (!entry) return this.loading(node, bar, "Loading the page…");
      drawArtifact(entry, { bar, body, acts });
      // An ended page leaves the layout with its submit; until that card
      // lands it shows read-only, and its card keeps its own line.
      if (entry.status === "live") hold(id, (next) => this.onEntry(next), entry, "docked");
      else hold(id, null, entry);
    } else {
      const f = this.latestFile(id);
      if (!f) return this.loading(node, bar, "Loading the file…");
      drawFile(f, { remote: false, glyph: "file", bar, body, acts });
    }
    bar.append(acts);
  }

  // A pane whose entry this browser does not have yet: the layout came on the
  // sessions channel before the transcript's snapshot. refresh() fills it.
  loading(node, bar, text) {
    node.dataset.loading = "";
    bar.append(el("span", "fn", text));
  }

  // The transcript's entries arrived or changed in bulk (a snapshot, a
  // restore): fill the panes that were waiting for theirs.
  refresh() {
    for (const [id, node] of this.panes) if ("loading" in node.dataset) this.draw(id);
  }

  // An entry was drawn again. A pane redraws only when what it shows changed:
  // a new page (src) or a new status. Files: task 10.
  onEntry(e) {
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

  // No session, or another one: every pane goes and the transcript fills the
  // canvas again.
  clear() {
    for (const [id, node] of this.panes) this.drop(id, node);
    this.key = null;
    this.layout = null;
    for (const k of ["left", "top", "width", "height", "right", "bottom"]) this.tr.style[k] = "";
    this.tr.classList.remove("lx", "ty");
    this.root.dataset.docked = "0";
  }
}
