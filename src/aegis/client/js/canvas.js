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
//
// A column whose share is under its minimum width is drawn at the minimum,
// and the columns above theirs give up the difference; the stored shares do
// not change. On a phone (a touch screen under the client's 760 px drawer
// breakpoint), and whenever the minimums added up are wider than the canvas,
// the canvas draws as tabs: a strip over one pane at a time, the others kept
// running behind it (visibility, so a page's frame is never reloaded). The
// layout is not changed, so a wider window draws the same columns again. The
// composer stays at the foot of the transcript's pane, which fills the canvas
// under every tab.
//
// A canvas can span browser windows. Window 1 is the session's tab; window n
// is a tab at #w=<n>:<key> that draws window n alone, with no transcript.
// Each tab draws only its own window, so a page still runs in one place: the
// card of an item in another window holds "in window n · open" instead of its
// frame. A phone has one window, so on a phone window 1 draws every window's
// panes as its tabs, window 1's first.
//
// Window 1 shows a chip for every other window in the layout, always: this
// browser cannot know whether another one shows it. A pane moves between
// windows by Move to window on its bar, and Pop out moves it to a new one.
// Either opens the window's tab in the click itself, before the call: a
// browser lets a click open a tab only for a few seconds (Chrome) or not at
// all after an await (Safari).

import { drawArtifact, drawFile } from "./viewer.js";
import { DOCKED, fileUrl, hold, inWindow, popButton } from "./entries.js";
import { icon } from "./glyphs.js";

const TRANSCRIPT = "transcript";
// The canvas with nothing docked: the transcript alone (layout.DEFAULT).
const DEFAULT = { windows: [{ n: 1, columns: [{ w: 1, panes: [{ id: TRANSCRIPT, h: 1 }] }] }] };
// layout.py's limits, and the spec's minimums a grip stops at.
const MAX_COLUMNS = 4;
const MAX_PANES = 3;
const MAX_WINDOWS = 4;
const MIN_W = 280;
const MIN_W_TRANSCRIPT = 360;
const MIN_H = 120;
// A tab held this long offers Undock.
const LONG_PRESS = 500;

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined && text !== null) n.textContent = text;
  return n;
}

const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
const minWidth = (col) => (col.panes.some((p) => p.id === TRANSCRIPT) ? MIN_W_TRANSCRIPT : MIN_W);

// The columns' shares as drawn in `width` px: each under its minimum raised
// to it, and the difference taken from the columns above theirs, in
// proportion to how far above they are. When the minimums do not fit, the
// shares as they are (the canvas is tabs then).
function fit(cols, width) {
  const ws = cols.map((c) => c.w);
  if (!width) return ws;
  const mins = cols.map((c) => minWidth(c) / width);
  const need = ws.reduce((s, w, i) => s + Math.max(0, mins[i] - w), 0);
  const spare = ws.reduce((s, w, i) => s + Math.max(0, w - mins[i]), 0);
  if (need <= 0 || spare < need) return ws;
  return ws.map((w, i) => (w < mins[i] ? mins[i] : w - ((w - mins[i]) * need) / spare));
}

export class Canvas {
  constructor(root, { transcriptPane, entryOf, latestFile, call, onChange, refuse, openWindow }) {
    this.root = root; // #canvas
    this.tr = transcriptPane; // #tr-pane
    this.entryOf = entryOf; // (id) -> the shown transcript's entry, or undefined
    this.latestFile = latestFile; // (key) -> the newest sent file with that key, or undefined
    this.call = call; // (op, params) -> Promise, on the shown session
    this.onChange = onChange; // (before, after): the docked ids changed
    this.refuse = refuse; // (message): a refusal the person should read
    this.openWindow = openWindow; // (n, tab?) -> the tab of window n, focused or opened; or `tab` pointed at it
    this.key = null; // the session shown
    this.n = 1; // the window this tab draws
    this.layout = null; // the card's layout; null is the default
    this.preview = null; // while a grip moves: (window) => void, its shares put on a copy
    this.dragged = null; // while something is dragged: {alive, end}
    this.writable = false;
    this.panes = new Map(); // pane id -> its element, for the shown session
    this.away = new Map(); // a page in another window -> that window's number, held on its card
    this.grips = new Map(); // "x<i>" or "y<i>.<j>" -> its element
    // The transcript's handle: a thin bar over its top edge, since its own
    // column has no bar to drag by. It has no ×: the transcript always stays.
    this.trBar = el("div", "pbar tr-handle");
    this.trBar.title = "Drag to move the transcript";
    this.target = el("div", "droptarget");
    this.chips = el("div", "wchips"); // window 1's chips, one per other window
    this.chipSig = "";
    root.addEventListener("pointerdown", (ev) => {
      if (ev.target.closest(".cgrip")) this.resizeFrom(ev);
      else if (ev.target.closest(".pbar")) this.grab(ev);
    });
    // Tab mode: the strip, the tab open, and the tabs whose pane changed
    // while another was open.
    this.tabbed = false;
    this.open = TRANSCRIPT;
    this.dots = new Set();
    this.tabs = new Map(); // pane id -> its tab
    this.strip = el("div", "ptabs");
    this.strip.setAttribute("role", "tablist");
    this.menu = null; // a long press's menu: {id, node}
    this.pressed = false; // a long press just opened the menu: its click opens no tab
    this.strip.addEventListener("click", (ev) => {
      const b = ev.target.closest("button[data-pane]");
      if (!b) return;
      if (this.pressed) this.pressed = false;
      else this.openTab(b.dataset.pane);
    });
    this.strip.addEventListener("pointerdown", (ev) => this.press(ev));
    this.strip.addEventListener("contextmenu", (ev) => ev.preventDefault());
    // A phone, not a narrow desktop window: that one has windows of its own.
    this.phone = matchMedia("(max-width: 760px) and (pointer: coarse)");
    // A phone draws every window's panes in window 1: crossing the
    // breakpoint changes what is docked here.
    this.phone.addEventListener("change", () => {
      if (this.key === null) return;
      this.redraw();
      this.onChange(this.ids(), this.ids()); // Pop out shows only off a phone
    });
    // The redraw waits for the next frame: resizing a page's frame while
    // observers are being told of sizes raises the page's own "ResizeObserver
    // loop" error, which would reach the agent as the page's error.
    new ResizeObserver(() =>
      requestAnimationFrame(() => {
        if (this.key !== null && this.wantTabs() !== this.tabbed) this.place();
      }),
    ).observe(root);
  }

  // Draw window n of a session: on every tab switch, and on every card of the
  // shown session, so a change from the agent or another browser lands here.
  show(key, layout, { n = 1, writable = false } = {}) {
    const before = this.ids();
    const same = key === this.key && n === this.n;
    if (!same) this.clear();
    this.key = key;
    this.n = n;
    this.writable = writable;
    this.layout = layout || null;
    this.redraw(before, same);
  }

  // place(), and tell the cards when what is docked here changed. A newly
  // docked pane, by the agent or another browser or this one, gets a dot; the
  // open tab stays open.
  redraw(before = this.ids(), marks = true) {
    const was = new Map(this.away);
    this.place();
    const after = this.ids();
    if (marks) for (const id of after) if (!before.includes(id)) this.mark(id);
    const moved = was.size !== this.away.size || [...this.away].some(([id, n]) => was.get(id) !== n);
    if (moved || before.length !== after.length || before.some((id, i) => id !== after[i])) this.onChange(before, after);
  }

  // Window 1 on a phone: every window's panes, window 1's first.
  merged() {
    return this.n === 1 && this.phone.matches;
  }

  // The shown window as drawn: the card's, with a grip's drag on a copy. The
  // card's layout itself is never changed here, so a card that lands while a
  // grip moves is drawn with the drag on top, not overwritten by it.
  window() {
    const ws = (this.layout || DEFAULT).windows;
    let w = ws.find((w) => w.n === this.n) || { n: this.n, columns: [] };
    if (this.merged()) w = { n: 1, columns: [...w.columns, ...ws.filter((x) => x.n !== 1).flatMap((x) => x.columns)] };
    if (!this.preview) return w;
    const copy = structuredClone(w);
    this.preview(copy);
    return copy;
  }

  // The windows in the layout, by number.
  windows() {
    return (this.layout || DEFAULT).windows;
  }

  // The other window that shows `id`, or null: here, or docked nowhere.
  elsewhere(id) {
    if (this.key === null || this.merged()) return null;
    for (const w of this.windows()) {
      if (w.n === this.n) continue;
      if (w.columns.some((c) => c.panes.some((p) => p.id === id))) return w.n;
    }
    return null;
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
  // their pages go back to their cards, or to a line naming the window that
  // now shows them.
  place() {
    const keep = new Set(this.ids());
    const away = new Map();
    if (this.key !== null && !this.merged())
      for (const w of this.windows())
        if (w.n !== this.n) for (const c of w.columns) for (const p of c.panes) away.set(p.id, w.n);
    const was = this.away;
    this.away = away;
    for (const id of was.keys()) if (!away.has(id) && !keep.has(id) && id.startsWith("art-")) hold(id, null, this.entryOf(id));
    for (const [id, node] of this.panes) {
      if (keep.has(id)) continue;
      this.drop(id, node);
    }
    for (const [id, n] of away) if (was.get(id) !== n && id.startsWith("art-")) hold(id, () => {}, this.entryOf(id), inWindow(n));
    for (const id of this.dots) if (!keep.has(id)) this.dots.delete(id);
    if (!keep.has(this.open)) this.open = this.n === 1 ? TRANSCRIPT : [...keep][0] || TRANSCRIPT;
    if (this.menu && !keep.has(this.menu.id)) this.closeMenu();
    this.tabbed = this.wantTabs();
    if (!this.tabbed) this.dots.clear(); // a dot is a tab's, and the tabs are gone
    const cols = this.window().columns;
    const ws = fit(cols, this.root.clientWidth);
    let x = 0;
    for (const [i, col] of cols.entries()) {
      let y = 0;
      for (const p of col.panes) {
        const node = p.id === TRANSCRIPT ? this.tr : this.pane(p.id);
        if (this.tabbed) this.tabBox(node, p.id);
        else {
          this.box(node, x, y, ws[i], p.h);
          if (p.id === TRANSCRIPT) this.box(this.trBar, x, y, ws[i], null);
        }
        // A rule on the edges a neighbour shares, drawn by the pane right or below.
        node.classList.toggle("lx", !this.tabbed && x > 0.0005);
        node.classList.toggle("ty", !this.tabbed && y > 0.0005);
        y += p.h;
      }
      x += ws[i];
    }
    this.root.dataset.docked = String(keep.size);
    if (this.writable) this.root.dataset.writable = "";
    else delete this.root.dataset.writable;
    if (this.tabbed) this.root.dataset.mode = "tabs";
    else delete this.root.dataset.mode;
    if (this.merged()) this.root.dataset.merged = "";
    else delete this.root.dataset.merged;
    this.syncStrip();
    this.syncChips();
    // Handles only where there is something to arrange, for someone who may,
    // and not on tabs: dragging is a desktop act. The transcript's, only in
    // the window that holds it.
    const handles = keep.size && this.writable && !this.tabbed;
    if (handles && this.n === 1) {
      if (!this.trBar.isConnected) this.root.append(this.trBar);
    } else this.trBar.remove();
    this.placeGrips(handles);
    // A handle this redraw took away (its pane undocked elsewhere, its grip
    // gone with a column) never sees its pointer released.
    if (this.dragged && !this.dragged.alive()) this.dragged.end();
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

  // -- tabs ----------------------------------------------------------------

  // Tabs on a phone, and when the canvas is narrower than its columns'
  // minimums added up (the transcript's 360 px, 280 for each other). A column
  // under its minimum otherwise draws at it (fit). A canvas not laid out yet
  // (its view hidden) keeps its mode.
  wantTabs() {
    if (!this.ids().length) return false;
    if (this.phone.matches) return true;
    const width = this.root.clientWidth;
    if (!width) return this.tabbed;
    return this.window().columns.reduce((s, c) => s + minWidth(c), 0) > width;
  }

  // Every pane fills the canvas under the strip; a docked one stops above
  // the composer, which stays at the foot of the transcript's pane. A window
  // other than 1 has no composer.
  tabBox(node, id) {
    Object.assign(node.style, {
      left: "0",
      top: "var(--ptabs-h)",
      width: "100%",
      height: "auto",
      right: "auto",
      bottom: id === TRANSCRIPT || this.n !== 1 ? "0" : "var(--composer-h, 94px)",
    });
    node.classList.toggle("on", id === this.open);
  }

  // The strip: the transcript first, then the panes in reading order, column
  // by column, top to bottom. Tabs are kept by id, so one being pressed keeps
  // its pointer across a redraw.
  syncStrip() {
    if (!this.tabbed) {
      this.strip.remove();
      this.closeMenu();
      return;
    }
    const order = this.n === 1 ? [TRANSCRIPT, ...this.ids()] : this.ids();
    for (const [id, b] of this.tabs) if (!order.includes(id)) this.tabs.delete(id);
    const tabs = order.map((id) => {
      let b = this.tabs.get(id);
      if (!b) {
        b = el("button");
        b.type = "button";
        b.setAttribute("role", "tab");
        b.dataset.pane = id;
        this.tabs.set(id, b);
      }
      b.textContent = this.label(id);
      b.title = b.textContent;
      b.classList.toggle("on", id === this.open);
      b.setAttribute("aria-selected", String(id === this.open));
      b.classList.toggle("dot", this.dots.has(id));
      return b;
    });
    this.strip.replaceChildren(...tabs);
    if (!this.strip.isConnected) this.root.append(this.strip);
  }

  label(id) {
    if (id === TRANSCRIPT) return "transcript";
    if (id.startsWith("art-")) return this.entryOf(id)?.title || "page";
    return this.latestFile(id)?.name || "file";
  }

  openTab(id) {
    if (!this.panes.has(id) && id !== TRANSCRIPT) return;
    this.closeMenu();
    this.open = id;
    this.dots.delete(id);
    this.place();
  }

  // A pane changed: a dot on its tab, unless it is the one open.
  mark(id) {
    if (!this.tabbed || id === this.open || !this.panes.has(id)) return;
    this.dots.add(id);
    this.syncStrip();
  }

  // Hold a tab other than the transcript's: Undock, in a one-item menu under it.
  press(ev) {
    const b = ev.target.closest("button[data-pane]");
    this.pressed = false;
    if (!b || b.dataset.pane === TRANSCRIPT || !this.writable || ev.button !== 0) return;
    const id = b.dataset.pane;
    const timer = setTimeout(() => {
      this.pressed = true;
      this.openMenu(id, b);
    }, LONG_PRESS);
    const cancel = () => {
      clearTimeout(timer);
      for (const t of ["pointerup", "pointercancel", "pointerleave"]) b.removeEventListener(t, cancel);
    };
    for (const t of ["pointerup", "pointercancel", "pointerleave"]) b.addEventListener(t, cancel);
  }

  // A small menu under `anchor` (a tab, a pane's Move button): [label, act]
  // items, closed by a choice or a press anywhere else.
  openMenu(id, anchor, items = [["Undock", () => this.undock(id)]]) {
    this.closeMenu();
    const node = el("div", "pmenu");
    node.setAttribute("role", "menu");
    for (const [label, act] of items) {
      const b = el("button", "btn", label);
      b.type = "button";
      b.setAttribute("role", "menuitem");
      b.addEventListener("click", () => {
        this.closeMenu();
        act();
      });
      node.append(b);
    }
    const r = anchor.getBoundingClientRect();
    const box = this.root.getBoundingClientRect();
    node.style.left = `${Math.max(0, Math.min(r.left - box.left, box.width - 160))}px`;
    node.style.top = `${r.bottom - box.top + 4}px`;
    // A press anywhere else closes it.
    const away = (e) => {
      if (!node.contains(e.target) && !anchor.contains(e.target)) this.closeMenu();
    };
    document.addEventListener("pointerdown", away, true);
    this.menu = { id, node, away };
    this.root.append(node);
    node.firstChild.focus({ preventScroll: true });
  }

  closeMenu() {
    if (!this.menu) return;
    document.removeEventListener("pointerdown", this.menu.away, true);
    this.menu.node.remove();
    this.menu = null;
  }

  // One grip on each boundary between columns, and one between each two
  // stacked panes. Kept by key across redraws, so the grip being dragged keeps
  // its pointer while the panes around it move.
  placeGrips(on) {
    const want = new Map();
    if (on) {
      const cols = this.window().columns;
      const ws = fit(cols, this.root.clientWidth);
      let x = 0;
      cols.forEach((c, i) => {
        if (i > 0) want.set(`x${i}`, { i, j: null, x, y: 0, w: null });
        let y = 0;
        c.panes.forEach((p, j) => {
          if (j > 0) want.set(`y${i}.${j}`, { i, j, x, y, w: ws[i] });
          y += p.h;
        });
        x += ws[i];
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

  // A page that moved to another window keeps its card's line: releasing it
  // would mount the card's frame for a moment and start the page twice.
  drop(id, node) {
    node.remove();
    this.panes.delete(id);
    if (id.startsWith("art-") && !this.away.has(id)) hold(id, null, this.entryOf(id));
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
      drawArtifact(entry, { bar, body, acts, popout: popButton(id) });
      node.dataset.agentRev = String(entry.detail?.state_rev);
      // An ended page leaves the layout with its submit; until that card
      // lands it shows read-only, and its card keeps its own line.
      if (entry.status === "live") hold(id, (next) => this.onEntry(next), entry, DOCKED);
      else hold(id, null, entry);
    } else {
      const f = e || this.latestFile(id);
      if (!f) return this.loading(node, bar, acts, "Loading the file…");
      drawFile(f, { remote: false, glyph: "file", bar, body, acts, isCurrent: () => node._drawn === drawn });
      // A pane can be narrow: its bar keeps the name and the version, and the
      // size and type move to the bar's tooltip.
      bar.querySelector(".fs")?.remove();
      bar.title = f.summary || "";
      bar.append(el("span", "ver", `v${f.version}`));
      node.dataset.version = String(f.version);
    }
    acts.append(this.moveButton(id), this.undockButton(id));
    bar.append(acts);
  }

  moveButton(id) {
    const b = el("button", "btn icon move");
    b.type = "button";
    b.append(icon("swap"));
    b.title = "Move to another browser window";
    b.setAttribute("aria-label", "Move to window");
    b.addEventListener("click", () => {
      if (this.menu?.id === id) return this.closeMenu();
      const ws = this.windows();
      const items = ws.filter((w) => w.n !== this.n).map((w) => [`Window ${w.n}`, () => this.toWindow(id, w.n)]);
      if (ws.length < MAX_WINDOWS) items.push(["New window", () => this.toWindow(id, "new")]);
      this.openMenu(id, b, items);
    });
    return b;
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
  // resume): fill the panes that were waiting for theirs, give a page what
  // changed while the tab was away (a resend, a status, the agent's state),
  // and show a file's newest version if one came with them.
  refresh() {
    for (const [id, node] of this.panes) {
      if (id.startsWith("art-")) {
        const e = this.entryOf(id);
        if (e) this.onEntry(e);
      } else if ("loading" in node.dataset) this.draw(id);
      else {
        const f = this.latestFile(id);
        if (f && String(f.version) !== node.dataset.version) {
          this.draw(id, f);
          this.mark(id);
        }
      }
    }
    this.syncStrip();
  }

  // An entry was drawn again. A pane redraws only when what it shows changed:
  // a new page (src) or a new status; a file pane, when a newer version of its
  // key lands, in place (a frame or an image gets the new URL, text is fetched
  // again).
  onEntry(e) {
    if (e.kind === "file") {
      for (const f of e.detail?.files || []) {
        const node = f.key && !f.replaced && this.panes.get(f.key);
        if (!node) continue;
        if ("loading" in node.dataset) this.draw(f.key, f);
        else if (node.dataset.version !== String(f.version)) {
          this.draw(f.key, f);
          this.mark(f.key);
        }
      }
      return;
    }
    const node = this.panes.get(e.id);
    if (!node || e.kind !== "artifact") return;
    if ("loading" in node.dataset) {
      this.draw(e.id, e);
      return this.syncStrip(); // its tab gets its title
    }
    const frame = node.querySelector("iframe[data-artifact]");
    if (frame && frame.dataset.status === e.status && frame.getAttribute("src") === fileUrl(e.detail?.url)) {
      // A state the agent set reaches the frame here too: the card's row
      // (entries.js, UPDATERS.artifact) may be unmounted, and then nothing
      // else would hand it on. The revision keeps it from arriving twice.
      // The pane keeps its own revision for the dot, since the row's updater
      // may have handed the state on before this.
      const rev = String(e.detail?.state_rev);
      if (e.detail?.state_by !== "agent" || rev === node.dataset.agentRev) return;
      node.dataset.agentRev = rev;
      this.mark(e.id);
      if (rev !== frame.dataset.stateRev) {
        frame.dataset.stateRev = rev;
        frame.dispatchEvent(new CustomEvent("aegis:state", { detail: e.detail.state, bubbles: true }));
      }
      return;
    }
    this.draw(e.id, e);
    this.mark(e.id);
  }

  // Outline a docked pane for a moment, opening its tab when the canvas is
  // tabs; false when it is not in this window.
  flash(id) {
    const node = this.panes.get(id);
    if (!node) return false;
    if (this.tabbed) this.openTab(id);
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

  // Move a pane to window n, or to a new one, and focus or open that
  // window's tab (window 1 is the session's own tab, which has no name to
  // find it by). A new window takes one more than the highest in use
  // (layout.place); if the agent took that number meanwhile, the tab opened
  // for it is pointed at the window the pane landed in.
  toWindow(id, n) {
    const ws = this.windows();
    if (n === "new" && ws.length >= MAX_WINDOWS) {
      this.refuse(`A canvas has at most ${MAX_WINDOWS} windows. Bring one back first.`);
      return Promise.resolve();
    }
    const want = n === "new" ? Math.max(...ws.map((w) => w.n)) + 1 : n;
    const tab = want === 1 ? null : this.openWindow(want);
    return this.call("layout.dock", { pane: id, where: `window:${n}` }).then(
      (lay) => {
        const got = lay?.windows?.find((w) => w.columns.some((c) => c.panes.some((p) => p.id === id)))?.n;
        if (tab && got && got !== want) this.openWindow(got, tab);
      },
      (err) => {
        if (tab && n === "new") tab.close();
        this.refuse(err.message || String(err));
      },
    );
  }

  // Pop out: a page alone in a new window. One already in another window
  // only has its tab focused or opened.
  popOut(id) {
    const n = this.elsewhere(id);
    if (n) this.openWindow(n);
    else this.toWindow(id, "new");
  }

  // Bring back: window n's panes into window 1, each of its columns a new
  // column at window 1's right edge, stacked as they were. One call per
  // pane, in order; a refusal (window 1 is full) stops there and says why.
  async bringBack(n) {
    const w = this.windows().find((x) => x.n === n);
    if (!w) return;
    try {
      for (const c of w.columns) {
        let above = null;
        for (const p of c.panes) {
          await this.call("layout.dock", { pane: p.id, where: above ? `below:${above}` : "right" });
          above = p.id;
        }
      }
    } catch (err) {
      this.refuse(err.message || String(err));
    }
  }

  // Window 1's chips: "window 2 · 1 pane", Open and Bring back. Rebuilt only
  // when they change, so a card landing mid-click keeps the button pressed.
  syncChips() {
    const others = this.n === 1 && this.key !== null && !this.merged() ? this.windows().filter((w) => w.n !== 1) : [];
    const counts = others.map((w) => [w.n, w.columns.reduce((s, c) => s + c.panes.length, 0)]);
    const sig = JSON.stringify([counts, this.writable]);
    if (sig === this.chipSig) return;
    this.chipSig = sig;
    if (!counts.length) {
      this.chips.remove();
      delete this.root.dataset.chips;
      return;
    }
    const link = (cls, text, title, act) => {
      const b = el("button", `btn link ${cls}`, text);
      b.type = "button";
      b.title = title;
      b.addEventListener("click", act);
      return b;
    };
    this.chips.replaceChildren(
      ...counts.map(([n, k]) => {
        const chip = el("span", "wchip", `window ${n} · ${k} pane${k === 1 ? "" : "s"} · `);
        chip.dataset.n = String(n);
        chip.append(link("show", "open", `Focus or open window ${n}'s tab`, () => this.openWindow(n)));
        if (this.writable)
          chip.append(" · ", link("bring", "bring back", `Move window ${n}'s panes here`, () => this.bringBack(n)));
        return chip;
      }),
    );
    if (!this.chips.isConnected) this.root.append(this.chips);
    this.root.dataset.chips = "";
  }

  // Drag a pane by its bar. While the pointer moves, the pane under it shows
  // where the drop goes: its left or right fifth makes a column on that side,
  // its top or bottom third stacks above or below it, its middle swaps the
  // two. Frames take no pointer meanwhile (.dragging), or they would swallow it.
  grab(ev) {
    if (!this.writable || this.tabbed || ev.button !== 0 || ev.target.closest("button, a, input")) return;
    const bar = ev.target.closest(".pbar");
    const id = bar === this.trBar ? TRANSCRIPT : bar.closest(".pane.docked")?.dataset.pane;
    if (!id) return;
    ev.preventDefault();
    const there = () => (id === TRANSCRIPT ? this.trBar.isConnected : this.panes.has(id));
    let where = null;
    this.follow(ev, bar, {
      alive: there,
      move: (e) => (where = this.aim(id, e.clientX, e.clientY)),
      end: (e) => {
        this.target.remove();
        if (e?.type === "pointerup" && where && there()) this.move(id, where);
      },
    });
  }

  // One drag's pointer, from pointerdown on `handle` to its release. The end is
  // heard on the document: a redraw can take the handle out of the DOM, and
  // its own listeners would then never hear the pointer go up, leaving every
  // frame without clicks (.dragging). place() ends a drag whose handle is gone.
  follow(ev, handle, { alive, move, end }) {
    this.dragged?.end();
    handle.setPointerCapture(ev.pointerId);
    this.root.classList.add("dragging");
    const stop = (e) => {
      if (e && e.pointerId !== ev.pointerId) return;
      for (const [t, f] of on) document.removeEventListener(t, f);
      this.dragged = null;
      this.root.classList.remove("dragging");
      end(e);
    };
    const step = (e) => {
      if (e.pointerId === ev.pointerId) move(e);
    };
    const on = [
      ["pointermove", step],
      ["pointerup", stop],
      ["pointercancel", stop],
      ["lostpointercapture", stop],
    ];
    for (const [t, f] of on) document.addEventListener(t, f);
    this.dragged = { alive, end: () => stop(null) };
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
  // shares are sent once, on release. Meanwhile the drag is a preview over the
  // card's layout (window()); once the resize is answered, or refused, the
  // canvas draws from the card alone again.
  resizeFrom(ev) {
    if (!this.writable || this.tabbed || ev.button !== 0) return;
    const grip = ev.target.closest(".cgrip");
    ev.preventDefault();
    grip.classList.add("on");
    const i = Number(grip.dataset.i);
    const j = grip.dataset.j === "" ? null : Number(grip.dataset.j);
    const box = this.root.getBoundingClientRect();
    let at = null; // the pointer, once it has moved
    // The dragged pair's shares at the pointer, on a copy of the shown window.
    // A card that changed the window's shape under the pointer leaves it be.
    // A column drag starts from the widths as drawn (fit), so the boundary
    // stays under the pointer, and those are the widths it sends.
    const drag = (win) => {
      if (at === null) return;
      if (j === null) {
        fit(win.columns, box.width).forEach((w, k) => (win.columns[k].w = w));
        const [a, b] = [win.columns[i - 1], win.columns[i]];
        if (!a || !b) return;
        const start = win.columns.slice(0, i - 1).reduce((s, c) => s + c.w, 0);
        const pair = a.w + b.w;
        const lo = minWidth(a) / box.width;
        const hi = pair - minWidth(b) / box.width;
        if (lo > hi) return; // too narrow for both minimums: leave it
        a.w = clamp((at.x - box.left) / box.width - start, lo, hi);
        b.w = pair - a.w;
      } else {
        const panes = win.columns[i]?.panes;
        const [a, b] = [panes?.[j - 1], panes?.[j]];
        if (!a || !b) return;
        const start = panes.slice(0, j - 1).reduce((s, p) => s + p.h, 0);
        const pair = a.h + b.h;
        const lo = MIN_H / box.height;
        const hi = pair - MIN_H / box.height;
        if (lo > hi) return;
        a.h = clamp((at.y - box.top) / box.height - start, lo, hi);
        b.h = pair - a.h;
      }
    };
    this.preview = drag;
    const settle = () => {
      if (this.preview !== drag) return; // another drag has started
      this.preview = null;
      this.place();
    };
    this.follow(ev, grip, {
      alive: () => grip.isConnected,
      move: (e) => {
        at = { x: e.clientX, y: e.clientY };
        this.place();
      },
      end: (e) => {
        grip.classList.remove("on");
        if (e?.type !== "pointerup" || at === null) return settle();
        const win = this.window();
        const widths = win.columns.map((c) => c.w);
        const heights = win.columns.map((c) => c.panes.map((p) => p.h));
        this.call("layout.resize", { n: this.n, widths, heights })
          .catch((err) => this.refuse(err.message || String(err)))
          .finally(settle);
      },
    });
  }

  // No session, or another one: every pane goes and the transcript fills the
  // canvas again.
  clear() {
    for (const id of this.away.keys()) if (id.startsWith("art-")) hold(id, null, this.entryOf(id));
    this.away.clear();
    for (const [id, node] of this.panes) this.drop(id, node);
    this.key = null;
    this.layout = null;
    for (const k of ["left", "top", "width", "height", "right", "bottom"]) this.tr.style[k] = "";
    this.tr.classList.remove("lx", "ty");
    this.preview = null;
    this.dragged?.end();
    this.trBar.remove();
    this.target.remove();
    this.placeGrips(false);
    this.root.dataset.docked = "0";
    this.tr.classList.remove("on");
    this.tabbed = false;
    this.open = TRANSCRIPT;
    this.dots.clear();
    this.tabs.clear();
    this.strip.remove();
    this.closeMenu();
    delete this.root.dataset.mode;
    delete this.root.dataset.merged;
    this.chips.remove();
    this.chipSig = "";
    delete this.root.dataset.chips;
  }
}
