// The aegis client: one server, many sessions.
//
// The tabs are the server's open sessions (the `sessions` channel), the same in
// every browser. Their order and which one is focused belong to this browser:
// the order in localStorage, the focus in the URL hash.

import { Connection } from "./protocol.js";
import { Transcript } from "./transcript.js";
import { TabOrder, patchTab, renderTabs } from "./tabs.js";
import { ago, money, patchCard, renderArchive, renderBand, renderBandQuota, renderCards } from "./fleet.js";
import { age, quotaSideRow } from "./gauges.js";
import { installKeys, renderKeys } from "./keys.js";
import { glyph, installGlyphs, LABEL } from "./glyphs.js";
import { CommandMenu } from "./commands.js";
import { closeMonitorCard, renderMonitors, tickMonitors } from "./monitors.js";
import { ask, cancelAsk } from "./dialog.js";

const $ = (id) => document.getElementById(id);
const root = $("a2");

// -- the token: from the URL once, then kept for this tab only ----------
const params = new URLSearchParams(location.search);
if (params.has("token")) {
  sessionStorage.setItem("aegis.token", params.get("token"));
  params.delete("token");
  const rest = params.toString();
  history.replaceState(null, "", location.pathname + (rest ? `?${rest}` : "") + location.hash);
}
const token = sessionStorage.getItem("aegis.token");

// Quota is one subscription for the page: the band and the sidebar both draw
// it. Host is subscribed only while the Fleet view shows, so a server nobody
// watches samples nothing.
let quota = { providers: [] };
let host = null;
let unsubHost = null;
let quotaDrawnFor = null; // the view the quota rows were last drawn for
const nowS = () => Date.now() / 1000;

// -- theme ----------------------------------------------------------------
const themePick = $("theme");
themePick.value = document.documentElement.dataset.theme;
themePick.addEventListener("change", () => {
  document.documentElement.dataset.theme = themePick.value;
  localStorage.setItem("aegis.theme", themePick.value);
});

// -- state ----------------------------------------------------------------
const sessions = new Map(); // log_id -> meta, from the `sessions` channel
const order = new TabOrder();
let ordered = []; // metas in this browser's tab order
let shown = null; // log_id whose transcript is subscribed
let unsubTranscript = null;
let workingSince = null;
let booted = false;
const transcript = new Transcript($("tr"), $("entries"), $("jump"));
installGlyphs();
// How the Fleet orders its cards: this browser's choice, like the tab order.
let fleetOrder = localStorage.getItem("aegis.fleetOrder") || "attention";
function markOrder() {
  for (const b of document.querySelectorAll("#fleet-order button")) b.classList.toggle("on", b.dataset.order === fleetOrder);
}
markOrder();
for (const b of document.querySelectorAll("#fleet-order button"))
  b.addEventListener("click", () => {
    fleetOrder = b.dataset.order;
    localStorage.setItem("aegis.fleetOrder", fleetOrder);
    markOrder();
    render();
  });

// -- routing: #fleet, #new, #s=<log_id>, #read=<log_id> -----------------------
function route() {
  const h = location.hash.slice(1);
  if (h.startsWith("s=")) return { view: "session", id: h.slice(2) };
  if (h.startsWith("read=")) return { view: "read", id: h.slice(5) };
  if (h === "new") return { view: "spawn" };
  return { view: "fleet" };
}

// Navigation renders in the same step as the URL changes. Setting
// location.hash would render later, on hashchange, and anything typed in
// between was wiped when the new tab's draft loaded (#128's CI failure).
// Back and Forward, and a hand-edited URL, still arrive through hashchange.
function go(hash) {
  if (location.hash !== hash) history.pushState(null, "", hash);
  render();
}

window.addEventListener("hashchange", render);
window.addEventListener("popstate", render);

function show(view, text) {
  root.dataset.view = view;
  if (text) $("boot-text").textContent = text;
}

// -- the connection -----------------------------------------------------------
const conn = new Connection(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`, token, {
  onState(state, server) {
    $("conn-dot").className = `dot ${state === "open" ? "ready" : state === "connecting" ? "ghost" : "err"}`;
    $("conn-text").textContent =
      state === "open" ? server : state === "connecting" ? "connecting" : state === "closed" ? "disconnected, retrying" : state;
    if (state === "unauthorized") show("boot", "The token was refused. Open the URL that `aegis serve` printed.");
    if (state === "version") show("boot", "This page and the server speak different protocol versions. Reload the page.");
    if (state === "open") {
      // Whether this browser runs on the server's desktop (Open natively).
      if (conn.native) root.dataset.native = "";
      else delete root.dataset.native;
      loadAgents();
      loadVersion();
    }
  },
});

if (!token) show("boot", "No token. Open the URL that `aegis serve` printed; it carries the token.");
else {
  conn.subscribe(
    "sessions",
    (metas) => {
      sessions.clear();
      for (const m of metas || []) sessions.set(m.log_id, m);
      booted = true;
      onSessions();
    },
    (ops) => {
      for (const op of ops) {
        if (op.upsert) {
          if (!sessions.has(op.upsert.log_id)) setChanged = true;
          sessions.set(op.upsert.log_id, op.upsert);
          changed.add(op.upsert.log_id);
        } else if (op.remove !== undefined) {
          sessions.delete(op.remove);
          setChanged = true;
        }
      }
      // A session added or removed redraws at once: a reply that navigates
      // to it (spawn, reopen) arrives right after this patch.
      if (setChanged) {
        setChanged = false;
        changed.clear();
        onSessions();
      } else if (!frame) frame = requestAnimationFrame(flushSessions);
    },
  );
  conn.subscribe(
    "quota",
    (snap) => {
      quota = snap || { providers: [] };
      drawQuota();
    },
    (ops) => {
      for (const op of ops) if (op.set) quota = op.set;
      drawQuota();
    },
  );
  conn.connect();
}

// Patches that only update sessions the page already shows redraw once a
// frame, however many arrive, and only those sessions' tab and card (#158).
let changed = new Set();
let setChanged = false;
let frame = 0;

function flushSessions() {
  frame = 0;
  const ids = changed;
  changed = new Set();
  if (!booted) return;
  ordered = ordered.map((m) => sessions.get(m.log_id));
  const r = route();
  for (const id of ids) patchTab($("tablist"), sessions.get(id), r.view === "session" ? r.id : null, tabActions);
  if (r.view === "fleet") {
    const regroup = [...ids].some((id) => !patchCard($("cards"), sessions.get(id), openSession, fleetOrder));
    if (regroup) renderCards($("cards"), ordered, openSession, fleetOrder);
    fleetMark(false);
    drawBand();
  } else if (r.view === "session" && ids.has(r.id)) renderMeta(sessions.get(r.id));
}

function onSessions() {
  const ids = order.arrange([...sessions.values()].sort((a, b) => a.created_at - b.created_at).map((m) => m.log_id));
  ordered = ids.map((id) => sessions.get(id));
  render();
}

// -- rendering -----------------------------------------------------------------
const openSession = (id) => go(`#s=${id}`);
const tabActions = {
  onFocus: openSession,
  onMove: (id, before) => {
    order.move(id, before);
    onSessions();
  },
};

function render() {
  if (!token || !booted) return;
  const r = route();
  if (r.view === "session" && !sessions.has(r.id)) {
    go("#fleet"); // closed here or elsewhere
    return;
  }
  renderTabs($("tablist"), ordered, r.view === "session" ? r.id : null, tabActions);
  $("tab-fleet").classList.toggle("on", r.view === "fleet");
  $("tab-add").classList.toggle("on", r.view === "spawn");
  root.dataset.mode = r.view === "read" ? "read" : "live";
  // Quota rows redraw on a quota patch, the timer, or a change of view; never
  // on a sessions patch, which would take the hover tooltip with them.
  const viewKey = `${r.view}:${r.id || ""}`;
  const newView = viewKey !== quotaDrawnFor;
  quotaDrawnFor = viewKey;
  if (r.view === "fleet") {
    follow(null);
    show("fleet");
    renderCards($("cards"), ordered, openSession, fleetOrder);
    fleetMark(false);
    watchHost(true);
    drawBand();
    if (newView) drawQuota();
    if (!archiveLoaded) loadArchive();
    document.title = "Fleet · aegis";
  } else if (r.view === "spawn") {
    watchHost(false);
    follow(null);
    show("spawn");
    $("sp-text").focus();
    document.title = "New session · aegis";
  } else if (r.view === "session") {
    watchHost(false);
    // Shown first: follow() sizes the message box, which measures 0 while hidden.
    show("session");
    follow(r.id);
    renderMeta(sessions.get(r.id));
    if (newView) drawQuota();
  } else {
    watchHost(false);
    show("session");
    follow(r.id);
    const m = archived.find((x) => x.log_id === r.id);
    if (m) renderMeta({ ...m, state: "archived" });
    else if (!archiveLoaded) loadArchive().then(render);
    if (newView) drawQuota();
  }
}

function watchHost(on) {
  if (on && !unsubHost) {
    unsubHost = conn.subscribe(
      "host",
      (snap) => {
        host = snap;
        drawBand();
      },
      (ops) => {
        for (const op of ops) if ("set" in op) host = op.set;
        drawBand();
      },
    );
  } else if (!on && unsubHost) {
    unsubHost();
    unsubHost = null;
    host = null;
  }
}

function drawBand() {
  if (root.dataset.view !== "fleet") return;
  renderBand($("band"), { metas: ordered, host, server: conn.server });
}

function drawSideQuota() {
  const p = quota.providers.find((x) => x.name === "claude");
  $("s-quota-sec").hidden = !p;
  if (!p) return;
  const now = nowS();
  const rows = p.state === "failed" ? [] : p.windows.map((w) => quotaSideRow(p, w, now));
  const foot = document.createElement("div");
  foot.className = "kv dim";
  const note = document.createElement("span");
  if (p.state === "ok") note.textContent = `read ${ago(p.read_at)}`; // "read just now", "read 2m ago"
  else {
    note.className = "gnote";
    note.textContent = p.state === "stale" ? `${p.note}, reading ${age(now - p.read_at)} old` : p.note;
  }
  foot.append(note);
  $("s-quota").replaceChildren(...rows, foot);
}

function drawQuota() {
  if (root.dataset.view === "fleet") renderBandQuota($("band"), { quota, now: nowS() });
  else if (root.dataset.view === "session") drawSideQuota();
}

// Countdowns and the tick move with the clock; their unit is minutes.
setInterval(drawQuota, 30 * 1000);

function follow(id) {
  if (shown === id) return;
  closeSide();
  if (unsubTranscript) unsubTranscript();
  unsubTranscript = null;
  transcript.clear();
  shown = id;
  if (!id) return;
  unsubTranscript = conn.subscribe(
    `transcript:${id}`,
    (entries) => {
      transcript.snapshot(entries || []);
      // Read by scripts/bench.py: when the snapshot was drawn and painted.
      const mark = (window.__a2snapshot = { at: performance.now(), count: (entries || []).length });
      requestAnimationFrame(() => (mark.painted = performance.now()));
    },
    (ops) => transcript.apply(ops),
  );
  menu.close();
  $("input").value = localStorage.getItem(`aegis.draft.${id}`) || "";
  autosize();
}

function fmtTokens(n) {
  return n >= 1000 ? `${Math.round(n / 1000)}k` : String(n);
}

function renderMeta(s) {
  if (!s) return;
  if (!editing.has("title")) $("s-title").textContent = s.title || "untitled";
  if (!editing.has("handle")) $("s-handle").textContent = s.handle;
  $("s-model").textContent = `${s.harness_label}, ${s.model}`;
  if (s.attention === undefined) {
    // The archived read view: a stored meta has no attention, so the state alone.
    $("s-status").textContent = s.state;
    $("s-status").className = "st";
  } else {
    $("s-status").replaceChildren(glyph(s.attention), document.createTextNode(` ${LABEL[s.attention] || s.state}`));
    $("s-status").className = `st at-${s.attention}`;
  }
  $("s-ask").hidden = !s.attention_line;
  $("s-ask").textContent = s.attention_line || "";
  $("s-ask").className = `askbox at-${s.attention}`;
  const plan = s.plan || [];
  $("s-plan-sec").hidden = !plan.length;
  const mark = { done: "done", doing: "working", pending: "waiting" };
  $("s-plan").replaceChildren(
    ...plan.map((i) => {
      const d = document.createElement("div");
      d.className = i.state;
      d.append(glyph(mark[i.state] || "waiting"), span("", i.text));
      return d;
    }),
  );
  drawReplies(s);
  $("s-cwd").textContent = s.cwd;
  $("chip-model").textContent = s.model;
  $("chip-effort").textContent = `${s.effort} effort`;
  $("chip-perm").textContent = s.permission;
  if (s.context_tokens != null) {
    const pct = s.context_window ? Math.min(100, Math.round((100 * s.context_tokens) / s.context_window)) : null;
    $("s-tokens").textContent = s.context_window
      ? `${fmtTokens(s.context_tokens)} of ${fmtTokens(s.context_window)}`
      : `${fmtTokens(s.context_tokens)} tokens`;
    $("s-pct").textContent = pct == null ? "" : `${pct}%`;
    $("s-bar").style.width = `${pct || 0}%`;
  } else {
    $("s-tokens").textContent = "no turn yet";
    $("s-pct").textContent = "";
    $("s-bar").style.width = "0%";
  }
  $("s-cost").textContent = money(s.cost_usd);
  const mons = s.monitors || [];
  $("s-mon-sec").hidden = !mons.length;
  renderMonitors($("s-monitors"), mons);
  const working = s.state === "working";
  $("interrupt").hidden = !working;
  $("restart").disabled = working;
  $("working").hidden = !working;
  $("stop-session").disabled = s.state === "stopped";
  if (working && workingSince == null) workingSince = Date.now();
  if (!working) workingSince = null;
  $("input").placeholder =
    s.state === "stopped"
      ? "Stopped; your next message resumes it."
      : touch.matches
        ? "Message the agent. ↵ sends, / for commands."
        : "Message the agent. Enter sends, Shift+Enter adds a line, / for commands, Esc interrupts.";
  document.title = `${working ? "● " : ""}${s.title || s.handle} · aegis`;
}

setInterval(() => {
  tickMonitors();
  if (workingSince != null) $("working-meta").textContent = `${Math.round((Date.now() - workingSince) / 1000)}s, Esc interrupts`;
  if (root.dataset.view === "fleet") for (const c of document.querySelectorAll(".card")) {
    const m = sessions.get(c.dataset.id);
    if (m) c.querySelector(".when").textContent = ago(m.last_activity);
  }
}, 1000);

// -- version: the running aegis and the latest release ------------------------
// Asked again every hour, so a tab left open learns about a new release; the
// server caches PyPI's answer for that long anyway.

function span(cls, text) {
  const el = document.createElement("span");
  el.className = cls;
  el.textContent = text;
  return el;
}

async function loadVersion() {
  let v;
  try {
    v = await conn.call("server.version");
  } catch (e) {
    return;
  }
  const run = v.running;
  const dev = run.dev;
  const short = run.commit ? run.commit.slice(0, 7) : null;
  const shown = dev && short ? short : run.version || "unknown";
  const behind = v.status === "behind";

  $("ver-head").textContent = `aegis on ${conn.server}`;
  $("ver-run").replaceChildren(span("v", shown), ...(dev ? [span("tag", "dev")] : []));
  // aegis-dev builds from the resolved commit, so the ref is often the commit again.
  const ref = run.ref && !(run.commit || "").startsWith(run.ref) ? run.ref : null;
  $("ver-ref-row").hidden = !ref;
  $("ver-ref").textContent = ref || "";
  $("ver-base-row").hidden = !dev || !run.version;
  $("ver-base").textContent = run.version || "";
  const mark = v.status === "current" ? [span("ok", "✓ current")] : behind ? [span("upd", "↑ update")] : [];
  $("ver-latest").replaceChildren(span("v", v.latest || "unknown"), ...mark);
  $("ver-sec").hidden = false;

  const top = $("ver-top");
  top.replaceChildren(span("", shown), ...(dev ? [span("tag", "dev")] : behind ? [span("upd", "↑")] : []));
  top.title = behind
    ? `aegis ${run.version}; ${v.latest} is out: uv tool upgrade aegis-harness`
    : dev
      ? `aegis built from ${run.commit || "a source tree"}${ref ? ` (${ref})` : ""}, based on ${run.version}; latest release ${v.latest || "unknown"}`
      : `aegis ${run.version}; latest release ${v.latest || "unknown"}`;
  top.hidden = false;
}
setInterval(() => conn.open && loadVersion(), 3600 * 1000);

// -- Open natively on a sent file's card ------------------------------------
$("entries").addEventListener("click", async (ev) => {
  const b = ev.target.closest(".fbar .native");
  if (!b) return;
  b.disabled = true;
  try {
    await conn.call("file.open", { file_id: b.dataset.fileId, name: b.dataset.name });
  } catch (e) {
    $("send-error").textContent = e.message;
  } finally {
    setTimeout(() => (b.disabled = false), 800);
  }
});

// -- archive -------------------------------------------------------------------
let archived = [];
let archiveLoaded = false;
let archiveTimer = null;

async function loadArchive() {
  archiveLoaded = true;
  try {
    const q = $("arch-q").value.trim();
    archived = await conn.call("archive.list", q ? { query: q } : {});
  } catch (e) {
    archived = [];
  }
  renderArchive($("arch-list"), archived, {
    onReopen: reopen,
    onRead: (id) => go(`#read=${id}`),
  });
  fleetMark(false);
}

// The Fleet's selection: a card or an archive row, by log id, re-marked after
// every redraw because both are rebuilt from scratch.
let fleetSel = null;

function fleetItems() {
  return [...document.querySelectorAll("#cards .card, #arch-list tr[data-id]")];
}

function fleetMark(scroll) {
  for (const n of document.querySelectorAll("#cards .sel, #arch-list .sel")) n.classList.remove("sel");
  const n = fleetItems().find((x) => x.dataset.id === fleetSel);
  if (!n) {
    fleetSel = null;
    return;
  }
  n.classList.add("sel");
  if (scroll) n.scrollIntoView({ block: "nearest" });
}

function fleetMove(delta) {
  const items = fleetItems();
  const i = items.findIndex((x) => x.dataset.id === fleetSel);
  const n = i < 0 ? items[0] : items[i + delta];
  if (!n) return;
  fleetSel = n.dataset.id;
  fleetMark(true);
}

function fleetOpen() {
  const n = fleetItems().find((x) => x.dataset.id === fleetSel);
  if (n) go(n.classList.contains("card") ? `#s=${fleetSel}` : `#read=${fleetSel}`);
}

$("arch-q").addEventListener("input", () => {
  clearTimeout(archiveTimer);
  archiveTimer = setTimeout(loadArchive, 200);
});

async function reopen(id) {
  try {
    await conn.call("session.reopen", { log_id: id });
    archiveLoaded = false;
    go(`#s=${id}`);
  } catch (e) {
    $("side-error").textContent = e.message;
  }
}
$("reopen").addEventListener("click", () => {
  const r = route();
  if (r.view === "read") reopen(r.id);
});

// -- the new-tab composer -------------------------------------------------------
// An agent is a preset: picking one fills the other chips, and changing a chip
// marks the agent `name*` until reset. Enter spawns and sends in one call.
let roster = { agents: [], harnesses: [], models: {}, default: null, cwd: "" };
const LAST_AGENT = "aegis.lastAgent";
const PICKS = ["harness", "model", "effort", "permission"];

async function loadAgents() {
  try {
    roster = await conn.call("agents.list");
  } catch (e) {
    $("sp-error").textContent = e.message;
    return;
  }
  // A reconnect rebuilds the options; the chips keep what the person set.
  const before = Object.fromEntries(PICKS.map((k) => [k, $(`sp-${k}`).value]));
  $("sp-harness").replaceChildren(
    ...roster.harnesses.map((h) => {
      const o = new Option(h.supported ? h.name : `${h.name} (not supported yet)`, h.name);
      o.disabled = !h.supported;
      return o;
    }),
  );
  $("sp-agent").replaceChildren(
    ...roster.agents.map((a) => {
      const why = a.error || (a.enabled ? "" : `${a.harness} is not supported yet`);
      const o = new Option(why ? `${a.name} (${why})` : a.name, a.name);
      o.disabled = !a.enabled;
      return o;
    }),
  );
  const usable = roster.agents.filter((a) => a.enabled).map((a) => a.name);
  const keep = $("sp-agent").dataset.picked;
  const start = [keep, localStorage.getItem(LAST_AGENT), roster.default].find((n) => usable.includes(n)) || usable[0];
  if (!$("sp-cwd").value) $("sp-cwd").value = roster.cwd;
  $("sp-error").textContent = roster.agents.length ? "" : "No agents: add an agents: map to .aegis.yaml.";
  if (keep && usable.includes(keep)) {
    $("sp-agent").value = keep;
    for (const k of PICKS) $(`sp-${k}`).value = before[k];
    fillModels($("sp-harness").value);
    markDiffs();
  } else if (start) pickAgent(start);
}

function current() {
  return roster.agents.find((a) => a.name === $("sp-agent").value);
}

function fillModels(harness) {
  $("sp-models").replaceChildren(...(roster.models[harness] || []).map((m) => new Option(m, m)));
}

function pickAgent(name) {
  const a = roster.agents.find((x) => x.name === name);
  if (!a) return;
  $("sp-agent").value = a.name;
  $("sp-agent").dataset.picked = a.name;
  fillModels(a.harness);
  for (const k of PICKS) $(`sp-${k}`).value = a[k];
  markDiffs();
}

function overrides() {
  const a = current();
  const out = {};
  if (!a) return out;
  for (const k of PICKS) {
    const v = $(`sp-${k}`).value.trim();
    if (v && v !== a[k]) out[k] = v;
  }
  return out;
}

function markDiffs() {
  const a = current();
  const diff = overrides();
  for (const k of PICKS) $(`sp-${k}`).classList.toggle("diff", k in diff);
  const changed = Object.keys(diff).length > 0;
  $("sp-reset").hidden = !changed;
  const opt = $("sp-agent").selectedOptions[0];
  if (a && opt) opt.textContent = changed ? `${a.name}*` : a.name;
}

async function spawnFromComposer() {
  const a = current();
  if (!a || $("sp-go").disabled) return;
  $("sp-go").disabled = true;
  $("sp-error").textContent = "";
  const text = $("sp-text").value.trim();
  const params = { agent: a.name, cwd: $("sp-cwd").value.trim() || null, ...overrides() };
  if (text) params.prompt = text;
  try {
    const r = await conn.call("session.spawn", params);
    localStorage.setItem(LAST_AGENT, a.name);
    $("sp-text").value = "";
    pickAgent(a.name);
    go(`#s=${r.log_id}`);
  } catch (e) {
    $("sp-error").textContent = e.message;
  } finally {
    $("sp-go").disabled = false;
  }
}

$("sp-agent").addEventListener("change", () => pickAgent($("sp-agent").value));
$("sp-harness").addEventListener("change", () => {
  fillModels($("sp-harness").value);
  markDiffs();
});
for (const k of ["model", "effort", "permission"]) $(`sp-${k}`).addEventListener("input", markDiffs);
$("sp-reset").addEventListener("click", () => pickAgent($("sp-agent").value));
$("spawn").addEventListener("submit", (ev) => {
  ev.preventDefault();
  spawnFromComposer();
});
// Enter in a field would submit the form and spawn a half-written session;
// there it means "done with this field".
for (const id of ["sp-model", "sp-cwd"]) {
  $(id).addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" && !ev.isComposing) {
      ev.preventDefault();
      $("sp-text").focus();
    }
  });
}
$("sp-text").addEventListener("keydown", (ev) => {
  if (ev.key === "Enter" && !ev.shiftKey && !ev.isComposing) {
    ev.preventDefault();
    spawnFromComposer();
  }
});

// -- tab bar and keys -----------------------------------------------------------
$("tab-fleet").addEventListener("click", () => go("#fleet"));
$("tab-add").addEventListener("click", () => go("#new"));
// The ? list: drawn once from the key table.
const keymap = $("keymap");
renderKeys(keymap);
const help = (open = keymap.hidden) => (keymap.hidden = !open);
$("keys-btn").addEventListener("click", () => help());
keymap.addEventListener("click", (ev) => ev.target === keymap && help(false));

// What each key in keys.js does. `input` and `editing` are declared below;
// a key is pressed only after this module has run.
installKeys(
  {
    composer() {
      const v = route().view;
      if (v === "session") input.focus();
      // The new tab's message box: Enter there spawns and sends.
      else if (v === "spawn") $("sp-text").focus();
    },
    browse() {
      const v = route().view;
      if (v === "session" || v === "read") {
        $("tr").focus({ preventScroll: true });
        transcript.pick();
      } else if (v === "fleet") {
        $("cards").focus({ preventScroll: true });
        if (!fleetSel) fleetMove(1);
      }
    },
    cycle(ev) {
      const all = ["#fleet", ...ordered.map((m) => `#s=${m.log_id}`)];
      const d = ev.code === "BracketRight" ? 1 : -1;
      const i = all.indexOf(location.hash || "#fleet");
      go(all[i < 0 ? (d > 0 ? 0 : all.length - 1) : (i + d + all.length) % all.length]);
    },
    next: () => transcript.move(1),
    prev: () => transcript.move(-1),
    turn: (ev) => transcript.moveTurn(ev.key === "J" ? 1 : -1),
    edge: (ev) => transcript.edge(ev.key === "G"),
    toggle: () => transcript.toggle(),
    press: () => transcript.press(),
    none() {},
    fleetNext: () => fleetMove(1),
    fleetPrev: () => fleetMove(-1),
    fleetOpen,
    filter: () => $("arch-q").focus(),
    spawn: () => go("#new"),
    tab(ev) {
      const n = Number(ev.altKey ? ev.code.slice(5) : ev.key);
      if (n === 0) go("#fleet");
      else if (ordered[n - 1]) go(`#s=${ordered[n - 1].log_id}`);
    },
    commands() {
      if (route().view !== "session") return;
      const v = input.value;
      if (!v || v.startsWith("/")) {
        if (!v) input.value = "/";
        input.focus();
        menu.openInline();
      } else menu.openOverlay();
    },
    escape() {
      if (cancelAsk()) return;
      if (root.dataset.side === "open") return closeSide();
      if (!keymap.hidden) help(false);
      else if (closeMonitorCard()) return;
      else if (route().view === "session" && !editing.size) interrupt();
    },
    help: () => help(),
  },
  () => (booted ? route().view : "boot"),
);

// -- composer ---------------------------------------------------------------
const input = $("input");
// On a touch screen Enter adds a line and the button sends: the key sits where
// a mistap lands, and half a message costs a turn.
const touch = matchMedia("(pointer: coarse)");

function autosize() {
  input.style.height = "auto";
  input.style.height = `${Math.min(input.scrollHeight, window.innerHeight * 0.4)}px`;
}

function focused() {
  const r = route();
  return r.view === "session" ? sessions.get(r.id) : null;
}

// The agent's suggested next messages, from its turn_end. Redrawn only when they
// change: renderMeta runs on every patch of the open session.
function drawReplies(s) {
  const box = $("replies");
  const replies = s.state === "working" ? [] : s.replies || [];
  const key = JSON.stringify([s.log_id, replies]);
  if (box.dataset.key === key) return;
  box.dataset.key = key;
  box.hidden = !replies.length;
  box.replaceChildren(
    span("lbl", "reply"),
    ...replies.map((text) => {
      const b = document.createElement("button");
      b.className = "rp";
      b.textContent = text;
      b.addEventListener("click", () => sendLine(text, false));
      return b;
    }),
  );
}

const askClose = (s) =>
  ask(`Close ${s.title || s.handle}? Its tab goes away in every browser; it stays in the archive.`, { ok: "Close" });

// A line from the composer, or from the menu's own filter (Alt+/ over a
// draft), which leaves the composer alone. The server resolves "/" lines.
async function sendLine(text, fromComposer) {
  const s = focused();
  if (!text || !s) return;
  if (text === "/help") {
    // The menu is the help: it lists every command with what it does.
    if (fromComposer) menu.setLine("/");
    menu.close();
    if (fromComposer) menu.openInline();
    else menu.openOverlay();
    return;
  }
  if (text === "/close" && !(await askClose(s))) return;
  $("send-error").textContent = "";
  const box = $("replies");
  const was = box.hidden;
  box.hidden = true; // any send answers the turn the pills belonged to
  delete box.dataset.key; // so the next drawReplies always redraws
  try {
    await conn.call("session.send", { log_id: s.log_id, text });
    if (/^\/model\s/.test(text)) catalogs.delete(s.log_id); // its efforts may differ
    if (fromComposer) {
      input.value = "";
      localStorage.removeItem(`aegis.draft.${s.log_id}`);
      autosize();
      // Clearing the box fires no input event; an open menu would take the next Esc.
      menu.close();
      $("composer").classList.remove("bad");
    }
    transcript.toBottom();
  } catch (e) {
    $("send-error").textContent = e.message;
    box.hidden = was;
  }
}

const send = () => sendLine(input.value.trim(), true);

// Catalogs per session, fetched when the menu first opens there. The promise
// is kept, so keystrokes that arrive while it loads wait for the same call
// instead of each asking the server again.
const catalogs = new Map();
async function loadCatalog() {
  const s = focused();
  if (!s) return null;
  if (!catalogs.has(s.log_id)) catalogs.set(s.log_id, conn.call("commands.list", { log_id: s.log_id }));
  try {
    return await catalogs.get(s.log_id);
  } catch (e) {
    catalogs.delete(s.log_id); // the next open asks again
    $("send-error").textContent = e.message;
    return null;
  }
}

const menu = new CommandMenu({
  box: $("cmd-menu"),
  rows: $("cmd-rows"),
  filter: $("cmd-filter"),
  load: loadCatalog,
  run: (line) => sendLine(line, false),
  getLine: () => input.value,
  setLine: (v) => {
    input.value = v;
    autosize();
    if (shown) localStorage.setItem(`aegis.draft.${shown}`, v);
    input.focus();
  },
  meta: () => focused(),
});

for (const [id, cmd] of [
  ["chip-model", "model"],
  ["chip-effort", "effort"],
  ["chip-perm", "permission"],
]) {
  $(id).classList.add("click");
  $(id).addEventListener("click", () => {
    // Over a draft, the menu's own filter line, as Alt+/ does, so the draft stays.
    if (input.value && !input.value.startsWith("/")) return menu.openOverlay(`/${cmd} `);
    input.value = `/${cmd} `;
    input.focus();
    menu.openInline();
  });
}

async function interrupt() {
  const s = focused();
  if (!s || s.state !== "working") return;
  try {
    await conn.call("session.interrupt", { log_id: s.log_id });
  } catch (e) {
    $("send-error").textContent = e.message;
  }
}

input.addEventListener("input", async () => {
  autosize();
  if (shown) localStorage.setItem(`aegis.draft.${shown}`, input.value);
  const v = input.value;
  if (v.startsWith("/") && !v.startsWith("//")) {
    if (menu.isOpen) menu.refresh();
    else await menu.openInline(); // the outline below needs the catalog it loads
  } else if (menu.isOpen) menu.close();
  const now = input.value;
  $("composer").classList.toggle("bad", now.startsWith("/") && !now.startsWith("//") && !menu.known(now));
});
input.addEventListener("keydown", (ev) => {
  if (menu.onKey(ev)) return;
  if (ev.key === "Enter" && !ev.shiftKey && !ev.isComposing && !touch.matches) {
    ev.preventDefault();
    send();
  }
});
$("send").addEventListener("click", send);
$("interrupt").addEventListener("click", interrupt);
// A nudge after an interrupt, an error or a stop: a message to a stopped
// session resumes it, so Restart is one sentence to the agent.
$("restart").addEventListener("click", () => sendLine("Continue", false));

$("stop-session").addEventListener("click", async () => {
  const s = focused();
  if (!s) return;
  try {
    await conn.call("session.stop", { log_id: s.log_id });
  } catch (e) {
    $("side-error").textContent = e.message;
  }
});

$("close").addEventListener("click", async () => {
  const s = focused();
  if (!s) return;
  if (!(await askClose(s))) return;
  try {
    await conn.call("session.close", { log_id: s.log_id });
    archiveLoaded = false;
  } catch (e) {
    $("side-error").textContent = e.message;
  }
});

// -- the drawer: the side panel on a phone (base.css, max-width 760px) --------
const closeSide = () => delete root.dataset.side;
// The header's height, for the drawer to start under it: it wraps to two rows
// on a phone and grows with the safe area.
const header = document.querySelector("#a2 > .tabs");
new ResizeObserver(() => root.style.setProperty("--hdr", `${header.getBoundingClientRect().height}px`)).observe(header);
$("side-btn").addEventListener("click", () => {
  if (root.dataset.side === "open") closeSide();
  else root.dataset.side = "open";
});
// The dimmed transcript is the session view's own ::after, so a tap on it
// lands on the view itself and goes no further.
document.querySelector(".v-session").addEventListener("click", (ev) => {
  if (ev.target === ev.currentTarget && root.dataset.side === "open") closeSide();
});

// -- rename in place -------------------------------------------------------------
const editing = new Set();

function editable(elId, field) {
  const node = $(elId);
  node.addEventListener("click", () => {
    const r = route();
    if (editing.has(field) || (r.view !== "session" && r.view !== "read")) return;
    editing.add(field);
    const old = node.textContent;
    const box = document.createElement("input");
    box.className = "rename";
    box.value = field === "title" && old === "untitled" ? "" : old;
    node.replaceChildren(box);
    box.focus();
    box.select();
    const finish = async (save) => {
      if (!editing.has(field)) return;
      editing.delete(field);
      const value = box.value.trim();
      node.textContent = old;
      if (!save || !value || value === old) return;
      try {
        await conn.call("session.rename", { log_id: r.id, [field]: value });
        node.textContent = value;
        archiveLoaded = false;
        $("side-error").textContent = "";
      } catch (e) {
        $("side-error").textContent = e.message;
      }
    };
    box.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") finish(true);
      if (ev.key === "Escape") {
        ev.stopPropagation();
        finish(false);
      }
    });
    box.addEventListener("blur", () => finish(true));
  });
}
editable("s-title", "title");
editable("s-handle", "handle");
