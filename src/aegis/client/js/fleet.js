// The Fleet view: a card per open session, then the archive.
// Every field comes from the session's meta; nothing here computes activity.

import { glyph, LABEL } from "./glyphs.js";
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

// The cards a person must act on, shown first when the Fleet is ordered by need.
const NEEDS = new Set(["needs_you", "error", "review"]);
const group = (m) => (NEEDS.has(m.attention) ? "needs" : "rest");

export function renderCards(box, metas, onOpen, order = "attention") {
  if (!metas.length) {
    box.replaceChildren(el("div", "empty", "No open sessions. Start one with +."));
    return;
  }
  if (order !== "attention") {
    box.replaceChildren(...metas.map((m) => card(m, onOpen)));
    return;
  }
  const out = [];
  const needs = metas.filter((m) => group(m) === "needs");
  const rest = metas.filter((m) => group(m) === "rest");
  if (needs.length) out.push(el("div", "grp-h", "Needs you"), ...needs.map((m) => card(m, onOpen)));
  if (rest.length) out.push(el("div", "grp-h", needs.length ? "Everything else" : "Sessions"), ...rest.map((m) => card(m, onOpen)));
  box.replaceChildren(...out);
}

// One session's card redrawn where it stands. False when its group changed and
// the caller must regroup with renderCards.
export function patchCard(box, m, onOpen, order = "attention") {
  const old = box.querySelector(`.card[data-id="${CSS.escape(m.log_id)}"]`);
  if (!old) return true;
  if (order === "attention" && old.dataset.group !== group(m)) return false;
  old.replaceWith(card(m, onOpen));
  return true;
}

function card(m, onOpen) {
  const c = el("div", `card ${m.state} at-${m.attention}`);
  c.dataset.id = m.log_id;
  c.dataset.group = group(m);
  const hd = el("div", "hd");
  hd.append(glyph(m.attention), el("span", "h", m.handle));
  if (m.worker) hd.append(el("span", "badge", `worker · ${m.worker.queue}`));
  const label = m.attention === "waiting" && m.waiting_on ? `waiting · ${m.waiting_on}` : LABEL[m.attention] || m.state;
  hd.append(el("span", `s at-${m.attention}`, label));
  const parts = [hd, el("div", "ttl", m.title || "untitled")];
  if (m.attention_line) parts.push(el("div", `ask at-${m.attention}`, m.attention_line));
  const sub = el("div", "ln");
  sub.append(el("b", null, `${m.agent}${(m.overridden || []).length ? "*" : ""}`), document.createTextNode(`${m.model} · ${cwdTail(m.cwd)}`));
  parts.push(sub);
  if (m.plan_now || m.plan_did) {
    const pl = el("div", "pl");
    if (m.plan_now) pl.append(planRow("now", m.plan_now, "now"));
    if (m.plan_did) pl.append(planRow("did", m.plan_did, ""));
    parts.push(pl);
  }
  if (m.attention === "working") parts.push(el("div", "act", m.activity || ""));
  const pct = m.context_window && m.context_tokens ? Math.min(100, Math.round((100 * m.context_tokens) / m.context_window)) : 0;
  const bar = el("div", "bar thin");
  const fill = el("i");
  fill.style.width = `${pct}%`;
  bar.append(fill);
  const ft = el("div", "ft");
  ft.append(el("span", "when", ago(m.last_activity)), el("span", null, money(m.cost_usd)));
  const mons = (m.monitors || []).length;
  if (mons) ft.append(el("span", "mons", `${mons} monitor${mons > 1 ? "s" : ""}`));
  if (m.plan_total) ft.append(el("span", "prog", `plan ${m.plan_done}/${m.plan_total}`));
  ft.append(el("span", "ctx", `${pct}%`));
  parts.push(bar, ft);
  c.append(...parts);
  c.addEventListener("click", () => onOpen(m.log_id));
  return c;
}

function planRow(key, text, cls) {
  const d = el("div", cls);
  d.append(el("span", "k", key), el("span", null, text));
  return d;
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

const ATTENTION_ORDER = ["needs_you", "error", "review", "working", "waiting", "done"];

// The band over the cards: this server's sessions by attention and its host.
// Counts and the average context come from the sessions the client already
// holds; the host meters from the host channel.
export function renderBand(band, { metas, host, server }) {
  band.querySelector("#band-server").textContent = server || "server";
  const counts = new Map();
  for (const m of metas) counts.set(m.attention, (counts.get(m.attention) || 0) + 1);
  const rows = ATTENTION_ORDER.filter((a) => counts.get(a)).map((a) => {
    const d = el("div");
    d.append(glyph(a), el("b", null, String(counts.get(a))), document.createTextNode(a === "needs_you" ? "need you" : LABEL[a]));
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
