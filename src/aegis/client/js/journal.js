// The Journal view: one search box over journal.rows, drawn as a timeline,
// newest first, 50 rows at a time. The server matches the box and decides
// every field a row shows, the marks in its text among them
// (src/aegis/journal/render.py); this module draws and asks, nothing more.

const KINDS = ["turn", "commit", "pr", "plan", "note", "session"];
const PAGE = 50;
const SVG = "http://www.w3.org/2000/svg";

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined && text !== null) n.textContent = text;
  return n;
}

function glass() {
  const s = document.createElementNS(SVG, "svg");
  s.setAttribute("viewBox", "0 0 16 16");
  s.setAttribute("aria-hidden", "true");
  s.innerHTML = '<circle cx="7" cy="7" r="4.5"/><path d="M10.5 10.5 14 14"/>';
  return s;
}

// `chars` with the code points at `marks` in <mark>s, runs of marked letters
// in one. The server counts code points, so this does too.
function marked(chars, marks) {
  const out = [];
  let run = "";
  let on = false;
  const flush = () => {
    if (!run) return;
    out.push(on ? el("mark", null, run) : document.createTextNode(run));
    run = "";
  };
  chars.forEach((c, i) => {
    const m = marks.has(i);
    if (m !== on) {
      flush();
      on = m;
    }
    run += c;
  });
  flush();
  return out;
}

// One entry's time, glyph on the thread, and body, for the view and for the
// sidebar's card alike. `who`: name the session above the text.
export function entry(r, who = true) {
  const row = el("div", `jent k-${r.kind}`);
  row.tabIndex = 0;
  row.setAttribute("role", "button");
  row.dataset.id = r.id;
  const g = el("span", "g");
  g.title = r.kind;
  g.append(el("span", null, r.glyph));
  const body = el("div", "b");
  if (who) {
    const line = el("div", "who");
    line.append(el("span", "h", r.handle));
    body.append(line);
  }
  const x = el("div", "x");
  if (r.tag) x.append(el("span", "jtag", r.tag));
  if (r.hash) {
    const hash = el("span", "hash");
    hash.append(...marked(Array.from(r.hash), new Set(r.hash_marks)));
    x.append(hash, " ");
  }
  x.append(...marked(Array.from(r.text), new Set(r.marks)));
  body.append(x);
  if (r.paths.length) {
    const ps = el("div", "ps");
    ps.append(...r.paths.map((p) => Object.assign(el("span", "pchip", p), { title: p })));
    if (r.more) ps.append(el("span", "pchip pmore", `+${r.more}`));
    body.append(ps);
  }
  row.append(el("span", "t", r.time), g, body);
  return row;
}

// Enter and Space open the entry; preventDefault keeps keys.js from acting
// on them too.
export function opens(row, open) {
  row.addEventListener("click", open);
  row.addEventListener("keydown", (e) => {
    if (e.key !== "Enter" && e.key !== " ") return;
    e.preventDefault();
    open();
  });
}

export class Journal {
  constructor(conn, box, { onOpen }) {
    this.conn = conn;
    this.box = box;
    this.onOpen = onOpen;
    this.rows = [];
    this.more = false;
    this.today = "";
    this.timer = null;
    this.patchTimer = null;
    this.seq = 0; // the newest request; an older answer is dropped
    this.drawn = false;
    this.kind = "";
    // "today", "any", or null until the person taps the day chip. Until then
    // an empty box shows the server's today and a typed one every day: "what
    // touched this" is rarely only today.
    this.day = null;
  }

  // The day the next load asks for: "today" or "any".
  when() {
    return this.day || (this.q.value.trim() ? "any" : "today");
  }

  draw() {
    if (this.drawn) return;
    this.drawn = true;
    this.q = Object.assign(el("input", "jq"), {
      placeholder: "Search the journal: words, a path, a session…",
      spellcheck: false,
      autocomplete: "off",
    });
    this.q.setAttribute("autocapitalize", "off");
    this.q.setAttribute("autocorrect", "off");
    this.q.setAttribute("aria-label", "Search the journal");
    this.dayBtn = el("button", "jwhen");
    this.dayBtn.type = "button";
    this.dayBtn.addEventListener("click", () => {
      this.day = this.when() === "today" ? "any" : "today";
      this.load(false);
    });
    const search = el("label", "jsearch");
    search.append(glass(), this.q, this.dayBtn);
    this.kinds = el("div", "jkinds");
    this.kinds.setAttribute("role", "group");
    this.kinds.setAttribute("aria-label", "Kind");
    for (const k of ["", ...KINDS]) {
      const b = el("button", "jkind", k || "all");
      b.type = "button";
      b.dataset.kind = k;
      b.addEventListener("click", () => {
        this.kind = k;
        this.load(false);
      });
      this.kinds.append(b);
    }
    this.count = el("div", "jcount");
    this.count.setAttribute("aria-live", "polite");
    this.list = el("div", "jlist");
    this.moreBtn = el("button", "btn jmore", `Show ${PAGE} more`);
    this.moreBtn.hidden = true;
    this.moreBtn.addEventListener("click", () => this.load(true));
    this.box.replaceChildren(search, this.kinds, this.count, this.list, this.moreBtn);
    this.q.addEventListener("input", () => {
      clearTimeout(this.timer);
      this.timer = setTimeout(() => this.load(false), 200);
    });
    this.chips();
  }

  // The kind and day chips, from the state, before the answer comes back.
  chips() {
    for (const b of this.kinds.children) b.setAttribute("aria-pressed", String(b.dataset.kind === this.kind));
    const today = this.when() === "today";
    this.dayBtn.textContent = today ? "today" : "any day";
    this.dayBtn.setAttribute("aria-pressed", String(today));
    this.dayBtn.title = today
      ? `${this.today || "Today"} only: tap for every day`
      : "Every day: tap for today only";
  }

  open() {
    this.draw();
    this.load(false);
    this.q.focus();
  }

  // A patch on the journal channel: reload only while the view shows, as long
  // as the list already is, so a paged list keeps its length.
  changed() {
    if (!this.drawn || this.box.offsetParent === null) return;
    clearTimeout(this.patchTimer);
    // journal.rows takes at most 500 rows; a longer list reloads its first 500.
    const limit = Math.min(500, Math.max(PAGE, this.rows.length));
    this.patchTimer = setTimeout(() => this.load(false, limit), 300);
  }

  params(offset, limit = PAGE) {
    const p = { limit, offset };
    const q = this.q.value.trim();
    if (q) p.q = q;
    if (this.kind) p.kind = [this.kind];
    if (this.when() === "today") {
      p.since = "today";
      p.until = "today";
    }
    return p;
  }

  async load(more, limit = PAGE) {
    if (more && this.moreBtn.disabled) return; // a page is already on its way
    clearTimeout(this.timer);
    const n = ++this.seq;
    this.moreBtn.disabled = true;
    this.chips();
    let res;
    const p = this.params(more ? this.rows.length : 0, limit);
    try {
      res = await this.conn.call("journal.rows", p);
    } catch (e) {
      if (n !== this.seq) return;
      this.moreBtn.disabled = false;
      this.rows = [];
      this.more = false;
      this.render(p, e.message);
      return;
    }
    if (n !== this.seq) return;
    this.moreBtn.disabled = false;
    this.today = res.today;
    this.rows = more ? this.rows.concat(res.rows) : res.rows;
    this.more = res.more;
    this.chips();
    this.render(p);
  }

  render(p, failed = null) {
    const held = this.list.contains(document.activeElement) ? document.activeElement.dataset.id : null;
    const days = [];
    let thread = null;
    let day = null;
    for (const r of this.rows) {
      if (r.day !== day) {
        day = r.day;
        const head = el("div", "jday-h");
        head.append(el("span", null, day));
        thread = el("div", "jthread");
        const group = el("section", "jday");
        group.append(head, thread);
        days.push(group);
      }
      const row = entry(r);
      row.classList.add("jrow");
      opens(row, () => this.onOpen(r));
      thread.append(row);
    }
    const q = p.q ? `“${p.q}”` : "";
    const when = p.since ? "today" : "on any day";
    const kind = this.kind ? `${this.kind} ` : "";
    if (!days.length) {
      const what = q ? `${kind}entries match ${q} ${when}` : `${kind}entries ${when}`;
      days.push(el("div", "empty", failed || `No ${what}.`));
    }
    this.list.replaceChildren(...days);
    if (held) this.list.querySelector(`[data-id="${CSS.escape(held)}"]`)?.focus();
    const n = this.rows.length;
    const counted = `${n}${this.more ? "+" : ""} ${kind}${n === 1 && !this.more ? "entry" : "entries"}`;
    this.count.textContent = failed || !n ? "" : q ? `${counted} matching ${q} ${p.since ? "today" : ""}`.trim() : `${counted} ${when}`;
    this.moreBtn.hidden = !this.more;
  }
}
