// The aegis2 client for slice 1: one server, one session, one tab.

import { Connection } from "./protocol.js";
import { Transcript } from "./transcript.js";

const $ = (id) => document.getElementById(id);
const root = $("a2");

// -- the token: from the URL once, then kept for this tab only ----------
const params = new URLSearchParams(location.search);
if (params.has("token")) {
  sessionStorage.setItem("aegis2.token", params.get("token"));
  params.delete("token");
  const rest = params.toString();
  history.replaceState(null, "", location.pathname + (rest ? `?${rest}` : "") + location.hash);
}
const token = sessionStorage.getItem("aegis2.token");

// -- theme ----------------------------------------------------------------
const themePick = $("theme");
themePick.value = document.documentElement.dataset.theme;
themePick.addEventListener("change", () => {
  document.documentElement.dataset.theme = themePick.value;
  localStorage.setItem("aegis2.theme", themePick.value);
});

// -- state ----------------------------------------------------------------
let session = null; // the `session` channel's value
let unsubTranscript = null;
let transcriptOf = null;
let workingSince = null;
const transcript = new Transcript($("tr"), $("entries"), $("jump"));

function show(view, text) {
  root.dataset.view = view;
  if (text) $("boot-text").textContent = text;
}

const conn = new Connection(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`, token, {
  onState(state, server) {
    const dot = $("conn-dot");
    dot.className = `dot ${state === "open" ? "ready" : state === "connecting" ? "ghost" : "err"}`;
    $("conn-text").textContent =
      state === "open" ? server : state === "connecting" ? "connecting" : state === "closed" ? "disconnected, retrying" : state;
    $("tab-srv").textContent = server || "";
    if (state === "unauthorized") show("boot", "The token was refused. Open the URL that `aegis2 serve` printed.");
    if (state === "version") show("boot", "This page and the server speak different protocol versions. Reload the page.");
    if (state === "open") onOpen();
  },
});

if (!token) show("boot", "No token. Open the URL that `aegis2 serve` printed; it carries the token.");
else {
  conn.subscribe("session", onSession, (ops) => {
    for (const op of ops) {
      if ("replace" in op) session = op.replace;
      else if (op.set) session = { ...(session || {}), ...op.set };
    }
    onSession(session);
  });
  conn.connect();
}

// -- spawn view -------------------------------------------------------------
let profiles = [];

async function onOpen() {
  try {
    const r = await conn.call("profiles.list");
    profiles = r.profiles;
    const sel = $("sp-profile");
    sel.replaceChildren(
      ...profiles.map((p) => {
        const o = new Option(p.enabled ? `${p.name}  (${p.model || "default model"})` : `${p.name}  (${p.harness}, not supported yet)`, p.name);
        o.disabled = !p.enabled;
        return o;
      }),
    );
    const first = profiles.find((p) => p.name === r.default && p.enabled) || profiles.find((p) => p.enabled);
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
    await conn.call("session.spawn", params);
  } catch (e) {
    $("sp-error").textContent = e.message;
  } finally {
    $("sp-go").disabled = false;
  }
});

// -- session view ---------------------------------------------------------
function onSession(s) {
  session = s;
  if (!s) {
    if (unsubTranscript) unsubTranscript();
    unsubTranscript = transcriptOf = null;
    transcript.clear();
    $("tab-name").textContent = "aegis2";
    $("tab-dot").className = "dot ghost";
    if (conn.open || !token) show("spawn");
    return;
  }
  if (transcriptOf !== s.log_id) {
    if (unsubTranscript) unsubTranscript();
    transcriptOf = s.log_id;
    unsubTranscript = conn.subscribe(
      `transcript:${s.log_id}`,
      (entries) => {
        transcript.snapshot(entries || []);
        // Read by scripts/bench2.py: when the snapshot was drawn and painted.
        const mark = (window.__a2snapshot = { at: performance.now(), count: (entries || []).length });
        requestAnimationFrame(() => (mark.painted = performance.now()));
      },
      (ops) => transcript.apply(ops),
    );
    $("input").value = localStorage.getItem(`aegis2.draft.${s.log_id}`) || "";
    autosize();
  }
  show("session");
  renderMeta(s);
}

function fmtTokens(n) {
  return n >= 1000 ? `${Math.round(n / 1000)}k` : String(n);
}

function renderMeta(s) {
  $("tab-name").textContent = s.profile;
  $("tab-dot").className = `dot ${s.status === "working" ? "working" : s.status === "error" ? "err" : s.status === "idle" ? "ready" : "ghost"}`;
  $("s-title").textContent = s.profile;
  $("s-model").textContent = `Claude Code, ${s.model}`;
  $("s-status").textContent = s.status;
  $("s-status").className = `st ${s.status === "error" ? "err" : s.status === "idle" ? "idle" : ""}`;
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
  }
  $("s-cost").textContent = `$${(s.cost_usd || 0).toFixed(2)}`;
  const working = s.status === "working";
  $("stop").hidden = !working;
  $("working").hidden = !working;
  if (working && workingSince == null) workingSince = Date.now();
  if (!working) workingSince = null;
  document.title = `${working ? "● " : ""}${s.profile} · aegis2`;
}

setInterval(() => {
  if (workingSince == null) return;
  const secs = Math.round((Date.now() - workingSince) / 1000);
  $("working-meta").textContent = `${secs}s, Esc interrupts`;
}, 500);

// -- composer ---------------------------------------------------------------
const input = $("input");

function autosize() {
  input.style.height = "auto";
  input.style.height = `${Math.min(input.scrollHeight, window.innerHeight * 0.4)}px`;
}

async function send() {
  const text = input.value.trim();
  if (!text || !session) return;
  $("send-error").textContent = "";
  try {
    await conn.call("session.send", { log_id: session.log_id, text });
    input.value = "";
    localStorage.removeItem(`aegis2.draft.${session.log_id}`);
    autosize();
    transcript.toBottom();
  } catch (e) {
    $("send-error").textContent = e.message;
  }
}

async function interrupt() {
  if (!session || session.status !== "working") return;
  try {
    await conn.call("session.interrupt", { log_id: session.log_id });
  } catch (e) {
    $("send-error").textContent = e.message;
  }
}

input.addEventListener("input", () => {
  autosize();
  if (session) localStorage.setItem(`aegis2.draft.${session.log_id}`, input.value);
});
input.addEventListener("keydown", (ev) => {
  if (ev.key === "Enter" && !ev.shiftKey && !ev.isComposing) {
    ev.preventDefault();
    send();
  }
});
document.addEventListener("keydown", (ev) => {
  if (ev.key === "Escape" && root.dataset.view === "session") interrupt();
});
$("send").addEventListener("click", send);
$("stop").addEventListener("click", interrupt);

$("close").addEventListener("click", async () => {
  if (!session) return;
  if (!confirm(`Close ${session.profile}? Its claude process ends; the transcript stays on disk.`)) return;
  try {
    await conn.call("session.close", { log_id: session.log_id });
  } catch (e) {
    $("send-error").textContent = e.message;
  }
});
