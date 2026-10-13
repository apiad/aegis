// One renderer per entry kind: render(entry) -> Node.
//
// Every fact in an entry (glyph, title, summary, status, what collapses) was
// decided in Python. These functions only turn it into the markup the themes
// style; they compute nothing about tools.

import markdownit from "../vendor/markdown-it.mjs";
import { copyButton } from "./copy.js";
import { money } from "./fleet.js";
import { entryIcon, icon } from "./glyphs.js";
import { diffView } from "./diff.js";
import { cachedColour, colour, isPlain, langName, paint, splitLines } from "./highlight.js";

const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

// Two renderers: the person's messages keep a typed newline (breaks), an
// agent's text follows Markdown's own rule.
const mdAgent = makeMd(false);
const mdTyped = makeMd(true);

function makeMd(breaks) {
  const m = markdownit({ html: false, linkify: true, breaks });
  const defaultLink = m.renderer.rules.link_open || ((t, i, o, e, s) => s.renderToken(t, i, o));
  m.renderer.rules.link_open = (tokens, idx, options, env, self) => {
    tokens[idx].attrSet("target", "_blank");
    tokens[idx].attrSet("rel", "noopener noreferrer");
    return defaultLink(tokens, idx, options, env, self);
  };
  // A table sits in a block that holds its copy button, over a scroller; its
  // source lines go along for the copy (copy.js).
  m.renderer.rules.table_open = (t, i) => `<div class="tblk" data-lines="${t[i].map[0]}-${t[i].map[1]}"><div class="tw"><table>`;
  m.renderer.rules.table_close = () => "</table></div></div>";
  m.renderer.rules.fence = (t, i, o, env) => fenceHtml(t[i].content, t[i].info.trim().split(/\s+/)[0] || "", !closed(t[i], env));
  m.core.ruler.after("inline", "tasks", tasks);
  return m;
}

// A fence is closed when its last line is a closing marker; an open one runs
// to the end of the text (a reply still streaming, or one cut off). Inside a
// quote or a list that line keeps the container's ">" and indent.
function closed(tok, env) {
  const [a, z] = tok.map;
  const last = env.lines?.[z - 1];
  if (z - 1 <= a || last === undefined) return false;
  const m = /^[\s>]*(`{3,}|~{3,})\s*$/.exec(last);
  return !!m && m[1][0] === tok.markup[0] && m[1].length >= tok.markup.length;
}

// A fenced block: a header with its language, then the code. A block whose
// colour is already cached is drawn coloured at once, so a reply re-rendered
// by every delta does not flash plain; the rest is coloured by
// highlight.paint when it nears the view (transcript.js).
export function fenceHtml(text, label, open = false) {
  const name = langName(label);
  const may = !open && !isPlain(label) && (name || !label);
  const hit = may ? cachedColour(text, name) : undefined;
  const tag = hit?.guessed ? `<span class="lang guessed">${esc(hit.lang)}</span>` : `<span class="lang">${esc(label)}</span>`;
  const attrs = `data-lang="${esc(label)}"${open ? " data-open" : ""}${hit !== undefined ? ' data-painted="1"' : ""}`;
  return `<div class="fence" ${attrs}><div class="fh">${tag}</div><pre><code${hit ? ' class="hljs"' : ""}>${hit ? hit.html : esc(text)}</code></pre></div>`;
}

// "- [ ] x" and "- [x] x" become a disabled checkbox; ten lines, no plugin.
function tasks(state) {
  const ts = state.tokens;
  for (let i = 2; i < ts.length; i++) {
    const inl = ts[i];
    if (inl.type !== "inline" || ts[i - 1].type !== "paragraph_open" || ts[i - 2].type !== "list_item_open") continue;
    const m = /^\[([ xX])\] /.exec(inl.content);
    const first = inl.children?.[0];
    if (!m || first?.type !== "text" || !first.content.startsWith(m[0])) continue;
    first.content = first.content.slice(m[0].length);
    const box = new state.Token("html_inline", "", 0);
    box.content = `<input type="checkbox" disabled${m[1] === " " ? "" : " checked"}> `;
    inl.children.unshift(box);
    ts[i - 2].attrJoin("class", "task");
  }
}

// A table wider than its row fades at the edge it can still scroll to.
function fade(tw) {
  tw.classList.toggle("more-r", tw.scrollLeft + tw.clientWidth < tw.scrollWidth - 2);
}
export function fadeTables(root) {
  for (const tw of root.querySelectorAll(".md .tw")) fade(tw);
}

// Where a sent file's URL is fetched from: "" on this server, `/via/<server>`
// for a transcript from a linked one (web.py streams it from there).
let fileBase = "";
export function setFileBase(base) {
  fileBase = base;
}

// A set of sent files as one card: a bar with the name and the actions, the
// preview below. A set pages in place (‹ ›), and a preview is built only when
// its file is shown. The preview kind was decided at send (files.py); Open
// hands the set to the viewer, at the file shown.
function fileCard(files, glyph) {
  const sent = files.map((f) => ({ ...f }));
  const remote = fileBase !== "";
  if (remote) {
    // A linked server's card: every link is rebuilt here from a path of the
    // one shape /via serves, so no URL the far server chose reaches an href
    // or a frame on this origin (a javascript: download ran with the cookie).
    for (const f of sent) {
      const ok = typeof f.url === "string" && /^\/files\/[A-Za-z0-9_-]+\/[^/?#]+$/.test(f.url);
      f.url = ok ? fileBase + f.url : "about:blank";
      f.download = ok ? `${f.url}?download=1` : "about:blank";
    }
  }
  const card = el("div", "fcard");
  const bar = el("div", "fbar");
  const name = el("span", "fn");
  const size = el("span", "fs");
  const { acts, set } = fileActions(remote);
  const view = iconButton(el("button", "btn primary view"), "expand", "Open");
  view.addEventListener("click", () => opener({ files: sent, at, remote, glyph }));
  const dock = dockButton("");
  acts.prepend(view, ...(remote ? [] : [dock]));
  bar.append(entryIcon(glyph), name, size);
  const prev = withIcon(el("button", "btn prev"), "left");
  const next = withIcon(el("button", "btn next"), "right");
  const count = el("span", "count");
  if (sent.length > 1) {
    prev.title = "Previous file";
    next.title = "Next file";
    const pager = el("span", "pager");
    pager.append(prev, count, next);
    bar.append(pager);
  }
  bar.append(acts);
  card.append(bar);
  let at = 0;
  let stage = null;
  const show = (i) => {
    at = i;
    const f = sent[i];
    name.textContent = f.name;
    size.textContent = f.summary;
    set(f);
    // An older version docks nothing: its key's pane shows the newest.
    dock.dataset.dock = f.replaced ? "" : f.key || "";
    dock.hidden = !docker.can(dock.dataset.dock);
    count.textContent = `${i + 1} / ${sent.length}`;
    prev.disabled = i === 0;
    next.disabled = i === sent.length - 1;
    stage?.remove();
    // A replaced file, or one docked as a pane, holds a line instead of its
    // preview; syncDock() redraws it when the docking changes.
    const hd = holds(f);
    card.dataset.held = hd;
    stage =
      hd === "replaced" ? el("div", "held", `replaced by v${f.replaced}`)
      : hd === "docked" ? heldLine(f.key, DOCKED)
      : hd ? heldLine(f.key, hd)
      : preview(f, remote, () => view.click());
    if (stage) card.append(stage);
  };
  // What the shown page holds now: "replaced", "docked", "in window <n>" or "".
  const holds = (f) => {
    if (f.replaced) return "replaced";
    const at = f.key && !remote && docker.docked(f.key);
    return at === true ? "docked" : at ? inWindow(at) : "";
  };
  card._sync = () => {
    if (holds(sent[at]) !== card.dataset.held) show(at);
  };
  prev.addEventListener("click", () => show(at - 1));
  next.addEventListener("click", () => show(at + 1));
  show(0);
  return card;
}

// The buttons a file offers on its card and in the viewer's bar: a new tab,
// the desktop app and a download, each an icon that names itself in its
// title. Open natively shows only where the server said so (#a2[data-native])
// and never for a linked server's file.
export function fileActions(remote) {
  const acts = el("span", "acts");
  const open = iconButton(el("a", "btn open"), "open", "Open in a new tab");
  open.target = "_blank";
  open.rel = "noopener noreferrer";
  const native = iconButton(el("button", "btn native"), "window", "Open in the desktop app for it");
  const dl = iconButton(el("a", "btn dl"), "latest", "Download");
  acts.append(open, ...(remote ? [] : [native]), dl);
  const set = (f) => {
    open.href = f.url;
    dl.href = f.download;
    native.dataset.fileId = f.file_id;
    native.dataset.name = f.name;
  };
  return { acts, set };
}

// What a card's Open does: app.js hands it the viewer (js/viewer.js), which
// takes {files, at, remote, glyph} for a set of files or {artifact} for a page.
let opener = () => {};
export function setOpener(fn) {
  opener = fn;
}

// What Dock does, on a card and on the viewer's bar: app.js hands it the
// canvas (js/canvas.js), so this module does not import it. can(id) says
// whether the button shows: not while the item is docked, not for a file
// sent before files had keys, and not on a session the person may not change.
// docked(id) is true when a pane shows it here, the window's number when a
// pane in another window does, and false otherwise. show(id) outlines that
// pane (a docked card's Show), open(n) focuses or opens window n's tab, and
// pop(id) moves a page to a window of its own; canPop(id) says whether Pop
// out shows (not on a phone, which has one window). frames() is false in a
// window tab: its transcript is never shown, and a page whose card mounted a
// frame there would run in window 1 and in that tab at once.
let docker = {
  dock: () => {},
  can: () => false,
  docked: () => false,
  show: () => {},
  open: () => {},
  pop: () => {},
  canPop: () => false,
  frames: () => true,
};
export function setDocker(d) {
  docker = { ...d, can: (id) => !!id && d.can(id) };
}
export function dockButton(id) {
  const b = iconButton(el("button", "btn dock"), "dock", "Dock beside the transcript");
  b.type = "button";
  b.dataset.dock = id;
  b.hidden = !docker.can(id);
  b.addEventListener("click", () => docker.dock(b.dataset.dock));
  return b;
}
// Pop out, on a live page's card and pane: it takes the place of Open in a
// new tab, which ran the page with no bridge behind it.
export function popButton(id) {
  const b = iconButton(el("button", "btn popout"), "open", "Pop out into a browser window of its own");
  b.type = "button";
  b.dataset.pop = id;
  b.hidden = !docker.canPop(id);
  b.addEventListener("click", () => docker.pop(b.dataset.pop));
  return b;
}
// The canvas or who may change it changed: every Dock and Pop out button
// drawn shows or hides, and a file card whose page was docked or undocked
// redraws its stage in place (an artifact's card follows hold() instead).
export function syncDock() {
  for (const b of document.querySelectorAll("#a2 .btn.dock[data-dock]")) b.hidden = !docker.can(b.dataset.dock);
  for (const b of document.querySelectorAll("#a2 .btn.popout[data-pop]")) b.hidden = !docker.canPop(b.dataset.pop);
  for (const card of document.querySelectorAll("#entries .fcard:not(.acard)")) card._sync?.();
}

// The line a held card shows instead of its stage. A docked one carries Show,
// which outlines its pane; one in another browser window carries Open, which
// focuses that window's tab or opens it.
export const DOCKED = "docked";
export const inWindow = (n) => `in window ${n}`;
function heldLine(id, label) {
  const line = el("div", "held", label);
  const away = /^in window (\d+)$/.exec(label);
  if (label === DOCKED || away) {
    const b = el("button", "btn link locate", label === DOCKED ? "show" : "open");
    b.type = "button";
    b.title = label === DOCKED ? "Outline its pane" : `Open window ${away[1]}`;
    b.addEventListener("click", () => (away ? docker.open(Number(away[1])) : docker.show(id)));
    line.append(" · ", b);
  }
  return line;
}

// One sent file's preview, or null when the browser cannot draw one. A click
// on an image calls zoom, when there is one.
export function preview(f, remote, zoom) {
  const pv = f.preview;
  let view = null;
  if (pv === "image") {
    view = el("img");
    view.src = f.url;
    view.alt = f.name;
    view.loading = "lazy";
    if (zoom) view.addEventListener("click", zoom);
  } else if (pv === "pdf" || pv === "html") {
    view = el("iframe");
    // A linked server's frame is sandboxed unless it is a PDF, which /via
    // serves as application/pdf whatever the far server says.
    if (pv === "html" || (remote && !/\.pdf$/i.test(f.url))) view.setAttribute("sandbox", "allow-scripts");
    view.loading = "lazy";
    view.src = f.url;
    view.title = f.name;
  } else if (pv === "markdown") {
    view = markdown(f.excerpt);
    view.classList.add("excerpt");
  } else if (pv === "text") {
    view = el("pre", "excerpt", f.excerpt);
  } else if (pv === "audio" || pv === "video") {
    view = el(pv);
    view.controls = true;
    view.preload = "metadata";
    view.src = f.url;
  }
  if (!view) return null;
  const stage = el("div", `stage ${pv}`);
  stage.append(view);
  return stage;
}

export function markdown(text, { breaks = false, paths = null } = {}) {
  const div = document.createElement("div");
  div.className = "md";
  const src = text || "";
  div._src = src; // what a table's copy slices its lines from (copy.js)
  div.innerHTML = (breaks ? mdTyped : mdAgent).render(src, { lines: src.split("\n") });
  for (const fh of div.querySelectorAll(".fence > .fh")) fh.append(copyButton("code"));
  for (const pre of div.querySelectorAll("pre")) if (!pre.closest(".fence")) pre.append(copyButton("code"));
  for (const blk of div.querySelectorAll(".tblk")) {
    blk.prepend(copyButton("table"));
    const heads = [...blk.querySelectorAll("thead th")].map((th) => th.textContent);
    for (const tr of blk.querySelectorAll("tbody tr")) {
      [...tr.children].forEach((td, i) => {
        td.dataset.label = heads[i] ?? "";
      });
    }
    const tw = blk.querySelector(".tw");
    tw.addEventListener("scroll", () => fade(tw), { passive: true });
  }
  // The spans a reply listed as paths (detail.paths) open the file in the
  // viewer; a linked server's do not, as the file is on the other machine.
  if (paths?.length && fileBase === "") {
    for (const code of div.querySelectorAll(":not(pre) > code")) {
      const i = paths.indexOf(code.textContent.trim());
      if (i < 0) continue;
      code.classList.add("path");
      code.dataset.index = String(i);
      code.title = "Open in the viewer";
    }
  }
  return div;
}

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined && text !== null) n.textContent = text;
  return n;
}

// A node holding a sprite symbol and, for a button, its label.
function withIcon(n, name, text) {
  n.append(entryIcon(name));
  if (text) n.append(el("span", null, text));
  return n;
}

// A button that is only its icon: the label is its tooltip and its name.
function iconButton(n, name, label) {
  n.classList.add("icon");
  n.append(entryIcon(name));
  n.title = label;
  n.setAttribute("aria-label", label);
  return n;
}

export function hhmm(ts) {
  if (!ts) return "";
  const d = new Date(ts * 1000);
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

function row(entry, cls, body) {
  const r = el("div", `row ${cls}`);
  r.dataset.id = entry.id;
  r.append(el("span", "t", hhmm(entry.ts)), withIcon(el("span", "g"), entry.glyph), body);
  return r;
}

// A file's lines, numbered from where the view starts, coloured once the
// language has loaded (the row is open, so it is in view).
function fileView(file, lang) {
  const view = el("div", "lines");
  const draw = (html) => {
    view.innerHTML = html.map((h, i) => `<span class="n">${file.first + i}</span><span class="c">${h}</span>`).join("");
  };
  draw(file.text.split("\n").map(esc));
  if (lang) colour(file.text, lang).then((got) => got && draw(splitLines(got.html)), () => {});
  return view;
}

// A tool's arguments: a coloured block for a command or other arguments, the
// plain lines for a file tool's (its path and range).
function argsView(text, lang) {
  if (!lang) return el("pre", "args", text);
  const wrap = el("div", "md args");
  wrap.innerHTML = fenceHtml(text, lang);
  wrap.querySelector(".fh").append(copyButton("code"));
  paint(wrap.firstChild);
  return wrap;
}

const RENDERERS = {
  user(e) {
    const body = el("div", "body");
    if (e.md) body.append(markdown(e.md, { breaks: true }));
    if (e.detail?.files?.length) {
      // What the person attached: one card that pages, as a file_send's.
      const card = fileCard(e.detail.files, "▤");
      card.classList.add("att");
      if (e.md) card.classList.add("after");
      body.append(card);
    }
    if (e.detail?.tail || e.detail?.more) {
      // A command OpenCode expanded: the line as typed, its template under it.
      const d = el("details");
      d.append(el("summary", null, "template"), e.detail.tail ? el("pre", "out", e.detail.tail) : el("div", "loading", "loading…"));
      body.append(d);
    }
    body.append(copyButton("message"));
    return row(e, `user ${e.status}`, body);
  },

  command(e) {
    const body = el("div", "body");
    body.append(el("div", "cmd", e.title));
    if (e.md) body.append(markdown(e.md));
    return row(e, `command ${e.status}`, body);
  },

  prose(e) {
    const body = markdown(e.md, { paths: e.detail?.paths });
    body.classList.add("body");
    const r = row(e, "prose", body);
    // Only the live view carries the flag; an archived transcript draws no mark.
    if (e.unread !== undefined) {
      const rm = el("span", "rm");
      rm.append(icon(e.unread ? "unread" : "read"));
      r.append(rm);
      r.classList.toggle("unread", e.unread);
      r.dataset.rev = String(e.rev);
    }
    return r;
  },

  thinking(e) {
    const body = el("div", "body");
    if (e.md || e.detail?.more) {
      const d = el("details");
      d.append(el("summary", null, e.title || "Thinking"), e.md ? markdown(e.md) : el("div", "loading", "loading…"));
      body.append(d);
    } else {
      body.textContent = e.summary || "thought";
    }
    return row(e, "think", body);
  },

  tool(e) {
    const det = e.detail || {};
    const d = el("details");
    const line = el("summary", "line");
    const name = el("span", "tn", e.title);
    const label = el("span", "ta", e.summary);
    if (det.steps) label.append(el("span", "steps", `${det.steps} steps`));
    line.append(name, label, el("span", "tr2", e.status === "running" ? "" : det.result || ""));
    if (e.status !== "running") line.append(copyButton("output"));
    d.append(line);
    const more = el("div", "more");
    if (det.path && fileBase === "") {
      // A file tool's row: the person can see its file without the agent
      // sending it (file.peek). Not on a linked server's row: the op is local.
      const bar = el("div", "peekbar");
      const b = det.peek ? withIcon(el("button", "btn peek"), "again", "Show the file again") : withIcon(el("button", "btn peek"), "file", "Show the file");
      b.dataset.entry = e.id;
      bar.append(b, el("span", "peekerr"));
      more.append(bar);
    }
    if (det.peek) {
      more.append(el("div", "peekcap", `as of ${hhmm(det.peek.ts)}`), fileCard(det.peek.files, det.peek.glyph));
    }
    if (det.file) more.append(fileView(det.file, det.lang));
    if (det.diff) more.append(diffView(det.diff, det.lang));
    if (det.tail) more.append(el("pre", "out", det.tail));
    if (det.args && !det.file && !det.diff) more.append(argsView(det.args, det.path ? null : det.lang));
    if (det.more) more.append(el("div", "loading", "loading…"));
    if (more.childNodes.length) d.append(more);
    const body = el("div", "body");
    body.append(d);
    return row(e, `tool ${e.status}`, body);
  },

  system(e) {
    const body = el("div", "body", e.summary);
    if (e.detail?.tail || e.detail?.more) {
      const d = el("details");
      d.append(el("summary", null, "show"), e.detail.tail ? el("pre", "out", e.detail.tail) : el("div", "loading", "loading…"));
      body.append(d);
    }
    return row(e, "sys", body);
  },

  inbox(e) {
    // A monitor wake, a queue result, a handoff: the header is the title.
    const body = el("div", "body");
    body.append(el("div", "from", e.title));
    const text = (e.md || "").split("\n").filter((l) => !l.startsWith("> from ")).join("\n");
    body.append(markdown(text));
    return row(e, "inbox", body);
  },

  file(e) {
    // An agent's file_send, as a card (fileCard) under its caption.
    const det = e.detail || {};
    // A linked server older than sets sends its one file's fields on the detail.
    const sent = det.files || [{ ...det, name: e.title, summary: e.summary }];
    const body = el("div", "body");
    if (e.md) {
      const cap = markdown(e.md);
      cap.classList.add("cap");
      body.append(cap);
    }
    body.append(fileCard(sent, e.glyph));
    return row(e, "file", body);
  },

  artifact(e) {
    // An agent's interactive page: a card like a file's, the frame while
    // live, one line with the label after. Status and label were decided in
    // the fold; the frame's state is handed over by js/artifacts.js on init.
    const det = e.detail || {};
    const body = el("div", "body");
    if (e.md) {
      const cap = markdown(e.md);
      cap.classList.add("cap");
      body.append(cap);
    }
    const card = el("div", "fcard acard");
    card.dataset.status = e.status;
    const bar = el("div", "fbar");
    const acts = el("span", "acts");
    const view = iconButton(el("button", "btn primary view"), "expand", "Open");
    view.addEventListener("click", () => opener({ artifact: e.id }));
    if (e.status === "live") acts.append(view, dockButton(e.id), popButton(e.id));
    else {
      const open = iconButton(el("a", "btn open"), "open", "Open in a new tab");
      open.href = fileUrl(det.url);
      open.target = "_blank";
      open.rel = "noopener noreferrer";
      acts.append(view, open);
    }
    if (e.status !== "live") acts.prepend(el("button", "btn show", "Show"));
    card.dataset.md = e.md || ""; // the caption's source, for update() to compare exactly
    bar.append(entryIcon(e.glyph), el("span", "fn", e.title), el("span", "fs", e.summary), acts);
    card.append(bar);
    const holder = held.get(e.id);
    holder?.onEntry(e);
    if (e.status === "live" && holder) card.append(heldLine(e.id, holder.label));
    else if (e.status === "live" && !docker.frames()) card.append(heldLine(e.id, "in window 1"));
    else if (e.status === "live") card.append(artifactStage(e));
    else {
      const what = det.label || (e.status === "closed" ? "closed by the agent" : "answered");
      card.append(el("div", "done", `${what} · ${hhmm(det.ended_ts)}`));
    }
    body.append(card);
    return row(e, "artifact", body);
  },

  recap(e) {
    const det = e.detail || {};
    const body = el("div", "body");
    if (det.folded) {
      body.append(el("span", "lbl", "recap · "), el("span", "ctx", det.context));
      const r = row(e, "recap folded", body);
      r.querySelector(".g").replaceChildren(icon("sparkle"));
      return r;
    }
    const hd = el("div", "hd");
    hd.append(icon("sparkle"), el("span", null, det.ask ? "recap · needs you" : "recap"));
    body.append(hd, el("div", "ctx", det.context));
    if (det.ask) body.append(el("div", "ask", det.ask));
    const ft = el("div", "ft");
    const secs = det.duration_ms ? `${(det.duration_ms / 1000).toFixed(1)}s` : "";
    ft.append(el("span", null, [det.model, secs, det.cost_usd ? money(det.cost_usd) : ""].filter(Boolean).join(" · ")));
    const again = el("button", "btn link refresh", "refresh");
    again.dataset.recap = "force";
    ft.append(again);
    body.append(ft);
    const r = row(e, "recap", body);
    r.querySelector(".g").replaceChildren(icon("sparkle"));
    return r;
  },

  error(e) {
    const body = el("div", "body", e.summary);
    if (e.detail?.tail) body.append(el("pre", "out", e.detail.tail));
    return row(e, "error", body);
  },
};

export function render(entry) {
  return (RENDERERS[entry.kind] || RENDERERS.system)(entry);
}

// A sent file's or artifact's URL as this page may use it: on a linked
// server's transcript, only a path of the one shape /via serves, rebuilt here,
// so no URL the far server chose reaches an href or a frame on this origin.
export function fileUrl(url) {
  if (fileBase === "") return url;
  const ok = typeof url === "string" && /^\/files\/[A-Za-z0-9_-]+\/[^/?#]+$/.test(url);
  return ok ? fileBase + url : "about:blank";
}

// The artifacts held elsewhere (the viewer, a pane), each with what to call
// when its entry is drawn again (a new status, a resend) and the line its card
// shows instead. While one is live its card draws no frame, so the page never
// runs twice; releasing it gives the card its frame back, if the card is on
// screen.
const held = new Map(); // artifact id -> {onEntry: (entry) => void, label}
export function hold(id, onEntry, entry, label = "open in the viewer") {
  const card = document.querySelector(`#entries .row[data-id="${CSS.escape(id)}"] .acard`);
  if (onEntry) {
    held.set(id, { onEntry, label });
    const was = card?.dataset.status === "live" && card.querySelector(":scope > .stage, :scope > .held");
    if (was) was.replaceWith(heldLine(id, label));
    return;
  }
  held.delete(id);
  const line = card?.querySelector(":scope > .held");
  if (line && entry?.status === "live" && docker.frames()) line.replaceWith(artifactStage(entry));
}

// The frame of an artifact's card, live or shown again read-only.
export function artifactStage(e) {
  const stage = el("div", "stage html");
  const f = el("iframe");
  f.setAttribute("sandbox", "allow-scripts");
  f.loading = "lazy";
  f.src = fileUrl(e.detail.url);
  f.title = e.title;
  f.dataset.artifact = e.id;
  f.dataset.status = e.status;
  f.dataset.stateRev = String(e.detail.state_rev); // the state it starts with
  stage.append(f);
  return stage;
}

// A row updated in place, so a live frame is not reloaded by every patch.
// True when the node now shows the entry; false when it must be remounted.
const UPDATERS = {
  // Being read re-sends the message with its revision unchanged: only the
  // mark flips, and the row keeps the pointer's hover and any selection in it.
  prose(e, node) {
    const rm = node.querySelector(":scope > .rm");
    if (!rm || e.unread === undefined || node.dataset.rev !== String(e.rev)) return false;
    rm.replaceChildren(icon(e.unread ? "unread" : "read"));
    node.classList.toggle("unread", e.unread);
    return true;
  },
  artifact(e, node) {
    const card = node.querySelector(".acard");
    // A page the viewer or a pane holds runs only there: the card keeps a
    // line, and the agent's state goes to that frame, when the session is shown.
    const holder = held.get(e.id);
    const lined = holder || !docker.frames(); // the card shows a line, not the page
    const sel = `iframe[data-artifact="${CSS.escape(e.id)}"]`;
    const frame = holder
      ? document.querySelector(`#viewer ${sel}, #canvas .pane.docked ${sel}`)
      : lined
        ? null
        : node.querySelector("iframe[data-artifact]");
    if (!card || card.dataset.status !== e.status) return false;
    if (lined ? !card.querySelector(".held") : !frame) return false;
    // A new caption, a new title or a new page (a resend) remounts the card.
    if (card.dataset.md !== (e.md || "")) return false;
    if (node.querySelector(".fbar .fn")?.textContent !== e.title) return false;
    if (frame && frame.getAttribute("src") !== fileUrl(e.detail?.url)) return false;
    // Only a state the agent set since the frame last had one: every page
    // record patches this entry, and an emit must not echo the state back.
    const rev = String(e.detail?.state_rev);
    if (frame && e.detail?.state_by === "agent" && rev !== frame.dataset.stateRev) {
      frame.dataset.stateRev = rev;
      frame.dispatchEvent(new CustomEvent("aegis:state", { detail: e.detail.state, bubbles: true }));
    }
    return true;
  },
};
export function update(e, node) {
  const u = UPDATERS[e.kind];
  return u ? u(e, node) : false;
}
