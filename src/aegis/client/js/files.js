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
//
// While the view is on screen it asks fs.stat about the open file every two
// seconds. An untouched buffer takes a change on disk quietly; an edited one
// shows the same banner a refused save does. It is a call, not a channel: a
// link's socket may subscribe to any channel, and a channel would let a far
// server watch the files here (spec, "Agents writing the same file").
//
// Markdown, HTML and SVG open rendered, with a switch to the editor on their
// source; the rendered view draws the buffer (Markdown) or the file on disk
// through a ticket URL (HTML in a sandboxed frame, SVG in an <img>), redrawn
// after a save. Images, PDF, audio and video draw from their ticket URL, and
// anything else offers its size, a download and the desktop's app for it.

import { fuzzy } from "./commands.js";
import { annotate } from "./annotations.js";
import { Tray } from "./comments.js";
import { ask, choose } from "./dialog.js";
import { diffView } from "./diff.js";
import { fileGlyph, open as openEditor } from "./editor.js";
import { CARET_ICON, icon } from "./glyphs.js";
import { markdown } from "./entries.js";
import * as menu from "./menu.js";
import { ago } from "./fleet.js";
import { langName, paint } from "./highlight.js";
import { docLocator, headingOf, offsetsIn, rangeAtPoint, rangeText, textLocator } from "./quote.js";

// A tree row's caret: open or shut for a folder, empty for a file.
function twisty(dir, open) {
  const s = el("span", "tw");
  if (dir) s.append(icon(CARET_ICON[open ? "open" : "shut"]));
  return s;
}

const SHOWN = 200; // rows the picker draws
const CONTEXT = 3; // unchanged lines around each change in the conflict's diff
const MAX_LCS = 4_000_000; // cells; a bigger change is drawn as all removed, all added
const POLL_MS = 2000; // how often the open file's size and mtime are asked for

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
    r.dataset.glyph = fileGlyph(p, false);
    r.append(icon(r.dataset.glyph), name, dir, el("span", "age", ago(m / 1000)));
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
const UNDO_MS = 10_000;

const parentOf = (p) => (p.includes("/") ? p.slice(0, p.lastIndexOf("/")) : "");
const join = (dir, name) => (dir ? `${dir}/${name}` : name);
// Whether `p` is `top` or inside it.
const under = (p, top) => p === top || p.startsWith(`${top}/`);
// A heading's anchor as GitHub writes it: "Getting started!" -> "getting-started".
const slug = (t) =>
  t
    .trim()
    .toLowerCase()
    .replace(/[^\p{L}\p{N}\s_-]/gu, "")
    .replace(/\s/g, "-");

// "a/./b/../c" -> "a/c"; "" when it climbs out of the root.
function normalize(p) {
  const out = [];
  for (const part of p.split("/")) {
    if (part === "..") {
      if (!out.length) return "";
      out.pop();
    } else if (part && part !== ".") out.push(part);
  }
  return out.join("/");
}

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
    menu.attach(this.rows, (t) => this.menuFor(t.closest(".trow")), { selector: ".trow" });
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
      await this.list(dir); // the tree does not poll: an expand is a fresh look
    }
    this.save();
    this.draw();
  }

  // Open every folder down to `path`, listed afresh so a file an agent just
  // wrote is there, and scroll its row into view.
  async reveal(path) {
    if (!this.started) await this.start();
    const dirs = [""];
    for (let d = parentOf(path); d; d = parentOf(d)) dirs.splice(1, 0, d);
    for (const d of dirs) if (d) this.open.add(d);
    await Promise.all(dirs.map((d) => this.list(d)));
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
        r.tabIndex = -1; // the menu key opens its menu, and Esc on the menu comes back here
        r.setAttribute("role", "treeitem");
        r.style.setProperty("--depth", depth);
        const open = isDir && this.open.has(path);
        if (isDir) r.setAttribute("aria-expanded", String(open));
        r.dataset.glyph = fileGlyph(path, isDir, open);
        r.append(twisty(isDir, open), icon(r.dataset.glyph), el("span", "nm", e.name));
        if (e.repo) r.append(el("span", "tag", "repo"));
        out.push(r);
        if (open) walk(path, depth + 1);
      }
    };
    walk("", 0);
    this.rows.replaceChildren(...out);
  }

  // -- the menu ---------------------------------------------------------------

  // The menu for row `r`, or for the root when the press missed every row.
  menuFor(r) {
    const path = r?.dataset.path ?? "";
    const isDir = !r || r.dataset.kind === "dir";
    const dir = isDir ? path : parentOf(path);
    const make = [
      { id: "new-file", label: "New file", icon: "file", run: () => this.create(dir, "file") },
      { id: "new-folder", label: "New folder", icon: "folder", run: () => this.create(dir, "dir") },
    ];
    const { here } = this.host;
    const herein = [
      { id: "session-here", label: "New session here", icon: "start", run: () => here.session(dir) },
      { id: "terminal-here", label: "Terminal here", icon: "terminal", run: () => here.terminal(dir) },
    ];
    if (!r) return { groups: [herein, make] };
    const copy = () => navigator.clipboard.writeText(path).then(() => this.host.toast(`Copied ${path}`));
    return {
      groups: [
        [{ id: "insert-path", label: "Insert path", icon: "prompt", run: () => here.insert(path) }, ...herein],
        [
          ...make,
          { id: "rename", label: "Rename", icon: "pencil", run: () => this.rename(path) },
          { id: "copy-path", label: "Copy path", icon: "copy", run: copy },
        ],
        [{ id: "delete", label: "Delete", icon: "trash", danger: true, run: () => this.remove(path, isDir) }],
      ],
    };
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
    row.append(twisty(kind === "dir", false), icon(kind === "dir" ? "folder" : "file"), el("span", "nm"));
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
    // A poll already under way would find the old path gone and call the
    // file deleted on disk.
    this.host.gen++;
    try {
      await this.conn.call("fs.move", { path, to });
    } catch (e) {
      this.host.toast(`Not moved: ${e.message}`, { kind: "error" });
      return this.draw();
    }
    this.host.moved(path, to);
    this.renamed(path, to);
    const moved = [...this.open].filter((d) => under(d, to));
    await Promise.all([parentOf(path), parentOf(to), ...moved].map((d) => this.list(d)));
    this.draw();
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
      // Every file under it, ignored ones too: what the delete takes.
      const n = await this.conn.call("fs.count", { path }).then(
        (r) => r.files,
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

// A selection Files' bar is about: inside the editor's lines or the rendered
// Markdown, start and end.
function filesUnit(range, root) {
  const n = range.startContainer;
  const content = (n.nodeType === 1 ? n : n.parentElement)?.closest(".fview > .md, .fed .cm-content");
  return content && root.contains(content) && content.contains(range.endContainer) ? { range, row: content } : null;
}

export class Files {
  // `point` is app.js's: items(ctx) gives a selection's items, send(text)
  // writes the comments' message where the quotes go. `here` is app.js's
  // too: a row's path into the composer, a session or a terminal in a folder.
  constructor(conn, box, { toast, onPick, go, point, here }) {
    Object.assign(this, { conn, box, toast, go, point, here });
    this.path = null; // the open file
    this.hash = null; // the hash the buffer is based on
    this.ed = null;
    this.readOnly = false;
    this.disk = null; // {text, hash} the last refused save found
    this.loading = 0;
    this.waiting = null; // {path, line} a read refused offline, for online()
    this.saving = null; // the save in flight, a Promise of whether it landed
    this.shown = null; // the last target show() applied
    this.stat = null; // {size, mtime} of the file the buffer last matched
    this.gen = 0; // bumped when a save or the banner changes the base, so a poll begun before drops its answer
    this.polling = false;
    this.kind = null; // the open file's kind, from fs.read
    this.tray = null; // the open file's comments
    this.annotation = null; // the open image or PDF's drawing layer
    this.viewMode = null; // "view" or "src" for a kind with a rendered view
    box.innerHTML =
      '<div class="fbar"><button class="btn tbtn" type="button" title="Folders" aria-label="Folders"><svg class="ic i-folder" aria-hidden="true"><use href="#g-folder"/></svg></button>' +
      '<span class="crumbs"></span><span class="dirty" title="Unsaved changes" hidden><svg class="ic i-dot" aria-hidden="true"><use href="#g-dot"/></svg></span>' +
      '<span class="gap"></span><button class="btn pick" type="button" title="Open a file (Alt+O)">Alt+O</button>' +
      '<button class="btn primary save" type="button" disabled>Save</button></div>' +
      '<div class="fbody"><nav class="ftree"></nav><div class="fmain">' +
      '<div class="fbanner" role="alert" hidden><span class="t"></span>' +
      '<span class="acts"><button class="btn" type="button" data-act="diff">Show diff</button>' +
      '<button class="btn" type="button" data-act="reload">Reload</button>' +
      '<button class="btn" type="button" data-act="keep">Keep mine</button>' +
      '<button class="btn" type="button" data-act="recreate">Save</button>' +
      '<button class="btn" type="button" data-act="close">Close</button></span><div class="fdiff" hidden></div></div>' +
      '<div class="fpane"></div></div></div>';
    this.$ = (s) => box.querySelector(s);
    this.tree = new FileTree(this.$(".ftree"), conn, this);
    this.$(".tbtn").addEventListener("click", () => this.drawer(!box.hasAttribute("data-drawer")));
    this.$(".pick").addEventListener("click", () => onPick());
    this.$(".save").addEventListener("click", () => this.save());
    // A selection in the editor or the rendered Markdown, and a right-click in
    // the latter: the editor keeps the browser's menu, with its paste.
    const pane = this.$(".fpane");
    menu.bar(pane, (range, content) => this.pointItems(range, content), { unit: filesUnit });
    menu.attach(pane, (t, ev) => this.pressed(t, ev));
    this.$(".fbanner .acts").addEventListener("click", (ev) => {
      const act = ev.target.closest("[data-act]")?.dataset.act;
      if (act === "diff") this.showDiff();
      else if (act === "reload") this.reload();
      else if (act === "keep") this.keep();
      else if (act === "recreate") this.save(true);
      else if (act === "close") this.closeFile();
    });
    setInterval(() => this.poll(), POLL_MS);
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
    this.annotation?.destroy();
    this.annotation = null;
    this.shown = null;
    this.ed?.destroy();
    this.ed = null;
    this.path = null;
    this.hash = null;
    this.stat = null;
    this.kind = null;
    this.viewMode = null;
    this.tray = null;
    this.banner(false);
    this.mark(false);
  }

  // Close leaves the deleted file's buffer behind for the empty view.
  closeFile() {
    this.close();
    this.empty();
    history.replaceState(null, "", "#files");
    this.tree.draw();
  }

  // Ask whether the open file moved on disk; see the module's comment.
  async poll() {
    const shown = document.getElementById("a2")?.dataset.view === "files";
    // A buffer keeps polling after its file was deleted (stat is null then),
    // so a file written again moves the banner to "changed".
    const idle = !this.path || (!this.ed && !this.stat);
    if (!shown || document.visibilityState !== "visible" || idle || this.polling || this.saving) return;
    this.polling = true;
    const path = this.path;
    const gen = this.gen;
    const stale = () => path !== this.path || this.saving || gen !== this.gen;
    try {
      const st = await this.conn.call("fs.stat", { path }).catch((e) => e);
      if (stale()) return;
      if (st instanceof Error) {
        if (st.code === "not_found" && !this.ed) {
          this.close();
          this.$(".fpane").replaceChildren(el("p", "notice", "This file was deleted on disk."));
        } else if (st.code === "not_found" && this.hash !== null) {
          this.hash = null; // a save now creates it again
          this.stat = null;
          this.banner("deleted");
        }
        return;
      }
      if (this.stat && st.mtime === this.stat.mtime && st.size === this.stat.size) return;
      if (!this.ed) return this.reopen(); // a preview draws the file as it is now
      const r = await this.conn.call("fs.read", { path }).catch(() => null);
      if (!r || stale()) return;
      this.stat = { size: r.size, mtime: r.mtime };
      if (r.hash === this.hash) return; // touched, not changed
      if (r.text === undefined || r.cut) {
        if (!this.dirty()) return this.reopen();
        this.disk = { text: null, hash: r.hash };
        return this.banner("changed");
      }
      if (this.dirty() || this.hash === null) {
        this.disk = { text: r.text, hash: r.hash };
        this.banner("changed");
      } else {
        this.ed.setValue(r.text);
        this.hash = r.hash;
        this.drawView();
      }
    } finally {
      this.polling = false;
    }
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
    menu.dropDetached(); // a page's menu or bar over the file that was open
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
    this.hash = r.hash;
    this.stat = { size: r.size, mtime: r.mtime };
    this.kind = r.kind;
    if (r.text === undefined) {
      const stage = this.preview(r);
      pane.append(stage);
      if (this.point && ["image", "pdf"].includes(r.kind)) {
        this.tray = new Tray(pane, `files:${path}:${r.hash}`, (text) => this.point.send(text), path);
        this.annotation = annotate(stage, this.tray, { path, kind: r.kind });
      }
      return;
    }
    this.readOnly = !!r.cut;
    if (r.cut) pane.append(el("p", "fnote", `Read-only: the first 2 MB of this ${size(r.size)} file.`));
    const rendered = ["markdown", "html", "image"].includes(r.kind);
    if (rendered) {
      const strip = el("div", "fstrip");
      for (const [mode, label] of [["view", "Rendered"], ["src", "Source"]]) {
        const b = el("button", "btn", label);
        b.type = "button";
        b.dataset.mode = mode;
        b.addEventListener("click", () => this.mode(mode));
        strip.append(b);
      }
      pane.append(strip, el("div", "fview"));
    }
    const host = el("div", "fed");
    pane.append(host);
    // Comments, by the path: a save here changes the mtime, and what was
    // said about the file before it still stands.
    this.tray = this.point ? new Tray(pane, `files:${path}`, (text) => this.point.send(text), path) : null;
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
    if (rendered) this.mode(line ? "src" : "view");
    if (line) ed.goto(line);
    else if (!rendered) ed.focus();
  }

  // What a selection says (spec 07): in the editor, the lines CodeMirror
  // counts and the text it holds, which may run past what is drawn; in the
  // rendered Markdown, the heading it sits under.
  pointItems(range, content) {
    if (!this.point || !this.path) return null;
    let locator;
    let text;
    let at = null;
    if (content.matches(".cm-content")) {
      const { state } = this.ed.view;
      const { from, to } = state.selection.main;
      if (from === to) return null;
      const first = state.doc.lineAt(from).number;
      const end = state.doc.lineAt(to);
      const last = end.from === to ? end.number - 1 : end.number;
      locator = textLocator(this.path, first, Math.max(first, last));
      text = state.sliceDoc(from, to).trim();
    } else {
      locator = docLocator(this.path, headingOf(range.startContainer, content));
      text = rangeText(range);
      at = offsetsIn(content, range);
    }
    if (!text) return null;
    return this.point.items({ locator, text, comment: () => this.tray?.add({ locator, quote: text, at }) });
  }

  // A right-click or a long press in the rendered Markdown: the selection, or
  // the block under it.
  pressed(t, ev) {
    const content = t.closest?.(".fview > .md");
    if (!ev || !content) return null;
    const sel = getSelection();
    const cur = sel.rangeCount && !sel.isCollapsed ? sel.getRangeAt(0) : null;
    const hit = (r) => ev.clientX >= r.left && ev.clientX <= r.right && ev.clientY >= r.top && ev.clientY <= r.bottom;
    const inSel = cur && filesUnit(cur, content)?.row === content && [...cur.getClientRects()].some(hit);
    const range = inSel ? cur : rangeAtPoint(content, ev.clientX, ev.clientY);
    const items = range && this.pointItems(range, content);
    return items?.length ? { groups: [items] } : null;
  }

  // Rendered or Source, for a kind that has both.
  mode(m) {
    this.viewMode = m;
    if (m !== "view") {
      this.annotation?.destroy();
      this.annotation = null;
    }
    for (const b of this.box.querySelectorAll(".fstrip .btn")) b.setAttribute("aria-pressed", String(b.dataset.mode === m));
    this.$(".fview").hidden = m !== "view";
    this.$(".fed").hidden = m === "view";
    if (m === "view") this.drawView();
    else this.ed?.focus();
  }

  // The rendered view: Markdown from the buffer, HTML and SVG from the disk
  // through a fresh ticket (one issued at open may have expired).
  async drawView() {
    if (this.viewMode !== "view" || !this.ed) return;
    const box = this.$(".fview");
    const path = this.path;
    if (this.kind === "markdown") {
      const doc = markdown(this.ed.value());
      box.replaceChildren(doc);
      for (const f of doc.querySelectorAll(".fence")) paint(f);
      this.relink(doc, path);
      this.tray?.layer(doc);
      return;
    }
    const r = await this.conn.call("fs.ticket", { path }).catch(() => null);
    if (!r || path !== this.path) return;
    const kind = this.kind === "html" ? "html" : "image";
    const stage = this.stage(kind, r.url, path);
    box.replaceChildren(stage);
    this.annotation?.destroy();
    this.annotation = annotate(stage, this.tray, { path, kind });
    menu.dropDetached(); // a page's menu or bar over the page drawn before
  }

  // A rendered Markdown file's relative images come through a ticket each,
  // resolved against its folder; its relative links open in this view.
  // A path climbing out of the root draws nothing.
  relink(doc, path) {
    const dir = parentOf(path);
    const local = (raw) => {
      if (!raw || /^([a-z][a-z0-9+.-]*:|\/|#)/i.test(raw)) return null;
      let rel;
      try {
        rel = normalize(join(dir, decodeURIComponent(raw.split(/[?#]/)[0])));
      } catch {
        return false;
      }
      return rel;
    };
    for (const img of doc.querySelectorAll("img")) {
      const rel = local(img.getAttribute("src"));
      if (rel === null) continue;
      img.removeAttribute("src");
      if (!rel) continue;
      this.conn.call("fs.ticket", { path: rel }).then(
        (r) => (img.src = r.url),
        () => (img.title = `${rel} could not be opened`),
      );
    }
    for (const a of doc.querySelectorAll("a[href]")) {
      const href = a.getAttribute("href");
      // A table of contents' "#install": the heading in this page, by the
      // slug GitHub gives it, never a new tab on the client's own address.
      if (href.startsWith("#")) {
        a.removeAttribute("target");
        a.addEventListener("click", (ev) => {
          ev.preventDefault();
          const want = decodeURIComponent(href.slice(1)).toLowerCase();
          const h = [...doc.querySelectorAll("h1,h2,h3,h4,h5,h6")].find((h) => slug(h.textContent) === want);
          h?.scrollIntoView({ block: "start" });
        });
        continue;
      }
      const rel = local(href);
      if (rel === null) continue;
      a.removeAttribute("target");
      if (rel) a.href = filesHash(rel);
      else a.removeAttribute("href");
    }
  }

  // An element drawing a ticket URL: a sandboxed frame for HTML, a frame
  // without the sandbox for a PDF (Chrome refuses to draw one under it), an
  // <img> fitted to the pane, or audio and video with their own controls.
  stage(kind, url, path) {
    const box = el("div", `fstage ${kind}`);
    let v;
    if (kind === "html" || kind === "pdf") {
      v = el("iframe");
      if (kind === "html") v.setAttribute("sandbox", "allow-scripts");
      v.title = path;
    } else if (kind === "image") {
      v = el("img");
      v.alt = path;
    } else {
      v = el(kind);
      v.controls = true;
      v.preload = "metadata";
    }
    v.src = url;
    box.append(v);
    return box;
  }

  // A file with no text: drawn from its ticket, or its size, type, a
  // download and the desktop's app for it.
  preview(r) {
    if (["image", "pdf", "audio", "video"].includes(r.kind)) return this.stage(r.kind, r.url, r.path);
    const box = el("div", "fother");
    box.append(el("p", "notice", `${r.mime || "A file"} · ${size(r.size)}. This kind of file has no preview here.`));
    const acts = el("div", "acts");
    const dl = el("a", "btn", "Download");
    dl.href = `${r.url}?download=1`;
    // The ticket from the open may have lapsed by the time of the click.
    dl.addEventListener("click", async (ev) => {
      ev.preventDefault();
      const t = await this.conn.call("fs.ticket", { path: r.path }).catch(() => null);
      location.assign(`${t?.url ?? r.url}?download=1`);
    });
    const native = el("button", "btn fnative", "Open in its app");
    native.type = "button";
    native.addEventListener("click", async () => {
      native.disabled = true;
      await this.conn.call("fs.open", { path: r.path }).catch((e) => this.toast(e.message, { kind: "error" }));
      setTimeout(() => (native.disabled = false), 800);
    });
    acts.append(dl, native);
    box.append(acts);
    return box;
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
  // `force` writes a clean buffer too: the file was deleted under it.
  save(force = false) {
    if (!this.ed || this.readOnly) return Promise.resolve(false);
    if (!this.saving && !this.ed.dirty() && !force) return Promise.resolve(true);
    this.saving ??= this.write().finally(() => (this.saving = null));
    return this.saving;
  }

  async write() {
    this.gen++;
    const content = this.ed.value();
    try {
      const r = await this.conn.call("fs.write", { path: this.path, content, base: this.hash });
      this.hash = r.hash;
      this.stat = { size: new TextEncoder().encode(content).length, mtime: r.mtime };
      if (this.ed.value() === content) this.ed.markSaved();
      this.banner(false);
      this.drawView();
      return true;
    } catch (e) {
      if (e.code === "changed_on_disk") {
        this.disk = e.data;
        this.banner("changed");
      } else this.toast(`Not saved: ${e.message}`, { kind: "error" });
      return false;
    }
  }

  // "changed": the disk has a version the buffer does not; "deleted": the
  // file is gone; false hides it.
  banner(on) {
    const b = this.$(".fbanner");
    b.hidden = !on;
    if (on) {
      b.dataset.kind = on;
      b.querySelector(".t").textContent =
        on === "deleted" ? "This file was deleted on disk." : "This file changed on disk since you opened it.";
    }
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
    this.gen++;
    const disk = this.disk;
    this.banner(false);
    if (disk?.text != null && this.ed) {
      this.ed.setValue(disk.text);
      this.hash = disk.hash;
      this.drawView();
      return;
    }
    await this.reopen();
  }

  async reopen() {
    const path = this.path;
    this.path = null; // load() opens it afresh
    await this.load(path);
  }

  keep() {
    this.gen++;
    // The next save overwrites the disk's version on purpose.
    this.hash = this.disk?.hash ?? this.hash;
    this.banner(false);
    this.ed?.focus();
  }
}
