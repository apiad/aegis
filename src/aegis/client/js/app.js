// The aegis client: one server, many sessions.
//
// The tabs are the server's open sessions (the `sessions` channel), the same in
// every browser. Their order and which one is focused belong to this browser:
// the order in localStorage, the focus in the URL hash.

import { Connection } from "./protocol.js";
import { Transcript } from "./transcript.js";
import { TabOrder, renderTabs } from "./tabs.js";
import { ago, money, renderArchive, renderCards } from "./fleet.js";

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
    if (state === "open") loadProfiles();
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
        if (op.upsert) sessions.set(op.upsert.log_id, op.upsert);
        else if (op.remove !== undefined) sessions.delete(op.remove);
      }
      onSessions();
    },
  );
  conn.connect();
}

function onSessions() {
  const ids = order.arrange([...sessions.values()].sort((a, b) => a.created_at - b.created_at).map((m) => m.log_id));
  ordered = ids.map((id) => sessions.get(id));
  render();
}

// -- rendering -----------------------------------------------------------------
function render() {
  if (!token || !booted) return;
  const r = route();
  if (r.view === "session" && !sessions.has(r.id)) {
    go("#fleet"); // closed here or elsewhere
    return;
  }
  renderTabs($("tablist"), ordered, r.view === "session" ? r.id : null, {
    onFocus: (id) => go(`#s=${id}`),
    onMove: (id, before) => {
      order.move(id, before);
      onSessions();
    },
  });
  $("tab-fleet").classList.toggle("on", r.view === "fleet");
  $("tab-add").classList.toggle("on", r.view === "spawn");
  root.dataset.mode = r.view === "read" ? "read" : "live";
  if (r.view === "fleet") {
    follow(null);
    show("fleet");
    renderCards($("cards"), ordered, (id) => go(`#s=${id}`));
    if (!archiveLoaded) loadArchive();
    document.title = "Fleet · aegis";
  } else if (r.view === "spawn") {
    follow(null);
    show("spawn");
    document.title = "New session · aegis";
  } else if (r.view === "session") {
    follow(r.id);
    show("session");
    renderMeta(sessions.get(r.id));
  } else {
    follow(r.id);
    show("session");
    const m = archived.find((x) => x.log_id === r.id);
    if (m) renderMeta({ ...m, state: "archived" });
    else if (!archiveLoaded) loadArchive().then(render);
  }
}

function follow(id) {
  if (shown === id) return;
  if (unsubTranscript) unsubTranscript();
  unsubTranscript = null;
  transcript.clear();
  shown = id;
  if (!id) return;
  unsubTranscript = conn.subscribe(
    `transcript:${id}`,
    (entries) => {
      transcript.snapshot(entries || []);
      // Read by scripts/bench2.py: when the snapshot was drawn and painted.
      const mark = (window.__a2snapshot = { at: performance.now(), count: (entries || []).length });
      requestAnimationFrame(() => (mark.painted = performance.now()));
    },
    (ops) => transcript.apply(ops),
  );
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
  $("s-model").textContent = `Claude Code, ${s.model}`;
  $("s-status").textContent = s.state;
  $("s-status").className = `st ${s.state === "error" ? "err" : s.state === "idle" ? "idle" : ""}`;
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
  $("s-monitors").replaceChildren(
    ...mons.map((m) => {
      const box = document.createElement("div");
      box.className = "mon";
      const kv = document.createElement("div");
      kv.className = "kv";
      const name = document.createElement("span");
      name.textContent = m.description;
      const pct = document.createElement("span");
      pct.textContent = m.progress == null ? "watching" : `${m.progress}%`;
      kv.append(name, pct);
      const bar = document.createElement("div");
      bar.className = "bar thin";
      const fill = document.createElement("i");
      fill.style.width = `${m.progress || 0}%`;
      bar.append(fill);
      box.append(kv, bar);
      return box;
    }),
  );
  const working = s.state === "working";
  $("stop").hidden = !working;
  $("working").hidden = !working;
  $("stop-session").disabled = s.state === "stopped";
  if (working && workingSince == null) workingSince = Date.now();
  if (!working) workingSince = null;
  $("input").placeholder =
    s.state === "stopped"
      ? "Stopped; your next message resumes it."
      : "Message the agent. Enter sends, Shift+Enter adds a line, Esc interrupts.";
  document.title = `${working ? "● " : ""}${s.title || s.handle} · aegis`;
}

setInterval(() => {
  if (workingSince != null) $("working-meta").textContent = `${Math.round((Date.now() - workingSince) / 1000)}s, Esc interrupts`;
  if (root.dataset.view === "fleet") for (const c of document.querySelectorAll(".card")) {
    const m = sessions.get(c.dataset.id);
    if (m) c.querySelector(".when").textContent = ago(m.last_activity);
  }
}, 1000);

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

// -- spawn -----------------------------------------------------------------------
let profiles = [];

async function loadProfiles() {
  try {
    const r = await conn.call("profiles.list");
    profiles = r.profiles;
    const sel = $("sp-profile");
    const keep = sel.value;
    sel.replaceChildren(
      ...profiles.map((p) => {
        const o = new Option(p.enabled ? `${p.name}  (${p.model || "default model"})` : `${p.name}  (${p.harness}, not supported yet)`, p.name);
        o.disabled = !p.enabled;
        return o;
      }),
    );
    const first = profiles.find((p) => p.name === keep && p.enabled) || profiles.find((p) => p.name === r.default && p.enabled) || profiles.find((p) => p.enabled);
    if (first) sel.value = first.name;
    if (!$("sp-cwd").value) $("sp-cwd").value = r.cwd;
    fillProfile();
    if (!profiles.length) $("sp-error").textContent = "No profiles: add an agents: map to .aegis.yaml.";
  } catch (e) {
    $("sp-error").textContent = e.message;
  }
}

function fillProfile() {
  const p = profiles.find((x) => x.name === $("sp-profile").value);
  if (!p) return;
  $("sp-model").value = p.model;
  $("sp-effort").value = p.effort;
  $("sp-permission").value = p.permission;
}
$("sp-profile").addEventListener("change", fillProfile);

$("spawn").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const p = profiles.find((x) => x.name === $("sp-profile").value);
  if (!p) return;
  $("sp-go").disabled = true;
  $("sp-error").textContent = "";
  const params = { profile: p.name, cwd: $("sp-cwd").value.trim() || null };
  if ($("sp-model").value.trim() !== p.model) params.model = $("sp-model").value.trim();
  if ($("sp-effort").value !== p.effort) params.effort = $("sp-effort").value;
  if ($("sp-permission").value !== p.permission) params.permission = $("sp-permission").value;
  try {
    const r = await conn.call("session.spawn", params);
    go(`#s=${r.log_id}`);
  } catch (e) {
    $("sp-error").textContent = e.message;
  } finally {
    $("sp-go").disabled = false;
  }
});

// -- tab bar and keys -----------------------------------------------------------
$("tab-fleet").addEventListener("click", () => go("#fleet"));
$("tab-add").addEventListener("click", () => go("#new"));
document.addEventListener("keydown", (ev) => {
  if (ev.altKey && /^Digit[0-9]$/.test(ev.code)) {
    ev.preventDefault();
    const n = Number(ev.code.slice(5));
    if (n === 0) go("#fleet");
    else if (ordered[n - 1]) go(`#s=${ordered[n - 1].log_id}`);
  } else if (ev.key === "Escape" && route().view === "session" && !editing.size) interrupt();
});

// -- composer ---------------------------------------------------------------
const input = $("input");

function autosize() {
  input.style.height = "auto";
  input.style.height = `${Math.min(input.scrollHeight, window.innerHeight * 0.4)}px`;
}

function focused() {
  const r = route();
  return r.view === "session" ? sessions.get(r.id) : null;
}

async function send() {
  const s = focused();
  const text = input.value.trim();
  if (!text || !s) return;
  $("send-error").textContent = "";
  try {
    await conn.call("session.send", { log_id: s.log_id, text });
    input.value = "";
    localStorage.removeItem(`aegis.draft.${s.log_id}`);
    autosize();
    transcript.toBottom();
  } catch (e) {
    $("send-error").textContent = e.message;
  }
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

input.addEventListener("input", () => {
  autosize();
  if (shown) localStorage.setItem(`aegis.draft.${shown}`, input.value);
});
input.addEventListener("keydown", (ev) => {
  if (ev.key === "Enter" && !ev.shiftKey && !ev.isComposing) {
    ev.preventDefault();
    send();
  }
});
$("send").addEventListener("click", send);
$("stop").addEventListener("click", interrupt);

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
  if (!confirm(`Close ${s.title || s.handle}? Its tab goes away in every browser; it stays in the archive.`)) return;
  try {
    await conn.call("session.close", { log_id: s.log_id });
    archiveLoaded = false;
  } catch (e) {
    $("side-error").textContent = e.message;
  }
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
