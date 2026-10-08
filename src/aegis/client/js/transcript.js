// The transcript: every entry's data, and rows for the tail of it.
//
// Only the last WINDOW entries are rows in the DOM; scrolling near the top
// mounts the PAGE before them. Every layout costs a hover hit test and a paint
// that walk all mounted rows, and a keystroke always lays out, so mounting a
// long transcript whole made typing cost grow with its length (#157). Mounted
// rows are always a suffix of the transcript.
//
// It follows the bottom while the reader is at the bottom; once they scroll
// up, new entries raise the jump pill instead of moving the page. Rows skip
// layout off screen (content-visibility in base.css) and take their real
// height when they near the viewport, so while following, a change in the
// list's height pins it to the bottom again.

import { render } from "./entries.js";

const WINDOW = 200;
const PAGE = 100;
const NEAR_TOP_PX = 400;

export class Transcript {
  constructor(scroller, list, jump) {
    this.scroller = scroller;
    this.list = list;
    this.jump = jump;
    this.entries = new Map(); // id -> entry, in transcript order
    this.nodes = new Map(); // id -> row, for the mounted suffix
    this.opened = new Map(); // id -> open, for rows whose details the reader toggled
    this.touched = new Set();
    this.following = true;
    this.selected = null; // an entry id: apply() replaces nodes, ids stay
    // Tab walks the rows' summaries and buttons; the row holding focus is the selection.
    list.addEventListener("focusin", (ev) => {
      const r = ev.target.closest(".row");
      if (!r) return;
      this.selected = r.dataset.id;
      this.mark();
    });
    scroller.addEventListener("scroll", () => {
      this.following = this.atBottom();
      if (this.following) this.jump.hidden = true;
      if (scroller.scrollTop < NEAR_TOP_PX) this.mountEarlier();
    });
    jump.addEventListener("click", () => this.toBottom());
    // A row the reader opened or closed keeps that state across updates and
    // across being unmounted and mounted again.
    list.addEventListener("click", (ev) => {
      const id = ev.target.closest("summary")?.closest(".row")?.dataset.id;
      if (id) this.touched.add(id);
    });
    list.addEventListener(
      "toggle",
      (ev) => {
        const id = ev.target.closest(".row")?.dataset.id;
        if (id && this.touched.has(id)) this.opened.set(id, ev.target.open);
      },
      true, // toggle does not bubble
    );
    new ResizeObserver(() => {
      if (this.following) this.toBottom();
    }).observe(list);
  }

  atBottom() {
    const s = this.scroller;
    return s.scrollHeight - s.scrollTop - s.clientHeight < 48;
  }

  toBottom() {
    this.scroller.scrollTop = this.scroller.scrollHeight;
    this.following = true;
    this.jump.hidden = true;
  }

  mount(e) {
    const n = render(e);
    if (this.opened.has(e.id)) {
      const d = n.querySelector("details");
      if (d) d.open = this.opened.get(e.id);
    }
    this.nodes.set(e.id, n);
    return n;
  }

  snapshot(entries) {
    this.clear();
    for (const e of entries) this.entries.set(e.id, e);
    const frag = document.createDocumentFragment();
    for (const e of entries.slice(-WINDOW)) frag.append(this.mount(e));
    this.list.replaceChildren(frag);
    this.mark();
    this.toBottom();
  }

  // The PAGE entries before the first mounted row, mounted above it, with the
  // reader's place kept: the row that was first stays where it was on screen.
  // False when every entry is already mounted.
  mountEarlier() {
    const start = this.entries.size - this.nodes.size;
    if (start <= 0) return false;
    const ids = [...this.entries.keys()].slice(Math.max(0, start - PAGE), start);
    const ref = this.list.firstElementChild;
    const top = ref?.getBoundingClientRect().top;
    const frag = document.createDocumentFragment();
    for (const id of ids) frag.append(this.mount(this.entries.get(id)));
    this.list.prepend(frag);
    if (ref) this.scroller.scrollTop += ref.getBoundingClientRect().top - top;
    return true;
  }

  apply(ops) {
    let added = false;
    for (const op of ops) {
      if (op.upsert) {
        const e = op.upsert;
        const known = this.entries.has(e.id);
        this.entries.set(e.id, e);
        const old = this.nodes.get(e.id);
        if (old) old.replaceWith(this.mount(e));
        else if (!known) {
          this.list.append(this.mount(e));
          added = true;
        }
        // A known entry above the mounted rows changes only its data.
      } else if (op.remove !== undefined) {
        this.entries.delete(op.remove);
        this.nodes.get(op.remove)?.remove();
        this.nodes.delete(op.remove);
      }
    }
    if (this.following) {
      // Rows above the window go while the reader is at the bottom.
      while (this.nodes.size > WINDOW + PAGE) {
        const first = this.list.firstElementChild;
        this.nodes.delete(first.dataset.id);
        first.remove();
      }
    }
    this.mark();
    if (this.following) this.toBottom();
    else if (added) this.jump.hidden = false;
  }

  clear() {
    this.entries.clear();
    this.nodes.clear();
    this.opened.clear();
    this.touched.clear();
    this.following = true;
    this.selected = null;
    this.list.replaceChildren();
  }

  // -- the selection --------------------------------------------------------

  // Puts the mark on the selected entry's current node; a removed entry
  // clears the selection.
  mark() {
    this.list.querySelector(".row.sel")?.classList.remove("sel");
    const n = this.selected ? this.nodes.get(this.selected) : null;
    if (n) n.classList.add("sel");
    else this.selected = null;
    return n;
  }

  select(id) {
    this.selected = id;
    const n = this.mark();
    if (!n) return;
    // A focused summary in another row would take the next Enter.
    if (this.list.contains(document.activeElement)) this.scroller.focus({ preventScroll: true });
    n.scrollIntoView({ block: "nearest" });
  }

  // The row the reader is looking at: the last whose top is on screen.
  inView() {
    const bottom = this.scroller.getBoundingClientRect().bottom;
    const rows = [...this.list.children];
    for (let i = rows.length - 1; i >= 0; i--) if (rows[i].getBoundingClientRect().top < bottom) return rows[i];
    return null;
  }

  pick() {
    if (!this.selected) this.select(this.inView()?.dataset.id || null);
  }

  // Walking up past the first mounted row mounts the page before it.
  move(delta, keep = () => true) {
    let n = this.selected ? this.nodes.get(this.selected) : null;
    if (!n) return this.pick();
    for (;;) {
      const next = delta > 0 ? n.nextElementSibling : n.previousElementSibling;
      if (!next && delta < 0 && this.mountEarlier()) continue;
      n = next;
      if (!n || keep(n)) break;
    }
    if (n) this.select(n.dataset.id);
  }

  moveTurn(delta) {
    this.move(delta, (n) => n.classList.contains("user"));
  }

  // The first entry is mounted on the way: an explicit jump may pay for it.
  edge(last) {
    if (!last) while (this.mountEarlier());
    const n = last ? this.list.lastElementChild : this.list.firstElementChild;
    if (last) this.toBottom();
    if (n) this.select(n.dataset.id);
  }

  toggle() {
    const n = this.selected ? this.nodes.get(this.selected) : null;
    const d = n?.querySelector("details");
    if (!d) return;
    this.touched.add(this.selected);
    d.open = !d.open;
  }

  press() {
    const n = this.selected ? this.nodes.get(this.selected) : null;
    n?.querySelector("a.btn, button")?.click();
  }
}
