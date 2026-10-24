// The Fleet view: a card per open session, then the archive.
// Every field comes from the session's meta; nothing here computes activity.

import { factIcon, glyph, icon, iconText, LABEL, PANE_ICON } from "./glyphs.js";
import { idleFor } from "./lifecycle.js";
import { diskRows, hostRow, netRows, noteRow, providerLine, quotaHeading, quotaRow } from "./gauges.js";
import { shownOwner } from "./me.js";
import { dur, planTimes } from "./plantime.js";
import { workflowLocked, workflowTitle } from "./entries.js";
import { blocksOf, hueOf } from "./groups.js";

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

// transcript/describe.py's money(): a sub-cent session (cheap OpenCode models)
// would otherwise read $0.00.
// A session's model and, on a cascade, its place in it (session.py meta).
export function modelLine(m) {
  const c = m.cascade;
  if (!c || c.at === null || c.at === undefined) return m.model;
  return c.exhausted ? `${m.model} · cascade exhausted` : `${m.model} · ${c.at + 1} of ${c.models.length}`;
}

export function money(usd) {
  const v = usd || 0;
  return v >= 0.005 || v === 0 ? `$${v.toFixed(2)}` : `$${v.toFixed(4)}`;
}

// The cards a person must act on, shown first when the Fleet is ordered by need.
// Grouped on the drawn mark, so a review already read joins the rest.
const NEEDS = new Set(["needs_you", "error", "review"]);

// A queue worker whose task is over and that is quiet: its result went to whoever
// enqueued it, so nothing on its card is for you. A failed task, an errored
// turn and a worker at work are all still yours to look at.
export const finished = (m) =>
  !!m.worker && ["completed", "cancelled"].includes(m.worker.status) && ["done", "review"].includes(m.attention);

// A quiet session (#386) needs nobody yet: its own group, last.
const group = (m) => (m.quiet ? "quiet" : NEEDS.has(m.mark) && !finished(m) ? "needs" : "rest");

// Which session needs you most: what blocks on you, then failures, then results
// to read, and within each the one waiting longest. A read review or done shows
// the idle mark and drops out. last_activity is when the last turn ended, since
// a read does not move it.
const RANK = { needs_you: 0, error: 1, review: 2, done: 3 };
const byWait = (a, b) => RANK[a.mark] - RANK[b.mark] || (a.last_activity || 0) - (b.last_activity || 0);

// The sessions Alt+J walks, in order. A queue worker's result goes to whoever
// enqueued it, so only its error is yours.
// What places a card inside the needs-you group. Empty for the rest, which a
// working session's every tick would otherwise regroup.
const rank = (m) => (group(m) === "needs" ? `${m.mark}:${m.last_activity}` : "");

export function byNeed(metas) {
  return metas.filter((m) => !m.quiet && Object.hasOwn(RANK, m.mark) && (!m.worker || m.mark === "error")).sort(byWait);
}

// `stacks` ({here, open, onToggle}) draws a group of several sessions as one
// stack of cards, or unfolded across the grid when its key is in `open` (#586).
// A unit is a lone session or a group; the attention order ranks a group by
// its most urgent member.
export function renderCards(box, metas, onOpen, order = "attention", onClose = () => {}, stacks = null) {
  if (!metas.length) {
    box.replaceChildren(el("div", "empty", "No open sessions. Start one with +."));
    return;
  }
  // A quiet session has no tab and belongs to no group (#386, #586), and a
  // queue worker's card carries its task's life, fading and sinking when it
  // ends (#249): both stay cards of their own.
  const lone = (m) => ({ key: m.key, head: m, members: [m] });
  const apart = (m) => m.quiet || m.worker;
  const units = stacks
    ? [...blocksOf(metas.filter((m) => !apart(m)), stacks.here), ...metas.filter(apart).map(lone)]
    : metas.map(lone);
  const draw = (u) => unit(u, metas, onOpen, onClose, stacks);
  if (order !== "attention") {
    box.replaceChildren(...units.map(draw));
    return;
  }
  const out = [];
  const best = (u) => u.members.filter((m) => group(m) === "needs").sort(byWait)[0];
  const kind = (u) => (best(u) ? "needs" : u.members.every((m) => group(m) === "quiet") ? "quiet" : "rest");
  const needs = units.filter((u) => kind(u) === "needs").sort((a, b) => byWait(best(a), best(b)));
  // Finished workers sink below the live cards; the sort is stable.
  const done = (u) => u.members.every(finished);
  const rest = units.filter((u) => kind(u) === "rest").sort((a, b) => done(a) - done(b));
  if (needs.length) out.push(el("div", "grp-h", "Needs you"), ...needs.map(draw));
  // With nobody needing you, the order bar's "Sessions" heading names the list.
  const quiet = units.filter((u) => kind(u) === "quiet");
  if (rest.length && (needs.length || quiet.length)) out.push(el("div", "grp-h", "Everything else"));
  out.push(...rest.map(draw));
  if (quiet.length) out.push(el("div", "grp-h", "Quiet"), ...quiet.map(draw));
  box.replaceChildren(...out);
}

function unit(u, metas, onOpen, onClose, stacks) {
  if (u.members.length < 2) return card(u.head, onOpen, onClose);
  const rest = u.members.slice(1);
  const hue = `var(--group-${hueOf(u.key)})`;
  if (stacks.open.has(u.key)) {
    const g = el("div", "gopen");
    g.dataset.group = u.key;
    g.style.setProperty("--g", hue);
    const gh = el("div", "gh");
    const working = u.members.filter((m) => m.attention === "working").length;
    const fold = el("button", null, "Stack");
    fold.type = "button";
    fold.addEventListener("click", () => stacks.onToggle(u.key));
    gh.append(markNode(u.head), el("b", null, u.head.handle), el("span", "n", `${u.members.length} sessions · ${working} working`), fold);
    const handles = new Map(metas.map((m) => [m.log_id, m.handle]));
    const cards = u.members.map((m) => {
      const c = card(m, onOpen, onClose);
      const by = handles.get(m.spawned_by);
      if (by) c.append(el("div", "by", `started by ${by}`));
      return c;
    });
    g.append(gh, el("div", "gcards"));
    g.lastChild.append(...cards);
    return g;
  }
  const needy = rest.filter((m) => m.mark === "needs_you");
  const s = el("div", `stack${needy.length ? " need" : ""}`);
  s.dataset.group = u.key;
  s.dataset.members = u.members.map((m) => m.key).join(" ");
  s.style.setProperty("--g", hue);
  const c = card(u.head, onOpen, onClose);
  const row = el("div", "mrow");
  row.append(...rest.map(markNode), el("span", null, `+${rest.length}`), ...needy.map((m) => el("span", "alert", `${m.handle} needs you`)));
  c.append(row);
  s.append(el("div", "ghost g2"), el("div", "ghost g1"), el("span", "badge", String(u.members.length)), c);
  // A click on the stack unfolds it; the card's own buttons still act.
  s.addEventListener(
    "click",
    (ev) => {
      if (ev.target.closest("button")) return;
      ev.stopPropagation();
      stacks.onToggle(u.key);
    },
    true,
  );
  return s;
}

// One session's card redrawn where it stands. False when its group, or its place
// in the needs-you group, changed and the caller must regroup with renderCards.
export function patchCard(box, m, onOpen, order = "attention", onClose = () => {}) {
  const old = box.querySelector(`.card[data-id="${CSS.escape(m.key)}"]`);
  // A member hidden in a stack changes the stack.
  if (!old) return !box.querySelector(`.stack[data-members~="${CSS.escape(m.key)}"]`);
  if (old.closest(".stack, .gopen")) return false; // a group redraws whole
  if (order === "attention" && (old.dataset.group !== group(m) || old.dataset.rank !== rank(m) || old.classList.contains("finished") !== finished(m))) return false;
  old.replaceWith(card(m, onOpen, onClose));
  return true;
}

// A terminal's mark: running, a non-zero exit, or ended.
const TERM_DOT = { running: "ready", failed: "err", ended: "ghost" };
// A browser's mark: who holds the page, or ended.
const BROWSER_DOT = { agent: "ready", person: "ready", ended: "ghost" };

// A session's mark: its attention, or the plain idle dot once a done or review is read.
export function markNode(m) {
  if (m.kind === "term") return el("span", `dot ${TERM_DOT[m.mark] || "ghost"}`);
  if (m.kind === "browser") return el("span", `dot ${BROWSER_DOT[m.mark] || "ghost"}`);
  if (m.mark === "idle") return el("span", "dot ready");
  return glyph(m.mark || m.attention);
}

// A terminal's card: its command, its state and the last line it printed.
function termCard(m, onOpen, onClose) {
  const c = el("div", `card term${m.off ? " off" : ""}`);
  c.dataset.id = m.key;
  c.dataset.group = group(m);
  c.dataset.rank = rank(m);
  const hd = el("div", "hd");
  const browser = m.kind === "browser";
  const h = el("span", "h");
  h.append(...iconText(PANE_ICON[browser ? "browser" : "term"], m.name));
  hd.append(markNode(m), h);
  const owner = shownOwner(m);
  if (owner) hd.append(el("span", "who", owner));
  const state = browser
    ? m.state === "running" ? (m.holder === "person" ? (m.ask ? `the agent asks: ${m.ask}` : "you hold it") : "the agent holds it") : `stopped: ${m.reason || ""}`
    : m.state === "exited" ? `exited (${m.code})` : m.state === "ended" ? `ended: ${m.reason}` : "running";
  c.append(hd, el("div", "ttl", m.title), el("div", "ask", state), el("div", "ln", browser ? m.url || "" : m.last_line || ""));
  c.addEventListener("click", (ev) => {
    if (!ev.target.closest("button")) onOpen(m.key);
  });
  closeOnMiddle(c, () => onClose(m.key));
  return c;
}

export function card(m, onOpen, onClose) {
  if (m.kind === "term" || m.kind === "browser") return termCard(m, onOpen, onClose);
  const c = el("div", `card ${m.state} at-${m.attention}${m.off ? " off" : ""}${finished(m) ? " finished" : ""}${m.quiet ? " quiet" : ""}`);
  c.dataset.id = m.key;
  c.dataset.group = group(m);
  c.dataset.rank = rank(m);
  const hd = el("div", "hd");
  hd.append(markNode(m), el("span", "h", m.handle));
  const owner = shownOwner(m);
  if (owner) hd.append(el("span", "who", owner));
  if (m.worker) hd.append(el("span", "badge", `worker · ${m.worker.queue}`));
  if (m.workflow) {
    // A run's title can be a sentence: it gives way, the node that tells
    // steps apart stays, and the tooltip has it whole (#670).
    const title = workflowTitle(m.workflow.run) || m.workflow.run;
    const b = el("span", "badge wf");
    b.title = `workflow · ${title} · ${m.workflow.node}`;
    b.append("workflow · ", el("span", "t", title), ` · ${m.workflow.node}`);
    hd.append(b);
  }
  // Two axes, two marks: quiet hides the tab, asleep has no process (#479).
  if (m.quiet) {
    const q = el("span", "quiet");
    q.append(icon("quiet"), "quiet");
    hd.append(q);
  }
  if (m.state === "asleep") {
    const idle = m.asleep_why === "idle" && idleFor(m);
    hd.append(el("span", "zz pk", idle ? `zz idle ${idle}` : m.asleep_why ? `zz ${m.asleep_why}` : "zz"));
  }
  const label = m.attention === "waiting" && m.waiting_on ? `waiting · ${m.waiting_on}` : LABEL[m.attention] || m.state;
  hd.append(el("span", `s at-${m.attention}`, label));
  if (m.attention === "done") hd.append(archiveButton(m, onClose));
  const parts = [hd, el("div", "ttl", m.title || "untitled")];
  if (m.attention_line) parts.push(el("div", `ask at-${m.attention}`, m.attention_line));
  const sub = el("div", "ln");
  sub.append(factIcon("h", m.harness), el("b", null, `${m.agent}${(m.overridden || []).length ? "*" : ""}`), document.createTextNode(`${modelLine(m)} · ${cwdTail(m.cwd)}`));
  parts.push(sub);
  const skills = m.skills || [];
  if (m.plan_now || m.plan_did || skills.length) {
    const pl = el("div", "pl");
    if (m.plan_now) pl.append(planRow("now", m.plan_now, "now", el("span", "t")));
    if (m.plan_did) pl.append(planRow("did", m.plan_did, ""));
    // The skills it loaded, in order: which worker follows which procedure.
    if (skills.length) pl.append(planRow("skills", skills.join(" · "), "skills"));
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
  if (m.unread) ft.append(el("span", "unr", `${m.unread} unread`));
  const mons = (m.monitors || []).length;
  if (mons) ft.append(el("span", "mons", `${mons} monitor${mons > 1 ? "s" : ""}`));
  if (m.plan_total) ft.append(planProg(m));
  ft.append(el("span", "ctx", `${pct}%`));
  parts.push(bar, ft);
  c.append(...parts);
  tickPlan(c, m);
  // A control inside the card is its own: a click on it opens nothing.
  c.addEventListener("click", (ev) => {
    if (!ev.target.closest("button")) onOpen(m.key);
  });
  closeOnMiddle(c, () => onClose(m.key));
  return c;
}

// A middle click is Close, on a card or a tab alike. The default of its
// mousedown is autoscroll, or a paste on Linux, so that is stopped first.
export function closeOnMiddle(node, close) {
  node.addEventListener("mousedown", (ev) => {
    if (ev.button === 1) ev.preventDefault();
  });
  node.addEventListener("auxclick", (ev) => {
    if (ev.button !== 1) return;
    ev.preventDefault();
    close();
  });
}

// Close for a finished session, from the Fleet. A real button, so Tab reaches
// it and Enter or Space presses it like the archive rows' Read and Reopen.
function archiveButton(m, onClose) {
  const b = el("button", "btn archive needs-net", "Archive");
  b.type = "button";
  b.title = "Close this session; it stays in the archive";
  if (workflowLocked(m)) {
    // A live workflow's step closes with its run; the server refuses it too.
    b.disabled = true;
    b.title = "A step of a running workflow: it closes when the run ends";
  }
  b.addEventListener("click", () => onClose(m.key));
  return b;
}

function planRow(key, text, cls, tail) {
  const d = el("div", cls);
  const body = el("span", null, text);
  if (tail) body.append(tail);
  d.append(el("span", "k", key), body);
  return d;
}

// One segment per item: done filled, doing pulsing while the agent works.
function planProg(m) {
  const bar = el("span", `pbar${m.attention === "working" ? " live" : ""}`);
  for (const i of m.plan || []) bar.append(el("i", i.state === "pending" ? "" : i.state));
  const prog = el("span", "prog");
  prog.append(bar, el("span", "pt"));
  return prog;
}

// The parts of a card that move with the clock: the count and ETA, and the
// current item's time. Called when the card is drawn and every second after.
export function tickPlan(c, m) {
  const t = planTimes(m);
  const pt = c.querySelector(".ft .prog .pt");
  if (pt) pt.textContent = `${m.plan_done}/${m.plan_total}${t && t.left != null ? ` · ~${dur(t.left)}` : ""}`;
  const now = c.querySelector(".pl .now .t");
  const doing = (m.plan || []).findIndex((i) => i.state === "doing");
  if (now) now.textContent = t && doing >= 0 ? ` · ${dur(t.items[doing])}` : "";
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
    tr.dataset.id = m.key;
    // A click anywhere but a button reads it, as Enter on a selected row does;
    // a phone hides the Read button and keeps this.
    tr.addEventListener("click", (ev) => {
      if (!ev.target.closest("button")) onRead(m.key);
    });
    const actions = el("td", "acts");
    actions.append(
      rowButton("read", "book", "Read", "Read it without reopening it", () => onRead(m.key)),
      rowButton("reopen", "again", "Reopen", "Reopen it to continue the conversation", () => onReopen(m.key)),
    );
    tr.append(
      el("td", null, m.title || "untitled"),
      handleCell(m),
      el("td", "m", cwdTail(m.cwd)),
      el("td", "m", ago(m.last_activity)),
      el("td", "m", money(m.cost_usd)),
      actions,
    );
    t.append(tr);
  }
  box.replaceChildren(t);
}

// An archive row's action: an icon, named by aria-label and on hover.
function rowButton(cls, glyphName, name, title, onClick) {
  const b = el("button", `btn ${cls}`);
  b.type = "button";
  b.title = title;
  b.setAttribute("aria-label", name);
  b.append(icon(glyphName));
  b.addEventListener("click", onClick);
  return b;
}

// A handle, with its server's tag when the row is from a linked server.
function handleCell(m) {
  const td = el("td", "m", m.handle);
  if (m.server) td.append(el("span", "where", m.server));
  return td;
}

const ATTENTION_ORDER = ["needs_you", "error", "review", "working", "waiting", "done"];

// The band over the cards: this server's sessions by attention and its host.
// Counts and the average context come from the sessions the client already
// holds; the host meters from the host channel.
// `link` is the line beside a linked server's name: its host, its state.
// `measure` takes a speed reading on this band's server.
export function renderBand(band, { metas, host, server, link = "", off = false, measure = null }) {
  band.querySelector(".band-server").textContent = server || "server";
  band.querySelector(".band-link").textContent = link;
  band.classList.toggle("off", off);
  const counts = new Map();
  for (const m of metas) counts.set(m.attention, (counts.get(m.attention) || 0) + 1);
  const rows = ATTENTION_ORDER.filter((a) => counts.get(a)).map((a) => {
    const d = el("div");
    d.append(glyph(a), el("b", null, String(counts.get(a))), document.createTextNode(a === "needs_you" ? "need you" : LABEL[a]));
    return d;
  });
  band.querySelector(".band-counts").replaceChildren(...(rows.length ? rows : [el("div", "empty", "no sessions")]));

  const meters = [];
  if (host) {
    meters.push(hostRow("CPU", host.cpu, ""));
    meters.push(hostRow("RAM", host.ram.pct, `${host.ram.used_gb} / ${host.ram.total_gb} GB`));
    meters.push(...diskRows(host));
    meters.push(...netRows(host.net, Date.now() / 1000, off ? null : measure));
  }
  const live = metas.filter((m) => m.state !== "asleep" && m.context_window && m.context_tokens);
  if (live.length) {
    const avg = Math.round(live.reduce((a, m) => a + Math.min(100, (100 * m.context_tokens) / m.context_window), 0) / live.length);
    meters.push(hostRow("Context", avg, `avg of ${live.length} live`));
  }
  // How many have no process, and what parking freed: the harnesses' own
  // memory when each was parked (park.py).
  const asleep = metas.filter((m) => m.state === "asleep");
  if (asleep.length) {
    const gb = asleep.reduce((n, m) => n + (m.parked_rss || 0), 0) / 2 ** 30;
    meters.push(el("span", "parked-count", `${asleep.length} asleep · ~${gb.toFixed(1)} GB idle`));
  }
  band.querySelector(".band-host").replaceChildren(...meters);
}

// The quota column, apart from the rest of the band: sessions patch up to four
// times a second per working agent and quota about once a minute, and a
// rebuilt row loses the hover tooltip that spells its reading out.
// `same`: providers a band above already shows for the same account, drawn as
// one line naming that server instead of their gauges again.
export function renderBandQuota(band, { quota, now, same = [], home = "" }) {
  const providers = (quota && quota.providers) || [];
  band.querySelector(".band-quota-col").hidden = !providers.length && !same.length;
  band.querySelector(".band-quota-age").textContent = quotaHeading(providers, now);
  const out = [];
  for (const p of providers) {
    out.push(el("div", "prov", providerLine(p, now)));
    if (p.state === "failed") out.push(noteRow(p.note));
    else for (const w of p.windows) out.push(quotaRow(p, w, now));
  }
  for (const p of same) out.push(el("div", "prov same", `${p.label} · same account as ${home}`));
  band.querySelector(".band-quota").replaceChildren(...out);
}
