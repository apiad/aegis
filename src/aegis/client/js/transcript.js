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
// up, new entries light the navigator's latest button instead of moving the
// page. Rows skip
// layout off screen (content-visibility in base.css) and take their real
// height when they near the viewport, so while following, a change in the
// list's height pins it to the bottom again.
//
// It also watches what the reader has seen: an unread agent message on screen
// for a second is reported through onRead. Only mounted unread rows are
// observed, and a row is unobserved as it is replaced or dropped.
//
// The fold levels hide the work between what was said: at level 1 the tool
// calls, thinking and notes, at level 2 everything but the messages. Each run
// of entries whose `fold` (set by the server) is within the level is drawn as
// one line on its first mounted row, and the rest of the run is hidden (fold()). Hidden rows stay mounted, so the
// window above still holds; the walks below skip them.

import { hhmm, render, update } from "./entries.js";

const WINDOW = 200;

// A row replaced while one of its controls has focus (a detail fetched, a
// patch) hands the focus to the same control in its new node. Without it, an
// answer landing after Tab reached the row's summary dropped the focus on the
// page, and the next Enter went nowhere.
const FOCUSABLE = "summary, button, a[href]";
function swap(old, n) {
  const a = document.activeElement;
  const at = a && old.contains(a) ? [...old.querySelectorAll(FOCUSABLE)].indexOf(a) : -1;
  old.replaceWith(n);
  if (at >= 0) n.querySelectorAll(FOCUSABLE)[at]?.focus({ preventScroll: true });
}
const PAGE = 100;
const NEAR_TOP_PX = 400;

export class Transcript {
  constructor(scroller, list, jump, { loadDetail, onRead = () => {}, onSelect = () => {} } = {}) {
    this.scroller = scroller;
    this.list = list;
    this.jump = jump;
    this.entries = new Map(); // id -> entry, in transcript order
    this.nodes = new Map(); // id -> row, for the mounted suffix
    this.opened = new Map(); // id -> open, for rows whose details the reader toggled
    this.touched = new Set();
    this.following = true;
    this.selected = null; // an entry id: apply() replaces nodes, ids stay
    this.sinceId = null; // the entry the "new since you left" divider sits above
    this.sinceText = "";
    this.onSelect = onSelect; // after every selection change: each path ends in mark()
    this.loadDetail = loadDetail; // ids -> Promise of whole entries
    this.rev = -1; // the highest revision seen: what a resubscribe asks since
    this.full = new Map(); // id -> the whole entry, fetched when its row opened
    this.fetching = new Set();
    this.foldLevel = 0; // 0 everything shown; 1 the work folded; 2 all but the messages
    this.openRuns = new Set(); // the first entry id of each run the reader opened
    this.headOf = new Map(); // id of a hidden row -> id of the row drawing its run's line
    this.marked = false; // some mounted row carries a fold mark
    // Tab walks the rows' summaries and buttons; the row holding focus is the selection.
    list.addEventListener("focusin", (ev) => {
      const r = ev.target.closest(".row");
      if (!r) return;
      this.selected = r.dataset.id;
      this.mark();
    });
    scroller.addEventListener("scroll", () => {
      this.following = this.atBottom();
      if (this.following) this.jump.classList.remove("new");
      this.fillTop();
    });
    jump.addEventListener("click", () => this.toBottom());
    // A row the reader opened or closed keeps that state across updates and
    // across being unmounted and mounted again.
    list.addEventListener("click", (ev) => {
      const id = ev.target.closest("summary")?.closest(".row")?.dataset.id;
      if (id) this.touched.add(id);
      const line = ev.target.closest(".runline");
      if (line) this.toggleRun(line.parentElement);
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
    // Reading: an unread agent message counts as read once its row has been
    // seen for a second while the page is visible and focused. Seen means half
    // of the row is visible, or it fills half the view: a reply taller than
    // twice the view is never half visible. The observer calls back only when
    // the ratio crosses a threshold, and a row five views tall never passes
    // 0.2, so the thresholds step every 1%.
    this.onRead = onRead;
    this.since = new Map(); // id -> when it was first seen
    this.sent = new Set(); // ids reported and not yet echoed back as read
    this.watch = new IntersectionObserver(
      (items) => {
        for (const it of items) {
          const id = it.target.dataset.id;
          // Ids repeat across sessions: a late entry for a dropped node is not
          // this session's row.
          if (this.nodes.get(id) !== it.target) continue;
          const seen =
            it.isIntersecting &&
            (it.intersectionRatio >= 0.5 ||
              it.intersectionRect.height >= it.rootBounds.height / 2);
          // A replaced row's new node keeps the time its old one was first seen.
          if (!seen) this.since.delete(id);
          else if (!this.since.has(id)) this.since.set(id, performance.now());
        }
      },
      { root: scroller, threshold: Array.from({ length: 101 }, (_, i) => i / 100) },
    );
    const looking = () => document.visibilityState === "visible" && document.hasFocus();
    const restart = () => {
      for (const id of this.since.keys()) this.since.set(id, performance.now());
    };
    window.addEventListener("focus", restart);
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible") restart();
    });
    setInterval(() => {
      if (!looking()) return;
      const now = performance.now();
      const ids = [];
      for (const [id, t] of this.since) {
        if (now - t >= 1000 && this.entries.get(id)?.unread && !this.sent.has(id)) ids.push(id);
      }
      if (ids.length) {
        for (const id of ids) this.sent.add(id);
        this.onRead(ids);
      }
    }, 300);
  }

  atBottom() {
    const s = this.scroller;
    return s.scrollHeight - s.scrollTop - s.clientHeight < 48;
  }

  toBottom() {
    this.scroller.scrollTop = this.scroller.scrollHeight;
    this.following = true;
    this.jump.classList.remove("new");
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
    // Every path that mounts the divider's row draws the divider with it.
    if (e.id === this.sinceId) {
      n.classList.add("since");
      n.dataset.since = this.sinceText;
    }
    this.nodes.set(e.id, n);
    if (e.unread) this.watch.observe(n);
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
          if (cur && old && cur.rev === f.rev) swap(old, this.mount(cur));
        }
        this.fold();
        this.mark();
        const f = got.find((x) => x.id === id);
        if (f && this.entries.get(id)?.rev !== f.rev && this.nodes.get(id)?.querySelector("details")?.open) this.fetch(id);
      })
      .catch(() => this.fetching.delete(id));
  }

  // A whole snapshot of the session already shown (a reconnect the server
  // answers in full, a gap in the patches) keeps the divider where it was;
  // switching sessions clears it (stash, clear).
  snapshot(data) {
    const { sinceId, sinceText } = this;
    this.clear();
    this.sinceId = sinceId;
    this.sinceText = sinceText;
    this.rev = data.rev;
    for (const e of data.entries) this.entries.set(e.id, e);
    const frag = document.createDocumentFragment();
    for (const e of data.entries.slice(-WINDOW)) frag.append(this.mount(e));
    this.list.replaceChildren(frag);
    this.fold();
    this.mark();
    this.toBottom();
    this.fillTop();
  }

  // A delta: what changed since the revision this tab held, removals first.
  resume(data) {
    this.apply([...data.removed.map((id) => ({ remove: id })), ...data.entries.map((e) => ({ upsert: e }))]);
    this.rev = Math.max(this.rev, data.rev);
  }

  // Hands over this tab's data and starts empty; restore() takes it back.
  stash() {
    const s = {
      entries: this.entries,
      full: this.full,
      opened: this.opened,
      touched: this.touched,
      openRuns: this.openRuns,
      rev: this.rev,
    };
    this.entries = new Map();
    this.full = new Map();
    this.opened = new Map();
    this.touched = new Set();
    this.openRuns = new Set();
    this.clear();
    return s;
  }

  restore(s) {
    this.clear();
    const { entries, full, opened, touched, openRuns, rev } = s;
    Object.assign(this, { entries, full, opened, touched, openRuns, rev });
    const frag = document.createDocumentFragment();
    for (const e of [...this.entries.values()].slice(-WINDOW)) frag.append(this.mount(e));
    this.list.replaceChildren(frag);
    this.fold();
    this.mark();
    this.toBottom();
    this.fillTop();
  }

  // The PAGE entries before the first mounted row, mounted above it, with the
  // reader's place kept: the row that was first stays where it was on screen.
  // In the prose view that is the first row shown, or the line of its run once
  // the run's line moves up to a row mounted above it.
  // False when every entry is already mounted.
  mountEarlier() {
    const start = this.entries.size - this.nodes.size;
    if (start <= 0) return false;
    const ids = [...this.entries.keys()].slice(Math.max(0, start - PAGE), start);
    const ref = [...this.list.children].find((n) => this.shown(n));
    const top = ref?.getBoundingClientRect().top;
    const frag = document.createDocumentFragment();
    for (const id of ids) frag.append(this.mount(this.entries.get(id)));
    this.list.prepend(frag);
    this.fold();
    const now = ref && this.nodes.get(this.headOf.get(ref.dataset.id) ?? ref.dataset.id);
    if (now) this.scroller.scrollTop += now.getBoundingClientRect().top - top;
    return true;
  }

  // Mounts earlier pages while the reader is near the top. One page is enough
  // unless the prose view hid most of it.
  fillTop() {
    while (this.scroller.scrollTop < NEAR_TOP_PX && this.mountEarlier());
  }

  apply(ops) {
    let added = false;
    for (const op of ops) {
      if (op.upsert) {
        const e = op.upsert;
        if (e.rev > this.rev) this.rev = e.rev;
        const known = this.entries.has(e.id);
        this.entries.set(e.id, e);
        if (!e.unread) {
          this.sent.delete(e.id);
          this.since.delete(e.id);
        }
        const old = this.nodes.get(e.id);
        if (old) {
          if (update(e, old)) this.nodes.set(e.id, old);
          else {
            this.watch.unobserve(old);
            swap(old, this.mount(e));
          }
        }
        else if (!known) {
          this.list.append(this.mount(e));
          added = true;
        }
        // A known entry above the mounted rows changes only its data.
      } else if (op.remove !== undefined) {
        this.entries.delete(op.remove);
        const old = this.nodes.get(op.remove);
        if (old) {
          this.watch.unobserve(old);
          old.remove();
        }
        this.since.delete(op.remove);
        this.nodes.delete(op.remove);
      }
    }
    if (this.following) {
      // Rows above the window go while the reader is at the bottom.
      while (this.nodes.size > WINDOW + PAGE) {
        const first = this.list.firstElementChild;
        this.nodes.delete(first.dataset.id);
        this.watch.unobserve(first);
        this.since.delete(first.dataset.id);
        first.remove();
      }
    }
    this.fold();
    this.mark();
    if (this.following) this.toBottom();
    else if (added) this.jump.classList.add("new");
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
    this.sinceId = null;
    this.openRuns.clear();
    this.headOf.clear();
    this.marked = false;
    this.watch.disconnect();
    this.since.clear();
    this.sent.clear();
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
    this.list.querySelector(".row.since")?.classList.remove("since");
    const s = this.sinceId ? this.nodes.get(this.headOf.get(this.sinceId) ?? this.sinceId) : null;
    if (s) {
      s.classList.add("since");
      s.dataset.since = this.sinceText;
    }
    this.onSelect();
    return n;
  }

  // "New since you left": a style on the row it sits above, never a row of its
  // own, so j/k and the navigator cannot land on it. Placed once, when the tab
  // is opened, after the last read agent message before the first unread one,
  // and left there while the tab stays open.
  setSince(text) {
    this.sinceText = text;
    const ids = [...this.entries.keys()];
    const firstUnread = ids.findIndex((id) => this.entries.get(id).unread);
    if (firstUnread < 0) {
      this.sinceId = null;
    } else {
      let at = firstUnread;
      for (let i = firstUnread - 1; i >= 0; i--) {
        const e = this.entries.get(ids[i]);
        if (e.kind === "prose") break;
        if (e.kind === "user") {
          at = i;
          break;
        }
        at = i;
      }
      this.sinceId = ids[at];
    }
    this.mark();
  }

  // A hidden row is selected through the line of its run.
  select(id) {
    this.selected = this.headOf.get(id) ?? id;
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
    for (let i = rows.length - 1; i >= 0; i--) {
      if (this.shown(rows[i]) && rows[i].getBoundingClientRect().top < bottom) return rows[i];
    }
    return null;
  }

  shown(n) {
    return !this.headOf.has(n.dataset.id);
  }

  pick() {
    if (!this.selected) this.select(this.inView()?.dataset.id || null);
  }

  // The find bar's step (js/find.js): the entry's row mounted, its run open,
  // selected and centered. The row it returns may still be skipping layout.
  reveal(id) {
    while (!this.nodes.has(id) && this.mountEarlier());
    const n = this.nodes.get(id);
    if (!n) return null;
    if (n.dataset.fold && !n.classList.contains("run-open")) {
      this.openRuns.add(this.nodes.get(this.headOf.get(id) ?? id).dataset.run);
      this.fold();
    }
    this.following = false;
    this.selected = id;
    this.mark();
    n.scrollIntoView({ block: "center" });
    return n;
  }

  // Opens a row's details as the reader would, so they stay open.
  open(id) {
    const d = this.nodes.get(id)?.querySelector("details");
    if (!d || d.open) return;
    this.touched.add(id);
    d.open = true;
  }

  // Walking up past the first mounted row mounts the page before it.
  move(delta, keep = () => true) {
    let n = this.selected ? this.nodes.get(this.selected) : null;
    if (!n) return this.pick();
    for (;;) {
      const next = delta > 0 ? n.nextElementSibling : n.previousElementSibling;
      if (!next && delta < 0 && this.mountEarlier()) continue;
      n = next;
      if (!n || (this.shown(n) && keep(n))) break;
    }
    if (n) this.select(n.dataset.id);
  }

  moveTurn(delta) {
    this.move(delta, (n) => n.classList.contains("user"));
  }

  // -- the navigator: agent messages ---------------------------------------

  isProse = (n) => n.classList.contains("prose");

  message(delta) {
    if (!this.selected) this.pick();
    this.move(delta, this.isProse);
  }

  firstUnread() {
    const id = [...this.entries.keys()].find((i) => this.entries.get(i).unread);
    if (!id) return this.edge(true);
    while (!this.nodes.has(id) && this.mountEarlier());
    this.select(id);
  }

  // "2 unread · message 3 of 4": counts over every entry, not only the mounted.
  position() {
    const prose = [...this.entries.values()].filter((e) => e.kind === "prose");
    const unread = prose.filter((e) => e.unread).length;
    const at = this.selected ? prose.findIndex((e) => e.id === this.selected) : -1;
    return { index: at < 0 ? prose.length : at + 1, total: prose.length, unread };
  }

  // The first entry is mounted on the way: an explicit jump may pay for it.
  edge(last) {
    if (!last) while (this.mountEarlier());
    const n = last ? this.list.lastElementChild : this.list.firstElementChild;
    if (last) this.toBottom();
    if (n) this.select(n.dataset.id);
  }

  // On a run's line, Enter opens or closes the run, not the row's details.
  toggle() {
    const n = this.selected ? this.nodes.get(this.selected) : null;
    if (n?.dataset.fold === "head") return this.toggleRun(n);
    const d = n?.querySelector("details");
    if (!d) return;
    this.touched.add(this.selected);
    d.open = !d.open;
  }

  press() {
    const n = this.selected ? this.nodes.get(this.selected) : null;
    n?.querySelector("a.btn, button:not(.copy)")?.click();
  }

  // -- the fold levels ------------------------------------------------------

  // The row the reader looks at stays on screen across the switch.
  setFoldLevel(level) {
    const at = this.selected || this.inView()?.dataset.id;
    this.foldLevel = level;
    this.list.classList.toggle("prose-view", level > 0);
    this.fold();
    if (this.selected) this.selected = this.headOf.get(this.selected) ?? this.selected;
    this.mark();
    if (this.following) this.toBottom();
    else if (at) this.nodes.get(this.headOf.get(at) ?? at)?.scrollIntoView({ block: "nearest" });
    this.fillTop();
  }

  toggleRun(head) {
    const key = head.dataset.run;
    if (this.openRuns.has(key)) this.openRuns.delete(key);
    else this.openRuns.add(key);
    this.fold();
    this.select(head.dataset.id);
  }

  // Each run of consecutive entries folded at this level gets its line on its first mounted
  // row; the rest of the run is hidden while the run is closed. The line is
  // counted from the whole run's data, so a run that starts above the mounted
  // rows still says all it holds. A run of system notes alone stays as it is:
  // each is one line already. A node whose part did not change is not written.
  fold() {
    if (!this.foldLevel && !this.marked) return; // off, and no row left to undo
    this.headOf.clear();
    const marked = new Set();
    if (this.foldLevel) {
      let run = [];
      const close = (next) => {
        const rows = run.map((e) => this.nodes.get(e.id)).filter(Boolean);
        if (rows.length && run.some((e) => e.kind !== "system")) {
          const open = this.openRuns.has(run[0].id);
          const head = rows[0];
          for (const n of rows) {
            const part = n === head ? "head" : "in";
            if (n.dataset.fold !== part) {
              if (n.dataset.fold === "head") n.querySelector(":scope > .runline")?.remove();
              n.dataset.fold = part;
            }
            n.classList.toggle("run-open", open);
            if (!open && n !== head) this.headOf.set(n.dataset.id, head.dataset.id);
            marked.add(n);
          }
          head.dataset.run = run[0].id;
          drawLine(head, run, next, open);
        }
        run = [];
      };
      for (const e of this.entries.values()) {
        if (e.fold && e.fold <= this.foldLevel) run.push(e);
        else if (run.length) close(e);
      }
      if (run.length) close(null);
    }
    for (const n of this.list.querySelectorAll(".row[data-fold]")) {
      if (marked.has(n)) continue;
      if (n.dataset.fold === "head") n.querySelector(":scope > .runline")?.remove();
      delete n.dataset.fold;
      delete n.dataset.run;
      n.classList.remove("run-open");
    }
    this.marked = marked.size > 0;
    if (this.headOf.has(this.selected)) this.selected = this.headOf.get(this.selected);
  }
}

// A run's line, as a button laid out like a row: its time, a caret, and what
// the run holds.
function drawLine(head, run, next, open) {
  let b = head.querySelector(":scope > .runline");
  if (!b) {
    b = document.createElement("button");
    b.type = "button";
    b.className = "runline";
    for (const cls of ["t", "g", "rs"]) {
      const s = document.createElement("span");
      s.className = cls;
      b.append(s);
    }
    head.prepend(b);
  }
  const [t, g, rs] = b.children;
  const text = runText(run, next);
  const time = hhmm(run[0].ts);
  const caret = open ? "▾" : "▸";
  if (t.textContent !== time) t.textContent = time;
  if (g.textContent !== caret) g.textContent = caret;
  if (rs.textContent !== text) rs.textContent = text;
  b.classList.toggle("running", run.some((e) => e.status === "running"));
  b.title = open ? "Fold these steps" : "Show these steps";
}

// What the rest of a run holds, by kind, in this order: "2 notes".
const COUNTED = [
  ["thinking", "thought"],
  ["system", "note"],
  ["inbox", "inbox message"],
  ["file", "file"],
  ["artifact", "page"],
  ["error", "error"],
  ["recap", "recap"],
];

// "14 tool calls · Bash ×8, Read ×3, Edit · 2 thoughts · 1 failed · 4m 12s".
// The time runs from the run's first entry to the entry after it; a run still
// working names the tool it is in instead. A card of several files counts each.
function runText(run, next) {
  const count = (n, one) => `${n} ${one}${n === 1 ? "" : "s"}`;
  const tools = run.filter((e) => e.kind === "tool");
  const parts = [];
  if (tools.length) {
    const by = new Map();
    for (const e of tools) by.set(e.title, (by.get(e.title) || 0) + 1);
    const names = [...by].sort((a, b) => b[1] - a[1]);
    const listed = names.slice(0, 4).map(([name, n]) => (n > 1 ? `${name} ×${n}` : name));
    if (names.length > 4) listed.push(`${names.length - 4} more`);
    parts.push(count(tools.length, "tool call"), listed.join(", "));
  }
  for (const [kind, one] of COUNTED) {
    const n = run.filter((e) => e.kind === kind).reduce((a, e) => a + (e.detail?.files?.length || 1), 0);
    if (n) parts.push(count(n, one));
  }
  const failed = tools.filter((e) => e.status === "err").length;
  if (failed) parts.push(`${failed} failed`);
  const live = tools.find((e) => e.status === "running");
  if (live) parts.push(`running ${[live.title, live.summary].filter(Boolean).join(": ")}`);
  else {
    const end = next?.ts ?? run.at(-1).ts;
    if (end && run[0].ts && end - run[0].ts >= 1) parts.push(took(end - run[0].ts));
  }
  return parts.join(" · ");
}

function took(secs) {
  const s = Math.round(secs);
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${s % 60}s`;
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
}
