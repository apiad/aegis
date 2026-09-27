import { Terminal } from "/static/vendor/xterm/xterm.mjs";
import { FitAddon } from "/static/vendor/xterm/addon-fit.mjs";
import { FrameDecoder, encodeData, hello, resize } from "/static/frames.js";

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
  if (typeof event.data === "string") return;
  for (const [type, payload] of decoder.feed(new Uint8Array(event.data))) {
    if (type === "D") term.write(payload);
  }
};
ws.onclose = () => say("aegis web is gone — reload to reconnect");

term.onData((data) => send(encodeData(data)));
term.onBinary((data) => send(encodeData(Uint8Array.from(data, (c) => c.charCodeAt(0)))));
term.onResize(({ cols, rows }) => send(resize(cols, rows)));
new ResizeObserver(() => fit.fit()).observe(document.getElementById("term"));
term.focus();
