// One renderer per entry kind: render(entry) -> Node.
//
// Every fact in an entry (glyph, title, summary, status, what collapses) was
// decided in Python. These functions only turn it into the markup the themes
// style; they compute nothing about tools.

import markdownit from "../vendor/markdown-it.mjs";
import { copyButton } from "./copy.js";
import { money } from "./fleet.js";
import { icon } from "./glyphs.js";

const md = markdownit({ html: false, linkify: true, breaks: false });
const defaultLink = md.renderer.rules.link_open || ((t, i, o, e, s) => s.renderToken(t, i, o));
md.renderer.rules.link_open = (tokens, idx, options, env, self) => {
  tokens[idx].attrSet("target", "_blank");
  tokens[idx].attrSet("rel", "noopener noreferrer");
  return defaultLink(tokens, idx, options, env, self);
};

// Where a sent file's URL is fetched from: "" on this server, `/via/<server>`
// for a transcript from a linked one (web.py streams it from there).
let fileBase = "";
export function setFileBase(base) {
  fileBase = base;
}

// A set of sent files as one card: a bar with the name and the actions, the
// preview below. A set pages in place (‹ ›), and a preview is built only when
// its file is shown. The preview kind was decided at send (files.py); Open
// natively shows only where the server said so (#a2[data-native]).
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
  const acts = el("span", "acts");
  const open = el("a", "btn primary open", "↗ Open");
  open.target = "_blank";
  open.rel = "noopener noreferrer";
  const native = el("button", "btn native", "⧉ Open natively");
  native.title = "Open in the desktop app for it";
  const dl = el("a", "btn dl", "↓ Download");
  acts.append(open, ...(remote ? [] : [native]), dl); // never natively from a linked server
  bar.append(el("span", "ic", glyph), name, size);
  const prev = el("button", "btn prev", "‹");
  const next = el("button", "btn next", "›");
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
    open.href = f.url;
    dl.href = f.download;
    native.dataset.fileId = f.file_id;
    native.dataset.name = f.name;
    count.textContent = `${i + 1} / ${sent.length}`;
    prev.disabled = i === 0;
    next.disabled = i === sent.length - 1;
    stage?.remove();
    stage = preview(f, remote);
    if (stage) card.append(stage);
  };
  prev.addEventListener("click", () => show(at - 1));
  next.addEventListener("click", () => show(at + 1));
  show(0);
  return card;
}

// One sent file's preview, or null when the browser cannot draw one.
function preview(f, remote) {
  const pv = f.preview;
  let view = null;
  if (pv === "image") {
    view = el("img");
    view.src = f.url;
    view.alt = f.name;
    view.loading = "lazy";
    view.addEventListener("click", () => window.open(f.url, "_blank", "noopener"));
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

export function markdown(text) {
  const div = document.createElement("div");
  div.className = "md";
  div.innerHTML = md.render(text || "");
  for (const pre of div.querySelectorAll("pre")) pre.append(copyButton("code"));
  return div;
}

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined && text !== null) n.textContent = text;
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
  r.append(el("span", "t", hhmm(entry.ts)), el("span", "g", entry.glyph), body);
  return r;
}

function diffBlock(diff) {
  const d = el("div", "diff");
  d.append(el("div", "path", diff.path));
  for (const line of diff.removed) d.append(el("div", "del", `- ${line}`));
  for (const line of diff.added) d.append(el("div", "add", `+ ${line}`));
  if (diff.elided) d.append(el("div", "elided", `… ${diff.elided} more changed lines`));
  return d;
}

const RENDERERS = {
  user(e) {
    const body = el("div", "body", e.md);
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
    const body = markdown(e.md);
    body.classList.add("body");
    body.append(copyButton("message"));
    const r = row(e, "prose", body);
    // Only the live view carries the flag; an archived transcript draws no mark.
    if (e.unread !== undefined) {
      const rm = el("span", "rm");
      rm.append(icon(e.unread ? "unread" : "read"));
      r.append(rm);
      r.classList.toggle("unread", e.unread);
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
      const b = el("button", "btn peek", det.peek ? "↻ Show the file again" : "▤ Show the file");
      b.dataset.entry = e.id;
      bar.append(b, el("span", "peekerr"));
      more.append(bar);
    }
    if (det.peek) {
      more.append(el("div", "peekcap", `as of ${hhmm(det.peek.ts)}`), fileCard(det.peek.files, det.peek.glyph));
    }
    if (det.diff) more.append(diffBlock(det.diff));
    if (det.tail) more.append(el("pre", "out", det.tail));
    if (det.args) more.append(el("pre", "args", det.args));
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
    body.append(markdown(text), copyButton("message"));
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
    const open = el("a", "btn open", "↗ Open");
    open.href = fileUrl(det.url);
    open.target = "_blank";
    open.rel = "noopener noreferrer";
    acts.append(open);
    if (e.status !== "live") acts.prepend(el("button", "btn show", "Show"));
    card.dataset.md = e.md || ""; // the caption's source, for update() to compare exactly
    bar.append(el("span", "ic", e.glyph), el("span", "fn", e.title), el("span", "fs", e.summary), acts);
    card.append(bar);
    if (e.status === "live") card.append(artifactStage(e));
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
  artifact(e, node) {
    const card = node.querySelector(".acard");
    const frame = node.querySelector("iframe[data-artifact]");
    if (!card || card.dataset.status !== e.status || !frame) return false;
    // A new caption, a new title or a new page (a resend) remounts the card.
    if (card.dataset.md !== (e.md || "")) return false;
    if (node.querySelector(".fbar .fn")?.textContent !== e.title) return false;
    if (frame.getAttribute("src") !== fileUrl(e.detail?.url)) return false;
    // Only a state the agent set since the frame last had one: every page
    // record patches this entry, and an emit must not echo the state back.
    const rev = String(e.detail?.state_rev);
    if (e.detail?.state_by === "agent" && rev !== frame.dataset.stateRev) {
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
