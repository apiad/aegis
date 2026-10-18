// The Files view (#458): a person opens any file under the config root, edits a
// text file and saves it. Alt+O opens the picker over fs.find's list, newest
// first; the view at #files=<path>[:line] draws the open file in the editor
// (editor.js). Every call is an fs.* operation; the server decides what may be
// read or written (fs_paths.py), this module draws and asks.
//
// A save carries the hash the buffer was based on. When the file changed on
// disk since, the server writes nothing and answers changed_on_disk with the
// disk's text, and the banner offers the diff, the disk's version, or the
// buffer on purpose: an agent's edit is never dropped silently.

import { fuzzy } from "./commands.js";
import { choose } from "./dialog.js";
import { diffView } from "./diff.js";
import { open as openEditor } from "./editor.js";
import { ago } from "./fleet.js";
import { langName } from "./highlight.js";

const SHOWN = 200; // rows the picker draws
const CONTEXT = 3; // unchanged lines around each change in the conflict's diff
const MAX_LCS = 4_000_000; // cells; a bigger change is drawn as all removed, all added

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined && text !== null) n.textContent = text;
  return n;
}

function size(n) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

// The hash for a file, its folders' slashes kept readable.
export function filesHash(path, line) {
  const p = path.split("/").map(encodeURIComponent).join("/");
  return `#files=${p}${line ? `:${line}` : ""}`;
}

// "src/a.py:12" -> {path: "src/a.py", line: 12}.
export function parseTarget(s) {
  const m = /^(.*):(\d+)$/.exec(s || "");
  return m ? { path: m[1], line: Number(m[2]) } : { path: s || "", line: null };
}

// -- the picker ---------------------------------------------------------------

export class FilePicker {
  // onPick(path, line, newTab)
  constructor(box, conn, onPick) {
    Object.assign(this, { box, conn, onPick });
    this.files = []; // [[path, mtime ms], ...], newest first
    this.items = [];
    this.at = 0;
    this.back = null;
    const panel = el("div", "panel");
    panel.setAttribute("role", "dialog");
    panel.setAttribute("aria-label", "Open a file");
    this.q = el("input");
    this.q.id = "fpick-q";
    this.q.spellcheck = false;
    this.q.autocomplete = "off";
    this.q.placeholder = "Open a file: type to filter, path:line to jump";
    this.q.setAttribute("role", "combobox");
    this.q.setAttribute("aria-controls", "fpick-rows");
    this.q.setAttribute("aria-expanded", "true");
    this.rows = el("div", "rows");
    this.rows.id = "fpick-rows";
    this.rows.setAttribute("role", "listbox");
    panel.append(this.q, this.rows);
    box.replaceChildren(panel);
    this.q.addEventListener("input", () => this.refresh());
    this.q.addEventListener("keydown", (ev) => this.onKey(ev));
    this.rows.addEventListener("mousedown", (ev) => {
      const r = ev.target.closest("[role=option]");
      if (!r) return;
      ev.preventDefault(); // the field keeps focus
      this.at = [...this.rows.children].indexOf(r);
      this.accept(ev.shiftKey);
    });
    box.addEventListener("mousedown", (ev) => ev.target === box && this.close());
  }

  get isOpen() {
    return !this.box.hidden;
  }

  async open() {
    if (this.isOpen) return this.q.focus();
    this.back = document.activeElement;
    this.q.value = "";
    this.box.hidden = false;
    this.q.focus();
    if (!this.files.length) this.rows.replaceChildren(el("div", "empty", "Listing files…"));
    else this.refresh();
    try {
      this.files = (await this.conn.call("fs.find", {})).files;
    } catch (e) {
      this.rows.replaceChildren(el("div", "empty", e.message));
      return;
    }
    if (this.isOpen) this.refresh();
  }

  close() {
    if (!this.isOpen) return;
    this.box.hidden = true;
    if (this.back?.isConnected) this.back.focus({ preventScroll: true });
    this.back = null;
  }

  refresh() {
    const { path: q } = parseTarget(this.q.value.trim());
    if (!q) {
      this.items = this.files.slice(0, SHOWN).map(([p, m]) => ({ p, m, positions: [] }));
    } else {
      const hits = [];
      for (const [p, m] of this.files) {
        const f = fuzzy(q, p);
        if (f) hits.push({ p, m, s: f.score, positions: f.positions });
      }
      // Ties by mtime, newest first: fs.find's order, which sort keeps.
      hits.sort((a, b) => b.s - a.s);
      this.items = hits.slice(0, SHOWN);
    }
    this.at = 0;
    if (!this.items.length) {
      this.rows.replaceChildren(el("div", "empty", this.files.length ? "No file matches" : "No files"));
      return;
    }
    this.rows.replaceChildren(...this.items.map((it, i) => this.row(it, i)));
    this.mark();
  }

  row({ p, m, positions }, i) {
    const r = el("div", "cmd-row file-row");
    r.id = `fpick-${i}`;
    r.dataset.path = p;
    r.setAttribute("role", "option");
    const cut = p.lastIndexOf("/") + 1;
    const hit = new Set(positions);
    const marked = (from, to, cls) => {
      const s = el("span", cls);
      for (let j = from; j < to; j++) {
        if (hit.has(j)) s.append(el("b", null, p[j]));
        else s.append(p[j]);
      }
      return s;
    };
    const name = marked(cut, p.length, "nm");
    const dir = marked(0, cut, "dir");
    r.append(name, dir, el("span", "age", ago(m / 1000)));
    return r;
  }

  mark() {
    [...this.rows.children].forEach((r, i) => {
      r.classList.toggle("on", i === this.at);
      r.setAttribute("aria-selected", String(i === this.at));
    });
    this.q.setAttribute("aria-activedescendant", `fpick-${this.at}`);
    this.rows.children[this.at]?.scrollIntoView({ block: "nearest" });
  }

  move(d) {
    if (!this.items.length) return;
    this.at = (this.at + d + this.items.length) % this.items.length;
    this.mark();
  }

  accept(newTab) {
    const it = this.items[this.at];
    if (!it) return;
    const { line } = parseTarget(this.q.value.trim());
    this.close();
    this.onPick(it.p, line, newTab);
  }

  // Every key it answers is prevented, so keys.js's listener skips it.
  onKey(ev) {
    if (ev.isComposing) return;
    const d = { ArrowDown: 1, ArrowUp: -1, Tab: ev.shiftKey ? -1 : 1 }[ev.key];
    if (d) this.move(d);
    else if (ev.key === "Enter") this.accept(ev.shiftKey);
    else if (ev.key === "Escape") this.close();
    else return;
    ev.preventDefault();
  }
}

// -- the conflict's diff ------------------------------------------------------

function lineOps(a, b) {
  let pre = 0;
  while (pre < a.length && pre < b.length && a[pre] === b[pre]) pre++;
  let suf = 0;
  while (suf < a.length - pre && suf < b.length - pre && a[a.length - 1 - suf] === b[b.length - 1 - suf]) suf++;
  const x = a.slice(pre, a.length - suf);
  const y = b.slice(pre, b.length - suf);
  const mid = [];
  if (x.length * y.length > MAX_LCS) {
    for (const t of x) mid.push(["-", t]);
    for (const t of y) mid.push(["+", t]);
  } else {
    const n = x.length;
    const m = y.length;
    const L = Array.from({ length: n + 1 }, () => new Int32Array(m + 1));
    for (let i = n - 1; i >= 0; i--)
      for (let j = m - 1; j >= 0; j--) L[i][j] = x[i] === y[j] ? L[i + 1][j + 1] + 1 : Math.max(L[i + 1][j], L[i][j + 1]);
    let i = 0;
    let j = 0;
    while (i < n || j < m) {
      if (i < n && j < m && x[i] === y[j]) mid.push([" ", x[i++], j++]);
      else if (i < n && (j >= m || L[i + 1][j] >= L[i][j + 1])) mid.push(["-", x[i++]]);
      else mid.push(["+", y[j++]]);
    }
  }
  return [...a.slice(0, pre).map((t) => [" ", t]), ...mid, ...a.slice(a.length - suf).map((t) => [" ", t])];
}

// diff.js's hunks from the disk's text to the buffer's, numbered in the file.
export function hunks(disk, mine) {
  const ops = lineOps(disk.split(/\r?\n/), mine.split(/\r?\n/));
  const out = [];
  let a = 1;
  let b = 1;
  let cur = null;
  let tail = 0; // unchanged lines since the last change
  ops.forEach(([s, t], k) => {
    const near = ops.slice(k + 1, k + 1 + CONTEXT).some(([s2]) => s2 !== " ");
    if (s !== " " || near || (cur && tail < CONTEXT)) {
      if (!cur) {
        cur = { a, b, lines: [] };
        out.push(cur);
      }
      cur.lines.push(s + t);
      tail = s === " " ? tail + 1 : 0;
    } else cur = null;
    if (s !== "+") a++;
    if (s !== "-") b++;
  });
  return out;
}

// -- the view -----------------------------------------------------------------

export class Files {
  constructor(conn, box, { toast, onPick }) {
    Object.assign(this, { conn, box, toast });
    this.path = null; // the open file
    this.hash = null; // the hash the buffer is based on
    this.ed = null;
    this.readOnly = false;
    this.disk = null; // {text, hash} the last refused save found
    this.loading = 0;
    this.waiting = null; // {path, line} a read refused offline, for online()
    this.saving = null; // the save in flight, a Promise of whether it landed
    box.innerHTML =
      '<div class="fbar"><span class="crumbs"></span><span class="dirty" title="Unsaved changes" hidden>●</span>' +
      '<span class="gap"></span><button class="btn pick" type="button" title="Open a file (Alt+O)">Alt+O</button>' +
      '<button class="btn primary save" type="button" disabled>Save</button></div>' +
      '<div class="fbanner" role="alert" hidden><span class="t">This file changed on disk since you opened it.</span>' +
      '<span class="acts"><button class="btn" type="button" data-act="diff">Show diff</button>' +
      '<button class="btn" type="button" data-act="reload">Reload</button>' +
      '<button class="btn" type="button" data-act="keep">Keep mine</button></span><div class="fdiff" hidden></div></div>' +
      '<div class="fpane"></div>';
    this.$ = (s) => box.querySelector(s);
    this.$(".pick").addEventListener("click", () => onPick());
    this.$(".save").addEventListener("click", () => this.save());
    this.$(".fbanner .acts").addEventListener("click", (ev) => {
      const act = ev.target.closest("[data-act]")?.dataset.act;
      if (act === "diff") this.showDiff();
      else if (act === "reload") this.reload();
      else if (act === "keep") this.keep();
    });
    window.addEventListener("beforeunload", (ev) => {
      if (!this.dirty()) return;
      ev.preventDefault();
      ev.returnValue = "";
    });
  }

  dirty() {
    return !!this.ed?.dirty();
  }

  // Whether showing route `r` would leave the open file's unsaved edits.
  leaving(r) {
    if (!this.dirty()) return false;
    return r.view !== "files" || (r.id && parseTarget(r.id).path !== this.path);
  }

  // Save, Discard or Cancel; true when the edits may be left behind.
  async confirmLeave() {
    if (!this.dirty()) return true;
    const body = el("p", null, `${this.path} has changes that are not saved.`);
    const v = await choose("Unsaved changes", body, [
      { label: "Cancel", value: "cancel" },
      { label: "Discard", value: "discard" },
      { label: "Save", value: "save", primary: true },
    ]);
    if (v === "save") return await this.save();
    if (v === "discard") {
      this.close();
      return true;
    }
    return false;
  }

  // Show `target` ("path" or "path:line"); empty keeps what is open.
  async show(target) {
    const { path, line } = parseTarget(target);
    if (!path || path === this.path) {
      if (!this.path) this.empty();
      else if (line) this.ed?.goto(line);
      return;
    }
    await this.load(path, line);
  }

  empty() {
    this.crumbs(null);
    this.$(".fpane").replaceChildren(
      el("p", "notice", "Press Alt+O to open any file under the root, newest first."),
    );
  }

  close() {
    this.ed?.destroy();
    this.ed = null;
    this.path = null;
    this.hash = null;
    this.banner(false);
    this.mark(false);
  }

  crumbs(path) {
    const box = this.$(".crumbs");
    box.replaceChildren();
    if (!path) return box.append(el("span", "here", "Files"));
    const parts = path.split("/");
    parts.forEach((p, i) => {
      if (i) box.append(el("span", "sep", "/"));
      box.append(el("span", i === parts.length - 1 ? "here" : "dir", p));
    });
    box.title = path;
  }

  async load(path, line) {
    const n = ++this.loading;
    this.close();
    this.path = path;
    this.crumbs(path);
    const pane = this.$(".fpane");
    pane.replaceChildren(el("p", "notice", "Opening…"));
    let r;
    try {
      r = await this.conn.call("fs.read", { path });
    } catch (e) {
      if (n !== this.loading) return;
      if (e.code === "offline") {
        // A page opened on #files=… draws before its socket is up.
        this.waiting = { path, line };
        pane.replaceChildren(el("p", "notice", "Waiting for the connection…"));
      } else pane.replaceChildren(el("p", "notice err-text", e.message));
      return;
    }
    if (n !== this.loading) return;
    pane.replaceChildren();
    if (r.text === undefined) {
      pane.append(
        el("p", "notice", `${r.mime || "A file"} · ${size(r.size)}. Its preview is not built yet; only text opens here for now.`),
      );
      return;
    }
    this.hash = r.hash;
    this.readOnly = !!r.cut;
    if (r.cut) pane.append(el("p", "fnote", `Read-only: the first 2 MB of this ${size(r.size)} file.`));
    const host = el("div", "fed");
    pane.append(host);
    const ed = await openEditor(host, {
      text: r.text,
      path,
      readOnly: this.readOnly,
      onChange: (d) => this.mark(d),
      onSave: () => this.save(),
    });
    if (n !== this.loading) return ed.destroy();
    this.ed = ed;
    this.mark(false);
    if (line) ed.goto(line);
    else ed.focus();
  }

  // The socket is up: open what waited for it.
  online() {
    const w = this.waiting;
    this.waiting = null;
    if (!w || w.path !== this.path) return;
    this.path = null;
    this.load(w.path, w.line);
  }

  mark(dirty) {
    this.$(".dirty").hidden = !dirty;
    this.$(".save").disabled = !this.ed || this.readOnly || !dirty;
  }

  // True when the buffer is on disk afterwards.
  // A save while one is in flight joins it: a second one would carry the
  // same base and come back changed_on_disk against our own write. A clean
  // buffer is already on disk.
  save() {
    if (!this.ed || this.readOnly) return Promise.resolve(false);
    if (!this.saving && !this.ed.dirty()) return Promise.resolve(true);
    this.saving ??= this.write().finally(() => (this.saving = null));
    return this.saving;
  }

  async write() {
    const content = this.ed.value();
    try {
      const r = await this.conn.call("fs.write", { path: this.path, content, base: this.hash });
      this.hash = r.hash;
      if (this.ed.value() === content) this.ed.markSaved();
      this.banner(false);
      return true;
    } catch (e) {
      if (e.code === "changed_on_disk") {
        this.disk = e.data;
        this.banner(true);
      } else this.toast(`Not saved: ${e.message}`, { kind: "error" });
      return false;
    }
  }

  banner(on) {
    this.$(".fbanner").hidden = !on;
    this.$(".fdiff").hidden = true;
    this.$(".fdiff").replaceChildren();
    if (!on) this.disk = null;
  }

  showDiff() {
    const box = this.$(".fdiff");
    if (!box.hidden) {
      box.hidden = true;
      return;
    }
    const disk = this.disk?.text;
    if (disk == null) {
      box.replaceChildren(el("p", "notice", "The file on disk is no longer text."));
    } else {
      const ext = this.path.split(".").pop();
      box.replaceChildren(diffView({ hunks: hunks(disk, this.ed.value()), numbered: "file", elided: 0 }, langName(ext)));
    }
    box.hidden = false;
  }

  // The disk's version, keeping the cursor's line; read again when the
  // refused save did not carry it as text.
  async reload() {
    const disk = this.disk;
    this.banner(false);
    if (disk?.text != null && this.ed) {
      this.ed.setValue(disk.text);
      this.hash = disk.hash;
      return;
    }
    const path = this.path;
    this.path = null; // load() opens it afresh
    await this.load(path);
  }

  keep() {
    // The next save overwrites the disk's version on purpose.
    this.hash = this.disk?.hash ?? this.hash;
    this.banner(false);
    this.ed?.focus();
  }
}
