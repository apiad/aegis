// The Knowledge view: the person's own skills and memories, one search box,
// a kind filter and the rows, with an item's text in the editor, its history
// and Delete beside them. The server decides each field (knowledge.list,
// .get, .put, .history, .restore, .remove in knowledge_ops.py); this module
// draws and asks. Every text it draws is set as textContent.

import { open as openEditor } from "./editor.js";
import { ago } from "./fleet.js";
import { icon } from "./glyphs.js";
import { opens } from "./journal.js";
import { ask } from "./dialog.js";

const KINDS = [
  ["all", "all"],
  ["skill", "skills"],
  ["memory", "memories"],
  ["flagged", "flagged"],
];

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined && text !== null) n.textContent = text;
  return n;
}

function button(cls, text, onClick) {
  const b = el("button", cls, text);
  b.type = "button";
  b.addEventListener("click", onClick);
  return b;
}

// The flag a row shows, and its colour class: a proposal first, then the
// server's flags (expired, stale:<path>), else ok.
function flagOf(r) {
  if (r.status === "proposed") return ["proposed", "prop", ""];
  const f = r.flags[0];
  if (!f) return ["ok", "ok", ""];
  const [name, ...rest] = f.split(":");
  return [name, name === "expired" ? "exp" : "stale", rest.join(":")];
}

function used(r) {
  if (!r.last_used) return "never used";
  return `used ${ago(r.last_used)}`;
}

function when(ts) {
  const d = new Date(ts * 1000);
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

export class Knowledge {
  constructor(conn, box, { onOpen, onBack }) {
    this.conn = conn;
    this.box = box;
    this.onOpen = onOpen; // a row's name: the app routes to #knowledge=<name>
    this.onBack = onBack;
    this.kind = "all";
    this.rows = [];
    this.name = null; // the item open in the detail pane
    this.ed = null;
    this.seq = 0;
    this.openSeq = 0;
    this.timer = null;
    this.drawn = false;
  }

  draw() {
    if (this.drawn) return;
    this.drawn = true;
    this.q = Object.assign(el("input", "jq kq"), { type: "search", spellcheck: false, autocomplete: "off" });
    this.q.placeholder = "Search skills and memories";
    this.q.setAttribute("aria-label", "Search skills and memories");
    this.q.setAttribute("autocapitalize", "off");
    this.q.addEventListener("input", () => {
      clearTimeout(this.timer);
      this.timer = setTimeout(() => this.load(), 200);
    });
    const search = el("label", "jsearch");
    search.append(icon("search"), this.q);
    this.kinds = el("div", "jkinds kkinds");
    this.kinds.setAttribute("role", "group");
    this.kinds.setAttribute("aria-label", "Kind");
    for (const [k, label] of KINDS) {
      const b = button("jkind", label, () => {
        this.kind = k;
        this.load();
      });
      b.dataset.kind = k;
      this.kinds.append(b);
    }
    this.count = el("div", "jcount kcount");
    this.count.setAttribute("aria-live", "polite");
    this.list = el("div", "klist");
    this.detail = el("div", "kdetail");
    this.detail.hidden = true;
    const panes = el("div", "kpanes");
    panes.append(this.list, this.detail);
    const top = el("div", "ktop");
    top.append(search, this.kinds, this.count);
    this.box.replaceChildren(top, panes);
  }

  // The view at #knowledge, or #knowledge=<name> with that item open.
  async show(name) {
    this.draw();
    if ((name || null) !== this.name && !(await this.leave())) {
      if (this.name) this.onOpen(this.name); // the URL back on the open item
      return;
    }
    this.load();
    // Back on the item already open, as from another view: its edit stays.
    if (name && (name !== this.name || !this.ed)) this.openItem(name);
    else if (!name) this.closeItem();
    this.box.dataset.open = name ? "1" : "";
    if (!name) this.q.focus();
  }

  // Whether the open edit may be dropped: asks when it has unsaved changes.
  async leave() {
    if (!this.ed?.dirty()) return true;
    return ask(`Discard your unsaved edit of ${this.name}?`, { ok: "Discard", cancel: "Keep editing" });
  }

  chips() {
    for (const b of this.kinds.children) b.setAttribute("aria-pressed", String(b.dataset.kind === this.kind));
  }

  async load() {
    clearTimeout(this.timer);
    const n = ++this.seq;
    this.chips();
    const p = { query: this.q.value.trim(), kind: this.kind };
    let res;
    try {
      res = await this.conn.call("knowledge.list", p);
    } catch (e) {
      if (n !== this.seq) return;
      this.rows = [];
      this.render(p, e.message);
      return;
    }
    if (n !== this.seq) return;
    this.rows = res.items;
    this.render(p);
  }

  render(p, failed = null) {
    const rows = this.rows.map((r) => this.row(r));
    if (!rows.length) {
      const what = p.query ? `Nothing matches “${p.query}”.` : this.kind === "all" ? "No skills or memories yet. An agent saves one with memory_save or skill_save, and /remember asks for one." : `No ${KINDS.find(([k]) => k === this.kind)[1]}.`;
      rows.push(el("div", "empty", failed || what));
    }
    this.list.replaceChildren(...rows);
    const n = this.rows.length;
    this.count.textContent = failed || !n ? "" : `${n} ${n === 1 ? "item" : "items"}`;
  }

  row(r) {
    const row = el("div", `krow k-${r.kind}`);
    row.tabIndex = 0;
    row.setAttribute("role", "button");
    row.dataset.name = r.name;
    if (r.name === this.name) row.classList.add("on");
    const ic = el("span", "ic");
    ic.title = r.kind;
    ic.append(icon(r.kind === "skill" ? "play" : "book"));
    const [flag, cls, path] = flagOf(r);
    const fl = el("span", `fl ${cls}`, flag);
    if (path) fl.title = `missing: ${path}`;
    const use = el("span", "use", r.reads ? `${used(r)} · ${r.reads} ${r.reads === 1 ? "read" : "reads"}` : used(r));
    row.append(ic, el("span", "nm", r.name), use, fl, el("span", "ds", r.description));
    opens(row, () => this.onOpen(r.name));
    return row;
  }

  closeItem() {
    this.openSeq++;
    this.ed?.destroy();
    this.ed = null;
    this.name = null;
    this.detail.hidden = true;
    this.detail.replaceChildren();
    for (const x of this.list.querySelectorAll(".krow.on")) x.classList.remove("on");
  }

  async openItem(name) {
    const n = ++this.openSeq;
    this.ed?.destroy();
    this.ed = null;
    this.note = null;
    this.name = name;
    for (const x of this.list.querySelectorAll(".krow")) x.classList.toggle("on", x.dataset.name === name);
    this.detail.hidden = false;
    this.detail.replaceChildren(this.head(name), el("div", "kmeta", "Loading…"));
    let it;
    try {
      it = await this.conn.call("knowledge.get", { name });
    } catch (e) {
      if (n !== this.openSeq) return;
      const msg = e.code === "not_found" ? `No item named ${name} in your knowledge.` : e.message;
      this.detail.replaceChildren(this.head(name), el("div", "kmeta err", msg));
      this.drawHistory(n, name, false);
      return;
    }
    if (n !== this.openSeq) return;
    this.drawItem(n, it);
  }

  // The pane's first line: Back (on a phone, where the pane replaces the
  // list), the name, and the item's kind and state.
  head(name, it = null) {
    const h = el("div", "khead");
    const back = button("btn kback", "", () => this.onBack());
    back.append(icon("left"), "All items");
    back.setAttribute("aria-label", "Back to all items");
    const title = el("div", "ktitle");
    title.append(el("span", "kname", name));
    if (it) {
      const [flag, cls] = flagOf(it);
      title.append(el("span", "kkind", it.kind), el("span", `fl ${cls}`, flag));
    }
    h.append(back, title);
    return h;
  }

  drawItem(n, it) {
    this.stamp = it.stamp;
    const facts = [used(it), `${it.reads} ${it.reads === 1 ? "read" : "reads"}`];
    for (const f of it.flags) facts.push(f.startsWith("stale:") ? `stale: ${f.slice(6)} is gone` : f);
    if (it.status === "proposed") facts.push("proposed by an agent: saving your edit approves it");
    const kids = [this.head(it.name, it), el("div", "kdesc", it.description), el("div", "kmeta", facts.join(" · "))];
    if (it.files.length > 1) kids.push(el("div", "kmeta kfiles", `files: ${it.files.join(" · ")}`));
    const host = el("div", "ked");
    this.note = el("div", "knote");
    this.note.setAttribute("aria-live", "polite");
    this.saveBtn = button("btn primary ksave", "Save", () => this.save());
    this.saveBtn.disabled = true;
    const del = button("btn kdel", "", () => this.remove(it));
    del.append(icon("trash"), "Delete");
    const acts = el("div", "kacts");
    acts.append(this.saveBtn, del, this.note);
    kids.push(host, acts);
    this.detail.replaceChildren(...kids);
    openEditor(host, {
      text: it.text,
      path: `${it.name}.md`,
      wrap: true,
      onChange: (dirty) => {
        this.saveBtn.disabled = !dirty;
        if (dirty) this.say("");
      },
      onSave: () => this.save(),
    }).then(
      (ed) => {
        if (n !== this.openSeq) return ed.destroy();
        this.ed = ed;
      },
      (e) => this.say(`The editor did not load: ${e.message}`, true),
    );
    this.drawHistory(n, it.name, true);
  }

  say(text, err = false) {
    if (!this.note) return;
    this.note.replaceChildren(text);
    this.note.classList.toggle("err", err);
  }

  async save() {
    if (!this.ed?.dirty() || this.saveBtn.disabled) return;
    const name = this.name;
    this.saveBtn.disabled = true;
    try {
      const r = await this.conn.call("knowledge.put", { name, text: this.ed.value(), stamp: this.stamp });
      if (name !== this.name) return;
      this.stamp = r.stamp;
      this.ed.markSaved();
      this.say("Saved and approved.");
      this.load();
      this.drawHistory(this.openSeq, name, true);
    } catch (e) {
      if (name !== this.name) return;
      this.saveBtn.disabled = false;
      if (e.code === "stale") {
        this.say("It changed since you opened it, so nothing was saved. ", true);
        this.note.append(button("btn sm", "Reload", () => this.reload()));
      } else this.say(e.message, true);
    }
  }

  // The stored text again, dropping the edit (after a stale save).
  async reload() {
    const name = this.name;
    if (!(await this.leave())) return;
    if (name === this.name) this.openItem(name);
  }

  async remove(it) {
    const ok = await ask(`Delete the ${it.kind} ${it.name}? Its history keeps it, and Restore brings it back.`, { ok: "Delete" });
    if (!ok || it.name !== this.name) return;
    try {
      await this.conn.call("knowledge.remove", { name: it.name });
    } catch (e) {
      this.say(e.message, true);
      return;
    }
    this.load();
    this.openItem(it.name); // the history stays, with Restore
  }

  async drawHistory(n, name, live) {
    let res;
    try {
      res = await this.conn.call("knowledge.history", { name });
    } catch (e) {
      if (n !== this.openSeq) return;
      this.detail.querySelector(".khist")?.remove();
      this.detail.append(el("div", "khist kmeta err", e.message));
      return;
    }
    if (n !== this.openSeq) return;
    const sec = el("section", "khist");
    sec.append(el("h4", null, "History"));
    if (!res.history.length) sec.append(el("div", "kmeta", "No history: it was never saved through aegis."));
    res.history.forEach((h, i) => {
      const row = el("div", "kver");
      row.dataset.sha = h.sha;
      const what = el("div", "kvw");
      what.append(el("span", "kvm", h.message), el("span", "kvt", `${when(h.at)}${h.by ? ` · ${h.by}` : ""}`));
      row.append(what);
      // The newest version is the item as it is, and a deletion holds nothing.
      const current = i === 0 && live;
      if (!current && !h.message.startsWith("delete ")) {
        row.append(button("btn sm krestore", "Restore", () => this.restore(name, h)));
      } else if (current) row.append(el("span", "kvnow", "now"));
      sec.append(row);
    });
    this.detail.querySelector(".khist")?.remove();
    this.detail.append(sec);
  }

  async restore(name, h) {
    if (!(await this.leave())) return;
    try {
      await this.conn.call("knowledge.restore", { name, sha: h.sha });
    } catch (e) {
      if (this.note) this.say(e.message, true);
      else this.detail.append(el("div", "kmeta err", e.message));
      return;
    }
    this.load();
    this.openItem(name);
  }
}
