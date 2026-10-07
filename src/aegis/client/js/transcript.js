// The transcript: entry nodes keyed by id, patched in place.
//
// It follows the bottom while the reader is at the bottom; once they scroll
// up, new entries raise the jump pill instead of moving the page.

import { render } from "./entries.js";

export class Transcript {
  constructor(scroller, list, jump) {
    this.scroller = scroller;
    this.list = list;
    this.jump = jump;
    this.nodes = new Map();
    this.selected = null; // an entry id: apply() replaces nodes, ids stay
    // Tab walks the rows' summaries and buttons; the row holding focus is the selection.
    list.addEventListener("focusin", (ev) => {
      const r = ev.target.closest(".row");
      if (!r) return;
      this.selected = r.dataset.id;
      this.mark();
    });
    scroller.addEventListener("scroll", () => {
      if (this.atBottom()) this.jump.hidden = true;
    });
    jump.addEventListener("click", () => this.toBottom());
  }

  atBottom() {
    const s = this.scroller;
    return s.scrollHeight - s.scrollTop - s.clientHeight < 48;
  }

  toBottom() {
    this.scroller.scrollTop = this.scroller.scrollHeight;
    this.jump.hidden = true;
  }

  snapshot(entries) {
    this.nodes.clear();
    const frag = document.createDocumentFragment();
    for (const e of entries) {
      const n = render(e);
      this.nodes.set(e.id, n);
      frag.append(n);
    }
    this.list.replaceChildren(frag);
    this.mark();
    this.toBottom();
  }

  apply(ops) {
    const follow = this.atBottom();
    let added = false;
    for (const op of ops) {
      if (op.upsert) {
        const e = op.upsert;
        const n = render(e);
        const old = this.nodes.get(e.id);
        if (old) {
          // A row the reader opened or closed keeps that state across updates.
          const was = old.querySelector("details");
          const now = n.querySelector("details");
          if (was && now && old.dataset.touched) {
            now.open = was.open;
            n.dataset.touched = "1";
          }
          old.replaceWith(n);
        } else {
          this.list.append(n);
          added = true;
        }
        this.nodes.set(e.id, n);
        n.querySelector("summary")?.addEventListener("click", () => (n.dataset.touched = "1"));
      } else if (op.remove !== undefined) {
        this.nodes.get(op.remove)?.remove();
        this.nodes.delete(op.remove);
      }
    }
    this.mark();
    if (follow) this.toBottom();
    else if (added) this.jump.hidden = false;
  }

  clear() {
    this.nodes.clear();
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

  move(delta, keep = () => true) {
    let n = this.selected ? this.nodes.get(this.selected) : null;
    if (!n) return this.pick();
    do n = delta > 0 ? n.nextElementSibling : n.previousElementSibling;
    while (n && !keep(n));
    if (n) this.select(n.dataset.id);
  }

  moveTurn(delta) {
    this.move(delta, (n) => n.classList.contains("user"));
  }

  edge(last) {
    const n = last ? this.list.lastElementChild : this.list.firstElementChild;
    if (last) this.toBottom();
    if (n) this.select(n.dataset.id);
  }

  toggle() {
    const n = this.selected ? this.nodes.get(this.selected) : null;
    const d = n?.querySelector("details");
    if (!d) return;
    d.open = !d.open;
    n.dataset.touched = "1";
  }

  press() {
    const n = this.selected ? this.nodes.get(this.selected) : null;
    n?.querySelector("a.btn, button")?.click();
  }
}
