// Multi-tab controller for the aegis web client. The session_list stream is
// the source of truth for which tabs exist; event/state/inbox frames route
// by handle to per-tab transcripts. Inactive tabs accrue an unseen marker
// and pulse the document title.
import { WSClient } from "./ws.js";
import { coalesceInto } from "./coalesce.js";
import { renderMarkdown } from "./markdown.js";
import { renderEvent } from "./renderEvent.js";
import { reconcileTabs, cycleHandle, gotoHandle, swipeDirection } from "./tabs.js";
import { formatStrip } from "./queues.js";

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

// --- theme: apply the persisted choice immediately ---------------------
const THEME_KEY = "aegis_theme";
function applyTheme(name) {
  const link = document.getElementById("theme-link");
  if (link) link.href = "/theme.css?name=" + encodeURIComponent(name);
  localStorage.setItem(THEME_KEY, name);
}
const savedTheme = localStorage.getItem(THEME_KEY);
if (savedTheme) applyTheme(savedTheme);

const tabbarEl = document.getElementById("tabbar");
const panesEl = document.getElementById("panes");
const statusDot = document.getElementById("status-dot");
const statusHandle = document.getElementById("status-handle");
const statusMetrics = document.getElementById("status-metrics");
const input = document.getElementById("input");
const modalRoot = document.getElementById("modal-root");
const queuestripEl = document.getElementById("queuestrip");

const tabs = new Map();      // handle -> Tab
let activeHandle = null;
let client = null;

// --- PWA install button (registered early so beforeinstallprompt is caught) -
let _deferredInstall = null;
const installBtn = document.getElementById("install-btn");
window.addEventListener("beforeinstallprompt", (e) => {
  e.preventDefault();
  _deferredInstall = e;
  if (installBtn) installBtn.hidden = false;
});
if (installBtn) installBtn.addEventListener("click", async () => {
  if (!_deferredInstall) return;
  _deferredInstall.prompt();
  await _deferredInstall.userChoice;
  _deferredInstall = null;
  installBtn.hidden = true;
});
window.addEventListener("appinstalled", () => {
  if (installBtn) installBtn.hidden = true;
});
let pendingActivate = null;  // activate this handle once its tab appears
let modalClose = null;       // closes the open modal, or null
let latestDigest = { queues: [], tasks: [], last_started: null };
let dashboardBody = null;    // set while the queue dashboard is open

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
  const html = renderEvent(rec);
  return html ? (nodeFromHtml(html) || textBlock(rec)) : textBlock(rec);
}
function nearBottom(el) {
  return el.scrollHeight - el.scrollTop - el.clientHeight < 48;
}
function mountCommandBlock(handle, cr) {
  const tab = tabs.get(handle);
  if (!tab) return;
  const stick = nearBottom(tab.transcriptEl);
  const div = document.createElement("div");
  div.className = "command-block" + (cr.ok ? "" : " error");
  const head = document.createElement("div");
  head.className = "command-title";
  head.textContent = "/ " + cr.title;
  div.appendChild(head);
  if (cr.body) {
    const body = document.createElement("pre");
    body.className = "command-body";
    body.textContent = cr.body;
    div.appendChild(body);
  }
  tab.transcriptEl.appendChild(div);
  if (stick) tab.transcriptEl.scrollTop = tab.transcriptEl.scrollHeight;
}
function applyCommandEffect(handle, effect) {
  if (!effect) return;
  if (effect.kind === "theme") {
    applyTheme(effect.name);
  } else if (effect.kind === "clear") {
    const tab = tabs.get(handle);
    if (!tab) return;
    tab.blocks.length = 0;
    tab.nodes.length = 0;
    tab.transcriptEl.innerHTML = "";
    const div = document.createElement("div");
    div.className = "command-block cleared-marker";
    div.textContent = "──── transcript cleared ────";
    tab.transcriptEl.appendChild(div);
  }
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
      if (rec.event_type === "AssistantText") {
        node.innerHTML = renderMarkdown(rec.text);
      } else if (rec.event_type === "ToolUse") {
        // A folded ToolResult arrived — re-render the whole call block.
        const fresh = blockEl(rec);
        tab.nodes[index] = fresh;
        node.replaceWith(fresh);
      } else {
        node.textContent = rec.text;
      }
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

// --- tap-to-expand truncated blocks ------------------------------------

const detailCache = new Map();   // `${handle}:${seq}` -> full event dict

function fullBody(event) {
  if (event.t === "ToolUse") return JSON.stringify(event.raw_input ?? {}, null, 2);
  return event.text || "";       // ToolResult / AssistantThinking
}

// Click a subagent box header to collapse/expand its body.
panesEl.addEventListener("click", (e) => {
  const head = e.target.closest(".subagent-header");
  if (!head) return;
  const box = head.closest(".subagent");
  if (box) box.toggleAttribute("data-collapsed");
});

panesEl.addEventListener("click", async (e) => {
  const ctl = e.target.closest(".expand");
  if (!ctl) return;
  const handle = ctl.dataset.handle;
  const seq = Number(ctl.dataset.seq);
  const block = ctl.closest(".tool-use, .tool-result, .thinking");
  if (!block) return;
  const existing = block.parentElement.querySelector(
    `pre.expanded[data-seq="${seq}"]`);
  if (existing) { existing.remove(); return; }   // toggle off
  const key = `${handle}:${seq}`;
  let ev = detailCache.get(key);
  if (!ev) {
    ctl.classList.add("loading");
    try { ev = (await client.getEvent(handle, seq)).event; }
    finally { ctl.classList.remove("loading"); }
    if (!ev) return;
    detailCache.set(key, ev);
  }
  const pre = document.createElement("pre");
  pre.className = "expanded";
  pre.dataset.seq = String(seq);
  pre.textContent = fullBody(ev);
  block.insertAdjacentElement("afterend", pre);
});

// --- swipe between agents (mobile conversation view) -------------------

let _touchX = 0, _touchY = 0;
panesEl.addEventListener("touchstart", (e) => {
  const t = e.changedTouches[0]; _touchX = t.clientX; _touchY = t.clientY;
}, { passive: true });
panesEl.addEventListener("touchend", (e) => {
  const t = e.changedTouches[0];
  const dir = swipeDirection(t.clientX - _touchX, t.clientY - _touchY);
  if (!dir) return;
  const next = cycleHandle([...tabs.keys()], activeHandle, dir);
  if (next && next !== activeHandle) activateTab(next);
}, { passive: true });

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
  document.getElementById("app").classList.add("conversation");
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
const connBanner = document.getElementById("conn-banner");
function onConnection(frame) {
  if (connBanner) connBanner.hidden = frame.connected;
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

// --- queue strip + dashboard -------------------------------------------

function onQueueDigest(frame) {
  latestDigest = frame;
  renderStrip();
  if (dashboardBody) renderDashboardBody();
}

function renderStrip() {
  const parts = formatStrip(latestDigest.queues);
  if (!parts.length) { queuestripEl.style.display = "none"; return; }
  queuestripEl.style.display = "block";
  queuestripEl.textContent = parts.join("   ");
}

function taskRow(t) {
  const row = document.createElement("div");
  row.className = "qd-task " + t.state;
  const jumpable = t.worker_handle && tabs.has(t.worker_handle);
  if (jumpable) row.classList.add("jumpable");
  row.textContent =
    `${t.queue} · ${t.payload_summary} · ${t.worker_handle || "—"} · ${t.state}`;
  row.addEventListener("click", async () => {
    if (jumpable) { if (modalClose) modalClose(); activateTab(t.worker_handle); return; }
    try {
      const { lines } = await client.rpc("queue_tail", { task_id: t.task_id });
      showTail(row, lines);
    } catch { /* ignore */ }
  });
  return row;
}

function showTail(afterRow, lines) {
  const existing = afterRow.nextElementSibling;
  if (existing && existing.classList.contains("qd-tail")) { existing.remove(); return; }
  const tail = document.createElement("div");
  tail.className = "qd-tail muted";
  tail.textContent = (lines && lines.length) ? lines.join("\n") : "(no output yet)";
  afterRow.after(tail);
}

function band(title, tasks) {
  const wrap = document.createElement("div");
  wrap.className = "qd-band";
  const h = document.createElement("div");
  h.className = "qd-band-title";
  h.textContent = `${title} (${tasks.length})`;
  wrap.appendChild(h);
  for (const t of tasks) wrap.appendChild(taskRow(t));
  return wrap;
}

function renderDashboardBody() {
  if (!dashboardBody) return;
  dashboardBody.replaceChildren();
  const qline = document.createElement("div");
  qline.className = "qd-queues";
  qline.textContent = formatStrip(latestDigest.queues).join("   ") || "no queues";
  dashboardBody.appendChild(qline);
  const tasks = latestDigest.tasks || [];
  dashboardBody.appendChild(band("IN-FLIGHT", tasks.filter((t) => t.state === "running")));
  dashboardBody.appendChild(band("QUEUED", tasks.filter((t) => t.state === "queued")));
  dashboardBody.appendChild(band("RECENT",
    tasks.filter((t) => t.state === "ok" || t.state === "err")));
}

function openDashboard() {
  if (modalClose) return;
  const overlay = document.createElement("div");
  overlay.className = "modal-overlay";
  const box = document.createElement("div");
  box.className = "modal qd-modal";
  const title = document.createElement("div");
  title.className = "modal-title";
  title.textContent = "Queues";
  dashboardBody = document.createElement("div");
  dashboardBody.className = "qd-body";
  box.append(title, dashboardBody);
  overlay.appendChild(box);
  modalRoot.appendChild(overlay);
  modalClose = () => { overlay.remove(); modalClose = null; dashboardBody = null; };
  overlay.addEventListener("click", (e) => { if (e.target === overlay) modalClose(); });
  renderDashboardBody();
}
function gotoTab(n) {
  const h = gotoHandle([...tabs.keys()], n);
  if (h) activateTab(h);
}

// --- group dashboard (poll-on-open) ------------------------------------

let groupBody = null;
let groupTimer = null;

function groupBlock(g) {
  const wrap = document.createElement("div");
  wrap.className = "gd-group";
  const h = document.createElement("div");
  h.className = "gd-group-title";
  h.textContent = `${g.name} (${(g.members || []).length})`;
  wrap.appendChild(h);
  for (const m of (g.members || [])) {
    const row = document.createElement("div");
    row.className = "gd-member" + (tabs.has(m.handle) ? " jumpable" : "");
    const dot = document.createElement("span");
    dot.className = "dot " + (m.state || "");
    const label = document.createElement("span");
    label.textContent = ` ${m.handle} · ${m.profile}`;
    row.append(dot, label);
    if (tabs.has(m.handle)) {
      row.addEventListener("click", () => {
        if (modalClose) modalClose();
        activateTab(m.handle);
      });
    }
    wrap.appendChild(row);
  }
  const cb = g.current_broadcast;
  const cbEl = document.createElement("div");
  cbEl.className = "gd-broadcast muted";
  cbEl.textContent = cb ? `▶ ${cb.objective} (${cb.started_at})` : "(idle)";
  wrap.appendChild(cbEl);
  return wrap;
}

async function refreshGroups() {
  if (!groupBody) return;
  try {
    const { groups } = await client.rpc("group_status");
    if (!groupBody) return;
    groupBody.replaceChildren();
    if (!groups || !groups.length) {
      const e = document.createElement("div");
      e.className = "muted";
      e.textContent = "no groups";
      groupBody.appendChild(e);
      return;
    }
    for (const g of groups) groupBody.appendChild(groupBlock(g));
  } catch { /* ignore */ }
}

function openGroupDashboard() {
  if (modalClose) return;
  const overlay = document.createElement("div");
  overlay.className = "modal-overlay";
  const box = document.createElement("div");
  box.className = "modal qd-modal";
  const title = document.createElement("div");
  title.className = "modal-title";
  title.textContent = "Groups";
  groupBody = document.createElement("div");
  groupBody.className = "gd-body";
  box.append(title, groupBody);
  overlay.appendChild(box);
  modalRoot.appendChild(overlay);
  modalClose = () => {
    overlay.remove();
    modalClose = null;
    groupBody = null;
    if (groupTimer) { clearInterval(groupTimer); groupTimer = null; }
  };
  overlay.addEventListener("click", (e) => { if (e.target === overlay) modalClose(); });
  refreshGroups();
  groupTimer = setInterval(refreshGroups, 2000);
}

// --- file picker + viewer ----------------------------------------------

let fileSearchTimer = null;

function openFilePicker() {
  if (modalClose) return;
  const overlay = document.createElement("div");
  overlay.className = "modal-overlay";
  const box = document.createElement("div");
  box.className = "modal file-picker";
  const inp = document.createElement("input");
  inp.className = "file-search";
  inp.placeholder = "Find file…";
  const list = document.createElement("div");
  list.className = "file-results";
  box.append(inp, list);
  overlay.appendChild(box);
  modalRoot.appendChild(overlay);
  modalClose = () => {
    overlay.remove();
    modalClose = null;
    if (fileSearchTimer) { clearTimeout(fileSearchTimer); fileSearchTimer = null; }
  };
  overlay.addEventListener("click", (e) => { if (e.target === overlay) modalClose(); });

  const search = async () => {
    try {
      const { paths } = await client.rpc("file_search", { query: inp.value });
      list.replaceChildren();
      for (const p of (paths || [])) {
        const row = document.createElement("div");
        row.className = "file-result";
        row.textContent = p;
        row.addEventListener("click", () => { modalClose(); openFileViewer(p); });
        list.appendChild(row);
      }
    } catch { /* ignore */ }
  };
  inp.addEventListener("input", () => {
    if (fileSearchTimer) clearTimeout(fileSearchTimer);
    fileSearchTimer = setTimeout(search, 120);
  });
  inp.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      const first = list.querySelector(".file-result");
      if (first) first.click();
    }
  });
  search();
  inp.focus();
}

async function openFileViewer(path) {
  if (modalClose) return;
  let data;
  try { data = await client.rpc("file_read", { path }); }
  catch (e) { showError("file_read failed: " + e.message); return; }

  const overlay = document.createElement("div");
  overlay.className = "modal-overlay";
  const box = document.createElement("div");
  box.className = "modal file-modal";
  const header = document.createElement("div");
  header.className = "file-header";
  header.textContent = `${path}  ·  ${data.kind || ""}`;
  const body = document.createElement("div");
  body.className = "file-body";

  if (data.error) {
    body.classList.add("muted");
    body.textContent = data.error;
  } else if (data.kind === "markdown") {
    body.classList.add("assistant-text");
    body.innerHTML = renderMarkdown(data.content);
  } else if (data.kind === "html") {
    const frame = document.createElement("iframe");
    frame.className = "file-frame";
    frame.setAttribute("sandbox", "");   // render natively, no scripts
    frame.srcdoc = data.content;
    body.appendChild(frame);
  } else {
    const pre = document.createElement("pre");
    pre.className = "file-source";
    pre.textContent = data.content;
    body.appendChild(pre);
  }

  box.append(header, body);
  overlay.appendChild(box);
  modalRoot.appendChild(overlay);
  modalClose = () => { overlay.remove(); modalClose = null; };
  overlay.addEventListener("click", (e) => { if (e.target === overlay) modalClose(); });
}

// --- theme picker (Alt+Y) ----------------------------------------------

async function openThemePicker() {
  if (modalClose) return;
  let names = [];
  try { const r = await client.rpc("list_themes"); names = r.names || []; }
  catch { names = []; }
  const current = localStorage.getItem(THEME_KEY) || "aegis-ink";

  const overlay = document.createElement("div");
  overlay.className = "modal-overlay";
  const box = document.createElement("div");
  box.className = "modal";
  const title = document.createElement("div");
  title.className = "modal-title";
  title.textContent = "Theme";
  const list = document.createElement("div");
  list.className = "agent-list";
  for (const name of names) {
    const item = document.createElement("div");
    item.className = "agent-item" + (name === current ? " current" : "");
    item.textContent = name + (name === current ? "  ✓" : "");
    item.addEventListener("click", () => { applyTheme(name); modalClose(); });
    list.appendChild(item);
  }
  box.append(title, list);
  overlay.appendChild(box);
  modalRoot.appendChild(overlay);
  modalClose = () => { overlay.remove(); modalClose = null; };
  overlay.addEventListener("click", (e) => { if (e.target === overlay) modalClose(); });
}

// --- config panel (F2) — view + add/remove agents & queues -------------

function cfgInput(placeholder, width) {
  const i = document.createElement("input");
  i.className = "cfg-input";
  i.placeholder = placeholder;
  if (width) i.style.width = width;
  return i;
}
function cfgSelect(options) {
  const s = document.createElement("select");
  s.className = "cfg-input";
  for (const o of options) {
    const opt = document.createElement("option");
    opt.value = opt.textContent = o;
    s.appendChild(opt);
  }
  return s;
}
function cfgErr(afterEl, msg) {
  const e = document.createElement("div");
  e.className = "cfg-err err";
  e.textContent = msg;
  afterEl.after(e);
  setTimeout(() => e.remove(), 4000);
}

async function refreshConfig(body) {
  let cfg;
  try { cfg = await client.rpc("config_show"); }
  catch (e) { body.textContent = "config_show failed: " + e.message; return; }
  body.replaceChildren();
  const edit = async (method, params, el) => {
    const r = await client.rpc(method, params).catch((e) => ({ error: e.message }));
    if (r && r.error) cfgErr(el, r.error);
    else refreshConfig(body);
  };

  // AGENTS
  const agBand = document.createElement("div");
  agBand.className = "cfg-band";
  const agH = document.createElement("div");
  agH.className = "cfg-band-title";
  agH.textContent = `AGENTS (${cfg.agents.length})`;
  agBand.appendChild(agH);
  for (const a of cfg.agents) {
    const row = document.createElement("div");
    row.className = "cfg-row";
    const rm = document.createElement("span");
    rm.className = "cfg-rm";
    rm.textContent = "×";
    rm.title = "remove";
    rm.addEventListener("click", () => edit("config_remove_agent", { slug: a.slug }, row));
    const txt = document.createElement("span");
    txt.textContent = ` ${a.slug} · ${a.model || a.harness} · ${a.effort || ""} · ${a.permission || ""}`;
    row.append(rm, txt);
    agBand.appendChild(row);
  }
  const agForm = document.createElement("div");
  agForm.className = "cfg-form";
  const aSlug = cfgInput("slug", "6rem");
  const aProv = cfgSelect(["claude-code", "gemini", "opencode"]);
  const aModel = cfgInput("model", "6rem");
  const aEff = cfgInput("effort", "5rem");
  const aPerm = cfgInput("permission", "6rem");
  const aAdd = document.createElement("button");
  aAdd.className = "cfg-add";
  aAdd.textContent = "+ agent";
  aAdd.addEventListener("click", () => {
    if (!aSlug.value.trim() || !aModel.value.trim()) { cfgErr(agForm, "slug + model required"); return; }
    edit("config_add_agent", {
      slug: aSlug.value.trim(), provider: aProv.value, model: aModel.value.trim(),
      effort: aEff.value.trim() || null, permission: aPerm.value.trim() || null,
    }, agForm);
  });
  agForm.append(aSlug, aProv, aModel, aEff, aPerm, aAdd);
  agBand.appendChild(agForm);
  body.appendChild(agBand);

  // QUEUES
  const qBand = document.createElement("div");
  qBand.className = "cfg-band";
  const qH = document.createElement("div");
  qH.className = "cfg-band-title";
  qH.textContent = `QUEUES (${cfg.queues.length})`;
  qBand.appendChild(qH);
  for (const q of cfg.queues) {
    const row = document.createElement("div");
    row.className = "cfg-row";
    const rm = document.createElement("span");
    rm.className = "cfg-rm";
    rm.textContent = "×";
    rm.title = "remove";
    rm.addEventListener("click", () => edit("config_remove_queue", { name: q.name }, row));
    const txt = document.createElement("span");
    txt.textContent = ` ${q.name} · ${q.agent} · ×${q.max_parallel}`;
    row.append(rm, txt);
    qBand.appendChild(row);
  }
  const qForm = document.createElement("div");
  qForm.className = "cfg-form";
  const qName = cfgInput("name", "6rem");
  const qAgent = cfgSelect(cfg.agents.map((a) => a.slug));
  const qPar = cfgInput("×N", "3rem");
  qPar.value = "1";
  const qAdd = document.createElement("button");
  qAdd.className = "cfg-add";
  qAdd.textContent = "+ queue";
  qAdd.addEventListener("click", () => {
    if (!qName.value.trim() || !qAgent.value) { cfgErr(qForm, "name + agent required"); return; }
    edit("config_add_queue", {
      name: qName.value.trim(), agent: qAgent.value,
      max_parallel: Number(qPar.value) || 1,
    }, qForm);
  });
  qForm.append(qName, qAgent, qPar, qAdd);
  qBand.appendChild(qForm);
  body.appendChild(qBand);

  // SCHEDULES (read-only)
  const scBand = document.createElement("div");
  scBand.className = "cfg-band";
  const scH = document.createElement("div");
  scH.className = "cfg-band-title";
  scH.textContent = `SCHEDULES (${cfg.schedules.length})`;
  scBand.appendChild(scH);
  for (const s of cfg.schedules) {
    const row = document.createElement("div");
    row.className = "cfg-row";
    row.textContent = `${s.name} · ${s.cron || ""} · ${s.workflow || ""}${s.enabled === false ? " · off" : ""}`;
    scBand.appendChild(row);
  }
  body.appendChild(scBand);
}

async function openConfigPanel() {
  if (modalClose) return;
  const overlay = document.createElement("div");
  overlay.className = "modal-overlay";
  const box = document.createElement("div");
  box.className = "modal cfg-modal";
  const title = document.createElement("div");
  title.className = "modal-title";
  title.textContent = "Config";
  const body = document.createElement("div");
  body.className = "cfg-body";
  box.append(title, body);
  overlay.appendChild(box);
  modalRoot.appendChild(overlay);
  modalClose = () => { overlay.remove(); modalClose = null; };
  overlay.addEventListener("click", (e) => { if (e.target === overlay) modalClose(); });
  refreshConfig(body);
}

// --- input + keys ------------------------------------------------------

const paletteEl = document.createElement("div");
paletteEl.id = "palette";
paletteEl.style.display = "none";
let palItems = [];
let palIdx = 0;

function renderPalette(items) {
  palItems = items || [];
  palIdx = 0;
  paletteEl.innerHTML = "";
  if (!palItems.length) { paletteEl.style.display = "none"; return; }
  palItems.forEach((it, i) => {
    const row = document.createElement("div");
    row.className = "palette-row" + (i === 0 ? " current" : "")
                    + " pl-source-" + (it.source || "builtin");
    const label = document.createElement("span");
    label.className = "pl-label";
    label.textContent = it.label;
    const detail = document.createElement("span");
    detail.className = "pl-detail";
    detail.textContent = it.detail || "";
    row.append(label, detail);
    row.addEventListener("mousedown", (e) => { e.preventDefault(); acceptPalette(i); });
    paletteEl.appendChild(row);
  });
  paletteEl.style.display = "block";
}

function movePalette(delta) {
  if (!palItems.length) return;
  paletteEl.children[palIdx].classList.remove("current");
  palIdx = (palIdx + delta + palItems.length) % palItems.length;
  paletteEl.children[palIdx].classList.add("current");
}

function acceptPalette(i) {
  const it = palItems[i];
  if (!it) return;
  const v = input.value;
  if (v.startsWith("/") && !v.includes(" ")) {
    input.value = it.insert;                      // completing the verb
  } else {
    const head = v.includes(" ") ? v.slice(0, v.lastIndexOf(" ")) : "";
    input.value = (head ? head + " " : "") + it.insert;
  }
  refreshPalette();
  input.focus();
}

function refreshPalette() {
  const v = input.value;
  if (!v.startsWith("/") || !activeHandle) { renderPalette([]); return; }
  client.rpc("complete", { message: v })
    .then((res) => renderPalette(res.items))
    .catch(() => renderPalette([]));
}

function wireComposer() {
  if (paletteEl.parentElement === null) {
    input.parentElement.insertBefore(paletteEl, input);   // drop-up: above input
  }
  const autogrow = () => {
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 200) + "px";
  };
  input.addEventListener("input", () => { autogrow(); refreshPalette(); });
  input.addEventListener("keydown", (e) => {
    if (paletteEl.style.display === "block") {
      if (e.key === "ArrowUp") { e.preventDefault(); movePalette(-1); return; }
      if (e.key === "ArrowDown") { e.preventDefault(); movePalette(1); return; }
      if (e.key === "Tab") { e.preventDefault(); acceptPalette(palIdx); return; }
      if (e.key === "Enter") { e.preventDefault(); acceptPalette(palIdx); return; }
      if (e.key === "Escape") { e.preventDefault(); renderPalette([]); return; }
    }
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      const text = input.value.trim();
      if (text && activeHandle) {
        const handle = activeHandle;
        client.rpc("deliver", { handle, message: text })
          .then((res) => {
            if (res && res.command_result) {
              mountCommandBlock(handle, res.command_result);
              applyCommandEffect(handle, res.command_result.effect);
            }
          })
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
    if (e.key === "F2") { e.preventDefault(); openConfigPanel(); return; }
    // Ctrl+Arrows cycle tabs — not browser-reserved, so they reach the page.
    if ((e.ctrlKey || e.metaKey) && !e.altKey) {
      if (e.key === "ArrowRight") { e.preventDefault(); navTab(1); return; }
      if (e.key === "ArrowLeft") { e.preventDefault(); navTab(-1); return; }
    }
    if (!e.altKey || e.ctrlKey || e.metaKey) return;
    const code = e.code;
    if (code === "KeyN") { e.preventDefault(); openPicker(); }
    else if (code === "KeyQ") { e.preventDefault(); openDashboard(); }
    else if (code === "KeyG") { e.preventDefault(); openGroupDashboard(); }
    else if (code === "KeyP") { e.preventDefault(); openFilePicker(); }
    else if (code === "KeyY") { e.preventDefault(); openThemePicker(); }
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

// --- mobile view -------------------------------------------------------

function wireMobile() {
  const back = document.getElementById("back-btn");
  if (back) back.addEventListener("click", () => {
    document.getElementById("app").classList.remove("conversation");
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
  client.on("queue_digest", onQueueDigest);
  client.on("connection", onConnection);

  wireComposer();
  wireKeys();
  wireMobile();

  await client.connect();
  client.subscribeGlobal("session_list");
  client.subscribeGlobal("queue_digest");

  // empty-state default: spawn the first configured agent if none exist.
  const { sessions } = await client.rpc("list_sessions");
  if (!sessions || !sessions.length) await spawnDefault();
}

boot().catch((err) => showError("connection error: " + (err && err.message)));
