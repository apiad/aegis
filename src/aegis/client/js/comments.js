// The comment tray (#479, spec 07): comments made while reading a file or a
// page, sent as one message. A tray sits at the bottom of the viewer or the
// Files pane, "3 comments · Send · Discard", with one row per comment whose
// note can be edited or the comment removed.
//
// Until Send or Discard the comments live in this browser's storage, under
// the key the host gives (a sent file's URL, unique per version; an
// artifact's id; a Files path), so a reload keeps them and nothing else sees
// them. Send
// hands the host one message and forgets them, pins included.
//
// A comment's `at` is the host's: text offsets inside the content element
// (quote.js offsetsIn) for a file, or the page's locator for a frame. A pin
// is drawn only for offsets, as a numbered span inserted at the start of its
// range; the number is CSS (::before), so the content's text and offsets
// stay as they were.

import { quoted, rangeIn } from "./quote.js";

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
}

// The one message: a header naming the file, then each comment's locator,
// quote and note, numbered in the order they were made.
export function message(path, items) {
  const visual = items.some((c) => c.annotation);
  const parts = [`${visual ? "Annotations" : "Comments"} on ${path}:`];
  items.forEach((c, i) => {
    if (c.annotation) parts.push(`${i + 1}. ${JSON.stringify(c.annotation)}`);
    else parts.push(`${i + 1}. ${c.locator}`);
    if (c.quote) parts.push(quoted(c.quote).replace(/^/gm, "   "));
    if (c.note.trim()) parts.push(c.note.trim().replace(/^/gm, "   "));
  });
  return parts.join("\n");
}

export class Tray {
  // `send(message)` takes the message, and answers false (or a promise of
  // false) when it did not land; `path` heads it.
  constructor(host, key, send, path) {
    this.store = `aegis.comments.${key}`;
    this.send = send;
    this.path = path;
    this.content = null; // where offsets point, once the host has drawn it
    try {
      this.items = JSON.parse(localStorage.getItem(this.store)) || [];
    } catch {
      this.items = [];
    }
    this.node = el("div", "ctray");
    host.append(this.node);
    this.draw();
  }

  // The host drew its content: pins go on it.
  layer(content) {
    this.content = content;
    this.pins();
  }

  add({ locator, quote, at, annotation = null }) {
    this.items.push({ locator, quote, note: "", at, ...(annotation ? { annotation } : {}) });
    this.save();
    this.draw();
    this.pins();
    this.node.querySelector("li:last-child input")?.focus();
  }

  clear() {
    this.items = [];
    localStorage.removeItem(this.store);
    this.draw();
    this.pins();
  }

  save() {
    if (this.items.length) localStorage.setItem(this.store, JSON.stringify(this.items));
    else localStorage.removeItem(this.store);
  }

  draw() {
    const n = this.items.length;
    this.node.hidden = !n;
    if (!n) return this.node.replaceChildren();
    const head = el("div", "chead");
    const send = el("button", "btn primary", "Send");
    send.type = "button";
    send.dataset.act = "send";
    // Forgotten only once the message landed: Files may ask to leave unsaved
    // edits first, and Cancel keeps both.
    send.addEventListener("click", async () => {
      if ((await this.send(message(this.path, this.items))) !== false) this.clear();
    });
    const discard = el("button", "btn", "Discard");
    discard.type = "button";
    discard.dataset.act = "discard";
    discard.addEventListener("click", () => this.clear());
    const annotations = this.items.every((c) => c.annotation);
    const count = annotations ? (n === 1 ? "1 annotation" : `${n} annotations`) : n === 1 ? "1 comment" : `${n} comments`;
    head.append(el("span", "cn", count), send, discard);
    const list = el("ol", "clist");
    this.items.forEach((c, i) => {
      const li = el("li");
      const note = el("input", "note");
      note.type = "text";
      note.placeholder = "Note";
      note.value = c.note;
      note.setAttribute("aria-label", `Note ${i + 1}`);
      note.addEventListener("input", () => {
        c.note = note.value;
        this.save();
      });
      // Enter is done with the note; it never sends.
      note.addEventListener("keydown", (ev) => ev.key === "Enter" && (ev.preventDefault(), note.blur()));
      const rm = el("button", "btn icon rm", "×");
      rm.type = "button";
      rm.title = "Remove this comment";
      rm.setAttribute("aria-label", rm.title);
      rm.addEventListener("click", () => {
        this.items.splice(i, 1);
        this.save();
        this.draw();
        this.pins();
      });
      li.append(el("span", "n", String(i + 1)), el("span", "loc", c.locator), note, rm);
      list.append(li);
    });
    this.node.replaceChildren(head, list);
  }

  // Each comment's number on its place in the content, drawn again whole.
  pins() {
    for (const p of document.querySelectorAll(".pin[data-tray]")) if (p.dataset.tray === this.store) p.remove();
    const root = this.content;
    if (!root?.isConnected) return;
    this.items.forEach((c, i) => {
      if (c.at && !Array.isArray(c.at)) {
        const pin = el("span", `pin vpin ${c.at.type || "region"}`);
        pin.dataset.n = String(i + 1);
        pin.dataset.tray = this.store;
        pin.dataset.chrome = "";
        pin.style.left = `${c.at.x * 100}%`;
        pin.style.top = `${c.at.y * 100}%`;
        pin.style.width = `${c.at.width * 100}%`;
        pin.style.height = `${c.at.height * 100}%`;
        root.append(pin);
        return;
      }
      if (!Array.isArray(c.at)) return;
      const r = rangeIn(root, c.at);
      if (!r) return;
      const pin = el("span", "pin");
      pin.dataset.n = String(i + 1);
      pin.dataset.tray = this.store;
      pin.dataset.chrome = "";
      r.collapse(true);
      r.insertNode(pin);
    });
  }
}
