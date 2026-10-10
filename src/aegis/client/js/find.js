// The find bar: a search scoped to the open transcript (#250).
//
// The browser's own find sees only what is laid out, and the transcript
// mounts rows for its tail, folds runs away and leaves a row's detail on the
// server until the row opens (js/transcript.js, transcript/wire.py), so Ctrl+F
// missed most of a long session. This searches the entries' data instead: the
// text the client holds, at once, and the detail the wire withheld, through
// transcript.search, which answers ids. A match is an entry, so "n of m"
// counts rows. Stepping to one mounts it, opens its run and, when the text is
// only in its closed detail, its details, then selects and centers it.
//
// Matches are painted with the CSS Custom Highlight API, not marks in the
// markup: rows are replaced on every patch and fetch, and a range over their
// text needs no node of its own. The ranges are redrawn when the rows change.

const WAIT_MS = 120; // after the last keystroke, before searching

// The text a row draws from its entry, as the client holds it.
function held(e) {
  const d = e.detail || {};
  const diff = d.diff ? [d.diff.path, ...d.diff.removed, ...d.diff.added] : [];
  const files = (d.files || []).map((f) => f.name);
  return [e.title, e.summary, e.md, d.result, d.context, d.ask, d.label, d.tail, d.args, ...diff, ...files]
    .filter(Boolean)
    .join("\n")
    .toLowerCase();
}

// Every occurrence of q (lower case) in the text under node. A match split
// across two text nodes, as by inline markup, is not found.
function ranges(node, q) {
  const out = [];
  const walk = document.createTreeWalker(node, NodeFilter.SHOW_TEXT);
  for (let t = walk.nextNode(); t; t = walk.nextNode()) {
    const s = t.data.toLowerCase();
    for (let i = s.indexOf(q); i >= 0; i = s.indexOf(q, i + q.length)) {
      const r = new Range();
      r.setStart(t, i);
      r.setEnd(t, i + q.length);
      out.push(r);
    }
  }
  return out;
}

// Inside a closed <details>, outside its summary.
function folded(r) {
  const el = r.startContainer.parentElement;
  const d = el.closest("details");
  return !!d && !d.open && !el.closest("summary");
}

// Brings r to the middle of every box between it and the scroller that
// clips it, such as an output's own scroll.
function center(r, scroller) {
  for (let el = r.startContainer.parentElement; el; el = el.parentElement) {
    if (el === scroller || (el.scrollHeight > el.clientHeight + 1 && getComputedStyle(el).overflowY !== "visible")) {
      const a = r.getBoundingClientRect();
      const b = el.getBoundingClientRect();
      if (a.top < b.top || a.bottom > b.bottom) el.scrollTop += a.top - b.top - (b.height - a.height) / 2;
    }
    if (el === scroller) return;
  }
}

const paint = (name, rs) => globalThis.Highlight && CSS.highlights.set(name, new Highlight(...rs));

export class Find {
  // search: q -> Promise of the ids of entries whose withheld detail holds q.
  constructor(bar, transcript, search) {
    this.bar = bar;
    this.t = transcript;
    this.search = search;
    this.input = bar.querySelector("input");
    this.count = bar.querySelector(".count");
    this.q = ""; // lower case, as searched
    this.hits = []; // matching entry ids, in transcript order
    this.far = new Set(); // what the server matched for q
    this.at = -1;
    this.seq = 0; // a server answer for an older query is dropped
    this.asking = false;
    this.back = null; // what had focus before the bar opened
    this.timer = 0;
    this.frame = 0;
    this.scroll = false; // the current match still has to be brought on screen
    this.input.addEventListener("input", () => {
      clearTimeout(this.timer);
      this.timer = setTimeout(() => this.run(), WAIT_MS);
    });
    this.input.addEventListener("keydown", (ev) => {
      if (ev.key !== "Enter" || ev.isComposing) return;
      ev.preventDefault();
      if (this.input.value.toLowerCase() !== this.q) this.run();
      else this.step(ev.shiftKey ? -1 : 1);
    });
    bar.querySelector(".prev").addEventListener("click", () => this.step(-1));
    bar.querySelector(".next").addEventListener("click", () => this.step(1));
    bar.querySelector(".close").addEventListener("click", () => this.close());
    // Rows are mounted, replaced and dropped by patches, fetches and scrolling.
    new MutationObserver(() => !this.bar.hidden && this.match(false)).observe(transcript.list, { childList: true });
  }

  open() {
    if (this.bar.hidden) {
      this.back = document.activeElement;
      this.bar.hidden = false;
      if (this.input.value) this.run();
    }
    this.input.focus();
    this.input.select();
  }

  // False when it was not open. `restore` gives the focus back to where it
  // was; inside the transcript, to the transcript, so j/k go on from the match.
  close(restore = true) {
    if (this.bar.hidden) return false;
    this.bar.hidden = true;
    clearTimeout(this.timer);
    this.seq++;
    this.q = "";
    this.hits = [];
    this.at = -1;
    if (globalThis.Highlight) {
      CSS.highlights.delete("find");
      CSS.highlights.delete("find-now");
    }
    const b = this.back;
    this.back = null;
    if (restore) {
      if (!b?.isConnected || this.t.scroller.contains(b)) this.t.scroller.focus({ preventScroll: true });
      else b.focus({ preventScroll: true });
    }
    return true;
  }

  run() {
    clearTimeout(this.timer);
    this.q = this.input.value.toLowerCase();
    this.far = new Set();
    this.at = -1;
    const seq = ++this.seq;
    this.asking = !!this.q;
    this.match(true);
    if (!this.q) return;
    this.search(this.input.value)
      .then((ids) => {
        if (seq !== this.seq) return;
        this.far = new Set(ids);
        this.asking = false;
        this.match(this.at < 0);
      })
      .catch(() => {
        if (seq !== this.seq) return;
        this.asking = false; // a linked server too old to search: what the client holds
        this.match(this.at < 0);
      });
  }

  // The hits again, keeping the current one. A fresh search starts at the
  // first hit from the row in view down, or the last one above it.
  match(fresh) {
    const cur = this.hits[this.at];
    this.hits = [];
    if (this.q) {
      for (const e of this.t.entries.values()) {
        if (this.far.has(e.id) || held(this.t.whole(e)).includes(this.q)) this.hits.push(e.id);
      }
    }
    this.at = this.hits.indexOf(cur);
    if (fresh && this.hits.length) {
      const from = this.t.selected || this.t.inView()?.dataset.id;
      const order = new Map([...this.t.entries.keys()].map((id, k) => [id, k]));
      const pos = order.get(from) ?? -1;
      const i = this.hits.findIndex((id) => order.get(id) >= pos);
      return this.go(i < 0 ? this.hits.length - 1 : i);
    }
    this.draw();
  }

  step(d) {
    const n = this.hits.length;
    if (n) this.go(this.at < 0 ? (d > 0 ? 0 : n - 1) : (this.at + d + n) % n);
  }

  go(i) {
    this.at = i;
    const id = this.hits[i];
    const n = this.t.reveal(id);
    if (n && !ranges(n, this.q).some((r) => !folded(r))) this.t.open(id);
    this.scroll = true;
    this.draw();
  }

  // The count now, the highlights once a frame.
  draw() {
    const n = this.hits.length;
    this.count.textContent = !this.q ? "" : n ? `${this.at + 1} of ${n}` : this.asking ? "searching…" : "no matches";
    this.bar.querySelector(".prev").disabled = this.bar.querySelector(".next").disabled = !n;
    if (!this.frame) this.frame = requestAnimationFrame(() => this.highlight());
  }

  highlight() {
    this.frame = 0;
    if (this.bar.hidden) return;
    const all = [];
    let now = [];
    const cur = this.hits[this.at];
    for (const id of this.hits) {
      const node = this.t.nodes.get(id);
      if (!node) continue;
      const rs = ranges(node, this.q).filter((r) => !folded(r));
      if (id === cur) now = rs;
      else all.push(...rs);
    }
    paint("find", all);
    paint("find-now", now);
    // Once the row has its detail: a fetch replaces it, and the observer
    // brings us back here.
    const node = this.t.nodes.get(cur);
    if (this.scroll && node && !node.querySelector(".loading")) {
      this.scroll = false;
      if (now.length) center(now[0], this.t.scroller);
    }
  }
}
