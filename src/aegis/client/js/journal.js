// The Journal view: a filter bar over journal.rows, newest first, 50 rows at a
// time. The server searches and decides every field a row shows
// (src/aegis/journal/render.py); this module draws and asks, nothing more.

import "./pick.js";

const KINDS = ["", "turn", "commit", "pr", "plan", "note", "session"];
const PAGE = 50;

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined && text !== null) n.textContent = text;
  return n;
}

export class Journal {
  constructor(conn, box, { onOpen }) {
    this.conn = conn;
    this.box = box;
    this.onOpen = onOpen;
    this.rows = [];
    this.more = false;
    this.timer = null;
    this.patchTimer = null;
    this.seq = 0; // the newest request; an older answer is dropped
    this.drawn = false;
    // The day input counts once the person picks or clears it. Until then the
    // view shows the server's today with every filter empty, and every day
    // as soon as one is set: "what touched this" is rarely only today.
    this.picked = false;
  }

  draw() {
    if (this.drawn) return;
    this.drawn = true;
    const field = (cls, placeholder) =>
      Object.assign(el("input", cls), { placeholder, spellcheck: false, autocomplete: "off" });
    this.q = field("jq", "pattern");
    this.path = field("jpath", "path prefix");
    this.session = field("jsess", "session");
    this.day = Object.assign(el("input", "jday"), { type: "date" });
    this.kind = document.createElement("pick-chip");
    this.kind.setAttribute("aria-label", "Kind");
    const bar = el("div", "jbar");
    bar.append(this.q, this.path, this.session, this.kind, this.day);
    this.list = el("div", "jlist");
    this.foot = el("div", "jfoot");
    this.moreBtn = el("button", "btn", `Show ${PAGE} more`);
    this.moreBtn.hidden = true;
    this.moreBtn.addEventListener("click", () => this.load(true));
    this.box.replaceChildren(bar, this.list, this.foot, this.moreBtn);
    this.kind.options = KINDS.map((k) => ({ value: k, label: k || "every kind" }));
    this.kind.value = "";
    for (const n of [this.q, this.path, this.session, this.kind, this.day]) {
      n.addEventListener("input", (e) => {
        // The chip's own input fires on typing in its list; only a pick counts.
        if (n === this.kind && e.target !== n) return;
        if (n === this.day) this.picked = true;
        clearTimeout(this.timer);
        this.timer = setTimeout(() => this.load(false), 200);
      });
    }
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
    const set = (k, v) => {
      if (v.trim()) p[k] = v.trim();
    };
    set("pattern", this.q.value);
    set("path", this.path.value);
    set("session", this.session.value);
    if (this.kind.value) p.kind = [this.kind.value];
    const day = this.picked ? this.day.value : Object.keys(p).length > 2 ? "" : "today";
    if (day) {
      p.since = day;
      p.until = day;
    }
    return p;
  }

  async load(more, limit = PAGE) {
    if (more && this.moreBtn.disabled) return; // a page is already on its way
    const n = ++this.seq;
    this.moreBtn.disabled = true;
    let res;
    const p = this.params(more ? this.rows.length : 0, limit);
    try {
      res = await this.conn.call("journal.rows", p);
    } catch (e) {
      if (n !== this.seq) return;
      this.moreBtn.disabled = false;
      this.rows = [];
      this.more = false;
      this.render(e.message);
      return;
    }
    if (n !== this.seq) return;
    this.moreBtn.disabled = false;
    const day = p.since === "today" ? res.today : p.since || "";
    if (!this.picked) this.day.value = day;
    this.rows = more ? this.rows.concat(res.rows) : res.rows;
    this.more = res.more;
    this.render(null, day ? `on ${day}` : "on any day");
  }

  render(failed, when = "") {
    const held = this.list.contains(document.activeElement) ? document.activeElement.dataset.id : null;
    const out = [];
    let day = null;
    for (const r of this.rows) {
      if (r.day !== day) {
        day = r.day;
        out.push(el("div", "jday-h", day));
      }
      const row = el("div", `jrow k-${r.kind}`);
      row.tabIndex = 0;
      row.setAttribute("role", "button");
      row.dataset.id = r.id;
      const body = el("span", "x", (r.tag ? `${r.tag}: ` : "") + r.text);
      if (r.paths.length) {
        body.append(el("span", "p", r.paths.join("  ") + (r.more ? `  +${r.more} more` : "")));
      }
      row.append(el("span", "t", r.time), el("span", "g", r.glyph), el("span", "h", r.handle), body);
      row.addEventListener("click", () => this.onOpen(r));
      row.addEventListener("keydown", (e) => {
        if (e.key !== "Enter" && e.key !== " ") return;
        e.preventDefault();
        this.onOpen(r);
      });
      out.push(row);
    }
    if (!out.length) out.push(el("div", "empty", failed || `Nothing in the journal matches ${when}.`));
    this.list.replaceChildren(...out);
    if (held) out.find((n) => n.dataset?.id === held)?.focus();
    this.foot.textContent = failed ? "" : `${this.rows.length} entries${this.more ? ", more below" : ""}`;
    this.moreBtn.hidden = !this.more;
  }
}
