// The Fleet view: a card per open session, then the archive.
// Every field comes from the session's meta; nothing here computes activity.

import { dotClass } from "./tabs.js";

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined && text !== null) n.textContent = text;
  return n;
}

export function ago(ts) {
  if (!ts) return "";
  const s = Math.max(0, Math.round(Date.now() / 1000 - ts));
  if (s < 10) return "just now";
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ${m % 60}m ago`;
  return `${Math.floor(h / 24)}d ago`;
}

function cwdTail(cwd) {
  return (cwd || "").split("/").filter(Boolean).slice(-2).join("/");
}

export function money(usd) {
  return `$${(usd || 0).toFixed(2)}`;
}

export function renderCards(box, metas, onOpen) {
  if (!metas.length) {
    box.replaceChildren(el("div", "empty", "No open sessions. Start one with +."));
    return;
  }
  box.replaceChildren(
    ...metas.map((m) => {
      const c = el("div", `card ${m.state}`);
      c.dataset.id = m.log_id;
      const hd = el("div", "hd");
      hd.append(el("span", `dot ${dotClass(m.state)}`), el("span", "h", m.handle));
      if (m.worker) hd.append(el("span", "badge", `worker · ${m.worker.queue}`));
      hd.append(el("span", "s", m.state));
      const ttl = el("div", "ttl", m.title || "untitled");
      const sub = el("div", "ln");
      sub.append(el("b", null, `${m.agent}${(m.overridden || []).length ? "*" : ""}`), document.createTextNode(`${m.model} · ${cwdTail(m.cwd)}`));
      const act = el("div", "act", m.activity || "");
      const pct = m.context_window && m.context_tokens ? Math.min(100, Math.round((100 * m.context_tokens) / m.context_window)) : 0;
      const bar = el("div", "bar thin");
      const fill = el("i");
      fill.style.width = `${pct}%`;
      bar.append(fill);
      const ft = el("div", "ft");
      ft.append(el("span", "when", ago(m.last_activity)), el("span", null, money(m.cost_usd)));
      const mons = (m.monitors || []).length;
      if (mons) ft.append(el("span", "mons", `${mons} monitor${mons > 1 ? "s" : ""}`));
      ft.append(el("span", "ctx", `${pct}%`));
      c.append(hd, ttl, sub, act, bar, ft);
      c.addEventListener("click", () => onOpen(m.log_id));
      return c;
    }),
  );
}

export function renderArchive(box, items, { onReopen, onRead }) {
  if (!items.length) {
    box.replaceChildren(el("div", "empty", "Nothing archived matches."));
    return;
  }
  const t = el("table", "tbl");
  const head = el("tr");
  for (const h of ["Title", "Handle", "Where", "Last active", "Cost", ""]) head.append(el("th", null, h));
  t.append(head);
  for (const m of items) {
    const tr = el("tr");
    tr.dataset.id = m.log_id;
    const actions = el("td", "acts");
    const read = el("button", "btn", "Read");
    read.addEventListener("click", () => onRead(m.log_id));
    const re = el("button", "btn primary", "Reopen");
    re.addEventListener("click", () => onReopen(m.log_id));
    actions.append(read, re);
    tr.append(
      el("td", null, m.title || "untitled"),
      el("td", "m", m.handle),
      el("td", "m", cwdTail(m.cwd)),
      el("td", "m", ago(m.last_activity)),
      el("td", "m", money(m.cost_usd)),
      actions,
    );
    t.append(tr);
  }
  box.replaceChildren(t);
}
