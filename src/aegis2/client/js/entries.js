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

  error(e) {
    const body = el("div", "body", e.summary);
    if (e.detail?.tail) body.append(el("pre", "out", e.detail.tail));
    return row(e, "error", body);
  },
};

export function render(entry) {
  return (RENDERERS[entry.kind] || RENDERERS.system)(entry);
}
