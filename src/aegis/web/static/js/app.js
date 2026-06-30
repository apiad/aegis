// Boot + single-tab render loop for the aegis web client.
import { WSClient } from "./ws.js";
import { coalesceInto } from "./coalesce.js";
import { renderMarkdown } from "./markdown.js";

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

const transcriptEl = document.getElementById("transcript");
const statusDot = document.getElementById("status-dot");
const statusHandle = document.getElementById("status-handle");
const statusMetrics = document.getElementById("status-metrics");
const input = document.getElementById("input");

let activeHandle = null;
const blocks = [];          // coalesced block records
const nodes = [];           // DOM node per block index

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

// Assistant replies render markdown; everything else uses the server's html.
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

function nearBottom() {
  return transcriptEl.scrollHeight - transcriptEl.scrollTop
    - transcriptEl.clientHeight < 48;
}

function onEvent(frame) {
  const stick = nearBottom();
  const { action, index } = coalesceInto(blocks, frame);
  const rec = blocks[index];
  if (action === "append") {
    const node = blockEl(rec);
    nodes[index] = node;
    transcriptEl.appendChild(node);
  } else {
    // streaming update — re-render the in-flight block
    const node = nodes[index];
    if (node) {
      if (rec.event_type === "AssistantText") {
        node.innerHTML = renderMarkdown(rec.text);
      } else {
        node.textContent = rec.text;
      }
    }
  }
  if (stick) transcriptEl.scrollTop = transcriptEl.scrollHeight;
}

function onState(frame) {
  statusDot.className = "dot " + (frame.state || "");
  statusMetrics.textContent = frame.metrics || "";
}

function onInbox(frame) {
  const msg = frame.msg || {};
  const div = document.createElement("div");
  div.className = "inbox";
  div.textContent = `✉ from ${msg.sender || "?"}: ${msg.body || ""}`;
  transcriptEl.appendChild(div);
}

function onWindowReset() {
  blocks.length = 0;
  nodes.length = 0;
  transcriptEl.replaceChildren();
}

function showError(text) {
  const div = document.createElement("div");
  div.className = "tool-result error";
  div.textContent = text;
  transcriptEl.appendChild(div);
}

// --- boot --------------------------------------------------------------

async function boot() {
  const client = new WSClient(wsUrl, token);
  client.on("event", onEvent);
  client.on("state", onState);
  client.on("inbox", onInbox);
  client.on("window_reset", onWindowReset);

  await client.connect();

  const { sessions } = await client.rpc("list_sessions");
  if (sessions && sessions.length) {
    activeHandle = sessions[0].handle;
  } else {
    const { agents } = await client.rpc("list_agents");
    if (!agents || !agents.length) { showError("no agents configured"); return; }
    const r = await client.rpc("spawn_session", { agent_profile: agents[0] });
    activeHandle = r.handle;
  }
  statusHandle.textContent = activeHandle;
  client.subscribe(activeHandle);

  // Auto-grow the composer up to a cap so Shift+Enter newlines are visible.
  const autogrow = () => {
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 200) + "px";
  };
  input.addEventListener("input", autogrow);

  input.addEventListener("keydown", (e) => {
    // Enter sends; Shift+Enter inserts a newline (textarea default).
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

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && document.activeElement !== input && activeHandle) {
      client.rpc("interrupt_session", { handle: activeHandle });
    }
  });
}

boot().catch((err) => showError("connection error: " + (err && err.message)));
