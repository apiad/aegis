// The Help tab: the help pages this server ships (src/aegis/help/), listed by
// kind and read with help.read, drawn by the transcript's Markdown renderer in
// the current theme. Which page shows is the URL's: #help is the list (on a
// desktop, beside the first tutorial) and #help=<page>, or <page>#<section>
// encoded, is a page, so Back on a phone returns from a page to the list. The
// server decides what a row says; this module draws and asks.

import { markdown } from "./entries.js";
import { icon } from "./glyphs.js";
import { paint } from "./highlight.js";

// The list's groups, in the spec's order; help.pages sorts concepts first.
const GROUPS = [
  ["tutorial", "Tutorials"],
  ["howto", "How-to"],
  ["concept", "Concepts"],
];

// The rows a search asks for. An agent's default is 5, so the first five are
// the pages an agent gets for the same words.
const LIMIT = 10;

const escRe = (w) => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

// `text` with each of `words` in a <mark>, any case.
function marked(text, words) {
  if (!words.length) return [text];
  const parts = text.split(new RegExp(`(${words.map(escRe).join("|")})`, "gi"));
  return parts.map((p, i) => (i % 2 ? el("mark", null, p) : p));
}

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined && text !== null) n.textContent = text;
  return n;
}

// helppages.slug, so a heading's id names the section a search hit points at.
export const slug = (s) =>
  s
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");

// The ref a link in a page names ("monitors", "monitors.md#start-one", or
// "#start-one" on the page `here`), or null for a link that leaves the help.
export function pageRef(href, ids, here) {
  const m = /^(?:\.\/)?([a-z0-9-]*)(?:\.md)?(?:#([a-z0-9-]+))?$/.exec(href || "");
  if (!m || (!m[1] && !m[2])) return null;
  const page = m[1] || here;
  if (!ids.includes(page)) return null;
  return m[2] ? `${page}#${m[2]}` : page;
}

export class Help {
  // go(hash): navigate as app.js does. onKeys(): open the ? list.
  // onStart(page): start that tutorial. Slice 3 passes it (help.guide); until
  // then a tutorial page carries the button hidden.
  constructor(conn, box, { go, onKeys, onStart = null }) {
    Object.assign(this, { conn, box, go, onKeys, onStart });
    this.pages = null; // help.pages's answer, once it arrives
    this.shown = null; // the page whose Markdown is drawn
    this.readSeq = 0; // the newest help.read; an older answer is dropped
    this.hits = null; // help.search's hits for the box, or null while it is empty
    this.seq = 0; // the newest help.search; an older answer is dropped
    this.timer = null;
    this.drawn = false;
  }

  draw() {
    if (this.drawn) return;
    this.drawn = true;
    this.q = Object.assign(el("input", "jq"), { spellcheck: false, autocomplete: "off", placeholder: "Search help" });
    this.q.setAttribute("aria-label", "Search help");
    this.q.setAttribute("autocapitalize", "off");
    this.q.setAttribute("autocorrect", "off");
    const search = el("label", "jsearch");
    search.append(icon("search"), this.q);
    this.rows = el("div", "hrows");
    const keys = el("button", "hkeys", "Every key");
    keys.type = "button";
    keys.append(el("kbd", null, "?"));
    keys.addEventListener("click", () => this.onKeys());
    const list = el("nav", "hlist");
    list.setAttribute("aria-label", "Help pages");
    list.append(search, this.rows, keys);
    this.back = el("button", "btn hback", "← Help");
    this.back.type = "button";
    this.back.addEventListener("click", () => this.go("#help"));
    this.start = el("button", "btn primary hstart", "Start this tutorial");
    this.start.type = "button";
    this.start.hidden = !this.onStart;
    this.start.addEventListener("click", () => this.onStart(this.shown));
    this.title = el("h2", "htitle");
    this.when = el("div", "hwhen");
    this.body = el("div", "hbody");
    this.page = el("article", "hpage");
    this.page.tabIndex = -1;
    this.page.append(this.back, this.title, this.when, this.body);
    this.box.replaceChildren(list, this.page);
    // A link to another page opens it here; any other keeps its new tab.
    this.body.addEventListener("click", (ev) => {
      const a = ev.target.closest("a[href]");
      const ref = a && pageRef(a.getAttribute("href"), this.pages.map((p) => p.id), this.shown);
      if (!ref) return;
      ev.preventDefault();
      this.go(this.hash(ref));
    });
    this.q.addEventListener("input", () => {
      clearTimeout(this.timer);
      this.timer = setTimeout(() => this.search(), 150);
    });
    // Enter reads the first row: the best hit, or the first tutorial.
    this.q.addEventListener("keydown", async (ev) => {
      if (ev.key !== "Enter" || ev.isComposing) return;
      ev.preventDefault();
      if (this.timer) await this.search();
      this.rows.querySelector(".hrow")?.click();
    });
  }

  hash(ref) {
    return `#help=${encodeURIComponent(ref)}`;
  }

  // What a desktop shows beside the list: the first tutorial.
  first() {
    return (this.pages.find((p) => p.kind === "tutorial") || this.pages[0])?.id || "";
  }

  // ref: "" for the list, else "page" or "page#section".
  async show(ref) {
    this.draw();
    this.box.dataset.screen = ref ? "page" : "list";
    if (!this.pages) {
      try {
        this.pages = await this.conn.call("help.pages");
      } catch (e) {
        this.rows.replaceChildren(el("div", "hnone", `Could not list the help pages: ${e.message}`));
        return;
      }
      this.drawList();
    }
    // A phone's keyboard would cover the list it opens over.
    if (!ref && matchMedia("(pointer: fine)").matches) this.q.focus({ preventScroll: true });
    const [page, section = ""] = (ref || this.first()).split("#");
    if (page) await this.read(page, section);
  }

  drawList() {
    if (!this.pages) return; // a search answered first; show() draws once the pages arrive
    const q = this.q.value.trim();
    const words = q
      .toLowerCase()
      .split(/\s+/)
      .filter((w) => w.length > 1);
    const out = [];
    if (this.hits?.length) {
      for (const h of this.hits) out.push(this.row(h.ref, this.titleOf(h), h.snippet || h.when, words));
    } else {
      // No hit: say so over the whole list, as help_search answers an agent.
      if (this.hits) out.push(el("div", "hnone", `No page matches “${q}”.`));
      for (const [kind, name] of GROUPS) {
        const ps = this.pages.filter((p) => p.kind === kind);
        if (!ps.length) continue;
        out.push(el("div", "hgrp", name));
        for (const p of ps) out.push(this.row(p.id, p.title, p.when));
      }
    }
    this.rows.replaceChildren(...out);
    this.mark();
  }

  // A hit's first line: its page's title, then the section's heading.
  titleOf(h) {
    const [page, section] = h.ref.split("#");
    const title = this.pages.find((p) => p.id === page)?.title || page;
    return section && h.heading ? `${title} › ${h.heading}` : title;
  }

  row(ref, title, sub, words = []) {
    const b = el("button", "hrow");
    b.type = "button";
    b.dataset.ref = ref;
    const t = el("b");
    t.append(...marked(title, words));
    const s = el("span");
    s.append(...marked(sub, words));
    b.append(t, s);
    b.addEventListener("click", () => this.go(this.hash(ref)));
    return b;
  }

  // The box's words through help.search, the op agents call as help_search.
  async search() {
    clearTimeout(this.timer);
    this.timer = null;
    const n = ++this.seq;
    const q = this.q.value.trim();
    if (!q) {
      this.hits = null;
      this.drawList();
      return;
    }
    let res;
    try {
      res = await this.conn.call("help.search", { query: q, limit: LIMIT });
    } catch (e) {
      if (n === this.seq) this.rows.replaceChildren(el("div", "hnone", e.message));
      return;
    }
    if (n !== this.seq) return;
    this.hits = res.hits;
    this.drawList();
  }

  // /help <words>: the box holds them and the list their hits.
  ask(q) {
    this.draw();
    this.q.value = q;
    return this.search();
  }

  // The rows of the page shown, marked.
  mark() {
    for (const b of this.rows.querySelectorAll(".hrow")) b.classList.toggle("on", b.dataset.ref.split("#")[0] === this.shown);
  }

  async read(page, section) {
    const n = ++this.readSeq;
    if (page !== this.shown) {
      let text;
      try {
        text = await this.conn.call("help.read", { page });
      } catch (e) {
        if (n !== this.readSeq) return;
        this.shown = null;
        this.title.textContent = page;
        this.when.textContent = "";
        this.start.remove();
        this.body.replaceChildren(el("div", "hnone", e.message));
        this.mark();
        return;
      }
      if (n !== this.readSeq) return;
      const meta = this.pages.find((p) => p.id === page);
      // The title and when: lines come from help.pages; a frontmatter block
      // in the answer, and the "# title" help.read puts over a whole page,
      // are never body.
      const doc = markdown(text.replace(/^---\n[\s\S]*?\n---\n/, "").replace(/^# .*\n+/, ""));
      doc.classList.add("doc");
      for (const h of doc.querySelectorAll("h2")) h.id = `h-${slug(h.textContent)}`;
      this.title.textContent = meta.title;
      this.when.textContent = `${page} · ${meta.when}`;
      if (meta.kind === "tutorial") this.back.after(this.start);
      else this.start.remove();
      this.body.replaceChildren(doc);
      this.shown = page;
      this.mark();
      await Promise.all([...doc.querySelectorAll(".fence")].map(paint));
      if (n !== this.readSeq) return;
    }
    const head = section ? this.body.querySelector(`#h-${CSS.escape(section)}`) : null;
    if (head) head.scrollIntoView({ block: "start" });
    else this.page.scrollTop = 0;
    if (this.box.dataset.screen === "page") this.page.focus({ preventScroll: true });
  }
}
