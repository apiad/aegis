// The viewer: a sent file or an agent's page, shown whole over the session's
// column (the transcript, the navigator and the composer) while the tabs and
// the session panel stay in reach. Each session keeps its own: a tab switch
// hides it and a return draws it again.
//
// Markdown and text are fetched whole (the card only carries an excerpt) and
// drawn in the theme: Markdown through the transcript's renderer, text as
// numbered lines coloured by highlight.js, which loads the first time it is
// needed. Everything else is the card's own preview at full size, so a frame
// keeps the sandbox the card decided.
//
// An artifact's page runs in one place only. While the viewer shows a live
// one, its card holds a line instead of a frame (entries.hold), and the
// agent's state reaches the viewer's frame instead.

import { artifactStage, dockButton, fileActions, fileUrl, hold, markdown, preview } from "./entries.js";
import { entryIcon, icon } from "./glyphs.js";
import { colour, langOfPath, paint } from "./highlight.js";

const MAX_TEXT = 2 * 1024 * 1024; // bytes drawn of a text file; a new tab has the rest

// A file's text, at most MAX_TEXT bytes of it, cut at a line when cut.
async function fetchText(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  const reader = r.body.getReader();
  const parts = [];
  let size = 0;
  while (size <= MAX_TEXT) {
    const { done, value } = await reader.read();
    if (done) break;
    parts.push(value);
    size += value.length;
  }
  const cut = size > MAX_TEXT;
  if (cut) reader.cancel();
  const bytes = new Uint8Array(Math.min(size, MAX_TEXT));
  let at = 0;
  for (const p of parts) {
    const take = Math.min(p.length, bytes.length - at);
    bytes.set(p.subarray(0, take), at);
    at += take;
  }
  let text = new TextDecoder().decode(bytes);
  if (cut) text = text.slice(0, text.lastIndexOf("\n") + 1);
  return { text, cut };
}

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined && text !== null) n.textContent = text;
  return n;
}

// One sent file at full size, drawn into a bar and a body: the viewer's and a
// docked pane's (js/canvas.js). The bar gets the file's glyph, name and size,
// and acts its buttons; the caller appends acts, after anything of its own.
// isCurrent() is false once the caller has drawn something else, so a fetch
// that returns late draws nothing.
export function drawFile(f, { remote, glyph, bar, body, acts, line, isCurrent = () => true }) {
  const { acts: own, set } = fileActions(remote);
  set(f);
  acts.append(...own.children);
  bar.append(entryIcon(glyph), el("span", "fn", f.name), el("span", "fs", f.summary));
  body.dataset.preview = f.preview || "other";
  if (f.preview === "markdown" || f.preview === "text") {
    body.append(el("div", "vnote", "loading…"));
    drawText(f, body, line, isCurrent);
    return;
  }
  const stage = preview(f, remote);
  if (stage) {
    const frame = stage.querySelector("iframe");
    if (frame) frame.loading = "eager";
    body.append(stage);
  } else {
    const none = el("div", "vnote", "There is no preview for this file. ");
    const dl = el("a", null, "Download it");
    dl.href = f.download;
    none.append(dl);
    body.append(none);
  }
}

async function drawText(f, body, line, isCurrent) {
  // Fetch only the text; the loader brings what colouring needs.
  let got;
  try {
    got = await fetchText(f.url);
  } catch (err) {
    if (isCurrent()) body.replaceChildren(el("div", "vnote", `Could not load the file: ${err.message}`));
    return;
  }
  if (!isCurrent()) return;
  const { text, cut } = got;
  const out = [];
  if (f.preview === "markdown") {
    const doc = markdown(text);
    doc.classList.add("doc");
    await Promise.all([...doc.querySelectorAll(".fence")].map(paint));
    if (!isCurrent()) return;
    out.push(doc);
  } else {
    const lines = text.endsWith("\n") ? text.slice(0, -1).split("\n") : text.split("\n");
    const view = el("div", "code");
    const gutter = el("pre", "ln", lines.map((_, i) => i + 1).join("\n"));
    gutter.setAttribute("aria-hidden", "true");
    const src = el("pre", "src");
    const code = el("code", "hljs");
    const lang = langOfPath(f.name);
    const hl = lang ? await colour(text, lang).catch(() => null) : null; // plain if it fails
    if (!isCurrent()) return;
    if (hl) code.innerHTML = hl.html;
    else code.textContent = text;
    src.append(code);
    view.append(gutter, src);
    out.push(view);
  }
  if (cut) out.push(el("div", "vnote", `Only the first ${MAX_TEXT / 1024 / 1024} MB is shown here; open it in a new tab for the rest.`));
  body.replaceChildren(...out);
  // A path link's line: scrolled to a third of the way down, and marked.
  const view = body.querySelector(".code");
  if (line && view) {
    const count = view.querySelector(".ln").textContent.split("\n").length;
    const at = Math.min(line, count);
    const src = view.querySelector(".src");
    const lh = parseFloat(getComputedStyle(src).lineHeight);
    const mark = el("div", "mark");
    mark.dataset.line = String(at);
    mark.style.top = `${(at - 1) * lh + src.offsetTop + parseFloat(getComputedStyle(src).paddingTop)}px`;
    mark.style.height = `${lh}px`;
    view.append(mark);
    body.scrollTop += mark.getBoundingClientRect().top - body.getBoundingClientRect().top - body.clientHeight / 3;
  }
}

// An artifact's page at full size, drawn into a bar and a body: its glyph,
// title and status in the bar, Open in a new tab in acts (or the `popout`
// button a pane passes instead), the frame in the body. Who holds the page
// (entries.hold) is the caller's business.
export function drawArtifact(e, { bar, body, acts, popout = null }) {
  if (popout) acts.append(popout);
  else {
    const open = el("a", "btn icon open");
    open.append(icon("open"));
    open.title = "Open in a new tab";
    open.setAttribute("aria-label", open.title);
    open.href = fileUrl(e.detail?.url);
    open.target = "_blank";
    open.rel = "noopener noreferrer";
    acts.append(open);
  }
  bar.append(entryIcon(e.glyph), el("span", "fn", e.title), el("span", "fs", e.summary));
  body.dataset.preview = "html";
  const stage = artifactStage(e);
  stage.querySelector("iframe").loading = "eager";
  body.append(stage);
}

export class Viewer {
  constructor(root, { entry }) {
    this.root = root; // #viewer, inside .col
    this.entry = entry; // (id) -> the shown transcript's entry, or undefined
    this.items = new Map(); // session key -> {files, at, remote, glyph, line?} or {artifact}
    this.key = null; // the session shown
    this.drawn = 0; // bumped by every draw, so a fetch for an older one is dropped
  }

  get isOpen() {
    return this.key !== null && this.items.has(this.key);
  }

  // A tab switch: the new session's viewer, or none.
  show(key) {
    this.key = key;
    this.render();
  }

  open(item) {
    if (this.key === null) return;
    this.release(this.items.get(this.key));
    this.items.set(this.key, { ...item });
    this.render();
    this.root.querySelector(".vbody")?.focus({ preventScroll: true });
  }

  // True when there was one to close (Esc asks).
  close() {
    if (!this.isOpen) return false;
    this.release(this.items.get(this.key));
    this.items.delete(this.key);
    this.render();
    return true;
  }

  release(item) {
    if (item?.artifact) hold(item.artifact, null, this.entry(item.artifact));
  }

  render(e) {
    const item = this.isOpen ? this.items.get(this.key) : null;
    const draw = ++this.drawn;
    this.root.hidden = !item;
    this.root.parentElement.classList.toggle("viewing", !!item);
    if (!item) {
      this.root.replaceChildren();
      return;
    }
    const bar = el("div", "fbar vbar");
    const body = el("div", "vbody");
    body.tabIndex = -1;
    this.root.replaceChildren(bar, body);
    if (item.artifact) this.drawArtifact(item, e || this.entry(item.artifact), bar, body);
    else this.drawFile(item, bar, body, draw);
  }

  closeButton() {
    const b = el("button", "btn icon vclose");
    b.append(icon("close"));
    b.title = "Close (Esc)";
    b.setAttribute("aria-label", "Close the viewer");
    b.addEventListener("click", () => this.close());
    return b;
  }

  drawFile(item, bar, body, draw) {
    const acts = el("span", "acts");
    const f = item.files[item.at];
    if (!item.remote) acts.append(dockButton(f.replaced ? "" : f.key || ""));
    drawFile(f, {
      remote: item.remote,
      glyph: item.glyph,
      bar,
      body,
      acts,
      line: item.line,
      isCurrent: () => draw === this.drawn,
    });
    if (item.files.length > 1) {
      const pager = el("span", "pager");
      const step = (d, name, title) => {
        const b = el("button", "btn icon");
        b.append(icon(name));
        b.title = title;
        b.setAttribute("aria-label", title);
        b.disabled = item.at + d < 0 || item.at + d >= item.files.length;
        b.addEventListener("click", () => {
          item.at += d;
          this.render();
        });
        return b;
      };
      pager.append(step(-1, "left", "Previous file"), el("span", "count", `${item.at + 1} / ${item.files.length}`), step(1, "right", "Next file"));
      bar.append(pager);
    }
    acts.append(this.closeButton());
    bar.append(acts);
  }

  drawArtifact(item, e, bar, body) {
    if (!e) {
      // The transcript is still arriving; drawing its card calls onArtifact.
      bar.append(el("span", "fn", "Loading the page…"), el("span", "acts"));
      bar.lastChild.append(this.closeButton());
      return;
    }
    const acts = el("span", "acts");
    if (e.status === "live") acts.append(dockButton(e.id));
    drawArtifact(e, { bar, body, acts });
    acts.append(this.closeButton());
    bar.append(acts);
    if (e.status === "live") hold(e.id, (next) => this.onArtifact(next));
    else hold(e.id, null, e);
  }

  // The held artifact's entry was drawn again: a new page or a new status
  // redraws the viewer's frame, and an ended page needs holding no more.
  onArtifact(e) {
    const item = this.isOpen ? this.items.get(this.key) : null;
    if (item?.artifact !== e.id) return;
    const frame = this.root.querySelector("iframe[data-artifact]");
    if (frame && frame.dataset.status === e.status && frame.getAttribute("src") === fileUrl(e.detail?.url)) return;
    this.render(e);
  }
}
