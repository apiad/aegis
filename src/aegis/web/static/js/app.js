// Multi-tab controller for the aegis web client. The session_list stream is
// the source of truth for which tabs exist; event/state/inbox frames route
// by handle to per-tab transcripts. Inactive tabs accrue an unseen marker
// and pulse the document title.
import { WSClient } from "./ws.js";
import { coalesceInto } from "./coalesce.js";
import { renderMarkdown } from "./markdown.js";
import { reconcileTabs, cycleHandle, gotoHandle } from "./tabs.js";

// --- token: read ?t= once, persist, strip from the address bar ---------
const params = new URLSearchParams(location.search);
let token = params.get("t");
if (token) {
  localStorage.setItem("aegis_token", token);
  history.replaceState({}, "", location.pathname);
} else {
  token = localStorage.getItem("aegis_token");
}
const scheme = location.protocol === "https:" ? "wss" : "ws";
const wsUrl = `${scheme}://${location.host}/ws?t=${encodeURIComponent(token || "")}`;

const tabbarEl = document.getElementById("tabbar");
const panesEl = document.getElementById("panes");
const statusDot = document.getElementById("status-dot");
const statusHandle = document.getElementById("status-handle");
const statusMetrics = document.getElementById("status-metrics");
const input = document.getElementById("input");
const modalRoot = document.getElementById("modal-root");

const tabs = new Map();      // handle -> Tab
let activeHandle = null;
let client = null;
let pendingActivate = null;  // activate this handle once its tab appears
let modalClose = null;       // closes the open modal, or null

// the "+" button lives at the end of the tabbar; chips insert before it
const plusBtn = document.createElement("button");
plusBtn.id = "tab-add";
plusBtn.textContent = "+";
plusBtn.title = "New agent (Alt+N)";
plusBtn.addEventListener("click", () => openPicker());
tabbarEl.appendChild(plusBtn);

// --- rendering ---------------------------------------------------------

function nodeFromHtml(html) {
  const tpl = document.createElement("template");
  tpl.innerHTML = html.trim();
  return tpl.content.firstElementChild;
}
function textBlock(rec) {
  const div = document.createElement("div");
  div.className = rec.event_type === "AssistantThinking"
    ? "thinking muted" : "assistant-text";
  div.textContent = rec.text;
  return div;
}
function blockEl(rec) {
  if (rec.event_type === "AssistantText") {
    const div = document.createElement("div");
    div.className = "assistant-text";
    div.innerHTML = renderMarkdown(rec.text);
    return div;
  }
  if (rec.html) return nodeFromHtml(rec.html) || textBlock(rec);
  return textBlock(rec);
}
function nearBottom(el) {
  return el.scrollHeight - el.scrollTop - el.clientHeight < 48;
}
function renderInto(tab, frame) {
  const stick = nearBottom(tab.transcriptEl);
  const { action, index } = coalesceInto(tab.blocks, frame);
  const rec = tab.blocks[index];
  if (action === "append") {
    const node = blockEl(rec);
    tab.nodes[index] = node;
    tab.transcriptEl.appendChild(node);
  } else {
    const node = tab.nodes[index];
    if (node) {
      if (rec.event_type === "AssistantText") node.innerHTML = renderMarkdown(rec.text);
      else node.textContent = rec.text;
    }
  }
  if (stick) tab.transcriptEl.scrollTop = tab.transcriptEl.scrollHeight;
}

function showError(text) {
  const tab = tabs.get(activeHandle);
  const div = document.createElement("div");
  div.className = "tool-result error";
  div.textContent = text;
  (tab ? tab.transcriptEl : panesEl).appendChild(div);
}

// --- tab lifecycle -----------------------------------------------------

function createTab(handle, agent) {
  if (tabs.has(handle)) return tabs.get(handle);

  const paneEl = document.createElement("div");
  paneEl.className = "pane hidden";
  const transcriptEl = document.createElement("div");
  transcriptEl.className = "transcript";
  paneEl.appendChild(transcriptEl);
  panesEl.appendChild(paneEl);

  const chipEl = document.createElement("div");
  chipEl.className = "chip";
  const dotEl = document.createElement("span");
  dotEl.className = "dot";
  const label = document.createElement("span");
  label.className = "chip-label";
  label.textContent = handle;
  const closeEl = document.createElement("span");
  closeEl.className = "close";
  closeEl.textContent = "×";
  chipEl.append(dotEl, label, closeEl);
  tabbarEl.insertBefore(chipEl, plusBtn);

  const tab = {
    handle, agent, blocks: [], nodes: [], paneEl, transcriptEl, chipEl,
    dotEl, state: "ready", metrics: "", unseen: false,
  };
  tabs.set(handle, tab);

  chipEl.addEventListener("click", (e) => {
    if (e.target === closeEl) { e.stopPropagation(); closeTab(handle); }
    else activateTab(handle);
  });

  client.subscribe(handle);
  return tab;
}

function removeTab(handle) {
  const tab = tabs.get(handle);
  if (!tab) return;
  tab.paneEl.remove();
  tab.chipEl.remove();
  tabs.delete(handle);
  if (activeHandle === handle) {
    activeHandle = null;
    const next = tabs.keys().next().value;
    if (next) activateTab(next);
    else {
      statusHandle.textContent = "—";
      statusDot.className = "dot";
      statusMetrics.textContent = "";
    }
  }
  updateTitle();
}

function activateTab(handle) {
  const tab = tabs.get(handle);
  if (!tab) return;
  activeHandle = handle;
  for (const t of tabs.values()) {
    t.paneEl.classList.toggle("hidden", t !== tab);
    t.chipEl.classList.toggle("active", t === tab);
  }
  tab.unseen = false;
  tab.chipEl.classList.remove("unseen");
  statusHandle.textContent = handle;
  statusDot.className = "dot " + (tab.state || "");
  statusMetrics.textContent = tab.metrics || "";
  updateTitle();
  input.focus();
  tab.transcriptEl.scrollTop = tab.transcriptEl.scrollHeight;
}

function closeTab(handle) {
  const tab = tabs.get(handle);
  if (!tab) return;
  if (tab.state === "working"
      && !confirm(`Close ${handle}? It is still working.`)) return;
  client.rpc("close_session", { handle })
    .catch((e) => showError("close failed: " + e.message));
  // the tab is removed when the session_list broadcast lands
}

function updateTitle() {
  const anyUnseen = [...tabs.values()].some((t) => t.unseen);
  document.title = (anyUnseen ? "* " : "") + "aegis";
}

function markUnseen(tab) {
  if (tab.handle !== activeHandle) {
    tab.unseen = true;
    tab.chipEl.classList.add("unseen");
    updateTitle();
  }
}

// --- frame routing -----------------------------------------------------

function onEvent(frame) {
  const tab = tabs.get(frame.handle);
  if (!tab) return;
  renderInto(tab, frame);
  markUnseen(tab);
}
function onState(frame) {
  const tab = tabs.get(frame.handle);
  if (!tab) return;
  tab.state = frame.state || "";
  tab.metrics = frame.metrics || "";
  tab.dotEl.className = "dot " + tab.state;
  if (tab.handle === activeHandle) {
    statusDot.className = "dot " + tab.state;
    statusMetrics.textContent = tab.metrics;
  }
}
function onInbox(frame) {
  const tab = tabs.get(frame.handle);
  if (!tab) return;
  const msg = frame.msg || {};
  const div = document.createElement("div");
  div.className = "inbox";
  div.textContent = `✉ from ${msg.sender || "?"}: ${msg.body || ""}`;
  tab.transcriptEl.appendChild(div);
  markUnseen(tab);
}
function onWindowReset(frame) {
  const tab = tabs.get(frame.handle);
  if (!tab) return;
  tab.blocks.length = 0;
  tab.nodes.length = 0;
  tab.transcriptEl.replaceChildren();
}
function onSessionList(frame) {
  const { added, removed } = reconcileTabs([...tabs.keys()], frame.sessions || []);
  for (const h of removed) removeTab(h);
  for (const s of added) createTab(s.handle, s.agent_slug);
  if (pendingActivate && tabs.has(pendingActivate)) {
    activateTab(pendingActivate);
    pendingActivate = null;
  } else if (!activeHandle && tabs.size) {
    activateTab(tabs.keys().next().value);
  }
}

// --- agent picker modal ------------------------------------------------

async function openPicker() {
  if (modalClose) return;
  const overlay = document.createElement("div");
  overlay.className = "modal-overlay";
  const box = document.createElement("div");
  box.className = "modal";
  const title = document.createElement("div");
  title.className = "modal-title";
  title.textContent = "New agent";
  const list = document.createElement("div");
  list.className = "agent-list";
  box.append(title, list);
  overlay.appendChild(box);
  modalRoot.appendChild(overlay);
  modalClose = () => { overlay.remove(); modalClose = null; };
  overlay.addEventListener("click", (e) => { if (e.target === overlay) modalClose(); });

  try {
    const { agents } = await client.rpc("list_agents");
    for (const a of (agents || [])) {
      const item = document.createElement("div");
      item.className = "agent-item";
      item.textContent = a;
      item.addEventListener("click", async () => {
        modalClose();
        try {
          const r = await client.rpc("spawn_session", { agent_profile: a });
          pendingActivate = r.handle;
        } catch (e) { showError("spawn failed: " + e.message); }
      });
      list.appendChild(item);
    }
  } catch (e) {
    showError("list_agents failed: " + e.message);
    if (modalClose) modalClose();
  }
}

async function spawnDefault() {
  try {
    const { agents } = await client.rpc("list_agents");
    if (!agents || !agents.length) { showError("no agents configured"); return; }
    const r = await client.rpc("spawn_session", { agent_profile: agents[0] });
    pendingActivate = r.handle;
  } catch (e) { showError("spawn failed: " + e.message); }
}

function navTab(dir) {
  const next = cycleHandle([...tabs.keys()], activeHandle, dir);
  if (next) activateTab(next);
}
function gotoTab(n) {
  const h = gotoHandle([...tabs.keys()], n);
  if (h) activateTab(h);
}

// --- input + keys ------------------------------------------------------

function wireComposer() {
  const autogrow = () => {
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 200) + "px";
  };
  input.addEventListener("input", autogrow);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      const text = input.value.trim();
      if (text && activeHandle) {
        client.rpc("deliver", { handle: activeHandle, message: text })
          .catch((err) => showError("deliver failed: " + err.message));
        input.value = "";
        autogrow();
      }
    }
  });
}

function wireKeys() {
  // App shortcuts use Alt — Ctrl chords (Ctrl+T/N/W/Tab/1-9) are reserved by
  // the browser and never reach the page. We key off e.code (layout- and
  // Alt-compose-independent). Esc stays plain.
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      if (modalClose) { modalClose(); return; }
      if (document.activeElement !== input && activeHandle) {
        client.rpc("interrupt_session", { handle: activeHandle });
      }
      return;
    }
    if (!e.altKey || e.ctrlKey || e.metaKey) return;
    const code = e.code;
    if (code === "KeyN") { e.preventDefault(); openPicker(); }
    else if (code === "KeyT") { e.preventDefault(); spawnDefault(); }
    else if (code === "KeyW") { e.preventDefault(); if (activeHandle) closeTab(activeHandle); }
    else if (code === "KeyJ") { e.preventDefault(); navTab(1); }
    else if (code === "KeyK") { e.preventDefault(); navTab(-1); }
    else if (/^Digit[1-9]$/.test(code)) {
      e.preventDefault();
      gotoTab(Number(code.slice(5)));
    }
  });
}

// --- boot --------------------------------------------------------------

async function boot() {
  client = new WSClient(wsUrl, token);
  client.on("event", onEvent);
  client.on("state", onState);
  client.on("inbox", onInbox);
  client.on("window_reset", onWindowReset);
  client.on("session_list", onSessionList);

  wireComposer();
  wireKeys();

  await client.connect();
  client.subscribeGlobal("session_list");

  // empty-state default: spawn the first configured agent if none exist.
  const { sessions } = await client.rpc("list_sessions");
  if (!sessions || !sessions.length) await spawnDefault();
}

boot().catch((err) => showError("connection error: " + (err && err.message)));
