// The session switcher (#781): Alt+P, or the tab strip's "N ▾" button, lists
// every open session of this server and of each linked server, one group per
// server in the Fleet's order, quiet sessions in one group last. Typing filters
// with the command menu's scorer; Enter opens through the Fleet card's own path
// (openCard), so a quiet session gets its tab back. It reads only the metas the
// client holds: opening it never waits on the network.

import { fuzzy } from "./commands.js";
import { ago, markNode } from "./fleet.js";
import { LABEL } from "./glyphs.js";

// Attention first, as Alt+J walks: needs_you, error, review, the longest
// waiting first; then working, waiting and the rest, the most recent first.
const RANK = { needs_you: 0, error: 1, review: 2, working: 3, waiting: 4 };
const rankOf = (m) => RANK[m.mark] ?? RANK[m.attention] ?? 5;
function byAttention(a, b) {
  const ra = rankOf(a);
  const d = ra - rankOf(b);
  if (d) return d;
  const ta = a.last_activity || 0;
  const tb = b.last_activity || 0;
  return ra < 3 ? ta - tb : tb - ta;
}

// The second line: what the person most needs to read about it.
export function lineOf(m) {
  if (m.kind === "term") return m.last_line || "";
  if (m.kind === "browser") return m.url || "";
  return m.attention_line || m.plan_now || (m.attention === "working" ? m.activity || "" : "");
}

const fields = (m) => [m.handle || m.name || "", m.title || "", lineOf(m), m.server || ""];

// The best score among a row's fields; positions only when the handle matched,
// since only the handle is drawn with its letters marked.
function match(q, m) {
  if (!q) return { score: 0, positions: [] };
  let best = null;
  fields(m).forEach((f, i) => {
    const x = fuzzy(q, f);
    if (x && (!best || x.score > best.score)) best = { score: x.score, positions: i === 0 ? x.positions : [] };
  });
  return best;
}

// [{name, server, off, quiet, total, needs, rows: [{m, score, positions}]}].
// `here` names this server; `links` is the links channel's list, in its order.
export function switcherRows(metas, here, links, query = "") {
  const q = query.trim();
  const take = (ms) => {
    const rows = ms.flatMap((m) => {
      const x = match(q, m);
      return x ? [{ m, ...x }] : [];
    });
    if (q) rows.sort((a, b) => b.score - a.score);
    return rows;
  };
  const servers = [
    { name: here, server: null, off: false },
    ...links.map((l) => ({ name: l.name, server: l.name, off: l.state !== "linked" })),
  ];
  const at = (m) => servers.findIndex((s) => s.server === (m.server || null));
  const groups = [];
  for (const s of servers) {
    const all = metas.filter((m) => (m.server || null) === s.server && !m.quiet);
    const rows = take(all.slice().sort(byAttention));
    const needs = all.filter((m) => m.mark === "needs_you").length;
    if (rows.length) groups.push({ ...s, quiet: false, total: all.length, needs, rows });
  }
  const quiet = metas.filter((m) => m.quiet);
  const rows = take(quiet.slice().sort((a, b) => at(a) - at(b) || (b.last_activity || 0) - (a.last_activity || 0)));
  if (rows.length) groups.push({ name: "Quiet", server: null, off: false, quiet: true, total: quiet.length, needs: 0, rows });
  return groups;
}

const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
};

// The overlay, on the palette's shape (palette.js). `src` gives the live
// values each time it draws: metas(), here(), links(), current() (the key in
// view) and open(key).
export class Switcher {
  constructor(box, src) {
    Object.assign(this, { box, src });
    this.flat = []; // [{key, node}] in drawn order, headers left out
    this.at = 0;
    this.back = null;
    const panel = el("div", "panel");
    panel.setAttribute("role", "dialog");
    panel.setAttribute("aria-label", "Sessions");
    panel.tabIndex = -1;
    this.q = el("input");
    this.q.id = "switcher-q";
    this.q.spellcheck = false;
    this.q.autocomplete = "off";
    this.q.placeholder = "Switch to a session";
    this.q.setAttribute("role", "combobox");
    this.q.setAttribute("aria-controls", "switcher-rows");
    this.q.setAttribute("aria-expanded", "true");
    this.rows = el("div", "rows");
    this.rows.id = "switcher-rows";
    this.rows.setAttribute("role", "listbox");
    const foot = el("div", "foot");
    for (const t of ["↑↓ Alt+P move", "PgUp PgDn server", "Enter open", "Esc close"]) foot.append(el("span", null, t));
    panel.append(this.q, this.rows, foot);
    box.replaceChildren(panel);
    this.panel = panel;
    this.q.addEventListener("input", () => this.refresh({ reselect: true }));
    // On the panel, not the field: opened from a phone's button the field has
    // no focus, so no on-screen keyboard covers the list.
    panel.addEventListener("keydown", (ev) => this.onKey(ev));
    this.rows.addEventListener("click", (ev) => {
      const r = ev.target.closest("[role=option]");
      if (!r) return;
      this.at = this.flat.findIndex((f) => f.node === r);
      this.accept();
    });
    box.addEventListener("mousedown", (ev) => ev.target === box && this.close());
  }

  get isOpen() {
    return !this.box.hidden;
  }

  // Open again: the next row, as Alt+P does while open.
  open({ focus = true } = {}) {
    if (this.isOpen) return this.move(1);
    this.back = document.activeElement;
    this.q.value = "";
    this.box.hidden = false;
    this.refresh({ reselect: true });
    (focus ? this.q : this.panel).focus({ preventScroll: true });
  }

  close() {
    if (!this.isOpen) return;
    this.box.hidden = true;
    if (this.back?.isConnected) this.back.focus({ preventScroll: true });
    this.back = null;
  }

  // On every sessions patch while open, and on each keystroke. The selection
  // follows its session by key; `reselect` puts it on the best row instead:
  // the best score under a query, else the first row not in view.
  refresh({ reselect = false } = {}) {
    if (!this.isOpen) return;
    const keep = this.flat[this.at]?.key;
    const query = this.q.value.trim();
    const groups = switcherRows(this.src.metas(), this.src.here(), this.src.links(), query);
    const out = [];
    this.flat = [];
    let best = 0;
    let top = -Infinity;
    for (const g of groups) {
      out.push(this.header(g));
      for (const r of g.rows) {
        if (r.score > top) [top, best] = [r.score, this.flat.length];
        const node = this.row(r, g);
        this.flat.push({ key: r.m.key, node });
        out.push(node);
      }
    }
    if (!this.flat.length) out.push(el("div", "empty", "No session matches"));
    this.rows.replaceChildren(...out);
    const kept = this.flat.findIndex((f) => f.key === keep);
    if (!reselect && kept >= 0) this.at = kept;
    else if (query) this.at = best;
    else this.at = Math.max(0, this.flat.findIndex((f) => f.key !== this.src.current()));
    this.mark();
  }

  header(g) {
    const h = el("div", "sw-gh");
    h.append(el("span", "nm", g.name), `${g.total} session${g.total === 1 ? "" : "s"}`);
    if (g.needs) h.append(` · ${g.needs} need${g.needs === 1 ? "s" : ""} you`);
    if (g.off) h.append(el("span", "off", "offline"));
    return h;
  }

  row({ m, positions }, g) {
    const r = el("div", `sw-row${m.quiet || g.off || m.off ? " dim" : ""}`);
    r.setAttribute("role", "option");
    r.dataset.id = m.key;
    const h = el("span", "h");
    const hit = new Set(positions);
    [...(m.handle || m.name || "")].forEach((ch, j) => h.append(hit.has(j) ? el("b", null, ch) : ch));
    if (g.quiet && m.server) h.append(el("span", "at", m.server));
    const st = el("span", "st");
    if (m.key === this.src.current()) st.append(el("span", "here", "here"));
    const label = m.attention === "waiting" && m.waiting_on ? `waiting · ${m.waiting_on}` : LABEL[m.attention] || m.state || "";
    st.append([label, ago(m.last_activity)].filter(Boolean).join(" · "));
    const mk = el("span", "mk");
    mk.append(markNode(m));
    r.append(mk, h, el("span", "t", m.title || ""), st, el("span", `ln at-${m.attention}`, lineOf(m)));
    return r;
  }

  mark() {
    this.flat.forEach((f, i) => {
      f.node.classList.toggle("on", i === this.at);
      f.node.id = i === this.at ? "switcher-on" : "";
      f.node.setAttribute("aria-selected", String(i === this.at));
    });
    this.q.setAttribute("aria-activedescendant", "switcher-on");
    this.flat[this.at]?.node.scrollIntoView({ block: "nearest" });
  }

  move(d) {
    if (!this.flat.length) return;
    this.at = (this.at + d + this.flat.length) % this.flat.length;
    this.mark();
  }

  // Page Down and Up: the first row of the next or previous group.
  jump(d) {
    const firsts = [...this.rows.querySelectorAll(".sw-gh + .sw-row")].map((n) => this.flat.findIndex((f) => f.node === n));
    if (!firsts.length) return;
    const now = firsts.filter((i) => i <= this.at).length - 1;
    this.at = firsts[(now + d + firsts.length) % firsts.length];
    this.mark();
  }

  accept() {
    const f = this.flat[this.at];
    if (!f) return;
    this.close();
    this.src.open(f.key);
  }

  // Every key it answers is prevented, so the listener in keys.js, which skips
  // a prevented event, never also runs it (the palette's rule).
  onKey(ev) {
    if (ev.isComposing) return;
    const step = { ArrowDown: 1, ArrowUp: -1, Tab: ev.shiftKey ? -1 : 1 }[ev.key];
    if (ev.altKey && ev.code === "KeyP") this.move(ev.shiftKey ? -1 : 1);
    else if (step && !ev.altKey) this.move(step);
    else if (ev.key === "PageDown" || ev.key === "PageUp") this.jump(ev.key === "PageDown" ? 1 : -1);
    else if (ev.key === "Enter") this.accept();
    else if (ev.key === "Escape") this.close();
    else return;
    ev.preventDefault();
    ev.stopPropagation();
  }
}
