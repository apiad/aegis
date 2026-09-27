import { Terminal } from "/static/vendor/xterm/xterm.mjs";
import { FitAddon } from "/static/vendor/xterm/addon-fit.mjs";
import { FrameDecoder, encodeData, hello, resize } from "/static/frames.js";
import { KEYS, ctrl } from "/static/keys.js";

// One view per tab. sessionStorage survives a reload of this tab and nothing
// else, so a reload reopens its view and a second tab gets its own. Except
// "Duplicate tab", which copies sessionStorage: the daemon refuses a second
// client on a live view id, so a copied id is checked against the tabs that
// hold one before this tab says hello. A reloading tab has no one to answer.
const claims = new BroadcastChannel("aegis-view");
async function claimViewId() {
  let id = sessionStorage.getItem("aegis-view");
  if (id) {
    const taken = await new Promise((resolve) => {
      const timer = setTimeout(() => resolve(false), 200);
      claims.onmessage = (e) => {
        if (e.data.taken === id) { clearTimeout(timer); resolve(true); }
      };
      claims.postMessage({ asking: id });
    });
    if (taken) id = null;
  }
  if (!id) {
    id = "web-" + crypto.randomUUID();
    sessionStorage.setItem("aegis-view", id);
  }
  claims.onmessage = (e) => {
    if (e.data.asking === id) claims.postMessage({ taken: id });
  };
  return id;
}
const viewId = await claimViewId();

const status = document.getElementById("status");
function say(text) {
  status.textContent = text;
  status.hidden = !text;
}

const term = new Terminal({
  fontFamily: "ui-monospace, Menlo, monospace",
  fontSize: 14,
  theme: { background: "#0b0b0c" },
});
const fit = new FitAddon();
term.loadAddon(fit);
term.open(document.getElementById("term"));
fit.fit();

const decoder = new FrameDecoder();
const scheme = location.protocol === "https:" ? "wss:" : "ws:";
const ws = new WebSocket(`${scheme}//${location.host}/term`);
ws.binaryType = "arraybuffer";

function send(bytes) {
  if (ws.readyState === WebSocket.OPEN) ws.send(bytes);
}

ws.onopen = () => send(hello(viewId, term.cols, term.rows));
ws.onmessage = (event) => {
  if (typeof event.data === "string") {
    const { type } = JSON.parse(event.data);
    if (type === "reconnecting") say("aegis is restarting — reconnecting…");
    if (type === "view_taken") {
      // Another tab still holds this id — a duplicated tab copies
      // sessionStorage, and the claim handshake can miss a busy holder.
      // Drop the id and reload, which takes a fresh one.
      sessionStorage.removeItem("aegis-view");
      say("this view belongs to another tab — opening a new one…");
      location.reload();
      return;
    }
    if (type === "attached") {
      // A new view is about to draw from scratch. Anything half-received
      // from the old one would corrupt it.
      decoder.reset();
      term.reset();
      say("");
    }
    return;
  }
  for (const [type, payload] of decoder.feed(new Uint8Array(event.data))) {
    if (type === "D") term.write(payload);
  }
};
ws.onclose = () => say("aegis web is gone — reload to reconnect");

let ctrlLatched = false;
const ctrlButton = document.querySelector("#keys [data-ctrl]");
function latch(on) {
  ctrlLatched = on;
  ctrlButton.setAttribute("aria-pressed", String(on));
}

term.onData((data) => {
  send(encodeData(ctrlLatched ? ctrl(data) : data));
  if (ctrlLatched) latch(false);
});

// pointerdown, and preventDefault: a click would move focus off xterm's
// hidden textarea and close the phone keyboard.
for (const button of document.querySelectorAll("#keys button")) {
  button.addEventListener("pointerdown", (event) => {
    event.preventDefault();
    if (button.hasAttribute("data-ctrl")) latch(!ctrlLatched);
    else send(encodeData(KEYS[button.dataset.key]));
    term.focus();
  });
}
term.onBinary((data) => send(encodeData(Uint8Array.from(data, (c) => c.charCodeAt(0)))));
term.onResize(({ cols, rows }) => send(resize(cols, rows)));
new ResizeObserver(() => fit.fit()).observe(document.getElementById("term"));
term.focus();
