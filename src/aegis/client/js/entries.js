// One renderer per entry kind: render(entry) -> Node.
//
// Every fact in an entry (glyph, title, summary, status, what collapses) was
// decided in Python. These functions only turn it into the markup the themes
// style; they compute nothing about tools.

import markdownit from "../vendor/markdown-it.mjs";
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

export function markdown(text) {
  const div = document.createElement("div");
  div.className = "md";
  div.innerHTML = md.render(text || "");
  return div;
}

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined && text !== null) n.textContent = text;
  return n;
}

function hhmm(ts) {
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
    if (e.detail?.tail || e.detail?.more) {
      // A command OpenCode expanded: the line as typed, its template under it.
      const d = el("details");
      d.append(el("summary", null, "template"), e.detail.tail ? el("pre", "out", e.detail.tail) : el("div", "loading", "loading…"));
      body.append(d);
    }
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
    d.append(line);
    const more = el("div", "more");
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
    body.append(markdown(text));
    return row(e, "inbox", body);
  },

  file(e) {
    // An agent's file_send, as a card: a bar with the name and the actions,
    // the preview below. The preview kind was decided at send (files.py);
    // Open natively shows only where the server said so (#a2[data-native]).
    const det = { ...(e.detail || {}) };
    const remote = fileBase !== "";
    if (remote) {
      // A linked server's card: every link is rebuilt here from a path of the
      // one shape /via serves, so no URL the far server chose reaches an href
      // or a frame on this origin (a javascript: download ran with the cookie).
      const ok = typeof det.url === "string" && /^\/files\/[A-Za-z0-9_-]+\/[^/?#]+$/.test(det.url);
      det.url = ok ? fileBase + det.url : "about:blank";
      det.download = ok ? `${det.url}?download=1` : "about:blank";
    }
    const body = el("div", "body");
    if (e.md) {
      const cap = markdown(e.md);
      cap.classList.add("cap");
      body.append(cap);
    }
    const card = el("div", "fcard");
    const bar = el("div", "fbar");
    const acts = el("span", "acts");
    const open = el("a", "btn primary open", "↗ Open");
    open.href = det.url;
    open.target = "_blank";
    open.rel = "noopener noreferrer";
    const native = el("button", "btn native", "⧉ Open natively");
    native.title = "Open in the desktop app for it";
    native.dataset.fileId = det.file_id;
    native.dataset.name = e.title;
    const dl = el("a", "btn dl", "↓ Download");
    dl.href = det.download;
    acts.append(open, ...(remote ? [] : [native]), dl); // never natively from a linked server
    bar.append(el("span", "ic", e.glyph), el("span", "fn", e.title), el("span", "fs", e.summary), acts);
    card.append(bar);
    const pv = det.preview;
    let view = null;
    if (pv === "image") {
      view = el("img");
      view.src = det.url;
      view.alt = e.title;
      view.loading = "lazy";
      view.addEventListener("click", () => window.open(det.url, "_blank", "noopener"));
    } else if (pv === "pdf" || pv === "html") {
      view = el("iframe");
      // A linked server's frame is sandboxed unless it is a PDF, which /via
      // serves as application/pdf whatever the far server says.
      if (pv === "html" || (remote && !/\.pdf$/i.test(det.url))) view.setAttribute("sandbox", "allow-scripts");
      view.loading = "lazy";
      view.src = det.url;
      view.title = e.title;
    } else if (pv === "markdown") {
      view = markdown(det.excerpt);
      view.classList.add("excerpt");
    } else if (pv === "text") {
      view = el("pre", "excerpt", det.excerpt);
    } else if (pv === "audio" || pv === "video") {
      view = el(pv);
      view.controls = true;
      view.preload = "metadata";
      view.src = det.url;
    }
    if (view) {
      const stage = el("div", `stage ${pv}`);
      stage.append(view);
      card.append(stage);
    }
    body.append(card);
    return row(e, "file", body);
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
