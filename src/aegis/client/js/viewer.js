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
// A workflow's card is drawn whole and redraws itself from the workflows
// channel (entries.js fillWorkflow), as it does in a canvas pane.
//
// An artifact's page runs in one place only. While the viewer shows a live
// one, its card holds a line instead of a frame (entries.hold), and the
// agent's state reaches the viewer's frame instead.

import { annotate } from "./annotations.js";
import { Tray } from "./comments.js";
import { artifactStage, dockButton, fileActions, fileUrl, fillWorkflow, hold, markdown, phoneButton, popButton, preview, swipe } from "./entries.js";
import { entryIcon, icon } from "./glyphs.js";
import { colour, langOfPath, paint } from "./highlight.js";
import { filesHash } from "./files.js";
import * as menu from "./menu.js";
import { docLocator, headingOf, linesOf, offsetsIn, rangeAtPoint, rangeText, textLocator } from "./quote.js";

// A peeked file's url -> its path under the root, from file.peek's answer
// (app.js). A tool row's card is drawn from the stored peek record, which
// keeps its shape, so this is how its viewer knows the file opens in Files;
// a card from before a reload shows no button until it is peeked again.
export const peekRoots = new Map();

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
export function drawFile(f, { remote, glyph, bar, body, acts, line, isCurrent = () => true, drawn = null }) {
  const { acts: own, set } = fileActions(remote);
  set(f);
  acts.append(...own.children);
  bar.append(entryIcon(glyph), el("span", "fn", f.name), el("span", "fs", f.summary));
  body.dataset.preview = f.preview || "other";
  if (f.preview === "markdown" || f.preview === "text") {
    body.append(el("div", "vnote", "loading…"));
    drawText(f, body, line, isCurrent, drawn);
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

// `drawn(content)` gets the code block or the Markdown once they are in.
async function drawText(f, body, line, isCurrent, drawn) {
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
  drawn?.(body.querySelector(".src > code, .doc"));
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
// title and status in the bar, Phone width and Open in a new tab in acts (or the `popout`
// button a live page's pane or the viewer passes instead), the frame in the body. Who holds the page
// (entries.hold) is the caller's business.
export function drawArtifact(e, { bar, body, acts, popout = null }) {
  acts.append(phoneButton(e.id));
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

// What a file's text says about a range in it (spec 07): the locator and
// the quote. `content` is the code block or the rendered Markdown.
export function pointAt(path, content, range) {
  const text = rangeText(range);
  if (content.matches("code")) {
    const [first, last] = linesOf(content, range);
    return { locator: textLocator(path, first, last), text };
  }
  return { locator: docLocator(path, headingOf(range.startContainer, content)), text };
}

// A selection the viewer's bar is about: one inside a file's code or its
// rendered Markdown, start and end.
export function contentUnit(range, root) {
  const n = range.startContainer;
  const content = (n.nodeType === 1 ? n : n.parentElement)?.closest(".src > code, .doc");
  return content && root.contains(content) && content.contains(range.endContainer) ? { range, row: content } : null;
}

export class Viewer {
  // `point` is app.js's: items({locator, text, to, comment, leave}) gives a
  // selection's items, send(key, text) writes a session's composer.
  constructor(root, { entry, point }) {
    this.root = root; // #viewer, inside .col
    this.entry = entry; // (id) -> the shown transcript's entry, or undefined
    this.point = point;
    this.items = new Map(); // session key -> {files, at, remote, glyph, line?}, {artifact} or {workflow}
    this.key = null; // the session shown
    this.drawn = 0; // bumped by every draw, so a fetch for an older one is dropped
    this.tray = null; // the shown file's comments
    this.annotation = null; // the shown image or PDF's drawing layer
    menu.bar(root, (range, content) => this.pointItems(range, content), { unit: contentUnit });
    menu.attach(root, (t, ev) => this.pressed(t, ev));
  }

  // The shown file's path for a locator: under the root when a peek said so,
  // else its name (a sent file's entry keeps no source path).
  pathOf() {
    const item = this.isOpen ? this.items.get(this.key) : null;
    const f = item?.files?.[item.at];
    return f && (f.root_path || peekRoots.get(f.url) || f.name);
  }

  pointItems(range, content) {
    const path = this.pathOf();
    if (!path || !this.point) return null;
    const { locator, text } = pointAt(path, content, range);
    if (!text) return null;
    const key = this.key;
    const at = offsetsIn(content, range);
    return this.point.items({
      locator,
      text,
      to: key,
      leave: () => this.close(),
      comment: () => this.tray?.add({ locator, quote: text, at }),
    });
  }

  // A right-click or a long press in a file's text: inside the selection, the
  // bar's items; elsewhere, the line or the block under it. The keyboard's
  // menu has no point, and nothing to say here.
  pressed(t, ev) {
    const content = t.closest?.(".src > code, .doc");
    if (!ev || !content || !this.root.contains(content)) return null;
    const sel = getSelection();
    const cur = sel.rangeCount && !sel.isCollapsed ? sel.getRangeAt(0) : null;
    const hit = (r) => ev.clientX >= r.left && ev.clientX <= r.right && ev.clientY >= r.top && ev.clientY <= r.bottom;
    const inSel = cur && contentUnit(cur, this.root)?.row === content && [...cur.getClientRects()].some(hit);
    const range = inSel ? cur : rangeAtPoint(content, ev.clientX, ev.clientY);
    const items = range && this.pointItems(range, content);
    return items?.length ? { groups: [items] } : null;
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
    this.annotation?.destroy();
    this.annotation = null;
    const item = this.isOpen ? this.items.get(this.key) : null;
    const draw = ++this.drawn;
    // A redraw of the same item keeps its tray (newTray), and the note being
    // typed in it, caught before the redraw takes the focus away.
    this.kept = this.tray;
    const typing = this.tray?.node.contains(document.activeElement) ? document.activeElement : null;
    this.typing = typing && { el: typing, caret: [typing.selectionStart, typing.selectionEnd] };
    this.tray = null;
    this.root.hidden = !item;
    this.root.parentElement.classList.toggle("viewing", !!item);
    if (!item) {
      this.root.replaceChildren();
      menu.dropDetached();
      return;
    }
    const bar = el("div", "fbar vbar");
    const body = el("div", "vbody");
    body.tabIndex = -1;
    this.root.replaceChildren(bar, body);
    menu.dropDetached(); // a page's menu or bar over the frame just replaced
    if (item.artifact) this.drawArtifact(item, e || this.entry(item.artifact), bar, body);
    else if (item.workflow) this.drawWorkflow(item, bar, body);
    else this.drawFile(item, bar, body, draw);
  }

  // The comment tray, below the body; Send closes the viewer over the composer.
  // A redraw of the same item (an artifact's status) keeps the tray it had,
  // with the note being typed and its caret (render caught them).
  newTray(key, path) {
    if (!this.point) return null;
    const kept = this.kept;
    this.kept = null;
    const typing = this.typing;
    this.typing = null;
    if (kept && kept.store === `aegis.comments.${key}` && kept.to === this.key) {
      this.root.append(kept.node);
      if (typing && kept.node.contains(typing.el)) {
        typing.el.focus({ preventScroll: true });
        typing.el.setSelectionRange(...typing.caret);
      }
      this.tray = kept;
      return kept;
    }
    const to = this.key;
    this.tray = new Tray(this.root, key, (text) => (this.close(), this.point.send(to, text)), path);
    this.tray.to = to;
    return this.tray;
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
    // A path under the root (file.peek's answer says so) opens in the Files
    // view, at the line the reply named.
    const rootPath = f.root_path || peekRoots.get(f.url);
    if (rootPath && !item.remote) {
      const files = el("a", "btn openfiles", "Open in Files");
      files.href = filesHash(rootPath, item.line);
      files.title = `Open ${rootPath} in the Files view, where it can be edited`;
      acts.append(files);
    }
    // Comments and visual annotations, kept per version (the file's URL).
    const tray = ["markdown", "text", "html", "image", "pdf"].includes(f.preview) ? this.newTray(f.url, this.pathOf()) : null;
    drawFile(f, {
      remote: item.remote,
      glyph: item.glyph,
      bar,
      body,
      acts,
      line: item.line,
      isCurrent: () => draw === this.drawn,
      drawn: (content) => tray?.layer(content),
    });
    if (["image", "pdf"].includes(f.preview)) {
      this.annotation = annotate(body.querySelector(".stage"), tray, {
        path: this.pathOf(),
        kind: f.preview,
      });
    }
    if (item.files.length > 1) {
      // Round from the last file to the first, as on the card.
      const go = (d) => {
        item.at = (item.at + d + item.files.length) % item.files.length;
        this.render();
      };
      const pager = el("span", "pager");
      const step = (d, name, title) => {
        const b = el("button", "btn icon");
        b.append(icon(name));
        b.title = title;
        b.setAttribute("aria-label", title);
        b.addEventListener("click", () => go(d));
        return b;
      };
      pager.append(step(-1, "left", "Previous file"), el("span", "count", `${item.at + 1} / ${item.files.length}`), step(1, "right", "Next file"));
      bar.append(pager);
      swipe(body, go);
    }
    acts.append(this.closeButton());
    bar.append(acts);
  }

  // A run's card at full size: its title goes into the bar before Dock and
  // Close (fillWorkflow, which keeps the bar's .acts).
  drawWorkflow(item, bar, body) {
    const acts = el("span", "acts");
    acts.append(dockButton(`wf-${item.workflow}`), this.closeButton());
    bar.append(acts);
    const card = el("div", "fcard wfcard");
    card.dataset.run = item.workflow;
    body.append(card);
    fillWorkflow(card);
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
    // A live page pops out into a window of its own, with the bridge behind
    // it; only an ended one opens raw in a new tab, as its card does.
    drawArtifact(e, { bar, body, acts, popout: e.status === "live" ? popButton(e.id) : null });
    acts.append(this.closeButton());
    bar.append(acts);
    // By the artifact's id: a page the agent sends again is a new file, and
    // the comments on it so far stay.
    this.newTray(`artifact:${e.id}`, e.id);
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
