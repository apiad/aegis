// The page's side of an agent's artifact: one message listener for every
// frame in the transcript and every hidden probe frame. A message is taken
// only from a window this page mounted, so a page can never name another
// artifact; the operations are called with the id the frame's row carries.
// The theme reaches a frame as CSS variables, read from #a2, where the themes
// declare them.

const VARS = ["--bg", "--ink", "--accent", "--accent-soft", "--on-accent", "--surface", "--raised",
  "--rule", "--muted", "--faint", "--strong", "--fill", "--ok", "--warn", "--err", "--err-bg",
  "--add-bg", "--del-bg", "--font-ui", "--font-mono", "--font-prose", "--font-chrome", "--font-head",
  "--prose-size", "--r", "--r-lg"];
const PROBE_GRACE_MS = 250; // an error right after ui/initialize (a throwing ready) still fails the probe
const PROBE_TIMEOUT_MS = 3000;
const MAX_HEIGHT = () => Math.round(window.innerHeight * 0.7);

let call = async () => {};
let entryOf = () => null;
const probes = new Map(); // iframe -> {id, key, timer, grace, done}
// A page posts state, then emit or submit, in order; each becomes its own
// call, so one artifact's calls are chained to reach the server in that order.
const chains = new Map(); // artifact id -> the last call's promise
function inOrder(id, fn) {
  const prev = chains.get(id) || Promise.resolve();
  const next = prev.then(fn, fn);
  chains.set(id, next);
  return next;
}

export function setup(opts) {
  call = opts.call; // (op, params, key?) -> Promise; key names the session, else the one shown
  entryOf = opts.entry;
}

export function themeVars() {
  const cs = getComputedStyle(document.getElementById("a2"));
  return Object.fromEntries(VARS.map((v) => [v, cs.getPropertyValue(v).trim()]).filter(([, v]) => v));
}

const send = (frame, m) => frame.contentWindow?.postMessage(m, "*");
const notify = (frame, method, params) => send(frame, { jsonrpc: "2.0", method, params });

export function theme() {
  const vars = themeVars();
  for (const f of document.querySelectorAll("iframe[data-artifact]")) notify(f, "aegis/theme", { theme: vars });
}

// A probe request from the transcript channel: run the page hidden, report
// once, to the session the request came from (the person may switch tabs
// while the probe runs, so the answer never goes to "the session shown").
export function probe(req, key) {
  const f = document.createElement("iframe");
  f.setAttribute("sandbox", "allow-scripts");
  f.src = req.url;
  const p = { id: req.id, key, done: false, grace: 0, timer: 0 };
  p.timer = setTimeout(() => finish(f, null), PROBE_TIMEOUT_MS); // the server has given up too
  probes.set(f, p);
  document.getElementById("probes").append(f);
}

function finish(frame, outcome) {
  const p = probes.get(frame);
  if (!p || p.done) return;
  p.done = true;
  clearTimeout(p.timer);
  clearTimeout(p.grace);
  probes.delete(frame);
  frame.remove();
  if (outcome) call("artifact.probed", { probe_id: p.id, ...outcome }, p.key);
}

function frameOf(source) {
  for (const f of document.querySelectorAll("iframe[data-artifact], #probes iframe")) if (f.contentWindow === source) return f;
  return null;
}

window.addEventListener("message", async (ev) => {
  const m = ev.data;
  if (!m || m.jsonrpc !== "2.0") return;
  const frame = frameOf(ev.source);
  if (!frame) return;
  const p = probes.get(frame);
  if (p) {
    if (m.method === "ui/initialize") {
      send(frame, { jsonrpc: "2.0", id: m.id, result: { artifact: null, state: {}, theme: themeVars(), status: "probe" } });
      p.grace = setTimeout(() => finish(frame, { started: true }), PROBE_GRACE_MS);
    } else if (m.method === "aegis/error") {
      finish(frame, { started: false, message: m.params?.message || "error", stack: m.params?.stack || "" });
    }
    return;
  }
  const id = frame.dataset.artifact;
  const e = entryOf(id);
  const status = frame.dataset.status || e?.status || "live";
  if (m.method === "ui/initialize") {
    send(frame, { jsonrpc: "2.0", id: m.id, result: { artifact: id, state: e?.detail?.state ?? {}, theme: themeVars(), status } });
    return;
  }
  if (m.method === "aegis/size") {
    frame.style.height = `${Math.min(Math.max(80, m.params?.height || 0), MAX_HEIGHT())}px`;
    return;
  }
  const ops = { "aegis/state": "artifact.state", "aegis/emit": "artifact.emit", "aegis/submit": "artifact.submit", "aegis/error": "artifact.error" };
  const op = ops[m.method];
  if (!op) return;
  try {
    await inOrder(id, () => call(op, { artifact_id: id, ...(m.params || {}) }));
    if (m.id !== undefined) send(frame, { jsonrpc: "2.0", id: m.id, result: "ok" });
    if (m.method === "aegis/submit") notify(frame, "aegis/status", { status: "submitted" });
  } catch (err) {
    if (m.id !== undefined) send(frame, { jsonrpc: "2.0", id: m.id, error: { code: err.code || "error", message: err.message } });
    if (err.code === "not_live") notify(frame, "aegis/status", { status: entryOf(id)?.status || "closed" });
  }
});

// entries.js asks, through a DOM event, for a state push or a status change on a frame it kept.
document.addEventListener("aegis:state", (ev) => notify(ev.target, "aegis/state", { state: ev.detail }));
document.addEventListener("aegis:status", (ev) => notify(ev.target, "aegis/status", { status: ev.detail }));
