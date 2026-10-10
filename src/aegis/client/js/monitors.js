// Monitors in the session sidebar: a row each, and a card on hover with what
// the server knows about one (#174). Verdicts, the ETA and its basis arrive
// decided (aegis/monitors.py); this file draws them, plus what moves with the
// clock: the countdown, "late", and how long ago a monitor started.
//
// The sidebar redraws on every session patch, many a second while an agent
// works. A row is replaced only when its monitor's data changed, and the card
// only then too, so the pointer keeps its hover, a selection in a command
// survives and a clicked-open command stays open.

import { age } from "./gauges.js";

const CARD_W = 392;
const OPEN_AFTER_MS = 220;
const narrow = matchMedia("(max-width: 760px)");
// Hover opens and closes the card for a mouse only. A tap fires a leave right
// after its click, which would close the card it just opened.
const byMouse = (fn) => (ev) => ev.pointerType === "mouse" && fn();
const CLOSE_AFTER_MS = 160;
const nowS = () => Date.now() / 1000;

function h(tag, cls, ...kids) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  for (const k of kids) if (k != null && k !== false) n.append(k);
  return n;
}

function clock(ts, seconds = true) {
  return new Date(ts * 1000).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    ...(seconds ? { second: "2-digit" } : {}),
  });
}

// "9m 12s", as the server's own _elapsed.
function dur(seconds) {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${s % 60}s`;
  return `${Math.floor(m / 60)}h ${m % 60}m`;
}

const late = (m) => m.eta_at != null && nowS() > m.eta_at;
const measured = (m) => m.progress != null && !m.broken;
const hasProgressCmd = (m) => !!m.checks.find((c) => c.kind === "progress")?.cmd;
// A monitor on sessions counts them instead of a percent: "1 of 2".
const tally = (m) => `${m.sessions.filter((r) => r.state === "finished").length} of ${m.sessions.length}`;

// -- the row ------------------------------------------------------------------
function rowRight(m, span) {
  const tail = (t) => h("span", "eta", ` · ${t}`);
  if (m.sessions) span.replaceChildren(tally(m), tail(age(nowS() - m.started_at)));
  else if (m.broken) span.replaceChildren("check fails");
  else if (!m.progress) span.replaceChildren("watching", tail(age(nowS() - m.started_at)));
  else if (late(m)) span.replaceChildren(`${m.progress}%`, tail(`${age(nowS() - m.eta_at)} late`));
  else if (m.eta_at != null) span.replaceChildren(`${m.progress}%`, tail(`~${age(m.eta_at - nowS())}`));
  else span.replaceChildren(`${m.progress}%`);
}

function row(m) {
  const box = h("div", "mon");
  box.dataset.id = m.id;
  box.tabIndex = 0;
  box.classList.toggle("bad", m.broken);
  box.classList.toggle("late", late(m));
  const right = h("span", "r");
  rowRight(m, right);
  const kv = h("div", "kv", h("span", null, m.description), right);
  // No command, no reading yet, or a reading of 0: the monitor is running and
  // how far it got is unknown, so the bar moves instead of sitting empty.
  const bar = h("div", m.progress || m.broken ? "bar thin" : "bar thin indet");
  const fill = h("i");
  if (m.broken) fill.style.width = "100%";
  else if (m.progress) fill.style.width = `${m.progress}%`;
  bar.append(fill);
  box.append(kv, bar);
  box.tick = () => {
    rowRight(m, right);
    box.classList.toggle("late", late(m));
  };
  box.addEventListener("pointerenter", byMouse(() => {
    clearTimeout(closeT);
    clearTimeout(openT);
    openT = setTimeout(() => open(box.dataset.id), OPEN_AFTER_MS);
  }));
  box.addEventListener("pointerleave", byMouse(soonClose));
  box.addEventListener("focus", () => box.matches(":focus-visible") && open(m.id));
  box.addEventListener("click", () => open(m.id));
  return box;
}

// -- the chart: readings as steps, a dashed line from the last to 100 at the ETA
const SVG = "http://www.w3.org/2000/svg";
function chart(m) {
  const W = 360, H = 78, top = 6, bot = H - 2;
  const now = nowS();
  const end = Math.max(now, m.eta_at || now);
  const t1 = end + (end - m.started_at) * 0.06;
  const x = (t) => (((t - m.started_at) / (t1 - m.started_at)) * W).toFixed(1);
  const y = (v) => (bot - (v / 100) * (bot - top)).toFixed(1);
  const rs = m.readings;
  const last = rs[rs.length - 1][1];
  let d = "";
  rs.forEach(([t, v], i) => {
    if (i) d += `L${x(t)},${y(rs[i - 1][1])}`;
    d += `${i ? "L" : "M"}${x(t)},${y(v)}`;
  });
  d += `L${x(now)},${y(last)}`;
  const svg = document.createElementNS(SVG, "svg");
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.setAttribute("preserveAspectRatio", "none");
  const mark = (tag, cls, attrs) => {
    const n = document.createElementNS(SVG, tag);
    n.setAttribute("class", cls);
    for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
    svg.append(n);
    return n;
  };
  for (const [v, op] of [[100, 1], [50, 0.5], [0, 1]]) mark("line", "grid", { x1: 0, x2: W, y1: y(v), y2: y(v), opacity: op });
  mark("path", "area", { d: `${d}L${x(now)},${bot}L0,${bot}Z` });
  mark("path", "line", { d });
  if (m.eta_at != null && !late(m)) mark("path", "proj", { d: `M${x(now)},${y(last)}L${x(m.eta_at)},${y(100)}` });
  mark("line", "nowl", { x1: x(now), x2: x(now), y1: top - 4, y2: bot });
  for (const [t, v] of rs.slice(1)) mark("circle", "dot", { cx: x(t), cy: y(v), r: 4 });
  if (m.eta_at != null) mark("circle", "tgt", { cx: x(m.eta_at), cy: y(100), r: 4 });
  const hx = mark("line", "hx", { x1: 0, x2: 0, y1: top, y2: bot, visibility: "hidden" });

  const tip = h("div", "tip");
  tip.hidden = true;
  svg.addEventListener("mousemove", (ev) => {
    const r = svg.getBoundingClientRect();
    const px = ((ev.clientX - r.left) / r.width) * W;
    const t = m.started_at + (px / W) * (t1 - m.started_at);
    let v = null;
    for (const [rt, rv] of rs) if (rt <= t) v = rv;
    if (t > nowS() || v == null) {
      tip.hidden = true;
      hx.setAttribute("visibility", "hidden");
      return;
    }
    hx.setAttribute("x1", px);
    hx.setAttribute("x2", px);
    hx.setAttribute("visibility", "visible");
    tip.hidden = false;
    tip.style.left = `${(px / W) * 100}%`;
    tip.textContent = `${clock(t)} · ${v}%`;
  });
  svg.addEventListener("mouseleave", () => {
    tip.hidden = true;
    hx.setAttribute("visibility", "hidden");
  });
  const ax = h(
    "div",
    "ax",
    h("span", null, `${clock(m.started_at, false)} start`),
    h("span", null, `now ${clock(now, false)}`),
    m.eta_at != null && h("span", null, `${late(m) ? "was due" : "ETA"} ${clock(m.eta_at, false)}`),
  );
  return h("div", "chart", svg, tip, ax, m.eta_basis && h("div", "basis", `rate: ${m.eta_basis}`));
}

// -- one check: its command and what it did last ------------------------------
function check(c) {
  if (!c.cmd) return h("div", "ck off", h("div", "h", h("span", "k", c.kind), h("span", "res", "none")));
  const ran = c.rc !== undefined;
  const res = h("span", "res");
  if (!ran) res.append("not run yet");
  else if (c.rc == null) res.append(c.verdict);
  else res.append("exit ", h("b", null, String(c.rc)), ` · ${c.verdict}`);
  const pre = h("pre", null, c.cmd);
  pre.title = "Click to show all of it";
  pre.addEventListener("click", () => {
    if (!getSelection().toString()) pre.classList.toggle("x");
  });
  return h(
    "div",
    `ck${c.bad ? " bad" : ""}`,
    h("div", "h", h("span", "k", c.kind), res, ran && h("span", "ago", `since ${clock(c.since)}`)),
    pre,
    c.err && h("div", "err", c.err),
  );
}

// -- one watched session: its handle and what it last said ---------------------
function watched(r) {
  return h(
    "div",
    `ck${r.state === "blocked" ? " bad" : ""}`,
    h("div", "h", h("span", "k", r.handle), h("span", "res", (r.attention || "not read yet").replace("_", " "))),
    r.line && h("div", "err", r.line),
  );
}

// -- the card -----------------------------------------------------------------
function card(m, live) {
  const pill = m.broken
    ? h("span", "pill bad", "check fails")
    : late(m)
      ? h("span", "pill late", "past ETA")
      : h("span", "pill", "watching");
  const hd = h("div", "hd", h("div", "t", h("div", "d", m.description), pill), h("div", "sub", m.id));

  const left = m.sessions
    ? h("span", "p", tally(m))
    : measured(m)
      ? h("span", "p", String(m.progress), h("small", null, "%"))
      : h("span", "p unk", !hasProgressCmd(m) ? "no progress command" : m.broken ? "no reading" : "no reading yet");
  const at = h("div", "at"), inn = h("div", "in"), e = h("div", "e", at, inn);
  const drawEta = () => {
    if (m.eta_at != null && !late(m)) {
      e.className = "e";
      at.textContent = `ETA ${clock(m.eta_at)}`;
      inn.textContent = `in about ${age(m.eta_at - nowS())}`;
    } else if (late(m)) {
      e.className = "e late";
      at.textContent = `was due ${clock(m.eta_at)}`;
      const lastAt = m.readings[m.readings.length - 1][0];
      inn.textContent = `${age(nowS() - m.eta_at)} late, no reading since ${clock(lastAt, false)}`;
    } else {
      e.className = "e none";
      at.textContent = "no ETA";
      inn.textContent = m.broken ? "its checks cannot run" : hasProgressCmd(m) ? "waiting for progress to move" : "nothing to estimate from";
    }
  };
  drawEta();
  live.push(drawEta);
  const now = h("div", "now", h("div", "big", left, e));
  if (m.readings.length > 1) now.append(chart(m));
  else {
    const fill = h("i"), used = h("span");
    const drawUsed = () => {
      const t = nowS() - m.started_at;
      fill.style.width = `${Math.min(100, (100 * t) / m.timeout_s).toFixed(1)}%`;
      used.textContent = `${dur(t)} of ${dur(m.timeout_s)}`;
    };
    drawUsed();
    live.push(drawUsed);
    now.append(h("div", "budget", h("div", "track", fill), h("div", "ax", h("span", null, "time used"), used)));
  }

  const startedAgo = h("span"), outIn = h("span");
  const drawFacts = () => {
    startedAgo.textContent = ` · ${dur(nowS() - m.started_at)} ago`;
    outIn.textContent = ` · in ${age(m.started_at + m.timeout_s - nowS())}`;
  };
  drawFacts();
  live.push(drawFacts);
  const facts = h(
    "dl",
    "facts",
    h("dt", null, "Started"), h("dd", null, clock(m.started_at), startedAgo),
    h("dt", null, "Checks"), h("dd", null, `every ${dur(m.interval_s)}`),
    h("dt", null, "Times out"), h("dd", null, clock(m.started_at + m.timeout_s), outIn),
  );
  const checks = m.sessions
    ? h("div", "checks", h("h5", null, "Sessions"), ...m.sessions.map(watched))
    : h("div", "checks", h("h5", null, "Checks"), ...m.checks.map(check));
  const cwd = h("span", "path", m.cwd);
  cwd.title = m.cwd;
  const ft = h("div", "ft", cwd, h("span", "hint", "Esc closes"));
  return [hd, now, facts, checks, ft];
}

// -- state: the rows drawn, the card open -------------------------------------
let rows = new Map(); // id -> {el, sig}
let mons = new Map(); // id -> monitor
let openId = null, openSig = null, openT = null, closeT = null;
let live = [];
let cardEl = null;

function cardBox() {
  if (cardEl) return cardEl;
  cardEl = h("div", "mcard");
  cardEl.id = "mcard";
  cardEl.addEventListener("pointerenter", byMouse(() => clearTimeout(closeT)));
  cardEl.addEventListener("pointerleave", byMouse(soonClose));
  document.getElementById("a2").append(cardEl);
  document.addEventListener("pointerdown", (ev) => {
    if (openId && !cardEl.contains(ev.target) && !ev.target.closest?.(".mon")) closeMonitorCard();
  });
  document.querySelector("#a2 .side")?.addEventListener("scroll", place);
  addEventListener("resize", place);
  return cardEl;
}

function place() {
  if (!openId) return;
  const r = rows.get(openId)?.el.getBoundingClientRect();
  const side = document.querySelector("#a2 .side")?.getBoundingClientRect();
  if (!r || !side || !r.height) return closeMonitorCard();
  // A phone has no room beside the drawer: base.css draws the card as a sheet.
  if (narrow.matches) {
    cardEl.style.removeProperty("left");
    cardEl.style.removeProperty("top");
    return;
  }
  const ch = cardEl.offsetHeight;
  cardEl.style.left = `${Math.max(8, side.left - CARD_W - 12)}px`;
  cardEl.style.top = `${Math.max(12, Math.min(r.top - 14, innerHeight - ch - 12))}px`;
}

function fill() {
  const m = mons.get(openId);
  live = [];
  cardEl.replaceChildren(...card(m, live));
  openSig = rows.get(openId).sig;
}

function open(id) {
  clearTimeout(closeT);
  if (!mons.has(id)) return;
  cardBox();
  for (const { el } of rows.values()) el.classList.toggle("open", el.dataset.id === id);
  if (openId !== id) {
    openId = id;
    fill();
  }
  place();
  cardEl.classList.add("show");
}

function soonClose() {
  clearTimeout(openT);
  clearTimeout(closeT);
  closeT = setTimeout(closeMonitorCard, CLOSE_AFTER_MS);
}

// Closes the card; false when none was open, so Esc can fall through to its
// other meanings.
export function closeMonitorCard() {
  clearTimeout(openT);
  if (!openId) return false;
  openId = openSig = null;
  live = [];
  cardEl.classList.remove("show");
  for (const { el } of rows.values()) el.classList.remove("open");
  return true;
}

export function renderMonitors(box, list) {
  mons = new Map(list.map((m) => [m.id, m]));
  const next = new Map();
  for (const m of list) {
    const sig = JSON.stringify(m);
    const had = rows.get(m.id);
    if (had && had.sig === sig && had.el.parentNode === box) next.set(m.id, had);
    else {
      const el = row(m);
      if (had) had.el.replaceWith(el);
      next.set(m.id, { el, sig });
    }
  }
  const order = [...next.values()].map((r) => r.el);
  if (order.length !== box.children.length || order.some((el, i) => box.children[i] !== el)) box.replaceChildren(...order);
  rows = next;
  if (!openId) return;
  if (!rows.has(openId)) return closeMonitorCard();
  rows.get(openId).el.classList.add("open");
  if (rows.get(openId).sig !== openSig) fill();
  place();
}

// Once a second: the countdowns in the rows and the open card.
export function tickMonitors() {
  for (const { el } of rows.values()) el.tick();
  for (const f of live) f();
}
