// The page's side of an agent's artifact: one message listener for every
// frame in the transcript and every hidden probe frame. A message is taken
// only from a window this page mounted, so a page can never name another
// artifact; the operations are called with the id the frame's row carries.
// The theme reaches a frame as CSS variables, read from #a2, where the themes
// declare them.
//
// aegis.dictate and aegis.upload end here too. A page's origin is opaque, so
// the microphone and the Cache Storage dictation needs refuse it, and the 64 KB
// of a state or an event would not carry a file: the host runs both for the page
// (DESIGN.md, "Audio never leaves the browser"). The microphone starts only
// inside a click in the page, and the bar that says it is on is drawn here, in
// this document, where no page can reach it.
//
// aegis/point (pagepoint.js) is taken from any frame under #a2, a sent page's
// or a Files page's as well as an artifact's: it only proposes a menu or a
// bar, which app.js draws in this document (setup's `point`).

import { stageFile } from "./attach.js";
import { icon } from "./glyphs.js";

const VARS = ["--bg", "--ink", "--accent", "--accent-soft", "--on-accent", "--surface", "--raised",
  "--rule", "--muted", "--faint", "--strong", "--fill", "--ok", "--warn", "--err", "--err-bg",
  "--add-bg", "--del-bg", "--font-ui", "--font-mono", "--font-prose", "--font-chrome", "--font-head",
  "--prose-size", "--r", "--r-lg"];
const PROBE_GRACE_MS = 250; // an error right after ui/initialize (a throwing ready) still fails the probe
const PROBE_TIMEOUT_MS = 3000;
const SAYS = { loading: "Loading dictation", listening: "Listening for this page", finishing: "Finishing dictation" };
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
  const settle = () => { if (chains.get(id) === next) chains.delete(id); }; // the map never outgrows the calls in flight
  next.then(settle, settle);
  return next;
}

let keyNow = () => null; // the session on screen, read when a message arrives
let point = () => {}; // (frame, params): a page's gesture, for the host to draw
let dictationOf = () => null; // app.js's Dictation
let toast = () => {};
let waitOnline = async () => {};
let local = () => true; // the key's session is on this server, not a linked one
export function setup(opts) {
  dictationOf = opts.dictation ?? dictationOf;
  toast = opts.toast ?? toast;
  waitOnline = opts.waitOnline ?? waitOnline;
  local = opts.local ?? local;
  call = opts.call; // (op, params, key) -> Promise; key names the session
  entryOf = opts.entry;
  keyNow = opts.key;
  point = opts.point ?? point;
}

// -- aegis.dictate ----------------------------------------------------------
let speaking = null; // {frame, n} while a page's recording is not idle
const shims = new WeakMap(); // frame -> Map(n -> what Dictation sees as the field)
const spoken = new Set(); // artifact ids with dictated text since their last answer

function drawBar(frame, state) {
  const stage = frame.parentElement;
  let bar = stage?.querySelector(":scope > .dictbar");
  if (state === "idle" || !stage) return bar?.remove();
  if (!bar) {
    bar = document.createElement("div");
    bar.className = "dictbar";
    bar.setAttribute("role", "status");
    const stop = document.createElement("button");
    stop.className = "btn";
    stop.type = "button";
    stop.textContent = "Stop";
    stop.addEventListener("click", () => dictationOf()?.stop("button"));
    bar.append(icon("mic"), document.createElement("span"), stop);
    stage.prepend(bar); // first, so it can stick to the top while the page scrolls
  }
  bar.dataset.state = state;
  bar.querySelector("span").textContent = SAYS[state];
}

async function startDictation(frame, id, params) {
  const d = dictationOf();
  if (!d) return;
  const { n, value, at } = params;
  const idle = () => notify(frame, "aegis/dictation", { n, state: "idle" });
  // Not while the page is on its own: a click in it activates this document too.
  if (!navigator.userActivation?.isActive) {
    toast("Dictation starts from a click in the page");
    return idle();
  }
  await d.stop(); // says idle or finishing to the recording it stops, not this one
  if (speaking) {
    notify(speaking.frame, "aegis/dictation", { n: speaking.n, state: "idle" });
    drawBar(speaking.frame, "idle");
  }
  const el = {
    value: String(value ?? ""),
    selectionStart: at,
    dispatchEvent: () => notify(frame, "aegis/dictated", { n, value: el.value }),
  };
  if (!shims.has(frame)) shims.set(frame, new Map());
  shims.get(frame).set(n, el);
  speaking = { frame, n };
  d.start({ el, key: null, current: () => null, heard: () => spoken.add(id) });
}

// What app.js's Dictation says, for the page that is recording.
export function dictationState(state) {
  if (!speaking) return;
  notify(speaking.frame, "aegis/dictation", { n: speaking.n, state });
  drawBar(speaking.frame, state);
  if (state === "idle") speaking = null;
}

// Called with every block of audio: a page that left the screen stops the
// microphone, whatever took it away (a tab switch, a close, a redrawn card).
export function dictationCheck() {
  if (speaking && !speaking.frame.isConnected) dictationOf()?.stop("tab");
}

function stopFor(frame) {
  if (speaking?.frame === frame) dictationOf()?.stop("button");
}

// -- aegis.upload -----------------------------------------------------------
async function upload(frame, id, key, m) {
  const reply = (body) => send(frame, { jsonrpc: "2.0", id: m.id, ...body });
  try {
    const file = m.params?.file;
    if (!(file instanceof Blob)) throw new Error("aegis.upload takes a File or a Blob");
    const uid = await stageFile(
      (op, params) => call(op, params, key),
      waitOnline,
      file,
      file.name || "upload",
      (sent, size) => notify(frame, "aegis/upload-progress", { id: m.id, sent, size }),
    );
    try {
      reply({ result: await inOrder(id, () => call("artifact.upload", { upload_id: uid, artifact_id: id }, key)) });
    } catch (e) {
      call("attachment.drop", { upload_id: uid }, key).catch(() => {});
      throw e;
    }
  } catch (e) {
    reply({ error: { code: e.code || "error", message: e.message } });
  }
}

// A message to a frame from the pointing host: aegis/point-open and -closed.
export const tell = (frame, method, params = {}) => notify(frame, method, params);

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
export function probe(req, key, src) {
  // src is req.url as the page may use it (entries.fileUrl): a linked server's
  // path rebuilt under /via/<server>, or about:blank, which no probe is run on.
  if (src === "about:blank") return;
  const f = document.createElement("iframe");
  f.setAttribute("sandbox", "allow-scripts");
  f.src = src;
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

// Any frame this page mounted and shows, for aegis/point only.
function anyFrameOf(source) {
  for (const f of document.querySelectorAll("#a2 iframe")) if (f.contentWindow === source && !f.closest("#probes")) return f;
  return null;
}

window.addEventListener("message", async (ev) => {
  const m = ev.data;
  if (!m || m.jsonrpc !== "2.0") return;
  if (m.method === "aegis/point") {
    const f = anyFrameOf(ev.source);
    if (f) point(f, m.params || {});
    return;
  }
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
  const key = keyNow(); // captured now: a tab switch while the call waits must not reroute it
  const status = frame.dataset.status || e?.status || "live";
  if (m.method === "ui/initialize") {
    stopFor(frame); // the page reloaded, so the field it was dictating into is gone
    send(frame, { jsonrpc: "2.0", id: m.id, result: { artifact: id, state: e?.detail?.state ?? {}, theme: themeVars(), status } });
    return;
  }
  if (m.method === "aegis/dictate") {
    if (status === "live") startDictation(frame, id, m.params || {});
    return;
  }
  if (m.method === "aegis/dictate-stop") return stopFor(frame);
  if (m.method === "aegis/dictate-edit") {
    const el = shims.get(frame)?.get(m.params?.n);
    if (el) el.value = String(m.params.value ?? "");
    return;
  }
  if (m.method === "aegis/upload") return upload(frame, id, key, m);
  if (m.method === "aegis/submit" && speaking?.frame === frame) {
    // Its data was read before the last words landed. Nothing is lost by asking
    // again, as the box's own send waits; a page cannot wait for words it cannot see.
    stopFor(frame);
    const refused = { code: "dictating", message: "dictation was still going. Send again once the text is in." };
    showRefused(frame, refused);
    notify(frame, "aegis/status", { status: "live", refused });
    return;
  }
  if (m.method === "aegis/size") {
    if (frame.closest("#viewer, #canvas .pane.docked")) return; // the viewer's frame and a pane's fill them
    frame.style.height = `${Math.min(Math.max(80, m.params?.height || 0), MAX_HEIGHT())}px`;
    return;
  }
  const ops = { "aegis/state": "artifact.state", "aegis/emit": "artifact.emit", "aegis/submit": "artifact.submit", "aegis/error": "artifact.error" };
  const op = ops[m.method];
  if (!op) return;
  if (m.method === "aegis/error") {
    // The server wakes the agent once per landed page; a frame's later errors
    // (a loop in its JS) would only be counted, so they never leave it.
    if (frame.dataset.errored) return;
    frame.dataset.errored = "1";
  }
  try {
    const said = (m.method === "aegis/emit" || m.method === "aegis/submit") && spoken.has(id);
    // The frame's id last, so a page that sends its own artifact_id cannot name another.
    const params = { ...(m.params || {}), artifact_id: id };
    await inOrder(id, () =>
      call(op, said ? { ...params, dictated: true } : params, key).catch((err) => {
        // A linked server older than the mark refuses it: the answer matters more.
        if (said && !local(key) && err.code === "bad_params") return call(op, params, key);
        throw err;
      }),
    );
    if (said) spoken.delete(id);
    if (m.id !== undefined) send(frame, { jsonrpc: "2.0", id: m.id, result: "ok" });
    if (m.method === "aegis/submit") {
      showRefused(frame, null);
      notify(frame, "aegis/status", { status: "submitted", refused: null });
    }
  } catch (err) {
    if (m.id !== undefined) send(frame, { jsonrpc: "2.0", id: m.id, error: { code: err.code || "error", message: err.message } });
    let now = "live";
    if (err.code === "not_live") {
      const was = entryOf(id)?.status; // the closing patch may not have landed yet
      now = was && was !== "live" ? was : "closed";
    }
    if (m.method === "aegis/submit") {
      // A refused submit is the person's answer not arriving: the card says so
      // and the page gets the same reason to render in its own look.
      const refused = reasonOf(err);
      showRefused(frame, refused);
      notify(frame, "aegis/status", { status: now, refused });
    } else if (now !== "live") notify(frame, "aegis/status", { status: now });
  }
});

// The server's refusal as {code, message}. OpError prefixes its message with
// the code, which the card would otherwise show twice.
function reasonOf(err) {
  const code = err.code || "error";
  const text = String(err.message || "");
  return { code, message: text.startsWith(`${code}: `) ? text.slice(code.length + 2) : text };
}

// The note under a card's frame, the viewer's or a docked pane's: set by a
// refused submit, cleared by the next one that lands. A card redrawn for a new
// status starts without it.
function showRefused(frame, refused) {
  const card = frame.closest(".acard, #viewer .vbody, #canvas .pane.docked .pbody");
  if (!card) return;
  let note = card.querySelector(":scope > .refused");
  if (!refused) {
    note?.remove();
    return;
  }
  if (!note) {
    note = document.createElement("div");
    note.className = "refused";
    note.setAttribute("role", "alert");
    card.append(note);
  }
  note.textContent = `Not sent: ${refused.message}`;
}

// entries.js asks, through a DOM event, for a state push on a frame it kept.
document.addEventListener("aegis:state", (ev) => notify(ev.target, "aegis/state", { state: ev.detail }));
