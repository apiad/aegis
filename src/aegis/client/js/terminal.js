// A terminal drawn by xterm.js (docs/superpowers/specs/2026-10-14-terminals-design.html).
// The term:<id> channel brings its output; term.input and term.resize go up as
// operations. xterm.js loads the first time a terminal opens, never at boot.
//
// Bytes from a terminal are untrusted, like agent text: there is no clipboard
// add-on, so OSC 52 cannot write the clipboard; a link opens only http and
// https, in a new tab, never on aegis's origin; a program's window title is
// ignored.

const BASE_PX = 13;
const MIN_PX = 9; // a terminal wider than the pane scales down to this, then scrolls
const BATCH_MS = 5;

let xterm = null;
function load() {
  xterm ??= Promise.all([
    import("../vendor/xterm/xterm.mjs"),
    import("../vendor/xterm/addon-fit.mjs"),
  ]).catch((err) => {
    xterm = null; // the next open tries again
    throw err;
  });
  return xterm;
}

const COLOURS = ["black", "red", "green", "yellow", "blue", "magenta", "cyan", "white"];
const VARS = [
  ["background", "--term-bg"],
  ["foreground", "--term-fg"],
  ["cursor", "--term-cursor"],
  ["cursorAccent", "--term-cursor-text"],
  ["selectionBackground", "--term-selection"],
  ["selectionInactiveBackground", "--term-selection-inactive"],
  ...COLOURS.map((c) => [c, `--ansi-${c}`]),
  ...COLOURS.map((c) => [`bright${c[0].toUpperCase()}${c.slice(1)}`, `--ansi-bright-${c}`]),
];

// Each variable resolved to rgb() by the browser, so color-mix() and var()
// reach xterm.js as colours it can parse.
export function themeOf(el) {
  const probe = document.createElement("span");
  el.append(probe);
  const theme = {};
  for (const [key, name] of VARS) {
    if (!getComputedStyle(el).getPropertyValue(name).trim()) continue;
    probe.style.color = `var(${name})`;
    theme[key] = getComputedStyle(probe).color;
  }
  probe.remove();
  return theme;
}

function openLink(uri) {
  let url;
  try {
    url = new URL(uri);
  } catch {
    return;
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") return;
  if (url.origin === location.origin) return;
  window.open(url.href, "_blank", "noopener,noreferrer");
}

// onError(err): a call this browser made for the person failed, such as
// input past the server's 1 MB cap ("busy").
export async function mountTerminal(host, { id, conn, server = null, readOnly = false, onError = () => {} }) {
  const [{ Terminal }, { FitAddon }] = await load();
  const term = new Terminal({
    theme: themeOf(host),
    fontFamily: getComputedStyle(host).getPropertyValue("--term-font").trim() || "monospace",
    fontSize: BASE_PX,
    scrollback: 5000,
    disableStdin: readOnly,
    linkHandler: { activate: (_ev, uri) => openLink(uri) },
  });
  const fit = new FitAddon();
  term.loadAddon(fit);
  term.open(host);

  let rev = -1;
  let cols = 120;
  let rows = 40;
  let direct = false;
  let typed = false;
  let ctrlNext = false;
  let buf = "";
  let timer = 0;

  // The size is the server's; this browser draws it, scaling the font to fit.
  function scale() {
    term.options.fontSize = BASE_PX;
    const dims = fit.proposeDimensions();
    if (!dims) return;
    term.options.fontSize = Math.max(MIN_PX, Math.floor(BASE_PX * Math.min(1, dims.cols / cols)));
  }
  function setSize(c, r) {
    cols = c;
    rows = r;
    term.resize(c, r);
    scale();
  }
  // The browser a person types in sets the size, to fit its pane at full size.
  function claimSize() {
    if (readOnly) return;
    term.options.fontSize = BASE_PX;
    const dims = fit.proposeDimensions();
    if (dims && (dims.cols !== cols || dims.rows !== rows)) {
      conn.call("term.resize", { id, cols: dims.cols, rows: dims.rows }, server).catch(() => {});
    }
    scale();
  }
  function send(data) {
    if (readOnly) return;
    buf += data;
    timer ||= setTimeout(() => {
      const d = buf;
      buf = "";
      timer = 0;
      conn.call("term.input", { id, data: d }, server).catch(onError);
    }, BATCH_MS);
  }
  function apply(op) {
    if (op.rev <= rev) return; // the snapshot already carried it
    rev = op.rev;
    if ("o" in op) term.write(op.o);
    else if (op.r) setSize(op.r[0], op.r[1]);
  }

  term.onData((d) => {
    if (!direct) return;
    if (!typed) {
      typed = true;
      claimSize();
    }
    if (ctrlNext && d.length === 1) {
      ctrlNext = false;
      d = String.fromCharCode(d.toUpperCase().charCodeAt(0) & 0x1f);
    }
    send(d);
  });
  // Esc leaves direct mode; a program that needs Esc gets it from Ctrl-[ or the key row.
  term.attachCustomKeyEventHandler((ev) => {
    if (ev.key !== "Escape" || ev.ctrlKey) return true;
    if (ev.type === "keydown") leave();
    return false;
  });

  function enter() {
    if (readOnly || direct) return;
    direct = true;
    host.classList.add("direct");
    host.dispatchEvent(new CustomEvent("termmode", { bubbles: true, detail: { direct } }));
    term.focus();
  }
  function leave() {
    if (!direct) return;
    direct = false;
    typed = false;
    host.classList.remove("direct");
    host.dispatchEvent(new CustomEvent("termmode", { bubbles: true, detail: { direct } }));
    term.blur();
  }
  host.addEventListener("click", enter);
  term.textarea?.addEventListener("blur", leave);
  const ro = new ResizeObserver(() => (direct && typed ? claimSize() : scale()));
  ro.observe(host);

  const unsub = conn.subscribe(
    `term:${id}`,
    (data) => {
      setSize(data.cols, data.rows);
      if (data.delta) data.delta.forEach(apply);
      else {
        term.reset();
        term.write(data.tail);
      }
      rev = Math.max(rev, data.rev);
    },
    (ops) => ops.forEach(apply),
    () => {},
    () => (rev >= 0 ? rev : null),
    server,
  );

  return {
    term,
    enter,
    leave,
    sendLine(text) {
      if (text.includes("\n") && term.modes.bracketedPasteMode) send(`\x1b[200~${text}\x1b[201~\r`);
      else send(text.replace(/\r?\n/g, "\r") + "\r");
    },
    sendKeys: send,
    latchCtrl() {
      ctrlNext = true;
      term.focus();
    },
    get direct() {
      return direct;
    },
    get appCursor() {
      return term.modes.applicationCursorKeysMode;
    },
    dispose() {
      unsub();
      ro.disconnect();
      clearTimeout(timer);
      term.dispose();
    },
  };
}