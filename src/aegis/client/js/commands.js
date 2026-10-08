// The composer's command menu: one list for typing "/" and for Alt+/.
//
// Everything it shows comes from `commands.list`: names, hints, descriptions,
// sources, models, efforts. It decides only which rows match what is typed;
// the server resolves and runs the line (commands.py).

// The legacy TUI's scorer (legacy/aegis/commands/fuzzy.py): a case-insensitive
// subsequence, +2 per character that follows the previous match, +3 at a word
// start, and -0.01 per character of length so shorter names win ties.
export function fuzzy(query, text) {
  const q = query.toLowerCase();
  const t = text.toLowerCase();
  let score = 0;
  let last = -2;
  const positions = [];
  let j = 0;
  for (let i = 0; i < t.length && j < q.length; i++) {
    if (t[i] !== q[j]) continue;
    if (i === last + 1) score += 2;
    if (i === 0 || /[\s:_\-/.]/.test(t[i - 1])) score += 3;
    positions.push(i);
    last = i;
    j++;
  }
  if (j < q.length) return null;
  return { score: score - 0.01 * t.length, positions };
}

const RANK = { aegis: 0, claude: 1 };

function ranked(query, rows, key) {
  if (!query) return rows.map((r) => ({ ...r, positions: [] })); // the list's own order: aegis first
  return rows
    .map((r, i) => ({ r, i, m: fuzzy(query, key(r)) }))
    .filter((x) => x.m)
    .sort((a, b) => b.m.score - a.m.score || (RANK[a.r.source] ?? 2) - (RANK[b.r.source] ?? 2) || a.i - b.i)
    .map((x) => ({ ...x.r, positions: x.m.positions }));
}

// What a line can become. `meta` is the session's card: its model, for /effort.
export function complete(line, catalog, meta) {
  if (!line.startsWith("/") || line.startsWith("//")) return { items: [] };
  const sp = line.indexOf(" ");
  if (sp < 0) {
    const items = ranked(line.slice(1), catalog.commands, (c) => c.name).map((c) => ({
      insert: `/${c.name} `,
      label: `/${c.name}`,
      positions: c.positions.map((p) => p + 1),
      hint: c.hint,
      doc: c.doc,
      source: c.source,
    }));
    return { items };
  }
  const name = line.slice(1, sp);
  const arg = line.slice(sp + 1).trim();
  const cmd = catalog.commands.find((c) => c.name === name);
  if (!cmd || !cmd.args || arg.includes(" ")) return { items: [] };
  let choices = [];
  if (cmd.args === "models")
    choices = catalog.models.map((m) => ({ v: m.value, doc: `${m.label}. ${m.doc}`, source: m.efforts.length ? "effort" : "" }));
  if (cmd.args === "permissions") choices = catalog.permissions.map((p) => ({ v: p, doc: "", source: "" }));
  if (cmd.args === "efforts") {
    const m = catalog.models.find((x) => meta && (x.value === meta.model || x.resolved === meta.model)) || null;
    choices = (m ? m.efforts : ["low", "medium", "high", "xhigh", "max"]).map((e) => ({ v: e, doc: "", source: "" }));
  }
  const items = ranked(arg, choices, (c) => c.v).map((c) => ({
    insert: `/${name} ${c.v} `,
    label: c.v,
    positions: c.positions,
    hint: "",
    doc: c.doc,
    source: c.source,
  }));
  return { items };
}

function rowNode(item, on) {
  const r = document.createElement("div");
  r.className = `cmd-row${on ? " on" : ""}`;
  const nm = document.createElement("span");
  nm.className = "nm";
  const hit = new Set(item.positions || []);
  [...item.label].forEach((ch, i) => {
    if (hit.has(i)) {
      const b = document.createElement("b");
      b.textContent = ch;
      nm.append(b);
    } else nm.append(ch);
  });
  const cell = (cls, text) => {
    const n = document.createElement("span");
    n.className = cls;
    n.textContent = text || "";
    return n;
  };
  r.append(nm, cell("hn", item.hint), cell("dc", item.doc), cell("src", item.source));
  return r;
}

export class CommandMenu {
  // load(): Promise<catalog> for the focused session, or null when there is none.
  // run(line): send a line from the overlay. getLine/setLine: the composer.
  constructor({ box, rows, filter, load, run, getLine, setLine, meta }) {
    Object.assign(this, { box, rows, filter, load, run, getLine, setLine, meta });
    this.items = [];
    this.at = 0;
    this.catalog = null;
    this.overlay = false;
    rows.addEventListener("mousedown", (ev) => {
      const r = ev.target.closest(".cmd-row");
      if (!r) return;
      ev.preventDefault();
      this.at = [...rows.children].indexOf(r);
      this.accept();
    });
    filter.addEventListener("input", () => this.refresh());
    filter.addEventListener("keydown", (ev) => {
      if (this.onKey(ev)) return;
      if (ev.key === "Enter") {
        ev.preventDefault();
        const line = filter.value.trim();
        this.close();
        if (line) this.run(line);
      }
    });
  }

  get isOpen() {
    return !this.box.hidden;
  }

  line() {
    return this.overlay ? this.filter.value : this.getLine();
  }

  setText(v) {
    if (this.overlay) this.filter.value = v;
    else this.setLine(v);
  }

  async show() {
    this.catalog = await this.load();
    if (!this.catalog) return;
    this.box.hidden = false;
    this.refresh();
  }

  openInline() {
    this.overlay = false;
    this.filter.hidden = true;
    return this.show();
  }

  async openOverlay() {
    this.overlay = true;
    this.filter.hidden = false;
    this.filter.value = "/";
    await this.show();
    this.filter.focus();
  }

  close() {
    this.box.hidden = true;
    this.overlay = false;
    this.filter.hidden = true;
  }

  refresh() {
    if (!this.catalog) return;
    const line = this.line();
    if (!line.startsWith("/") || line.startsWith("//")) {
      if (!this.overlay) this.close();
      return;
    }
    this.items = complete(line, this.catalog, this.meta()).items.slice(0, 50);
    this.at = 0;
    this.rows.replaceChildren(...this.items.map((it, i) => rowNode(it, i === this.at)));
  }

  // Whether the line names a known command, for the composer's outline.
  known(line) {
    if (!this.catalog) return true;
    const name = line.slice(1).split(/\s/)[0];
    return !name || this.catalog.commands.some((c) => c.name === name);
  }

  move(d) {
    if (!this.items.length) return;
    this.at = (this.at + d + this.items.length) % this.items.length;
    [...this.rows.children].forEach((r, i) => r.classList.toggle("on", i === this.at));
    this.rows.children[this.at]?.scrollIntoView({ block: "nearest" });
  }

  accept() {
    const it = this.items[this.at];
    if (!it) return;
    this.setText(it.insert);
    this.refresh();
  }

  // Tab accepts; Enter accepts while that would change the line, and is left
  // to the caller (send) once the line is a whole command. An aegis command
  // whose argument is missing never sends: Enter opens its arguments.
  onKey(ev) {
    if (!this.isOpen) return false;
    const line = this.line();
    const it = this.items[this.at];
    if (ev.key === "ArrowDown" || ev.key === "ArrowUp") {
      ev.preventDefault();
      this.move(ev.key === "ArrowDown" ? 1 : -1);
      return true;
    }
    if (ev.key === "Escape") {
      ev.preventDefault();
      ev.stopPropagation();
      this.close();
      return true;
    }
    if (ev.key === "Tab" && it) {
      ev.preventDefault();
      this.accept();
      return true;
    }
    if (ev.key === "Enter" && !ev.shiftKey && it && it.insert.trimEnd() !== line.trimEnd()) {
      ev.preventDefault();
      this.accept();
      return true;
    }
    if (ev.key === "Enter" && !ev.shiftKey) {
      const name = line.slice(1).split(/\s/)[0];
      const cmd = this.catalog?.commands.find((c) => c.name === name);
      if (cmd?.args && !line.trim().includes(" ")) {
        ev.preventDefault();
        this.setText(`/${name} `);
        this.refresh();
        return true;
      }
    }
    return false;
  }
}
