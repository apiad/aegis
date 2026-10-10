// The page's side of an aegis artifact. Included by the page aegis wrote
// (artifact_create's skeleton). Talks to the host page by JSON-RPC 2.0 over
// postMessage; the host is the parent window, and the target is "*" because
// this frame's origin is opaque (CSP sandbox) and it cannot know the host's.
// With no host (the raw document opened in its own tab) ready() fires with an
// empty state and the other calls only log.
(() => {
  const host = window.parent !== window ? window.parent : null;
  let seq = 0;
  let status = "live";
  let init = null; // {state, theme} once the host answered
  const readyFns = [];
  const stateFns = [];
  const statusFns = [];
  let pending; // the latest state() argument not yet posted
  let raf = 0;

  const post = (m) => host && host.postMessage(m, "*");
  const request = (method, params) => post({ jsonrpc: "2.0", id: ++seq, method, params });
  const notify = (method, params) => post({ jsonrpc: "2.0", method, params });
  const warn = (what) => console.warn(`aegis.${what}: this artifact is ${status}`);

  // What the server accepts as a submit's label (artifacts.LABEL_MAX and
  // PageSubmit.label): one non-empty line of at most 140 code points, well
  // formed. A longer or multi-line label is cut; one that cuts to nothing
  // becomes "answered". tests/test_artifact_label.py holds this to the server.
  const LABEL_MAX = 140;
  const labelOf = (label) => {
    const line = String(label || "").toWellFormed().split("\n")[0];
    return [...line].slice(0, LABEL_MAX).join("") || "answered";
  };

  function applyTheme(vars) {
    for (const [k, v] of Object.entries(vars || {})) document.documentElement.style.setProperty(k, v);
  }
  function setStatus(s) {
    status = s;
    document.documentElement.dataset.status = s;
  }
  // The host's refusal of the latest submit ({code, message}), null once one lands.
  let refused = null;
  function setRefused(r) {
    refused = r || null;
    if (refused) document.documentElement.dataset.refused = refused.message;
    else delete document.documentElement.dataset.refused;
  }
  function fire(state, theme) {
    if (init) return;
    init = { state, theme };
    applyTheme(theme);
    for (const fn of readyFns.splice(0)) fn(state, theme);
  }

  window.addEventListener("message", (ev) => {
    if (host && ev.source !== host) return;
    const m = ev.data;
    if (!m || m.jsonrpc !== "2.0") return;
    if (m.id !== undefined && m.result && m.result.artifact !== undefined) {
      setStatus(m.result.status === "probe" ? "live" : m.result.status);
      fire(m.result.state ?? {}, m.result.theme || {});
    } else if (m.id !== undefined && m.error) {
      console.warn(`aegis: ${m.error.code || ""} ${m.error.message || ""}`.trim());
    } else if (m.method === "aegis/state") {
      if (init) init.state = m.params?.state;  // a late ready() sees the latest
      for (const fn of stateFns) fn(m.params?.state);
    } else if (m.method === "aegis/theme") {
      applyTheme(m.params?.theme);
    } else if (m.method === "aegis/status") {
      setStatus(m.params?.status || "closed");
      setRefused(m.params?.refused);
      for (const fn of statusFns) fn({ status, refused });
    }
  });

  // A state write waits for the next frame; an emit or submit that follows
  // it must not overtake it, so both post the pending state first.
  function flushState() {
    if (!raf) return;
    cancelAnimationFrame(raf);
    raf = 0;
    if (status === "live") request("aegis/state", { state: pending });
  }

  window.aegis = {
    ready(fn) {
      if (init) fn(init.state, init.theme);
      else readyFns.push(fn);
    },
    onState(fn) {
      stateFns.push(fn);
    },
    onStatus(fn) {
      statusFns.push(fn);
    },
    state(obj) {
      if (status !== "live") return warn("state");
      if (init) init.state = obj;
      pending = obj;
      if (!raf) raf = requestAnimationFrame(() => { raf = 0; if (status === "live") request("aegis/state", { state: pending }); });
    },
    emit(name, data) {
      if (status !== "live") return warn("emit");
      flushState();
      request("aegis/emit", { name, data: data ?? null });
    },
    submit(data, label) {
      if (status !== "live") return warn("submit");
      flushState();
      request("aegis/submit", { data: data ?? null, label: labelOf(label) });
    },
  };

  const errorOf = (message, stack) => notify("aegis/error", { message: String(message), stack: String(stack || "") });
  window.addEventListener("error", (ev) => errorOf(ev.message, ev.error?.stack || `${ev.filename}:${ev.lineno}`));
  window.addEventListener("unhandledrejection", (ev) => errorOf(ev.reason?.message || ev.reason, ev.reason?.stack));

  // The root's scrollHeight inside a frame is at least the frame's height, so
  // it could never shrink; the root's box is the content's height.
  const size = () => notify("aegis/size", { height: Math.ceil(document.documentElement.getBoundingClientRect().height) });
  function start() {
    new ResizeObserver(size).observe(document.documentElement);
    size();
    // After the inline scripts ran, so an error in them is reported before the
    // handshake and a probe sees it first.
    if (host) request("ui/initialize", {});
    else fire({}, {});
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();  // included late: the DOM is already there
})();
