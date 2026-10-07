// The Fleet view: a card per open session, then the archive.
// Every field comes from the session's meta; nothing here computes activity.

import { dotClass } from "./tabs.js";
import { hostRow, noteRow, providerLine, quotaHeading, quotaRow } from "./gauges.js";

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

const STATE_ORDER = ["working", "idle", "error", "stopped"];

// The band over the cards: this server's sessions by state and its host.
// Counts and the average context come from the sessions the client already
// holds; the host meters from the host channel.
export function renderBand(band, { metas, host, server }) {
  band.querySelector("#band-server").textContent = server || "server";
  const counts = new Map();
  for (const m of metas) counts.set(m.state, (counts.get(m.state) || 0) + 1);
  const rows = STATE_ORDER.filter((s) => counts.get(s)).map((s) => {
    const d = el("div");
    d.append(el("span", `dot ${dotClass(s)}`), el("b", null, String(counts.get(s))), document.createTextNode(s));
    return d;
  });
  band.querySelector("#band-counts").replaceChildren(...(rows.length ? rows : [el("div", "empty", "no sessions")]));

  const meters = [];
  if (host) {
    meters.push(hostRow("CPU", host.cpu, ""));
    meters.push(hostRow("RAM", host.ram.pct, `${host.ram.used_gb} / ${host.ram.total_gb} GB`));
    if (host.disk) meters.push(hostRow("Disk", host.disk.pct, `${host.disk.used_gb} / ${host.disk.total_gb} GB`));
  }
  const live = metas.filter((m) => m.state !== "stopped" && m.context_window && m.context_tokens);
  if (live.length) {
    const avg = Math.round(live.reduce((a, m) => a + Math.min(100, (100 * m.context_tokens) / m.context_window), 0) / live.length);
    meters.push(hostRow("Context", avg, `avg of ${live.length} live`));
  }
  band.querySelector("#band-host").replaceChildren(...meters);
}

// The quota column, apart from the rest of the band: sessions patch up to four
// times a second per working agent and quota about once a minute, and a
// rebuilt row loses the hover tooltip that spells its reading out.
export function renderBandQuota(band, { quota, now }) {
  const providers = (quota && quota.providers) || [];
  band.querySelector("#band-quota-col").hidden = !providers.length;
  band.querySelector("#band-quota-age").textContent = quotaHeading(providers, now);
  const out = [];
  for (const p of providers) {
    out.push(el("div", "prov", providerLine(p, now)));
    if (p.state === "failed") out.push(noteRow(p.note));
    else for (const w of p.windows) out.push(quotaRow(p, w, now));
  }
  band.querySelector("#band-quota").replaceChildren(...out);
}
