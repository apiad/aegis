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
    this.toBottom();
  }

  // The PAGE entries before the first mounted row, mounted above it, with the
  // reader's place kept: the row that was first stays where it was on screen.
  mountEarlier() {
    const start = this.entries.size - this.nodes.size;
    if (start <= 0) return;
    const ids = [...this.entries.keys()].slice(Math.max(0, start - PAGE), start);
    const ref = this.list.firstElementChild;
    const top = ref?.getBoundingClientRect().top;
    const frag = document.createDocumentFragment();
    for (const id of ids) frag.append(this.mount(this.entries.get(id)));
    this.list.prepend(frag);
    if (ref) this.scroller.scrollTop += ref.getBoundingClientRect().top - top;
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
      this.toBottom();
    } else if (added) this.jump.hidden = false;
  }

  clear() {
    this.entries.clear();
    this.nodes.clear();
    this.opened.clear();
    this.touched.clear();
    this.following = true;
    this.list.replaceChildren();
  }
}
