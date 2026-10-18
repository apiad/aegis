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
import { ask, choose } from "./dialog.js";
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

// -- the tree -----------------------------------------------------------------

const OPEN_KEY = "aegis.files.open"; // the folders left expanded, in localStorage
const IGNORED_KEY = "aegis.files.ignored"; // "1" when ignored entries show
const LONG_PRESS = 500; // ms a touch holds a row before its menu opens
const UNDO_MS = 10_000;

const parentOf = (p) => (p.includes("/") ? p.slice(0, p.lastIndexOf("/")) : "");
const join = (dir, name) => (dir ? `${dir}/${name}` : name);
// Whether `p` is `top` or inside it.
const under = (p, top) => p === top || p.startsWith(`${top}/`);

function stored(key, fallback) {
  try {
    return JSON.parse(localStorage.getItem(key)) ?? fallback;
  } catch {
    return fallback;
  }
}

// The folder tree from the root down. A folder's entries load from fs.list
// the first time it opens; every write goes through the server and the
// folder it touched is listed again, so the tree never guesses.
export class FileTree {
  // host: the Files view, for the open file, navigation and the toast.
  constructor(box, conn, host) {
    Object.assign(this, { box, conn, host });
    this.kids = new Map(); // folder -> its entries, once listed
    this.open = new Set(stored(OPEN_KEY, []));
    this.showIgnored = stored(IGNORED_KEY, false) === true;
    this.started = false;
    this.menu = null;
    this.dragged = null;
    box.innerHTML =
      '<div class="trows" role="tree" aria-label="Files"></div>' +
      '<label class="tfoot"><input type="checkbox"> Show ignored</label>';
    this.rows = box.querySelector(".trows");
    const toggle = box.querySelector(".tfoot input");
    toggle.checked = this.showIgnored;
    toggle.addEventListener("change", () => {
      this.showIgnored = toggle.checked;
      localStorage.setItem(IGNORED_KEY, JSON.stringify(this.showIgnored));
      this.reload();
    });
    this.rows.addEventListener("click", (ev) => {
      const r = ev.target.closest(".trow");
      if (!r || ev.target.closest("input")) return;
      if (r.dataset.kind === "dir") this.toggle(r.dataset.path);
      else this.host.pick(r.dataset.path);
    });
    this.rows.addEventListener("contextmenu", (ev) => {
      if (ev.target.closest("input")) return;
      ev.preventDefault();
      this.openMenu(ev.target.closest(".trow"), ev.clientX, ev.clientY);
    });
    this.longPress();
    this.drag();
  }

  // The first time the view shows: the root and every folder left open.
  async start() {
    if (this.started) return;
    this.started = true;
    await this.reload();
  }

  async reload() {
    this.kids.clear();
    await this.list("");
    await Promise.all([...this.open].map((d) => this.list(d)));
    this.draw();
  }

  async list(dir) {
    try {
      const r = await this.conn.call("fs.list", { dir, ignored: this.showIgnored });
      this.kids.set(dir, r.entries);
    } catch (e) {
      if (e.code === "offline") this.started = false; // online() starts again
      else if (dir) this.open.delete(dir); // gone, or refused: forget it
      this.kids.delete(dir);
    }
  }

  // List `dir` again after a change in it, and draw.
  async refresh(dir) {
    if (dir === "" || this.kids.has(dir)) await this.list(dir);
    this.draw();
  }

  save() {
    localStorage.setItem(OPEN_KEY, JSON.stringify([...this.open]));
  }

  async toggle(dir) {
    if (this.open.has(dir)) this.open.delete(dir);
    else {
      this.open.add(dir);
      if (!this.kids.has(dir)) await this.list(dir);
    }
    this.save();
    this.draw();
  }

  // Open every folder down to `path` and scroll its row into view.
  async reveal(path) {
    if (!this.started) await this.start();
    const dirs = [];
    for (let d = parentOf(path); d; d = parentOf(d)) dirs.unshift(d);
    for (const d of dirs) {
      this.open.add(d);
      if (!this.kids.has(d)) await this.list(d);
    }
    this.save();
    this.draw();
    this.row(path)?.scrollIntoView({ block: "nearest" });
  }

  row(path) {
    return [...this.rows.children].find((r) => r.dataset.path === path);
  }

  draw() {
    const out = [];
    const walk = (dir, depth) => {
      for (const e of this.kids.get(dir) || []) {
        const path = join(dir, e.name);
        const isDir = e.kind === "dir";
        const r = el("div", `trow${e.ignored ? " ign" : ""}${path === this.host.path ? " on" : ""}`);
        r.dataset.path = path;
        r.dataset.kind = e.kind;
        r.draggable = true;
        r.setAttribute("role", "treeitem");
        r.style.setProperty("--depth", depth);
        const open = isDir && this.open.has(path);
        if (isDir) r.setAttribute("aria-expanded", String(open));
        r.append(el("span", "tw", isDir ? (open ? "▾" : "▸") : ""), el("span", "nm", e.name));
        if (e.repo) r.append(el("span", "tag", "repo"));
        out.push(r);
        if (open) walk(path, depth + 1);
      }
    };
    walk("", 0);
    this.rows.replaceChildren(...out);
  }

  // -- the menu ---------------------------------------------------------------

  // A long press on a touch screen opens the row's menu, as a right-click does.
  longPress() {
    let timer = null;
    let at = null;
    const stop = () => {
      clearTimeout(timer);
      timer = null;
    };
    this.rows.addEventListener("pointerdown", (ev) => {
      if (ev.pointerType === "mouse") return;
      at = [ev.clientX, ev.clientY];
      const r = ev.target.closest(".trow");
      timer = setTimeout(() => {
        timer = null;
        this.openMenu(r, at[0], at[1]);
      }, LONG_PRESS);
    });
    this.rows.addEventListener("pointermove", (ev) => {
      if (timer && Math.hypot(ev.clientX - at[0], ev.clientY - at[1]) > 8) stop();
    });
    for (const t of ["pointerup", "pointercancel"]) this.rows.addEventListener(t, stop);
  }

  // Removing the focused menu fires its focusout, which lands here again.
  closeMenu() {
    const m = this.menu;
    this.menu = null;
    m?.remove();
  }

  // The menu for row `r`, or for the root when the press missed every row.
  openMenu(r, x, y) {
    this.closeMenu();
    const path = r?.dataset.path ?? "";
    const isDir = !r || r.dataset.kind === "dir";
    const dir = isDir ? path : parentOf(path);
    const items = [
      ["New file", () => this.create(dir, "file")],
      ["New folder", () => this.create(dir, "dir")],
    ];
    if (r)
      items.push(
        ["Rename", () => this.rename(path)],
        ["Delete", () => this.remove(path, isDir)],
        ["Copy path", () => navigator.clipboard.writeText(path).then(() => this.host.toast(`Copied ${path}`))],
      );
    const m = el("div", "tmenu");
    m.setAttribute("role", "menu");
    for (const [label, run] of items) {
      const b = el("button", "btn", label);
      b.type = "button";
      b.setAttribute("role", "menuitem");
      b.addEventListener("click", () => {
        this.closeMenu();
        run();
      });
      m.append(b);
    }
    document.getElementById("a2").append(m);
    const w = m.offsetWidth;
    const h = m.offsetHeight;
    m.style.left = `${Math.max(4, Math.min(x, innerWidth - w - 4))}px`;
    m.style.top = `${Math.max(4, Math.min(y, innerHeight - h - 4))}px`;
    this.menu = m;
    m.querySelector("button").focus();
    const away = (ev) => {
      if (m.contains(ev.target)) return;
      document.removeEventListener("pointerdown", away, true);
      if (this.menu === m) this.closeMenu();
    };
    document.addEventListener("pointerdown", away, true);
    m.addEventListener("keydown", (ev) => {
      if (ev.key !== "Escape") return;
      ev.preventDefault();
      this.closeMenu();
    });
    m.addEventListener("focusout", (ev) => {
      if (!m.contains(ev.relatedTarget) && this.menu === m) this.closeMenu();
    });
  }

  // An input in place of a row's name: resolves to the text, or null on Esc
  // or when it loses focus empty-handed.
  edit(row, value) {
    return new Promise((done) => {
      const nm = row.querySelector(".nm");
      const input = el("input", "tname");
      input.value = value;
      input.spellcheck = false;
      input.setAttribute("aria-label", "Name");
      nm.replaceWith(input);
      let over = false;
      const end = (v) => {
        if (over) return;
        over = true;
        done(v);
      };
      input.addEventListener("keydown", (ev) => {
        if (ev.key === "Enter") end(input.value.trim() || null);
        else if (ev.key === "Escape") end(null);
        else return;
        ev.preventDefault();
        ev.stopPropagation();
      });
      input.addEventListener("blur", () => end(input.value.trim() && input.value !== value ? input.value.trim() : null));
      input.focus();
      const dot = value.lastIndexOf(".");
      input.setSelectionRange(0, dot > 0 ? dot : value.length);
    });
  }

  // Where a typed name leads: beside the entry, or from the root with a "/".
  target(dir, name) {
    return name.startsWith("/") ? name.replace(/^\/+/, "") : join(dir, name);
  }

  async create(dir, kind) {
    if (dir && !this.open.has(dir)) await this.toggle(dir);
    const depth = dir ? dir.split("/").length : 0;
    const row = el("div", "trow new");
    row.style.setProperty("--depth", depth);
    row.append(el("span", "tw", kind === "dir" ? "▸" : ""), el("span", "nm"));
    const first = [...this.rows.children].find((r) => parentOf(r.dataset.path) === dir);
    if (first) first.before(row);
    else if (dir) this.row(dir).after(row);
    else this.rows.prepend(row);
    const name = await this.edit(row, "");
    row.remove();
    if (!name) return;
    const path = this.target(dir, name);
    try {
      if (kind === "dir") await this.conn.call("fs.mkdir", { path });
      else await this.conn.call("fs.write", { path, content: "", base: null });
    } catch (e) {
      this.host.toast(`Not created: ${e.message}`, { kind: "error" });
      return;
    }
    await this.refresh(parentOf(path));
    if (kind === "file") this.host.pick(path);
  }

  async rename(path) {
    const row = this.row(path);
    if (!row) return;
    const name = await this.edit(row, path.slice(path.lastIndexOf("/") + 1));
    if (!name) return this.draw();
    await this.move(path, this.target(parentOf(path), name));
  }

  async move(path, to) {
    if (to === path) return this.draw();
    if (!(await this.host.mayDisturb(path, "Move"))) return this.draw();
    try {
      await this.conn.call("fs.move", { path, to });
    } catch (e) {
      this.host.toast(`Not moved: ${e.message}`, { kind: "error" });
      return this.draw();
    }
    this.renamed(path, to);
    await this.list(parentOf(path));
    await this.refresh(parentOf(to));
    this.host.moved(path, to);
  }

  // Keep the open folders under a moved one open at its new place.
  renamed(from, to) {
    for (const d of [...this.open]) {
      if (!under(d, from)) continue;
      this.open.delete(d);
      this.open.add(to + d.slice(from.length));
    }
    for (const d of [...this.kids.keys()]) if (under(d, from)) this.kids.delete(d);
    this.save();
  }

  async remove(path, isDir) {
    let what = path;
    if (isDir) {
      // The listed files under it, as the picker sees them.
      const n = await this.conn.call("fs.find", {}).then(
        (r) => r.files.filter(([p]) => p.startsWith(`${path}/`)).length,
        () => null,
      );
      what = n === null ? `the folder ${path}` : `the folder ${path} and the ${n} file${n === 1 ? "" : "s"} in it`;
    }
    if (!(await this.host.mayDisturb(path, "Delete"))) return;
    if (!(await ask(`Delete ${what}? Undo stays offered for 10 seconds.`, { ok: "Delete" }))) return;
    let r;
    try {
      r = await this.conn.call("fs.delete", { path });
    } catch (e) {
      this.host.toast(`Not deleted: ${e.message}`, { kind: "error" });
      return;
    }
    this.renamed(path, path); // forget the listings under it
    for (const d of [...this.open]) if (under(d, path)) this.open.delete(d);
    this.save();
    this.host.deleted(path);
    await this.refresh(parentOf(path));
    this.host.toast(`Deleted ${path}`, {
      ms: UNDO_MS,
      action: { label: "Undo", run: () => this.restore(r.trash_id) },
    });
  }

  async restore(id) {
    try {
      const r = await this.conn.call("fs.restore", { trash_id: id });
      if (this.kids.has(parentOf(r.path))) await this.list(parentOf(r.path));
      await this.reveal(r.path);
    } catch (e) {
      this.host.toast(`Not restored: ${e.message}`, { kind: "error" });
    }
  }

  // -- drag a row onto a folder -------------------------------------------------

  drag() {
    const folderAt = (ev) => {
      const r = ev.target.closest(".trow");
      if (!r) return ev.target.closest(".trows") ? "" : null; // below the rows: the root
      return r.dataset.kind === "dir" ? r.dataset.path : parentOf(r.dataset.path);
    };
    const mark = (dir) => {
      for (const r of this.rows.querySelectorAll(".drop")) r.classList.remove("drop");
      if (dir) this.row(dir)?.classList.add("drop");
    };
    this.rows.addEventListener("dragstart", (ev) => {
      const r = ev.target.closest(".trow");
      if (!r?.dataset.path) return;
      this.dragged = r.dataset.path;
      ev.dataTransfer.effectAllowed = "move";
      ev.dataTransfer.setData("text/plain", r.dataset.path);
    });
    this.rows.addEventListener("dragover", (ev) => {
      const dir = this.dragged === null ? null : folderAt(ev);
      if (dir === null || under(dir, this.dragged) || dir === parentOf(this.dragged)) return mark(null);
      ev.preventDefault();
      ev.dataTransfer.dropEffect = "move";
      mark(dir);
    });
    this.rows.addEventListener("drop", (ev) => {
      const from = this.dragged;
      const dir = folderAt(ev);
      mark(null);
      if (from === null || dir === null) return;
      ev.preventDefault();
      this.dragged = null;
      this.move(from, join(dir, from.slice(from.lastIndexOf("/") + 1)));
    });
    this.rows.addEventListener("dragend", () => {
      this.dragged = null;
      mark(null);
    });
  }
}

// -- the view -----------------------------------------------------------------

export class Files {
  constructor(conn, box, { toast, onPick, go }) {
    Object.assign(this, { conn, box, toast, go });
    this.path = null; // the open file
    this.hash = null; // the hash the buffer is based on
    this.ed = null;
    this.readOnly = false;
    this.disk = null; // {text, hash} the last refused save found
    this.loading = 0;
    this.waiting = null; // {path, line} a read refused offline, for online()
    this.saving = null; // the save in flight, a Promise of whether it landed
    this.shown = null; // the last target show() applied
    box.innerHTML =
      '<div class="fbar"><button class="btn tbtn" type="button" title="Folders" aria-label="Folders">☰</button>' +
      '<span class="crumbs"></span><span class="dirty" title="Unsaved changes" hidden>●</span>' +
      '<span class="gap"></span><button class="btn pick" type="button" title="Open a file (Alt+O)">Alt+O</button>' +
      '<button class="btn primary save" type="button" disabled>Save</button></div>' +
      '<div class="fbody"><nav class="ftree"></nav><div class="fmain">' +
      '<div class="fbanner" role="alert" hidden><span class="t">This file changed on disk since you opened it.</span>' +
      '<span class="acts"><button class="btn" type="button" data-act="diff">Show diff</button>' +
      '<button class="btn" type="button" data-act="reload">Reload</button>' +
      '<button class="btn" type="button" data-act="keep">Keep mine</button></span><div class="fdiff" hidden></div></div>' +
      '<div class="fpane"></div></div></div>';
    this.$ = (s) => box.querySelector(s);
    this.tree = new FileTree(this.$(".ftree"), conn, this);
    this.$(".tbtn").addEventListener("click", () => this.drawer(!box.hasAttribute("data-drawer")));
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

  // The tree as a drawer, below 640 px.
  drawer(on) {
    this.box.toggleAttribute("data-drawer", on);
  }

  // The tree opened a file.
  pick(path) {
    this.drawer(false);
    this.go(filesHash(path));
  }

  // Before a move or a delete takes `path` away: true when nothing unsaved
  // is under it, or the person says to go on.
  async mayDisturb(path, verb) {
    if (!this.path || !this.dirty() || !under(this.path, path)) return true;
    return ask(`${this.path} has changes that are not saved. ${verb} it anyway?`, { ok: verb });
  }

  // `from` is now `to`: the open file under it stays open at its new path.
  moved(from, to) {
    if (!this.path || !under(this.path, from)) return;
    this.path = to + this.path.slice(from.length);
    this.shown = this.path;
    this.crumbs(this.path);
    history.replaceState(null, "", filesHash(this.path));
    this.tree.draw();
  }

  // `path` went to the trash: so did the open file under it.
  deleted(path) {
    if (!this.path || !under(this.path, path)) return;
    this.close();
    this.empty();
    history.replaceState(null, "", "#files");
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
  // render() calls this on every redraw (a session added, a reconnect), so
  // a target already shown is left alone: its :line moved the cursor once.
  async show(target) {
    this.tree.start();
    if (target === this.shown) return;
    this.shown = target;
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
    this.shown = null;
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
    this.tree.reveal(path);
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
    if (this.shown !== null || this.path) this.tree.start();
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
