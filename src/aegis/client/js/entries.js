// One renderer per entry kind: render(entry) -> Node.
//
// Every fact in an entry (glyph, title, summary, status, what collapses) was
// decided in Python. These functions only turn it into the markup the themes
// style; they compute nothing about tools.

import markdownit from "../vendor/markdown-it.mjs";

const md = markdownit({ html: false, linkify: true, breaks: false });
const defaultLink = md.renderer.rules.link_open || ((t, i, o, e, s) => s.renderToken(t, i, o));
md.renderer.rules.link_open = (tokens, idx, options, env, self) => {
  tokens[idx].attrSet("target", "_blank");
  tokens[idx].attrSet("rel", "noopener noreferrer");
  return defaultLink(tokens, idx, options, env, self);
};

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
    return row(e, `user ${e.status}`, el("div", "body", e.md));
  },

  prose(e) {
    const body = markdown(e.md);
    body.classList.add("body");
    return row(e, "prose", body);
  },

  thinking(e) {
    const body = el("div", "body");
    if (e.md) {
      const d = el("details");
      d.append(el("summary", null, e.title || "Thinking"), markdown(e.md));
      body.append(d);
    } else {
      body.textContent = e.summary || "thought";
    }
    return row(e, "think", body);
  },

  tool(e) {
    const det = e.detail || {};
    const d = el("details");
    if (det.collapsed === false) d.open = true;
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
    if (more.childNodes.length) d.append(more);
    const body = el("div", "body");
    body.append(d);
    return row(e, `tool ${e.status}`, body);
  },

  system(e) {
    const body = el("div", "body", e.summary);
    if (e.detail?.tail) {
      const d = el("details");
      d.append(el("summary", null, "show"), el("pre", "out", e.detail.tail));
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
    const det = e.detail || {};
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
    acts.append(open, native, dl);
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
      if (pv === "html") view.setAttribute("sandbox", "allow-scripts");
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

  error(e) {
    const body = el("div", "body", e.summary);
    if (e.detail?.tail) body.append(el("pre", "out", e.detail.tail));
    return row(e, "error", body);
  },
};

export function render(entry) {
  return (RENDERERS[entry.kind] || RENDERERS.system)(entry);
}
