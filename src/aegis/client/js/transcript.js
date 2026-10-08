// The transcript: every entry's data, and rows for the tail of it.
//
// Only the last WINDOW entries are rows in the DOM; scrolling near the top
// mounts the PAGE before them. Every layout costs a hover hit test and a paint
// that walk all mounted rows, and a keystroke always lays out, so mounting a
// long transcript whole made typing cost grow with its length (#157). Mounted
// rows are always a suffix of the transcript. The data of a tab not on screen
// is kept by app.js (stash and restore), and a row the wire sent without its
// detail fetches it when it opens (transcript/wire.py).
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
  constructor(scroller, list, jump, loadDetail) {
    this.scroller = scroller;
    this.list = list;
    this.jump = jump;
    this.entries = new Map(); // id -> entry, in transcript order
    this.nodes = new Map(); // id -> row, for the mounted suffix
    this.opened = new Map(); // id -> open, for rows whose details the reader toggled
    this.touched = new Set();
    this.following = true;
    this.selected = null; // an entry id: apply() replaces nodes, ids stay
    this.loadDetail = loadDetail; // ids -> Promise of whole entries
    this.rev = -1; // the highest revision seen: what a resubscribe asks since
    this.full = new Map(); // id -> the whole entry, fetched when its row opened
    this.fetching = new Set();
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
        if (id && ev.target.open) this.fetch(id);
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

  // The entry to draw: the fetched whole one while it is still current.
  whole(e) {
    const f = this.full.get(e.id);
    return f && f.rev === e.rev ? f : e;
  }

  mount(e) {
    const n = render(this.whole(e));
    if (this.opened.has(e.id)) {
      const d = n.querySelector("details");
      if (d) d.open = this.opened.get(e.id);
      if (d?.open) this.fetch(e.id);
    }
    this.nodes.set(e.id, n);
    return n;
  }

  // A row opened: fetch what the wire left out, unless it is here and current.
  // An upsert that lands while the fetch is out fetches again once it answers.
  fetch(id) {
    const e = this.entries.get(id);
    if (!e?.detail?.more || this.whole(e) !== e || this.fetching.has(id)) return;
    this.fetching.add(id);
    this.loadDetail([id])
      .then((got) => {
        this.fetching.delete(id);
        for (const f of got) {
          this.full.set(f.id, f);
          const cur = this.entries.get(f.id);
          const old = this.nodes.get(f.id);
          if (cur && old && cur.rev === f.rev) old.replaceWith(this.mount(cur));
        }
        this.mark();
        const f = got.find((x) => x.id === id);
        if (f && this.entries.get(id)?.rev !== f.rev && this.nodes.get(id)?.querySelector("details")?.open) this.fetch(id);
      })
      .catch(() => this.fetching.delete(id));
  }

  snapshot(data) {
    this.clear();
    this.rev = data.rev;
    for (const e of data.entries) this.entries.set(e.id, e);
    const frag = document.createDocumentFragment();
    for (const e of data.entries.slice(-WINDOW)) frag.append(this.mount(e));
    this.list.replaceChildren(frag);
    this.mark();
    this.toBottom();
  }

  // A delta: what changed since the revision this tab held, removals first.
  resume(data) {
    this.apply([...data.removed.map((id) => ({ remove: id })), ...data.entries.map((e) => ({ upsert: e }))]);
    this.rev = Math.max(this.rev, data.rev);
  }

  // Hands over this tab's data and starts empty; restore() takes it back.
  stash() {
    const s = { entries: this.entries, full: this.full, opened: this.opened, touched: this.touched, rev: this.rev };
    this.entries = new Map();
    this.full = new Map();
    this.opened = new Map();
    this.touched = new Set();
    this.clear();
    return s;
  }

  restore(s) {
    this.clear();
    Object.assign(this, { entries: s.entries, full: s.full, opened: s.opened, touched: s.touched, rev: s.rev });
    const frag = document.createDocumentFragment();
    for (const e of [...this.entries.values()].slice(-WINDOW)) frag.append(this.mount(e));
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
        if (e.rev > this.rev) this.rev = e.rev;
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
    this.full.clear();
    this.fetching.clear();
    this.rev = -1;
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
