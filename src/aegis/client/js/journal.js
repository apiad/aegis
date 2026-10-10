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

export function today() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

export class Journal {
  constructor(conn, box, { onOpen }) {
    this.conn = conn;
    this.box = box;
    this.onOpen = onOpen;
    this.rows = [];
    this.more = false;
    this.timer = null;
    this.drawn = false;
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
      n.addEventListener("input", () => {
        clearTimeout(this.timer);
        this.timer = setTimeout(() => this.load(false), 200);
      });
    }
  }

  open() {
    this.draw();
    if (!this.day.value) this.day.value = today();
    this.load(false);
    this.q.focus();
  }

  // A patch on the journal channel: reload only while the view shows.
  changed() {
    if (this.drawn && this.box.offsetParent !== null) this.load(false);
  }

  params(offset) {
    const p = { limit: PAGE, offset };
    const set = (k, v) => {
      if (v.trim()) p[k] = v.trim();
    };
    set("pattern", this.q.value);
    set("path", this.path.value);
    set("session", this.session.value);
    if (this.kind.value) p.kind = [this.kind.value];
    if (this.day.value) {
      p.since = this.day.value;
      p.until = this.day.value;
    }
    return p;
  }

  async load(more) {
    let res;
    try {
      res = await this.conn.call("journal.rows", this.params(more ? this.rows.length : 0));
    } catch (e) {
      this.rows = [];
      this.more = false;
      this.render(e.message);
      return;
    }
    this.rows = more ? this.rows.concat(res.rows) : res.rows;
    this.more = res.more;
    this.render();
  }

  render(failed) {
    const out = [];
    let day = null;
    for (const r of this.rows) {
      if (r.day !== day) {
        day = r.day;
        out.push(el("div", "jday-h", day));
      }
      const row = el("div", `jrow k-${r.kind}`);
      row.tabIndex = 0;
      const body = el("span", "x", (r.tag ? `${r.tag}: ` : "") + r.text);
      if (r.paths.length) {
        body.append(el("span", "p", r.paths.join("  ") + (r.more ? `  +${r.more} more` : "")));
      }
      row.append(el("span", "t", r.time), el("span", "g", r.glyph), el("span", "h", r.handle), body);
      row.addEventListener("click", () => this.onOpen(r));
      row.addEventListener("keydown", (e) => {
        if (e.key === "Enter") this.onOpen(r);
      });
      out.push(row);
    }
    if (!out.length) out.push(el("div", "empty", failed || "Nothing in the journal matches."));
    this.list.replaceChildren(...out);
    this.foot.textContent = failed ? "" : `${this.rows.length} entries${this.more ? ", more below" : ""}`;
    this.moreBtn.hidden = !this.more;
  }
}
