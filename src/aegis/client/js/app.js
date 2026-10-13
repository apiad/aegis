// The aegis client: one home server, the servers it links, many sessions.
//
// The tabs are the open sessions of the home server and of every server it
// links (each one's `sessions` channel), the same in every browser. Their order
// and which one is focused belong to this browser: the order in localStorage,
// the focus in the URL hash. A session is known by its key: its log id at
// home, `<server>/<log_id>` on a linked server (links.py).

import { Connection } from "./protocol.js";
import { Transcript } from "./transcript.js";
import { artifactStage, fadeTables, fileUrl, setDocker, setFileBase, setOpener, syncDock } from "./entries.js";
import { setScroller } from "./highlight.js";
import { Viewer } from "./viewer.js";
import { Canvas } from "./canvas.js";
import { copyRow, installCopy } from "./copy.js";
import { Find } from "./find.js";
import * as artifacts from "./artifacts.js";
import { TabOrder, patchTab, renderTabs } from "./tabs.js";
import { linesOf, mine, ownerOf, setWho } from "./me.js";
import { ago, byNeed, money, patchCard, renderArchive, renderBand, renderBandQuota, renderCards, tickPlan } from "./fleet.js";
import { age, countdown, elapsed, hostRow, hostSeverity, providerFor, quotaSideRow, quotaTile, tile } from "./gauges.js";
import { closeCard, initSide, restState, toggleCollapsed } from "./side.js";
import { installKeys, renderKeys } from "./keys.js";
import { Palette } from "./palette.js";
import { entryIcon, glyph, gorgoneion, icon, installGlyphs, LABEL } from "./glyphs.js";
import { CommandMenu } from "./commands.js";
import { closeMonitorCard, renderMonitors, tickMonitors } from "./monitors.js";
import { Settings } from "./settings.js";
import { entry, Journal, opens } from "./journal.js";
import { Help } from "./help.js";
import { installBell, listenForOpen, pingToast, redrawFavicon, setTitle, updatePing } from "./ping.js";
import { ask, cancelAsk } from "./dialog.js";
import "./pick.js";
import { Dictation } from "./dictation.js";
import { Attachments } from "./attach.js";
import { dur, planTimes } from "./plantime.js";
import { setToastOpener, toast } from "./toast.js";
import { applyUpdate, checkForUpdate, setAutoApply, startWorker } from "./appworker.js";
import { closeInstallDialog, startInstall } from "./install.js";
import { checkRotation, closePushPanel, openPushPanel, pushSupported, setupPush } from "./pushpanel.js";
import { clearDevice, loadFleet, loadTranscript, saveFleet, saveTranscript } from "./devicestore.js";
import { markNeedsNet, offline } from "./net.js";
import { discard, draftFor, flushNow, loadDrafts, onDrafts, queue, sendAnyway, setSender } from "./outbox.js";

const $ = (id) => document.getElementById(id);
const root = $("a2");

// Quota is one subscription for the page: the band and the sidebar both draw
// it. Host is subscribed only while a view shows it, the Fleet every server's
// and a session its own server's, so a server nobody watches samples nothing.
let quota = { providers: [] };
let host = null;
let unsubHost = null;
let quotaDrawnFor = null; // the view the quota rows were last drawn for
const nowS = () => Date.now() / 1000;

// -- theme ----------------------------------------------------------------
// This browser's choice, picked in Settings; index.html applies it before the
// first paint.
const THEMES = [
  { value: "ink", label: "Ink" },
  { value: "logbook", label: "Logbook" },
  { value: "electric", label: "Electric" },
  { value: "phosphor", label: "Phosphor" },
  { value: "blueprint", label: "Blueprint" },
  { value: "amphora", label: "Amphora" },
  { value: "riso", label: "Riso" },
  { value: "solari", label: "Solari" },
  { value: "aizome", label: "Aizome" },
  { value: "poster", label: "Poster" },
];
function setTheme(name) {
  document.documentElement.dataset.theme = name;
  localStorage.setItem("aegis.theme", name);
  redrawFavicon();
  artifacts.theme();
}

// -- state ----------------------------------------------------------------
const sessions = new Map(); // key -> meta, from every server's `sessions` channel
let links = []; // the home server's links, from the `links` channel
let linksLoaded = false; // the first `links` snapshot has arrived
let linksSig = ""; // the links' names and states the archive was last read for
const remote = new Map(); // linked server -> {unsubs, quota, host, unsubHost, loaded, state}

// A session's key, and back. Server names never hold a slash (links.py).
const keyOf = (server, logId) => (server ? `${server}/${logId}` : logId);
function forKey(key) {
  const i = key.indexOf("/");
  return i < 0 ? { server: null, log_id: key } : { server: key.slice(0, i), log_id: key.slice(i + 1) };
}
// An operation on a session, on whichever server holds it.
function callFor(key, op, params = {}) {
  const { server, log_id } = forKey(key);
  return conn.call(op, { ...params, log_id }, server);
}
const linkOf = (server) => links.find((l) => l.name === server);
const isOff = (server) => !!server && linkOf(server)?.state !== "linked";
const withKey = (m, server = null) => ({ ...m, server, key: keyOf(server, m.log_id), off: isOff(server) });
const order = new TabOrder();
let ordered = []; // metas in this browser's tab order
let shown = null; // the key of the session whose transcript is subscribed
let landUnread = null; // the tab Alt+J opened, whose transcript lands on its first unread
let unsubTranscript = null;
let workingSince = null;
let booted = false;
const transcript = new Transcript($("tr"), $("entries"), $("jump"), {
  // A failed report is retried by the next tick: the ids stay unread in the view.
  // One call carries at most 500 ids, the operation's cap.
  onRead: (ids) => {
    if (!shown) return;
    for (let i = 0; i < ids.length; i += 500) {
      const batch = ids.slice(i, i + 500);
      callFor(shown, "session.read", { ids: batch }).catch(() => batch.forEach((id) => transcript.sent.delete(id)));
    }
  },
  onSelect: drawNav,
  // In tab mode with another tab open the transcript is laid out but hidden.
  seen: () => canvas.transcriptShown(),
  loadDetail: (ids) => {
    const id = shown;
    return callFor(id, "transcript.detail", { ids }).then((got) => (shown === id ? got : []));
  },
});
artifacts.setup({ call: (op, params, key) => callFor(key || shown, op, params), entry: (id) => transcript.entries.get(id), key: () => shown });
const viewer = new Viewer($("viewer"), { entry: (id) => transcript.entries.get(id) });
// Open on a docked item shows its pane, so a page never runs in the viewer
// and a pane at once. An older version of a docked file opens in the viewer:
// the pane shows the newest.
// One in another browser window focuses or opens that window's tab.
setOpener((item) => {
  const f = item.files?.[item.at];
  const id = item.artifact || (f && !f.replaced ? f.key : null);
  const away = id && canvas.elsewhere(id);
  if (away) openWindow(shown, away);
  else if (!(id && canvas.flash(id))) viewer.open(item);
});
// The newest sent file with a key, for its pane: the last file entry that
// holds it unreplaced. Walked on a pane's draw only.
function latestFile(key) {
  const es = [...transcript.entries.values()];
  for (let i = es.length - 1; i >= 0; i--) {
    if (es[i].kind !== "file") continue;
    const f = (es[i].detail?.files || []).find((x) => x.key === key && !x.replaced);
    if (f) return f;
  }
  return undefined;
}
// The canvas: docked pages and files beside the transcript (js/canvas.js).
const canvas = new Canvas($("canvas"), {
  transcriptPane: $("tr-pane"),
  entryOf: (id) => transcript.entries.get(id),
  latestFile,
  call: (op, params) => callFor(shown, op, params),
  // A card's Dock button hides while its item is docked, and a file card's
  // page swaps its preview for a line; a docked page's card swaps its frame
  // by itself (entries.hold).
  onChange: () => syncDock(),
  refuse: (message) => toast(message, { kind: "error" }),
  openWindow: (n, tab) => openWindow(shown, n, tab),
});
// Dock on a card or the viewer's bar. The viewer lets go of what it docks.
// The viewer lets go of what it docks or pops out.
function leaveViewer(id) {
  const item = viewer.isOpen ? viewer.items.get(viewer.key) : null;
  if (item && (item.artifact || item.files[item.at].key) === id) viewer.close();
}
setDocker({
  dock: (id) => {
    leaveViewer(id);
    canvas.dock(id);
  },
  can: (id) => canvas.writable && !canvas.has(id) && !canvas.elsewhere(id),
  docked: (id) => canvas.has(id) || canvas.elsewhere(id) || false,
  show: (id) => canvas.flash(id),
  open: (n) => openWindow(shown, n),
  pop: (id) => {
    leaveViewer(id);
    canvas.popOut(id);
  },
  canPop: () => canvas.writable && !canvas.merged(),
  frames: () => route().view !== "window",
});
// Window n of a session's canvas, in the browser tab named for it: focused if
// this browser has that tab open, opened if not. Given `tab`, a tab opened
// for another number is renamed and pointed at window n instead.
function windowUrl(key, n) {
  return `${location.pathname}${location.search}#w=${n}:${key}`;
}
function openWindow(key, n, tab = null) {
  const name = `aegis-${key}-w${n}`;
  if (!tab) return window.open(windowUrl(key, n), name);
  tab.name = name;
  tab.location.replace(windowUrl(key, n));
  return tab;
}
// The shown session's canvas, from its card: window 1 in the session view,
// window n in a window tab. A page the viewer shows that is now docked
// leaves the viewer first, so it never runs in both.
function showCanvas(s) {
  const lay = s.layout || null;
  const docked = new Set((lay?.windows || []).flatMap((w) => w.columns.flatMap((c) => c.panes.map((p) => p.id))));
  const item = viewer.isOpen ? viewer.items.get(viewer.key) : null;
  if (item?.artifact && docked.has(item.artifact)) viewer.close();
  const r = route();
  canvas.show(s.key, lay, { n: r.view === "window" ? r.n : 1, writable: !s.off && s.state !== "archived" && mine(s) });
  syncDock();
}
setScroller($("tr"));
new ResizeObserver(() => fadeTables($("entries"))).observe($("tr"));
const copyDeps = {
  entry: (id) => transcript.entries.get(id),
  output: (id) => callFor(shown, "transcript.output", { id }).then((r) => r.text),
  detail: (id) => callFor(shown, "transcript.detail", { ids: [id] }).then((got) => got[0]),
};
installCopy($("entries"), copyDeps);
installCopy($("viewer"), copyDeps);
const find = new Find($("find"), transcript, (q) => callFor(shown, "transcript.search", { q }).then((r) => r.ids));
$("find-prev").append(icon("up"));
$("find-next").append(icon("down"));
$("find-close").append(icon("close"));
installGlyphs();
// The navigator: previous / next agent message, the position, and the latest.
$("nav-recap").prepend(icon("sparkle"));
$("nav-needs").prepend(glyph("needs_you"));
$("nav-last").append(icon("start"));
$("nav-up").append(icon("up"));
$("nav-down").append(icon("down"));
$("jump").append(icon("latest"));
$("bell").append(icon("bell"));
$("settings-btn").prepend(icon("gear"));
$("journal-btn").append(icon("book"));
$("send").append(icon("send"));
$("sp-go").append(icon("send"));
$("hint-tutorial-x").append(icon("close"));
$("hint-whats-new-x").append(icon("close"));
$("interrupt").append(icon("stop"));
$("replies-g").append(icon("prompt"));
$("queued-g").append(icon("prompt"));
$("working-g").append(icon("think"));
$("coldnote-g").append(icon("wait"));
$("closing-g").append(glyph("done"));
$("sp-mark").prepend(gorgoneion());
$("boot-mark").append(gorgoneion());
$("band-mark").append(gorgoneion());
installBell($("bell"), pushSupported() ? openPushPanel : null);
$("nav-up").addEventListener("click", () => transcript.message(-1));
$("nav-down").addEventListener("click", () => transcript.message(1));
$("nav-pos").addEventListener("click", () => transcript.firstUnread());
$("nav-last").addEventListener("click", () => transcript.lastMessage());
$("nav-needs").addEventListener("click", () => nextNeed());
$("nav-recap").addEventListener("click", () => askRecap(true));
// Every redraw of the transcript re-marks the selection, which asks for the
// navigator, so it is drawn at most once a frame: position() walks every entry.
let navFrame = 0;
function drawNav() {
  if (!navFrame) navFrame = requestAnimationFrame(drawNavNow);
}
// Shown with any entry, so the latest button is there before the first agent
// message; with none, the position is empty and the arrows are off. The label
// is only "3/4", so the pill fits a phone; the words are its name. The needs
// button counts the other sessions Alt+J would open. Unchanged values are not
// written back.
let navDrawn = {};
function drawNavNow() {
  navFrame = 0;
  const { index, total, unread } = transcript.position();
  const r = route();
  const needs = byNeed(ordered).filter((m) => r.view !== "session" || m.key !== r.id).length;
  const now = {
    hidden: !transcript.entries.size,
    text: total ? `${index}/${total}` : "",
    label: total ? `${unread ? `${unread} unread · ` : ""}message ${index} of ${total}` : "",
    unread: unread > 0,
    off: !total,
    needs,
  };
  if (now.hidden !== navDrawn.hidden) $("nav").hidden = now.hidden;
  if (now.text !== navDrawn.text) $("nav-pos").textContent = now.text;
  if (now.label !== navDrawn.label) {
    $("nav-pos").setAttribute("aria-label", now.label);
    $("nav-pos").title = `${now.label}. First unread (Alt+U)`;
  }
  if (now.unread !== navDrawn.unread) $("nav-pos").classList.toggle("unread", now.unread);
  if (now.off !== navDrawn.off) $("nav-up").disabled = $("nav-down").disabled = $("nav-last").disabled = now.off;
  if (now.needs !== navDrawn.needs) {
    $("nav-needs").disabled = !needs;
    $("nav-needs-n").textContent = needs ? String(needs) : "";
  }
  navDrawn = now;
}
// The fold level: this browser's choice, for every tab. z steps through
// everything shown, the work folded, and all but the messages.
const FOLDS = ["Everything shown", "Tool calls and thinking folded", "Only messages shown"];
$("nav-fold").prepend(icon("fold"));
function foldLevel(level) {
  transcript.setFoldLevel(level);
  $("nav-fold").dataset.level = String(level);
  $("nav-fold").title = `${FOLDS[level]}. Press for: ${FOLDS[(level + 1) % 3].toLowerCase()} (Z)`;
  localStorage.setItem("aegis.foldLevel", String(level));
}
foldLevel(Number(localStorage.getItem("aegis.foldLevel")) % 3 || 0);
$("nav-fold").addEventListener("click", () => foldLevel((transcript.foldLevel + 1) % 3));
// How the Fleet orders its cards: this browser's choice, like the tab order.
let fleetOrder = localStorage.getItem("aegis.fleetOrder") || "attention";
function markOrder() {
  for (const b of document.querySelectorAll("#fleet-order button")) b.classList.toggle("on", b.dataset.order === fleetOrder);
}
markOrder();
for (const b of document.querySelectorAll("#fleet-order button"))
  b.addEventListener("click", () => {
    fleetOrder = b.dataset.order;
    localStorage.setItem("aegis.fleetOrder", fleetOrder);
    markOrder();
    render();
  });

// -- routing: #fleet, #new, #settings, #journal, #help[=<page>], #s=<key>, #read=<key>, #w=<n>:<key> --
// A help ref is URI-encoded; a hand-typed malformed one is a page name nobody has.
const unhash = (s) => {
  try {
    return decodeURIComponent(s);
  } catch {
    return s;
  }
};
function route() {
  const h = location.hash.slice(1);
  if (h.startsWith("s=")) return { view: "session", id: h.slice(2) };
  if (h.startsWith("read=")) return { view: "read", id: h.slice(5) };
  // Window n of a session's canvas, a browser tab of its own (js/canvas.js).
  const w = /^w=(\d+):(.+)$/.exec(h);
  if (w) return { view: "window", id: w[2], n: Number(w[1]) };
  if (h === "new") return { view: "spawn" };
  if (h === "settings") return { view: "settings" };
  if (h === "journal") return { view: "journal" };
  if (h === "help" || h.startsWith("help=")) return { view: "help", id: unhash(h.slice(5)) };
  return { view: "fleet" };
}

// Navigation renders in the same step as the URL changes. Setting
// location.hash would render later, on hashchange, and anything typed in
// between was wiped when the new tab's draft loaded (#128's CI failure).
// Back and Forward, and a hand-edited URL, still arrive through hashchange.
function go(hash) {
  if (location.hash !== hash) history.pushState(null, "", hash);
  render();
}

window.addEventListener("hashchange", render);
window.addEventListener("popstate", render);

function show(view, text) {
  root.dataset.view = view;
  if (text) $("boot-text").textContent = text;
}

// -- signing in: the socket said 4401, so this browser has no valid cookie -----
function showLogin() {
  show("boot", "This browser is not signed in to this server.");
  $("login").hidden = false;
  if (new URLSearchParams(location.search).has("refused")) {
    $("login-error").textContent = "That token was refused. Paste the one aegis serve printed.";
    history.replaceState(null, "", location.pathname + location.hash);
  }
  $("login-token").focus();
}

$("login").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  $("login-error").textContent = "";
  const r = await fetch("/login", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ token: $("login-token").value.trim() }),
  });
  if (r.ok) location.reload();
  else $("login-error").textContent = "That token was refused.";
});

// -- the connection -----------------------------------------------------------
let wasClosed = false; // a reconnect checks for a new build

// The banner while the connection is down: a state, so it stays at the top of
// the page rather than a toast. A socket that comes back within 3 s never
// shows it; one that stays down counts down to the next attempt.
const BANNER_AFTER_MS = 3000;
let closedSince = 0;
let bannerTimer = null;
function drawBanner() {
  const down = closedSince > 0 && Date.now() - closedSince >= BANNER_AFTER_MS;
  $("netbanner").hidden = !down;
  if (!down) return;
  const s = Math.ceil((conn.retryAt - Date.now()) / 1000);
  const retry = conn.retryAt && s > 0 ? `retrying in ${s} s` : "retrying now";
  $("netbanner-t").textContent = [seenText(), retry].filter(Boolean).join(" · ");
}

// When this device last saw the server live: the moment the socket last
// closed, or the saved Fleet's saved_at at boot. 0 while it never has.
let seenAt = 0;
let live = false; // the socket is open
function seenText() {
  if (!seenAt) return "";
  const m = Math.floor((Date.now() - seenAt) / 60000);
  return m < 1 ? "seen just now" : m < 60 ? `seen ${m} min ago` : `seen ${Math.floor(m / 60)} h ago`;
}
// Offline (the socket is not open), the page draws what the device kept and
// what needs the server is disabled.
function markOffline(state) {
  if (state === "open") {
    live = true;
    delete root.dataset.offline;
  } else {
    if (live) seenAt = Date.now();
    live = false;
    root.dataset.offline = "";
  }
  markNeedsNet();
}

// The token no longer works: what this device kept goes before the sign-in
// shows, and no saved card stays drawn behind it.
let refused = false;
function signedOut() {
  refused = true;
  clearTimeout(fleetSave);
  clearTimeout(trSave);
  fleetSave = trSave = null;
  sessions.clear();
  kept.clear();
  ordered = [];
  transcript.clear();
  renderTabs($("tablist"), [], null, tabActions);
  clearDevice().then(showLogin);
}
function bannerState(state) {
  if (state === "closed" && !closedSince) {
    closedSince = Date.now();
    bannerTimer = setInterval(drawBanner, 1000);
  }
  // A refused token or protocol is not a connection problem: they say so elsewhere.
  if (state === "open" || state === "unauthorized" || state === "version") {
    closedSince = 0;
    clearInterval(bannerTimer);
    bannerTimer = null;
    $("netbanner").hidden = true;
  }
}
// This device's push against the server's row (pushpanel.js): on every
// connect, and whenever the page comes back in front.
const checkPush = () => checkRotation().catch((e) => console.warn("aegis: checking push failed", e));
const conn = new Connection(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`, {
  onState(state, server) {
    $("conn-dot").className = `dot ${state === "open" ? "ready" : state === "connecting" ? "ghost" : "err"}`;
    $("conn-text").textContent =
      state === "open" ? server : state === "connecting" ? "connecting" : state === "closed" ? "disconnected, retrying" : state;
    // For the band offline, where the welcome that names the server is out of reach.
    if (state === "open" && server) localStorage.setItem("aegis.server", server);
    // What arrived before a close is still the server's latest: kept at once.
    if (state === "closed" && live) {
      if (fleetSave) flushFleet(true);
      if (trSave) flushTranscript(true);
    }
    markOffline(state);
    drawOfflineNotice();
    bannerState(state);
    if (state === "unauthorized") signedOut();
    if (state === "version") {
      show(
        "boot",
        // With a worker, a reload would serve this same build again: the new
        // one is fetched and taken as soon as it is ready (appworker.js).
        navigator.serviceWorker
          ? "This page and the server speak different protocol versions. Updating aegis…"
          : "This page and the server speak different protocol versions. Reload the page.",
      );
      setAutoApply();
    }
    if (state === "closed") wasClosed = true;
    if (state === "open") {
      if (wasClosed) checkForUpdate();
      wasClosed = false;
      setWho(conn.me, conn.owner);
      checkPush(); // after setWho: the rows are this token's
      // Whether this browser runs on the server's desktop (Open natively).
      if (conn.native) root.dataset.native = "";
      else delete root.dataset.native;
      loadAgents();
      loadVersion();
    }
  },
});
// Retry at once when the network or the app comes back, or when asked.
$("netbanner-retry").addEventListener("click", () => conn.retryNow());
window.addEventListener("online", () => conn.retryNow());
document.addEventListener("visibilitychange", () => {
  if (document.hidden) return;
  conn.retryNow();
  if (conn.open) checkPush(); // otherwise the connect does
});
const settings = new Settings(conn, $("settings"), { themes: THEMES, setTheme });
let landEntry = null; // {key, id}: reveal this entry once its transcript lands
const journal = new Journal(conn, $("journal"), {
  onOpen: (r) => {
    landEntry = r.source ? { key: r.log_id, id: r.source } : null;
    go(r.open ? `#s=${r.log_id}` : `#read=${r.log_id}`);
  },
});
// The Help tab (help.js), one per window like Settings. Its code blocks copy
// as the transcript's do.
// Start this tutorial: what /tutorial <page> does, on default_agent. A failure
// shows as an error toast, since the Help tab belongs to no session.
const helpTab = new Help(conn, $("help"), {
  go,
  onKeys: () => help(true),
  onStart: (id) => startGuide({ page: id }).catch((e) => toast(e.message, { kind: "error" })),
});
installCopy($("help"), copyDeps);
let sideJournalTimer = null;
const sideJournalChanged = () => {
  journal.changed();
  // Away from the session view nothing refetches; forget what was drawn so the
  // next renderMeta of any session does.
  if (root.dataset.view !== "session") {
    sideJournalFor = null;
    return;
  }
  clearTimeout(sideJournalTimer);
  sideJournalTimer = setTimeout(() => drawSideJournal(sideJournalFor, true), 300);
};
conn.subscribe("journal", sideJournalChanged, sideJournalChanged);

// Each server's `sessions` channel: a snapshot replaces that server's metas,
// patches upsert and remove them.
function sessionHandlers(server) {
  return [
    (metas) => {
      for (const [k, m] of sessions) if (m.server === server) sessions.delete(k);
      for (const m of metas || []) {
        const w = withKey(m, server);
        sessions.set(w.key, w);
      }
      if (server) remote.get(server).loaded = true;
      else booted = homeLive = true;
      onSessions();
      // The server is reachable again: what was written meanwhile goes out.
      flushNow((key) => forKey(key).server === server);
    },
    (ops) => {
      for (const op of ops) {
        if (op.upsert) {
          const w = withKey(op.upsert, server);
          if (!sessions.has(w.key)) setChanged = true;
          sessions.set(w.key, w);
          changed.add(w.key);
        } else if (op.remove !== undefined) {
          sessions.delete(keyOf(server, op.remove));
          setChanged = true;
          // The server archives a session before it publishes the removal, so
          // this is the moment the archive gains a row, wherever it was closed.
          archiveLoaded = false;
        }
      }
      // A session added or removed redraws at once: a reply that navigates
      // to it (spawn, reopen) arrives right after this patch.
      if (setChanged) {
        setChanged = false;
        changed.clear();
        onSessions();
      } else {
        if (!frame) frame = requestAnimationFrame(flushSessions);
        // Not in the frame: a hidden tab runs no frames, and that is when the
        // ping matters.
        ping();
        keepFleet();
      }
    },
  ];
}
conn.subscribe("sessions", ...sessionHandlers(null));

// The servers this one links. A link that comes back resubscribes everything
// on it, each with the revision it holds; one removed takes its sessions along.
function onLinks(list) {
  links = list || [];
  linksLoaded = true;
  settings.onLinks(links, conn.server);
  drawServerPick();
  const names = new Set(links.map((l) => l.name));
  for (const [name, r] of remote) {
    if (names.has(name)) continue;
    for (const u of r.unsubs) u();
    r.unsubHost?.();
    remote.delete(name);
    for (const [k, m] of sessions) if (m.server === name) sessions.delete(k);
  }
  for (const l of links) {
    let r = remote.get(l.name);
    if (!r) {
      r = { unsubs: [], quota: { providers: [] }, host: null, unsubHost: null, loaded: false, state: l.state };
      remote.set(l.name, r);
      r.unsubs.push(
        conn.subscribe("sessions", ...sessionHandlers(l.name), undefined, undefined, l.name),
        conn.subscribe(
          "quota",
          (snap) => {
            r.quota = snap || { providers: [] };
            drawQuota();
          },
          (ops) => {
            for (const op of ops) if (op.set) r.quota = op.set;
            drawQuota();
          },
          undefined,
          undefined,
          l.name,
        ),
      );
    } else if (l.state === "linked" && r.state !== "linked") conn.resubscribe(l.name);
    r.state = l.state;
  }
  // A saved session of a server no longer linked will never be replaced.
  for (const [k, m] of sessions) if (m.stale && m.server && !names.has(m.server)) sessions.delete(k);
  for (const [k, m] of sessions) if (m.server) sessions.set(k, { ...m, off: isOff(m.server) });
  // Which servers are up changes what the archive can list: read it again.
  const sig = links.map((l) => `${l.name}:${l.state}`).join(",");
  if (sig !== linksSig) {
    linksSig = sig;
    archiveLoaded = false;
    if (booted && root.dataset.view === "fleet") loadArchive();
  }
  if (root.dataset.view === "fleet") watchHost("all");
  if (booted) onSessions();
}
conn.subscribe(
  "links",
  onLinks,
  (ops) => {
    for (const op of ops) if (op.set) onLinks(op.set);
  },
  () => onLinks([]), // a server without links
);
conn.subscribe(
  "quota",
  (snap) => {
    quota = snap || { providers: [] };
    drawQuota();
    settings.onQuota(quota);
  },
  (ops) => {
    for (const op of ops) if (op.set) quota = op.set;
    drawQuota();
    settings.onQuota(quota);
  },
);
conn.subscribe(
  "config",
  (w) => settings.onHomeConfig(w),
  (ops) => {
    for (const op of ops) if (op.set) settings.onHomeConfig(op.set);
    loadAgents();
  },
);
conn.connect();

// The Fleet this device saw last, drawn at once: with no connection it is all
// there is, and with one it shows until the live snapshot replaces it. A saved
// session carries `stale` until its server's snapshot arrives.
loadFleet().then((saved) => {
  if (!saved || booted || refused) return;
  seenAt = saved.saved_at;
  for (const m of saved.metas) sessions.set(m.key, { ...m, stale: true });
  booted = true;
  onSessions();
});

// The Fleet is written to the device at most every SAVE_MS, SAVE_MS after the
// first change since the last write, and at once when the page goes hidden
// (Android may freeze it right after). Only while the socket is open, so
// saved cards are never written back as fresh ones.
const SAVE_MS = 2000;
let fleetSave = null;
function keepFleet() {
  if (!fleetSave && conn.open) fleetSave = setTimeout(flushFleet, SAVE_MS);
}
// `closing`: the socket has just closed, and what the page holds is still
// what it last carried.
function flushFleet(closing = false) {
  clearTimeout(fleetSave);
  fleetSave = null;
  if ((conn.open || closing) && homeLive) saveFleet([...sessions.values()].filter((m) => !m.stale));
}
// The shown transcript, likewise, after each snapshot, delta and patch.
let trSave = null;
function keepTranscript() {
  if (!trSave && conn.open && shown) trSave = setTimeout(flushTranscript, SAVE_MS);
}
function flushTranscript(closing = false) {
  clearTimeout(trSave);
  trSave = null;
  // Not mid-turn: streamed entries exist only in the server's memory until the
  // turn ends, so a copy taken now could bring back half-written rows after a
  // restart. The turn's last patch arms the save again.
  if (sessions.get(shown)?.state === "working") return;
  if ((conn.open || closing) && shown && transcript.rev >= 0)
    saveTranscript(shown, { entries: [...transcript.entries.values()], rev: transcript.rev });
}
// Going hidden writes the Fleet even unchanged: its saved_at is when this
// device last saw the server, which is what "seen N min ago" says offline.
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) return;
  flushFleet();
  if (trSave) flushTranscript();
});

// Offline, a session with nothing kept on this device says so.
function drawOfflineNotice() {
  $("tr-offline").hidden = live || !shown || transcript.entries.size > 0;
}

// Patches that only update sessions the page already shows redraw once a
// frame, however many arrive, and only those sessions' tab and card (#158).
let changed = new Set();
let setChanged = false;
let frame = 0;

function flushSessions() {
  frame = 0;
  const ids = changed;
  changed = new Set();
  if (!booted) return;
  ordered = ordered.map((m) => sessions.get(m.key)).filter(Boolean);
  const r = route();
  for (const id of ids) if (sessions.has(id)) patchTab($("tablist"), sessions.get(id), r.view === "session" ? r.id : null, tabActions);
  if (r.view === "fleet") {
    const home = [...ids].filter((id) => !sessions.get(id)?.server);
    const regroup = home.some((id) => !patchCard($("cards"), sessions.get(id), openSession, fleetOrder, closeById));
    if (regroup) renderCards($("cards"), local(), openSession, fleetOrder, closeById);
    if (home.length < ids.size) renderRemotes();
    fleetMark(false);
    drawBand();
    markNeedsNet(); // the cards' Archive buttons
  } else if (r.view === "session" && ids.has(r.id)) renderMeta(sessions.get(r.id));
  else if (r.view === "window" && ids.has(r.id)) drawWindow(r);
  drawNav(); // the needs count
}

function onSessions() {
  const ids = order.arrange([...sessions.values()].sort((a, b) => a.created_at - b.created_at).map((m) => m.key));
  ordered = ids.map((id) => sessions.get(id));
  render();
  drawNav(); // the needs count
  ping();
  keepFleet();
}

// Notifications follow live sessions only. Until this server's snapshot
// lands the saved Fleet is all there is, and a session that changed while
// the page was closed would ping on every cold start.
let homeLive = false;
function ping() {
  if (homeLive) updatePing([...sessions.values()].filter((m) => !m.stale), { onOpen: openSession });
}

// -- rendering -----------------------------------------------------------------
// A window tab draws one window of a session's canvas and never becomes
// another view: window 1's pages would run in it a second time.
const openSession = (id) => route().view === "window" || go(`#s=${id}`);
// The service worker (sw.js): it keeps this build for offline use and raises
// notifications. A new build downloads beside this one; reloading takes it.
startWorker({
  onUpdate: () =>
    toast("A new version of aegis is ready.", { ms: Infinity, action: { label: "Reload", run: applyUpdate } }),
});
startInstall($("install-offer"));
setupPush((op, params) => conn.call(op, params));
listenForOpen(openSession);
setToastOpener(openSession);
// Toasts from the server (App.toast): every browser shows each one once, and
// an agent's goes to the desktop too while the page is hidden. The snapshot is
// always empty, so a reconnect replays nothing.
conn.subscribe(
  "toasts",
  () => {},
  (ops) => {
    for (const { toast: t } of ops) {
      if (!t) continue;
      toast(t.text, { kind: t.kind, from: t.from });
      pingToast(t, openSession);
    }
  },
);
// Close for the Fleet's Archive button and a middle click on a card or a tab:
// the session as it is now, not as the card or tab drew it.
const closeById = (id) => {
  const s = sessions.get(id);
  if (s) closeSession(s);
};
const tabActions = {
  onFocus: openSession,
  onClose: closeById,
  onMove: (id, before) => {
    order.move(id, before);
    onSessions();
  },
};

// This server's sessions, and a linked server's, in tab order.
const local = () => ordered.filter((m) => !m.server);
const on = (server) => ordered.filter((m) => m.server === server);

function render() {
  if (!booted) return;
  tellView(); // the view may have changed with the same session shown
  const r = route();
  if (r.view === "session" && !sessions.has(r.id)) {
    const far = forKey(r.id).server;
    // A far session whose server's list has not arrived yet: wait for it.
    if (far && (!linksLoaded || (linkOf(far) && !remote.get(far)?.loaded))) return;
    // Likewise a home session while only the device's saved list is drawn: a
    // notification's tap or a link opens a session the saved list may predate.
    if (!far && !homeLive) return;
    go("#fleet"); // closed here or elsewhere
    return;
  }
  renderTabs($("tablist"), ordered, r.view === "session" ? r.id : null, tabActions);
  $("tablist").querySelector(".tab.on")?.scrollIntoView({ block: "nearest", inline: "nearest" });
  $("tab-fleet").classList.toggle("on", r.view === "fleet");
  $("tab-add").classList.toggle("on", r.view === "spawn");
  $("settings-btn").classList.toggle("on", r.view === "settings");
  $("journal-btn").classList.toggle("on", r.view === "journal");
  $("help-btn").classList.toggle("on", r.view === "help");
  root.dataset.mode = r.view === "read" ? "read" : "live";
  // Quota rows redraw on a quota patch, the timer, or a change of view; never
  // on a sessions patch, which would take the hover tooltip with them.
  const viewKey = `${r.view}:${r.id || ""}`;
  const newView = viewKey !== quotaDrawnFor;
  quotaDrawnFor = viewKey;
  if (r.view === "fleet") {
    follow(null);
    show("fleet");
    renderCards($("cards"), local(), openSession, fleetOrder, closeById);
    renderRemotes();
    fleetMark(false);
    watchHost("all");
    drawBand();
    markNeedsNet(); // the cards' Archive buttons
    if (newView) drawQuota();
    if (!archiveLoaded) loadArchive();
    setTitle("Fleet · aegis");
  } else if (r.view === "spawn") {
    watchHost(null);
    follow(null);
    show("spawn");
    if (newView) loadHints();
    $("sp-text").focus();
    setTitle("New session · aegis");
  } else if (r.view === "settings") {
    watchHost(null);
    follow(null);
    show("settings");
    if (newView) settings.open();
    setTitle("Settings · aegis");
  } else if (r.view === "journal") {
    watchHost(null);
    follow(null);
    show("journal");
    if (newView) journal.open();
    setTitle("Journal · aegis");
  } else if (r.view === "help") {
    watchHost(null);
    follow(null);
    show("help");
    if (newView) helpTab.show(r.id);
    setTitle("Help · aegis");
  } else if (r.view === "session") {
    watchHost(forKey(r.id).server || "");
    // Shown first: follow() sizes the message box, which measures 0 while hidden.
    show("session");
    follow(r.id);
    renderMeta(sessions.get(r.id));
    if (newView) {
      drawQuota();
      drawSideHost();
    }
  } else if (r.view === "window") {
    // The session's card and transcript, as the session view has them (the
    // panes need the entries), with only window n drawn.
    watchHost(null);
    show("window");
    follow(sessions.has(r.id) ? r.id : null);
    drawWindow(r);
  } else {
    watchHost(forKey(r.id).server || "");
    show("session");
    follow(r.id);
    const m = archived.find((x) => x.key === r.id);
    if (m) renderMeta({ ...m, state: "archived" });
    else if (!archiveLoaded) loadArchive().then(render);
    if (newView) drawQuota();
  }
}

// `want`: "all" for the Fleet, a server's name for a session ("" is this
// one), null for nothing.
function watchHost(want) {
  const wants = (name) => want === "all" || want === name;
  for (const [name, r] of remote) {
    if (wants(name) && !r.unsubHost)
      r.unsubHost = conn.subscribe(
        "host",
        (snap) => {
          r.host = snap;
          drawHost();
        },
        (ops) => {
          for (const op of ops) if ("set" in op) r.host = op.set;
          drawHost();
        },
        undefined,
        undefined,
        name,
      );
    else if (!wants(name) && r.unsubHost) {
      r.unsubHost();
      r.unsubHost = null;
      r.host = null;
    }
  }
  if (wants("") && !unsubHost) {
    unsubHost = conn.subscribe(
      "host",
      (snap) => {
        host = snap;
        drawHost();
      },
      (ops) => {
        for (const op of ops) if ("set" in op) host = op.set;
        drawHost();
      },
    );
  } else if (!wants("") && unsubHost) {
    unsubHost();
    unsubHost = null;
    host = null;
  }
}

function drawHost() {
  if (root.dataset.view === "fleet") drawBand();
  else if (root.dataset.view === "session") drawSideHost();
}

function drawBand() {
  if (root.dataset.view !== "fleet") return;
  // Saved cards say how old they are.
  const stale = local().some((m) => m.stale);
  renderBand($("band"), { metas: local(), host, server: conn.server || localStorage.getItem("aegis.server") || "", link: stale ? seenText() : links.length ? "this server" : "", off: stale });
  for (const l of links) {
    const box = $("remotes").querySelector(`[data-server="${CSS.escape(l.name)}"]`);
    if (!box) continue;
    const r = remote.get(l.name);
    renderBand(box.querySelector(".band"), {
      metas: on(l.name),
      host: r?.host,
      server: l.name,
      link: linkLine(l),
      off: l.state !== "linked",
    });
  }
}

// The line beside a linked server's name in its band.
function linkLine(l) {
  const host = l.url.replace(/^https?:\/\//, "");
  if (l.state === "linked") return `${host} · linked${l.rtt_ms != null ? ` · ${l.rtt_ms} ms` : ""}`;
  const since = new Date(l.since * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  if (l.state === "offline") return `${host} · offline since ${since} · retrying`;
  if (l.state === "connecting") return `${host} · connecting`;
  return `${host} · ${l.state}: ${l.error}`;
}

// A block per linked server under this server's cards: its band, then its
// cards. The band is a copy of this server's, so a theme styles both alike.
function renderRemotes() {
  const host = $("remotes");
  const names = new Set(links.map((l) => l.name));
  for (const box of [...host.children]) if (!names.has(box.dataset.server)) box.remove();
  for (const l of links) {
    let box = host.querySelector(`[data-server="${CSS.escape(l.name)}"]`);
    if (!box) {
      box = document.createElement("div");
      box.className = "remote";
      box.dataset.server = l.name;
      const band = $("band").cloneNode(true);
      band.removeAttribute("id");
      band.querySelector(".band-mark")?.remove();
      for (const n of band.querySelectorAll("[id]")) n.removeAttribute("id");
      band.classList.add("far");
      const cards = document.createElement("section");
      cards.className = "cards";
      box.append(band, cards);
      host.append(box);
      const r = remote.get(l.name);
      if (r) renderBandQuota(band, { ...farQuota(r.quota), now: nowS() });
    }
    host.append(box); // keep the links' order
    const metas = on(l.name);
    if (metas.length) renderCards(box.querySelector(".cards"), metas, openSession, fleetOrder, closeById);
    else box.querySelector(".cards").replaceChildren();
  }
  drawBand();
}

// -- the session panel: usage and host (side.js opens their cards) ----------
// The panel follows the session it shows: the quota its harness and model
// spend, the host of its server.
let sideSession = null;
let sideQuotaKey = null; // the provider the card's quota rows were drawn for
let sideTilesSig = null;

const quotaOf = (server) => (server ? remote.get(server)?.quota : quota) || { providers: [] };
const hostOf = (server) => (server ? remote.get(server)?.host : host);
const sideProvider = (s) => providerFor(s, quotaOf(s.server || "").providers || []);

function ctxPct(s) {
  if (s.context_tokens == null || !s.context_window) return null;
  return Math.min(100, Math.round((100 * s.context_tokens) / s.context_window));
}

// The tiles and the card's context: on every patch of the session, rebuilt
// only when what they show changed.
function drawSideUsage(s) {
  const now = nowS();
  const p = sideProvider(s);
  const windows = p && p.state !== "failed" ? p.windows : [];
  const pct = ctxPct(s);
  const tokens = s.context_tokens != null ? fmtTokens(s.context_tokens) : null;
  const cost = money(s.cost_usd);

  $("s-tokens").textContent = tokens ?? "no turn yet";
  $("s-of").textContent = tokens == null ? "" : s.context_window ? `of ${fmtTokens(s.context_window)} · ${pct}%` : "tokens";
  $("s-bar").style.width = `${pct || 0}%`;
  $("s-ctxbar").hidden = !s.context_window;
  $("s-ctxnote").textContent =
    tokens != null && !s.context_window ? `${s.harness_label} does not report the model's window, so there is no share of it.` : "";
  $("s-cost").textContent = cost;
  // What the cache did (cache.py): red when the warm calls miss.
  const cn = $("s-counters");
  cn.hidden = s.cache_hit == null;
  if (s.cache_hit != null) {
    const hit = document.createElement("span");
    hit.textContent = `cache ${s.cache_hit}%`;
    hit.classList.toggle("bad", !!s.cache_alarm);
    hit.title = s.cache_alarm ? "Below 80% while warm: something broke the cache" : "";
    cn.replaceChildren(`tools ${s.tool_calls ?? 0} · `, hit, ` · compactions ${s.compactions ?? 0}`);
  }

  // With a share of the window, context leads and the quota follows; without
  // one, the quota's windows take the row and context moves to the foot.
  const lead = pct != null || !windows.length;
  const shown = windows.slice(0, lead ? 2 : 3);
  const left = !windows.length ? "" : lead ? cost : `${tokens ? `${tokens} ctx · ` : ""}${cost}`;
  const next = shown.find((w) => w.resets_at != null);
  const right = next ? countdown(next.resets_at - now) : "";
  const sig = JSON.stringify([
    pct,
    tokens,
    cost,
    p && p.state,
    shown.map((w) => [w.kind, Math.round(w.percent), w.severity, w.projected, Math.round(100 * (elapsed(w, now) || 0))]),
    left,
    right,
  ]);
  if (sig === sideTilesSig) return;
  sideTilesSig = sig;
  const tiles = [];
  if (lead) tiles.push(tile("context", pct != null ? `${pct}%` : tokens ?? "–", { pct }));
  for (const w of shown) tiles.push(quotaTile(p, w, now));
  if (!windows.length) {
    tiles.push(tile("cost", cost));
    tiles.push(tile("quota", p ? "error" : "none", { severity: p ? "stale" : "normal", title: p ? p.note : "" }));
  }
  $("s-tiles").replaceChildren(...tiles);
  $("s-foot-l").textContent = left;
  $("s-foot-r").replaceChildren(...(right ? [icon("again"), ` ${right}`] : []));
  $("s-foot").hidden = !left && !right;
}

// The card's quota rows: on a quota patch, the timer, a change of view, or a
// session that now spends another provider; never on a plain sessions patch.
function drawSideQuota(s) {
  const p = sideProvider(s);
  sideQuotaKey = `${s.server || ""}:${p ? p.name : ""}`;
  const now = nowS();
  $("s-quota-head").textContent = p ? `Quota · ${p.label}` : "Quota";
  const head = $("s-quota-age");
  if (!p) {
    head.textContent = "";
    const what = s.harness === "opencode" ? (s.model || "").split("/")[0] || "this model" : s.harness_label;
    const n = document.createElement("p");
    n.className = "cnote";
    n.textContent = `aegis reads no quota for ${what}.`;
    $("s-quota").replaceChildren(n);
    return;
  }
  head.textContent = p.read_at != null ? `read ${ago(p.read_at)}` : ""; // "read just now", "read 2m ago"
  // A member's own lines, on its own server's gauges only.
  const lines = (w) => (s.server ? null : linesOf(p.name, w.kind));
  const rows = p.state === "failed" ? [] : p.windows.map((w) => quotaSideRow(p, w, now, lines(w)));
  if (p.state !== "ok") {
    const n = document.createElement("p");
    n.className = "cnote gnote";
    n.textContent = p.state === "stale" ? `${p.note}; this reading is ${age(now - p.read_at)} old.` : p.note;
    rows.push(n);
  }
  $("s-quota").replaceChildren(...rows);
}

function drawSideHost() {
  const s = sideSession;
  const server = s?.server || "";
  const h = s && hostOf(server);
  $("s-host-sec").hidden = !h;
  if (!h) return;
  const name = server || conn.server || "this server";
  $("s-host-name").textContent = name;
  $("s-host-head").textContent = `Host · ${name}`;
  const meter = (label, pct) => tile(label, `${pct}%`, { pct, kind: "h", severity: hostSeverity(pct) });
  const tiles = [meter("CPU", h.cpu), meter("RAM", h.ram.pct)];
  const rows = [hostRow("CPU", h.cpu, ""), hostRow("RAM", h.ram.pct, `${h.ram.used_gb} / ${h.ram.total_gb} GB`)];
  if (h.disk) {
    tiles.push(meter("disk", h.disk.pct));
    rows.push(hostRow("Disk", h.disk.pct, `${h.disk.used_gb} / ${h.disk.total_gb} GB`));
  }
  $("s-host-tiles").replaceChildren(...tiles);
  $("s-host-rows").replaceChildren(...rows);
  const live = (s.server ? on(s.server) : local()).filter((m) => m.state !== "stopped");
  const sized = live.filter((m) => m.context_window && m.context_tokens);
  $("s-host-live").textContent = String(live.length);
  $("s-host-ctx").textContent = sized.length
    ? `${Math.round(sized.reduce((a, m) => a + Math.min(100, (100 * m.context_tokens) / m.context_window), 0) / sized.length)}%`
    : "–";
}

// A linked server's quota, less the providers this server already shows for
// the same account (each reading names its account by a hash).
function farQuota(q) {
  const mine = new Set((quota.providers || []).filter((p) => p.account).map((p) => `${p.name}:${p.account}`));
  const providers = q.providers || [];
  const dup = (p) => p.account && mine.has(`${p.name}:${p.account}`);
  return { quota: { providers: providers.filter((p) => !dup(p)) }, same: providers.filter(dup), home: conn.server };
}

function drawQuota() {
  if (root.dataset.view === "fleet") {
    renderBandQuota($("band"), { quota, now: nowS() });
    for (const box of $("remotes").children) {
      const r = remote.get(box.dataset.server);
      if (r) renderBandQuota(box.querySelector(".band"), { ...farQuota(r.quota), now: nowS() });
    }
  } else if (root.dataset.view === "session" && sideSession) {
    drawSideQuota(sideSession);
    sideTilesSig = null; // the countdowns and ticks moved
    drawSideUsage(sideSession);
  }
}

// Countdowns and the tick move with the clock; their unit is minutes.
setInterval(drawQuota, 30 * 1000);

// The last TAB_CACHE tabs left, newest last: a return to one shows it at once
// and asks only for what changed since (transcript/wire.py, Fold.snapshot).
const TAB_CACHE = 8;
const kept = new Map(); // key -> transcript.stash()

let follows = 0; // which follow() a device read answers

// The worker shows a push unless a window shows its session in front of the
// person (sw.js), so it hears what this one shows whenever that may change.
// A window tab shows a session's panes, not its transcript: it quiets no push,
// and a notification's click never opens a session in it.
function tellView() {
  const win = route().view === "window";
  navigator.serviceWorker?.controller?.postMessage({
    t: "view",
    key: win ? "" : shown || "",
    visible: !document.hidden && document.hasFocus(),
    window: win,
  });
}
for (const ev of ["focus", "blur"]) window.addEventListener(ev, tellView);
document.addEventListener("visibilitychange", tellView);
// A worker that took this page over, on a first visit or after an update, has
// heard nothing from it yet.
navigator.serviceWorker?.addEventListener("controllerchange", tellView);

function follow(id) {
  if (shown === id) return;
  if (trSave) flushTranscript(); // before its rows go
  closeSide();
  if (unsubTranscript) unsubTranscript();
  unsubTranscript = null;
  if (shown) {
    kept.delete(shown);
    kept.set(shown, transcript.stash());
    while (kept.size > TAB_CACHE) kept.delete(kept.keys().next().value);
  } else transcript.clear();
  drawNavNow(); // at once: the old session's navigator goes with its rows
  find.close(false); // it searched the old session
  if (dictation.target?.el === input) dictation.stop("tab"); // a recording belongs to its session
  shown = id;
  tellView();
  const turn = ++follows;
  atts.show(id);
  drawQueued();
  if (!id) {
    canvas.clear();
    return viewer.show(null);
  }
  const saved = kept.get(id);
  if (saved) {
    kept.delete(id);
    transcript.restore(saved);
  }
  // The divider is placed by the first snapshot or delta after this switch,
  // and kept across a resubscribe of the same session.
  let placed = false;
  const where = forKey(id);
  setFileBase(where.server ? `/via/${where.server}` : "");
  viewer.show(id); // after the base: a far artifact's frame is built from it
  if (sessions.get(id)) showCanvas(sessions.get(id));
  else canvas.clear();
  const subscribe = () => {
    unsubTranscript = conn.subscribe(
      `transcript:${where.log_id}`,
      (data) => {
        // A server that cannot resume from the revision asked (the session was
        // reset, its store rewritten) answers in full, and the full answer
        // replaces whatever was drawn.
        if (data.since !== undefined) transcript.resume(data);
        else transcript.snapshot(data);
        canvas.refresh();
        keepTranscript();
        drawOfflineNotice();
        if (!placed) {
          transcript.setSince(sinceText(sessions.get(id)));
          askRecap(false); // the server decides whether it is worth one
          if (landUnread === id) transcript.firstUnread();
          landUnread = null;
          if (landEntry && landEntry.key === id) transcript.reveal(landEntry.id);
          landEntry = null;
        }
        placed = true;
        // Read by scripts/bench.py: when the snapshot was drawn and painted.
        const mark = (window.__a2snapshot = { at: performance.now(), count: transcript.entries.size });
        requestAnimationFrame(() => (mark.painted = performance.now()));
      },
      (ops) => {
        for (const op of ops) if (op.probe) artifacts.probe(op.probe, id, fileUrl(op.probe.url));
        // A new agent message while another tab is open dots the transcript's.
        const fresh = ops.some((op) => op.upsert?.unread && !transcript.entries.has(op.upsert.id));
        transcript.apply(ops);
        if (fresh) canvas.mark("transcript");
        for (const op of ops) if (op.upsert?.kind === "artifact" || op.upsert?.kind === "file") canvas.onEntry(op.upsert);
        keepTranscript();
      },
      undefined,
      // Holding nothing, a full snapshot mounts only the last rows; a delta
      // from -1 would mount every row through apply().
      () => (transcript.rev >= 0 ? transcript.rev : null),
      where.server,
    );
  };
  if (saved) {
    subscribe();
    drawOfflineNotice();
  } else
    // What this device kept of the session, drawn at once. The subscription
    // waits for it (a local read, a few ms) so that it asks only for what
    // changed since its revision.
    loadTranscript(id).then((copy) => {
      if (turn !== follows) return; // moved on
      if (copy) transcript.snapshot({ entries: copy.entries, rev: copy.rev });
      canvas.refresh();
      drawOfflineNotice();
      subscribe();
    });
  menu.close();
  $("input").value = localStorage.getItem(`aegis.draft.${id}`) || "";
  autosize();
  drawSending();
}

// The divider's label: how long since this session was last read.
function sinceText(s) {
  const t = s?.last_read_at;
  if (!t) return "new since you left";
  const m = Math.round((Date.now() / 1000 - t) / 60);
  return `new since you left · ${m < 60 ? `${m} min` : `${Math.round(m / 60)} h`}`;
}

function fmtTokens(n) {
  return n >= 1000 ? `${Math.round(n / 1000)}k` : String(n);
}

// The plan in the sidebar: totals in the heading, each item's time, and the
// doing spinner turning only while the agent works. The rows are rebuilt only
// when the items change: a session publishes several times a second during a
// turn, and a new row would restart the spinner's animation each time.
function drawPlan(s) {
  const plan = s.plan || [];
  $("s-plan-sec").hidden = !plan.length;
  $("s-plan").classList.toggle("live", s.attention === "working");
  const key = JSON.stringify([s.key, plan.map((i) => [i.text, i.state])]);
  if ($("s-plan").dataset.key !== key) {
    $("s-plan").dataset.key = key;
    drawPlanRows(plan);
  }
  tickSidePlan(s);
}

// The panel keeps the two items done last and everything still to do; the
// rest is one line whose card holds the whole plan. Each row carries its
// index, so the clock finds its time cell in either list.
function drawPlanRows(plan) {
  const mark = { done: "done", doing: "working", pending: "waiting" };
  const row = (i, k) => {
    const d = document.createElement("div");
    d.className = i.state;
    d.dataset.k = k;
    d.append(glyph(mark[i.state] || "waiting"), span("", i.text), span("t", ""));
    return d;
  };
  const open = plan.findIndex((i) => i.state !== "done");
  const from = Math.max(0, (open < 0 ? plan.length : open) - 2);
  $("s-plan").replaceChildren(...plan.slice(from).map((i, k) => row(i, from + k)));
  $("p-plan").hidden = !from;
  $("s-plan-more").textContent = `+ ${from} done above`;
  $("s-plan-all").replaceChildren(...(from ? plan.map(row) : []));
}

// The parts that move with the clock, updated in place every second: a
// redraw would restart the spinner's animation each time.
function tickSidePlan(s) {
  const plan = s.plan || [];
  const t = planTimes(s);
  const done = plan.filter((i) => i.state === "done").length;
  let head = `Plan ${done}/${plan.length}`;
  if (t) head += ` · ${dur(t.work)} work · ${dur(t.idle)} idle`;
  if (t && t.left != null) head += ` · ~${dur(t.left)} left`;
  $("s-plan-h").textContent = head;
  $("s-plan-h").title = head; // one line in the panel; whole here and in the card
  $("s-plan-card-h").textContent = head;
  for (const d of document.querySelectorAll("#s-plan > div, #s-plan-all > div")) {
    const k = Number(d.dataset.k);
    const i = plan[k];
    d.lastChild.textContent = t && i && i.state !== "pending" ? dur(t.items[k]) : "";
  }
}

// The sidebar's Journal row: the shown session's two newest entries today and
// its count; the card lists its five newest as the Journal view draws them.
// Fetched when the shown session changes or the journal changes.
let sideJournalFor = null;
let sideJournalDay = "";
const noJournal = () => {
  $("s-journal").textContent = "nothing yet today";
  $("s-journal-at").textContent = "";
  $("s-journal-peek").hidden = true;
  $("s-journal-peek").replaceChildren();
  $("s-journal-all").replaceChildren(span("cnote", "Nothing journaled today."));
};
async function drawSideJournal(key, force = false) {
  const day = new Date().toDateString(); // "today" moves at midnight
  if (!key || (!force && sideJournalFor === key && sideJournalDay === day)) return;
  // Another session's rows go at once: this one never shows them while it loads.
  if (sideJournalFor !== key) {
    $("s-journal").textContent = "…";
    $("s-journal-at").textContent = "";
    $("s-journal-peek").hidden = true;
    $("s-journal-peek").replaceChildren();
    $("s-journal-all").replaceChildren();
  }
  sideJournalFor = key;
  sideJournalDay = day;
  const linked = key.includes("/");
  $("s-journal-sec").hidden = linked;
  if (linked) return;
  let res;
  try {
    res = await conn.call("journal.rows", { session: key, since: "today", limit: 5, counts: true });
  } catch {
    if (sideJournalFor === key) {
      sideJournalFor = null; // the next render retries
      noJournal();
    }
    return;
  }
  if (sideJournalFor !== key) return;
  if (!res.rows.length) {
    noJournal();
    return;
  }
  const c = res.counts || {};
  const total = Object.values(c).reduce((a, b) => a + b, 0);
  $("s-journal").textContent =
    `${total} ${total === 1 ? "entry" : "entries"}` + (c.commit ? ` · ${c.commit} commit${c.commit === 1 ? "" : "s"}` : "");
  $("s-journal-at").textContent = `last ${res.rows[0].time}`;
  $("s-journal-peek").replaceChildren(...res.rows.slice(0, 2).map((r) => {
    const line = document.createElement("div");
    line.className = `k-${r.kind}`;
    const g = span("g", "");
    g.append(entryIcon(r.glyph));
    line.append(g, span("x", (r.tag ? `${r.tag}: ` : "") + (r.hash ? `${r.hash} ` : "") + r.text), span("t", r.time));
    return line;
  }));
  $("s-journal-peek").hidden = false;
  const thread = document.createElement("div");
  thread.className = "jthread";
  thread.append(...res.rows.map((r) => {
    const row = entry(r, false);
    row.classList.add("le");
    // On a phone the drawer covers the transcript: close it so the reveal shows.
    opens(row, () => {
      if (!r.source) return;
      transcript.reveal(r.source);
      closeCard();
      if (drawerMode.matches) closeSide();
    });
    return row;
  }));
  $("s-journal-all").replaceChildren(thread);
}

// A window tab: its panes, and the session's name in its title so the
// browser's tab list tells the windows apart. A session gone from the list
// leaves the window empty.
function drawWindow(r) {
  const s = sessions.get(r.id);
  if (s) showCanvas(s);
  else canvas.clear();
  setTitle(`${s ? s.title || s.handle : "aegis"} · window ${r.n}`);
}

function renderMeta(s) {
  if (!s) return;
  if (!editing.has("title")) $("s-title").textContent = s.title || "untitled";
  if (!editing.has("handle")) $("s-handle").textContent = s.handle;
  sideSession = s;
  $("c-title").textContent = s.title || "untitled";
  const born = s.created_at ? ` · started ${new Date(s.created_at * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}` : "";
  $("c-sub").textContent = `${s.handle}${born}`;
  $("s-harness").textContent = s.harness_label || s.harness || "";
  $("s-model").textContent = s.model_id && s.model_id !== s.model ? `${s.model} → ${s.model_id}` : s.model;
  $("s-effort").textContent = s.effort || "";
  $("s-perm").textContent = s.permission || "";
  $("s-server").textContent = s.server || conn.server || "this server";
  if (s.attention === undefined) {
    // The archived read view: a stored meta has no attention, so the state alone.
    $("s-status").textContent = s.state;
    $("s-status").className = "st";
  } else {
    $("s-status").replaceChildren(glyph(s.attention), document.createTextNode(` ${LABEL[s.attention] || s.state}`));
    $("s-status").className = `st at-${s.attention}`;
  }
  $("s-ask").hidden = !s.attention_line;
  $("s-ask").textContent = s.attention_line || "";
  $("s-ask").className = `askbox at-${s.attention}`;
  drawPlan(s);
  if (s.key === shown) showCanvas(s);
  drawSideJournal(s.key);
  drawReplies(s);
  drawCold(s);
  drawClosing(s);
  $("s-cwd").textContent = s.cwd;
  $("chip-model").textContent = s.model;
  $("chip-model").title = s.model_id || "";
  $("chip-effort").textContent = `${s.effort} effort`;
  $("chip-perm").textContent = s.permission;
  const p = sideProvider(s);
  if (`${s.server || ""}:${p ? p.name : ""}` !== sideQuotaKey) drawSideQuota(s);
  drawSideUsage(s);
  const mons = s.monitors || [];
  $("s-mon-sec").hidden = !mons.length;
  renderMonitors($("s-monitors"), mons);
  const working = s.state === "working";
  $("interrupt").hidden = !working;
  // A member reads another's session and changes nothing in it (tokens.py).
  const theirs = !mine(s);
  $("restart").disabled = working || theirs;
  $("working").hidden = !working;
  $("stop-session").disabled = s.state === "stopped" || theirs;
  $("interrupt").disabled = theirs;
  $("close").disabled = theirs;
  if (working && workingSince == null) workingSince = Date.now();
  if (!working) workingSince = null;
  // A linked server that is down: the transcript stays readable, nothing sends.
  const down = s.off ? linkOf(s.server) : null;
  input.disabled = $("attach").disabled = !!down || theirs;
  drawSending(!!down || theirs);
  // Chrome counts a placeholder in scrollHeight: measure again when it changes.
  const placeholder = theirs
    ? s.server
      ? `A session on ${s.server}. You can read it; acting through the link is for an admin here.`
      : `${ownerOf(s)}'s session. You can read it; only ${ownerOf(s)} can write to it.`
    : down
    ? `${s.server} is ${down.state} since ${new Date(down.since * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}; nothing can be sent until it is back.`
    : s.state === "stopped"
      ? "Stopped; your next message resumes it."
      : touch.matches
        ? "Message the agent"
        : "Message the agent. Enter sends, Shift+Enter adds a line, / for commands, Esc interrupts.";
  if (input.placeholder !== placeholder) {
    input.placeholder = placeholder;
    autosize();
  }
  setTitle(`${s.title || s.handle} · aegis`);
}

setInterval(() => {
  tickMonitors();
  if (workingSince != null) $("working-meta").textContent = `${Math.round((Date.now() - workingSince) / 1000)}s, Esc interrupts`;
  if (root.dataset.view === "fleet" && local().some((m) => m.stale)) drawBand();
  if (root.dataset.view === "fleet") for (const c of document.querySelectorAll(".card")) {
    const m = sessions.get(c.dataset.id);
    if (m) {
      c.querySelector(".when").textContent = ago(m.last_activity);
      tickPlan(c, m);
    }
  }
  if (root.dataset.view === "session" && shown) {
    const s = sessions.get(shown);
    if (s && (s.plan || []).length) tickSidePlan(s);
    drawCold(s);
  }
}, 1000);

// -- version: the running aegis and the latest release ------------------------
// Asked again every hour, so a tab left open learns about a new release; the
// server caches PyPI's answer for that long anyway.

function span(cls, text, name) {
  const el = document.createElement("span");
  el.className = cls;
  el.textContent = text;
  if (name) el.prepend(icon(name));
  return el;
}

async function loadVersion() {
  let v;
  try {
    v = await conn.call("server.version");
  } catch (e) {
    return;
  }
  const run = v.running;
  const dev = run.dev;
  const short = run.commit ? run.commit.slice(0, 7) : null;
  const shown = dev && short ? short : run.version || "unknown";
  const behind = v.status === "behind";

  $("ver-head").textContent = `aegis on ${conn.server}`;
  $("ver-run").replaceChildren(span("v", shown), ...(dev ? [span("tag", "dev")] : []));
  // aegis-dev builds from the resolved commit, so the ref is often the commit again.
  const ref = run.ref && !(run.commit || "").startsWith(run.ref) ? run.ref : null;
  $("ver-ref-row").hidden = !ref;
  $("ver-ref").textContent = ref || "";
  $("ver-base-row").hidden = !dev || !run.version;
  $("ver-base").textContent = run.version || "";
  const mark = v.status === "current" ? [span("ok", " current", "read")] : behind ? [span("upd", " update", "arrow-up")] : [];
  $("ver-latest").replaceChildren(span("v", v.latest || "unknown"), ...mark);
  $("ver-line").replaceChildren(
    span("", "aegis"),
    span("v", shown),
    ...(dev ? [span("tag", "dev")] : behind ? [span("upd", " update", "arrow-up")] : v.status === "current" ? [span("ok", "", "read")] : []),
  );
  $("ver-sec").hidden = false;

  const top = $("ver-top");
  top.replaceChildren(span("", shown), ...(dev ? [span("tag", "dev")] : behind ? [span("upd", "", "arrow-up")] : []));
  top.title = behind
    ? `aegis ${run.version}; ${v.latest} is out: uv tool upgrade aegis-harness`
    : dev
      ? `aegis built from ${run.commit || "a source tree"}${ref ? ` (${ref})` : ""}, based on ${run.version}; latest release ${v.latest || "unknown"}`
      : `aegis ${run.version}; latest release ${v.latest || "unknown"}`;
  top.hidden = false;
}
setInterval(() => conn.open && loadVersion(), 3600 * 1000);

// -- the recap -----------------------------------------------------------------
// Asked on landing and by the sparkle or the row's refresh. The entry arrives on
// the transcript channel; the answer only says why there is none, and only to a
// person who asked: the request on landing never writes a hint.
async function askRecap(force) {
  const id = shown;
  if (!id) return;
  let why = "";
  let kind = "error";
  try {
    const r = await callFor(id, "recap.request", { force });
    if (r.status === "off" || r.status === "failed") why = r.why;
    else if (r.status === "busy") {
      why = "the agent is still working; ask again when it is done";
      kind = "info";
    }
  } catch (e) {
    why = e.message;
  }
  if (force && why && shown === id) toast(why, { kind });
}
$("entries").addEventListener("click", (ev) => {
  if (ev.target.closest("[data-recap=force]")) askRecap(true);
});
// Coming back to the page is landing again; the server decides.
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible" && shown) askRecap(false);
});

// -- Open natively on a sent file's card, or in the viewer's bar --------------
document.querySelector(".v-session .col").addEventListener("click", async (ev) => {
  const b = ev.target.closest(".fbar .native");
  if (!b) return;
  b.disabled = true;
  try {
    await conn.call("file.open", { file_id: b.dataset.fileId, name: b.dataset.name });
  } catch (e) {
    toast(e.message, { kind: "error" });
  } finally {
    setTimeout(() => (b.disabled = false), 800);
  }
});

// Show on a finished artifact's card: the frame again, read-only.
$("entries").addEventListener("click", (ev) => {
  const b = ev.target.closest(".acard .show");
  if (!b) return;
  const rowEl = b.closest(".row");
  const e = transcript.entries.get(rowEl.dataset.id);
  const card = rowEl.querySelector(".acard");
  const old = card.querySelector(".stage");
  if (old) old.remove();
  else card.append(artifactStage(e));
});

// -- Show the file a Read, Write or Edit row used -------------------------------
$("entries").addEventListener("click", async (ev) => {
  const b = ev.target.closest(".peekbar .peek");
  if (!b) return;
  b.disabled = true;
  try {
    // The row comes back with the card in it; this node is replaced.
    await conn.call("file.peek", { log_id: forKey(shown).log_id, entry_id: b.dataset.entry });
  } catch (e) {
    b.nextElementSibling.textContent = e.message;
    b.disabled = false;
  }
});

// -- Open a path a reply named, in the viewer at its line -----------------------
$("entries").addEventListener("click", async (ev) => {
  const code = ev.target.closest(".md code.path");
  if (!code) return;
  const row = code.closest(".row");
  const body = row.querySelector(":scope > .body");
  const line = Number(/:(\d+)$/.exec(code.textContent.trim())?.[1]) || null;
  const open = async (pick) => {
    body.querySelector(":scope > .patherr")?.remove();
    try {
      const files = await conn.call("file.peek", { log_id: forKey(shown).log_id, entry_id: row.dataset.id, index: Number(code.dataset.index), pick });
      viewer.open({ files, at: 0, remote: false, glyph: "file", line });
    } catch (e) {
      const note = document.createElement("div");
      note.className = "patherr";
      note.textContent = e.message;
      // A path under both the config root and the session's folder: the person picks.
      if (e.code === "ambiguous_path") {
        const picks = document.createElement("div");
        for (const [pick, text] of [["root", "Open the root's"], ["cwd", "Open the session folder's"]]) {
          const b = document.createElement("button");
          b.className = "btn";
          b.textContent = text;
          b.addEventListener("click", () => open(pick));
          picks.append(b);
        }
        note.append(picks);
      }
      body.append(note);
    }
  };
  await open();
});

// -- archive -------------------------------------------------------------------
let archived = [];
let archiveCursor = null; // the next page's cursor; null: nothing more
let archiveTotal = 0; // the first page's count of everything matching
let archiveServer = null; // the server filter: null for every server
let archiveCounts = {}; // the first page's count per server
let archiveOffline = []; // linked servers that were down for the first page
let archiveWorkers = false; // queue workers shown: the chip is on
let archiveWorkerCount = 0; // the first page's count of matching queue workers
let archiveLoaded = false;
let archiveTimer = null;

// The first page, or with `more` the next one after what is shown. A button
// asks for more, never scrolling: the page grows only when a person asks.
async function loadArchive(more = false) {
  archiveLoaded = true;
  const q = $("arch-q").value.trim();
  const params = q ? { query: q } : {};
  if (archiveServer) params.server = archiveServer;
  if (archiveWorkers) params.workers = true;
  if (more && archiveCursor) params.cursor = archiveCursor;
  try {
    const r = await conn.call("archive.list", params);
    // Every row names its server; this server's rows are keyed by log id alone.
    const items = r.items.map((m) => withKey(m, m.server && m.server !== conn.server ? m.server : null));
    archived = more ? [...archived, ...items] : items;
    archiveCursor = r.cursor;
    if (!more) {
      archiveTotal = r.total;
      if (!archiveServer) archiveCounts = r.counts || {};
      archiveOffline = r.offline || [];
      archiveWorkerCount = r.workers || 0;
    }
  } catch (e) {
    // Offline, or failed: the next render reads it again.
    if (!more) [archived, archiveCursor, archiveTotal, archiveLoaded] = [[], null, 0, false];
  }
  renderArchive($("arch-list"), archived, {
    onReopen: reopen,
    onRead: (id) => go(`#read=${id}`),
  });
  $("arch-more").hidden = !archived.length;
  $("arch-count").textContent = `Showing ${archived.length} of ${Math.max(archiveTotal, archived.length)}`;
  $("arch-next").hidden = !archiveCursor;
  drawArchiveServers();
  drawArchiveWorkers();
  fleetMark(false);
}

// Queue workers are left out of the archive unless this chip is on (#331): a
// worker's result went to whoever enqueued it. The chip says how many match,
// and goes away when none do.
function drawArchiveWorkers() {
  const b = $("arch-workers");
  b.hidden = !archiveWorkerCount && !archiveWorkers;
  b.classList.toggle("on", archiveWorkers);
  b.setAttribute("aria-pressed", String(archiveWorkers));
  b.querySelector("b").textContent = archiveWorkerCount;
}
$("arch-workers").addEventListener("click", () => {
  archiveWorkers = !archiveWorkers;
  loadArchive();
});

// The archive's server filter, once a server is linked: every server with its
// count, and a down one named so its rows are not silently missing.
function drawArchiveServers() {
  const box = $("arch-servers");
  box.hidden = !links.length;
  if (!links.length) return;
  const all = Object.values(archiveCounts).reduce((a, n) => a + n, 0);
  const pick = (label, server, n) => {
    const b = document.createElement("button");
    b.type = "button";
    b.dataset.server = server || "";
    b.className = archiveServer === server ? "on" : "";
    b.append(label, Object.assign(document.createElement("b"), { textContent: n }));
    b.addEventListener("click", () => {
      archiveServer = server;
      loadArchive();
    });
    return b;
  };
  const off = (name) => Object.assign(document.createElement("span"), { className: "off", textContent: `${name} offline` });
  box.replaceChildren(
    pick("All", null, all),
    ...Object.entries(archiveCounts).map(([name, n]) => pick(name, name === conn.server ? conn.server : name, n)),
    ...archiveOffline.map(off),
  );
}
$("arch-next").addEventListener("click", () => loadArchive(true));

// The Fleet's selection: a card or an archive row, by log id, re-marked after
// every redraw because both are rebuilt from scratch.
let fleetSel = null;

function fleetItems() {
  return [...document.querySelectorAll("#cards .card, #remotes .card, #arch-list tr[data-id]")];
}

function fleetMark(scroll) {
  for (const n of document.querySelectorAll("#cards .sel, #remotes .sel, #arch-list .sel")) n.classList.remove("sel");
  const n = fleetItems().find((x) => x.dataset.id === fleetSel);
  if (!n) {
    fleetSel = null;
    return;
  }
  n.classList.add("sel");
  if (scroll) n.scrollIntoView({ block: "nearest" });
}

function fleetMove(delta) {
  const items = fleetItems();
  const i = items.findIndex((x) => x.dataset.id === fleetSel);
  const n = i < 0 ? items[0] : items[i + delta];
  if (!n) return;
  fleetSel = n.dataset.id;
  fleetMark(true);
}

function fleetOpen() {
  const n = fleetItems().find((x) => x.dataset.id === fleetSel);
  if (n) go(n.classList.contains("card") ? `#s=${fleetSel}` : `#read=${fleetSel}`);
}

$("arch-q").addEventListener("input", () => {
  clearTimeout(archiveTimer);
  archiveTimer = setTimeout(loadArchive, 200);
});

async function reopen(id) {
  try {
    await callFor(id, "session.reopen");
    archiveLoaded = false;
    go(`#s=${id}`);
  } catch (e) {
    $("side-error").textContent = e.message;
  }
}
$("reopen").addEventListener("click", () => {
  const r = route();
  if (r.view === "read") reopen(r.id);
});

// -- the new-tab composer -------------------------------------------------------
// An agent is a preset: picking one fills the other chips, and changing a chip
// marks the agent `name*` until reset. Enter spawns and sends in one call.
let roster = { agents: [], harnesses: [], models: {}, default: null, cwd: "" };
// What each harness can run, by server: the catalog `config.detect` reads from
// the harness itself, the one the server validates a model change against. The
// agents' own models are on offer until it answers, and after it.
let spModels = { server: undefined, by: {} };
// The server a new session starts on: null for this one. Its directory line
// names it once a server is linked, since a path exists on one machine only.
let spServer = null;
// What help.state said for this person when the new tab last opened; null
// until it answers, and then no hint shows.
let helpState = null;

function drawServerPick() {
  const pick = $("sp-server");
  const show = links.length > 0;
  pick.hidden = $("sp-sep").hidden = !show;
  if (!show) {
    if (spServer !== null) setSpServer(null);
    return;
  }
  pick.options = [
    { value: "", label: conn.server || "this server" },
    ...links.map((l) => ({
      value: l.name,
      label: l.state === "linked" ? l.name : `${l.name} (${l.state})`,
      disabled: l.state !== "linked",
    })),
  ];
  if (spServer && linkOf(spServer)?.state !== "linked") setSpServer(null);
  pick.value = spServer || "";
}

function setSpServer(server) {
  if (server === spServer) return;
  spServer = server;
  $("sp-server").value = server || "";
  $("sp-cwd").value = ""; // the other machine's directories
  delete $("sp-agent").dataset.picked;
  loadAgents();
  drawHints();
}
$("sp-server").addEventListener("change", () => setSpServer($("sp-server").value || null));
const LAST_AGENT = "aegis.lastAgent";
const PICKS = ["harness", "model", "effort", "permission"];

async function loadAgents() {
  // The picked agent as the chips last knew it, to tell the person's overrides
  // from values an edit to .aegis.yaml has since changed.
  const was = current();
  try {
    roster = await conn.call("agents.list", {}, spServer);
  } catch (e) {
    toast(e.message, { kind: "error" });
    return;
  }
  // A reconnect or a config change rebuilds the options; the chips keep what
  // the person set and follow the agent everywhere else.
  const before = Object.fromEntries(PICKS.map((k) => [k, $(`sp-${k}`).value]));
  $("sp-harness").options = roster.harnesses.map((h) => ({
    value: h.name,
    label: h.supported ? h.name : `${h.name} (not supported yet)`,
    disabled: !h.supported,
  }));
  $("sp-agent").options = roster.agents.map((a) => {
    const why = a.error || (a.enabled ? "" : `${a.harness} is not supported yet`);
    return { value: a.name, label: why ? `${a.name} (${why})` : a.name, disabled: !a.enabled };
  });
  const usable = roster.agents.filter((a) => a.enabled).map((a) => a.name);
  const keep = $("sp-agent").dataset.picked;
  const start = [keep, localStorage.getItem(LAST_AGENT), roster.default].find((n) => usable.includes(n)) || usable[0];
  if (!$("sp-cwd").value) $("sp-cwd").value = roster.cwd;
  $("sp-error").textContent = roster.config_error
    ? `.aegis.yaml does not parse; aegis is using the last version that did. ${roster.config_error}`
    : roster.agents.length
      ? ""
      : "No agents yet. Set them up in Settings (Alt+S).";
  if (keep && usable.includes(keep)) {
    $("sp-agent").value = keep;
    const now = current();
    for (const k of PICKS) $(`sp-${k}`).value = was && before[k] === was[k] ? now[k] : before[k];
    fillModels($("sp-harness").value);
    markDiffs();
  } else if (start) pickAgent(start);
  loadSpModels();
}

function current() {
  return roster.agents.find((a) => a.name === $("sp-agent").value);
}

async function loadSpModels() {
  const server = spServer;
  let found;
  try {
    found = await conn.call("config.detect", {}, server);
  } catch {
    return; // the agents' models stay on offer, and any text is still taken
  }
  if (server !== spServer) return;
  spModels = { server, by: Object.fromEntries(found.map((f) => [f.harness, f.models])) };
  fillModels($("sp-harness").value);
}

function fillModels(harness) {
  const listed = spModels.server === spServer ? spModels.by[harness] || [] : [];
  const have = new Set(listed.map((m) => m.value));
  const named = (roster.models[harness] || []).filter((m) => !have.has(m));
  $("sp-model").options = [
    ...listed.map((m) => ({ value: m.value, label: m.free ? `${m.value} (free)` : m.value })),
    ...named,
  ];
}

function pickAgent(name) {
  const a = roster.agents.find((x) => x.name === name);
  if (!a) return;
  $("sp-agent").value = a.name;
  $("sp-agent").dataset.picked = a.name;
  fillModels(a.harness);
  for (const k of PICKS) $(`sp-${k}`).value = a[k];
  markDiffs();
}

function overrides() {
  const a = current();
  const out = {};
  if (!a) return out;
  for (const k of PICKS) {
    const v = $(`sp-${k}`).value.trim();
    if (v && v !== a[k]) out[k] = v;
  }
  return out;
}

function markDiffs() {
  const a = current();
  const diff = overrides();
  for (const k of PICKS) $(`sp-${k}`).classList.toggle("diff", k in diff);
  const changed = Object.keys(diff).length > 0;
  $("sp-reset").hidden = !changed;
  $("sp-agent").suffix = changed ? "*" : "";
}

async function spawnFromComposer() {
  const a = current();
  if (!a || $("sp-go").disabled || offline()) return;
  $("sp-go").disabled = true;
  $("sp-error").textContent = "";
  await dictation.finish($("sp-text"));
  const text = $("sp-text").value.trim();
  const params = { agent: a.name, cwd: $("sp-cwd").value.trim() || null, ...overrides() };
  // With files, the session starts empty: they upload into its inbox and go
  // out with the text as its first message (sendFirst).
  const held = spAtts.list("spawn").length > 0;
  if (text && !held) params.prompt = text;
  try {
    const r = await conn.call("session.spawn", params, spServer);
    localStorage.setItem(LAST_AGENT, a.name);
    $("sp-text").value = "";
    pickAgent(a.name);
    const key = keyOf(spServer, r.log_id);
    go(`#s=${key}`);
    if (held) sendFirst(key, text, spAtts.take("spawn"));
  } catch (e) {
    toast(e.message, { kind: "error" });
  } finally {
    $("sp-go").disabled = false;
  }
}

$("sp-effort").options = ["low", "medium", "high", "xhigh", "max"].map((e) => ({ value: e, label: `effort ${e}` }));
$("sp-permission").options = ["read", "write", "auto", "full"].map((p) => ({ value: p, label: `perm ${p}` }));
$("sp-agent").addEventListener("change", () => pickAgent($("sp-agent").value));
$("sp-harness").addEventListener("change", () => {
  fillModels($("sp-harness").value);
  markDiffs();
});
for (const k of ["model", "effort", "permission"]) $(`sp-${k}`).addEventListener("input", markDiffs);
$("sp-reset").addEventListener("click", () => pickAgent($("sp-agent").value));
$("spawn").addEventListener("submit", (ev) => {
  ev.preventDefault();
  spawnFromComposer();
});
// Enter in a field would submit the form and spawn a half-written session;
// there it means "done with this field". In an open chip it is the chip's pick.
for (const id of ["sp-model", "sp-cwd"]) {
  $(id).addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" && !ev.isComposing && !ev.defaultPrevented) {
      ev.preventDefault();
      $("sp-text").focus();
    }
  });
}
// Enter spawns and sends; on a touch screen it adds a line, as in a session.
$("sp-text").addEventListener("keydown", (ev) => {
  if (ev.key === "Enter" && !ev.shiftKey && !ev.isComposing && !touch.matches) {
    ev.preventDefault();
    spawnFromComposer();
  }
});

// -- the new tab's hints --------------------------------------------------------
// Up to two lines under the chips: /tutorial while this person has started no
// tutorial, /whats-new while the release it runs is newer than their last
// tour. The record is the server's, per principal (helpstate.py), read each time the
// new tab opens, so every device shows the same hint. It acts on this server
// only: a linked server keeps its own record and may not know help.guide.
async function loadHints() {
  delete $("sp-hints").dataset.ready;
  try {
    helpState = await conn.call("help.state", {});
  } catch {
    helpState = null; // no hint rather than a wrong one
  }
  drawHints();
  $("sp-hints").dataset.ready = "";
}

function drawHints() {
  const away = !helpState || spServer !== null;
  $("hint-tutorial").hidden = away || helpState.tutorial;
  $("hint-whats-new").hidden = away || !helpState.whats_new;
  $("hint-whats-new-v").textContent = helpState?.whats_new || "";
}

// What running or dismissing a hint records, as the server records it
// (help_ops.help_guide, help_dismiss), so the hint hides without a new read.
function ranHint(kind) {
  if (!helpState) return;
  helpState = kind === "tutorial" ? { ...helpState, tutorial: true } : { ...helpState, whats_new: null };
}

// A guide session (help.guide), and its tab, as a spawn from the new tab
// opens one. `params` are spawn fields; none at all means default_agent.
async function startGuide(params, kind = "tutorial") {
  const r = await conn.call("help.guide", { kind, ...params });
  ranHint(kind);
  drawHints();
  go(`#s=${r.log_id}`);
  return r;
}

// A hint runs what Enter would, with the chips as they stand.
async function guideFromComposer(kind, btn) {
  const a = current();
  if (!a || btn.disabled) return;
  btn.disabled = true;
  $("sp-error").textContent = "";
  try {
    await startGuide({ agent: a.name, cwd: $("sp-cwd").value.trim() || null, ...overrides() }, kind);
    localStorage.setItem(LAST_AGENT, a.name);
    pickAgent(a.name);
  } catch (e) {
    toast(e.message, { kind: "error" });
  } finally {
    btn.disabled = false;
  }
}

// The cross records what running the hint would, and starts nothing.
async function dismissHint(kind, row) {
  row.hidden = true;
  try {
    await conn.call("help.dismiss", { what: kind });
    ranHint(kind);
  } catch (e) {
    toast(e.message, { kind: "error" });
    drawHints();
  }
}

for (const [kind, id] of [
  ["tutorial", "hint-tutorial"],
  ["whats_new", "hint-whats-new"],
]) {
  $(`${id}-go`).addEventListener("click", () => guideFromComposer(kind, $(`${id}-go`)));
  $(`${id}-x`).addEventListener("click", () => dismissHint(kind, $(id)));
}

// -- tab bar and keys -----------------------------------------------------------
$("tab-fleet").addEventListener("click", () => go("#fleet"));
$("tab-add").addEventListener("click", () => !offline() && go("#new"));
// The ? list: drawn once from the key table.
const keymap = $("keymap");
renderKeys(keymap);
const help = (open = keymap.hidden) => (keymap.hidden = !open);
$("help-btn").addEventListener("click", () => go("#help"));
$("settings-btn").addEventListener("click", () => go("#settings"));
$("journal-btn").addEventListener("click", () => go("#journal"));
keymap.addEventListener("click", (ev) => ev.target === keymap && help(false));
const palette = new Palette($("palette"), () => route().view);

// Alt+J and the navigator's button. In byNeed's order, from the tab after this
// one; from the top when this one is not in the list, as after reading a
// review, which drops it.
function nextNeed() {
  const list = byNeed(ordered);
  if (!list.length) return toast("Nobody needs you");
  const r = route();
  const i = r.view === "session" ? list.findIndex((m) => m.key === r.id) : -1;
  const id = list[(i + 1) % list.length].key;
  if (list[i]?.key === id) return transcript.firstUnread(); // the only one, and open
  landUnread = id;
  go(`#s=${id}`);
}

// What each key in keys.js does. `input` and `editing` are declared below;
// a key is pressed only after this module has run.
// A transcript key does nothing while the canvas's tabs hide the transcript.
const onTranscript = (run) => (ev) => canvas.transcriptShown() && run(ev);
installKeys(
  {
    composer() {
      const v = route().view;
      if (v === "session") input.focus();
      // The new tab's message box: Enter there spawns and sends.
      else if (v === "spawn") $("sp-text").focus();
    },
    dictate() {
      if (offline()) return;
      const v = route().view;
      if (!navigator.mediaDevices || (v !== "session" && v !== "spawn")) return;
      dictation.toggle(v === "spawn" ? spawnTarget() : sessionTarget(), "key");
    },
    browse() {
      const v = route().view;
      if (v === "session" || v === "read") {
        canvas.showTranscript();
        $("tr").focus({ preventScroll: true });
        transcript.pick();
      } else if (v === "fleet") {
        $("cards").focus({ preventScroll: true });
        if (!fleetSel) fleetMove(1);
      }
    },
    needs: nextNeed,
    tabPrev: () => cycle(-1),
    tabNext: () => cycle(1),
    next: onTranscript(() => transcript.move(1)),
    prev: onTranscript(() => transcript.move(-1)),
    turnNext: onTranscript(() => transcript.moveTurn(1)),
    turnPrev: onTranscript(() => transcript.moveTurn(-1)),
    first: onTranscript(() => transcript.edge(false)),
    last: onTranscript(() => transcript.edge(true)),
    messagePrev: onTranscript(() => transcript.message(-1)),
    messageNext: onTranscript(() => transcript.message(1)),
    firstUnread: onTranscript(() => transcript.firstUnread()),
    toggle: onTranscript(() => transcript.toggle()),
    find() {
      canvas.showTranscript(); // the bar is the transcript's
      find.open();
    },
    press: onTranscript(() => transcript.press()),
    copy: onTranscript(() => transcript.selected && copyRow(transcript.selected, transcript.nodes.get(transcript.selected), copyDeps)),
    foldLevel: () => foldLevel((transcript.foldLevel + 1) % 3),
    walk() {},
    fleetNext: () => fleetMove(1),
    fleetPrev: () => fleetMove(-1),
    fleetOpen,
    filter: () => $("arch-q").focus(),
    spawn: () => offline() || go("#new"), // a new session needs the server
    settings: () => go("#settings"),
    journal: () => go("#journal"),
    tab(ev) {
      const n = Number(ev.altKey ? ev.code.slice(5) : ev.key);
      if (n === 0) go("#fleet");
      else if (ordered[n - 1]) go(`#s=${ordered[n - 1].key}`);
    },
    commands() {
      if (route().view !== "session") return;
      const v = input.value;
      if (!v || v.startsWith("/")) {
        if (!v) input.value = "/";
        input.focus();
        menu.openInline();
      } else menu.openOverlay();
    },
    escape() {
      if (cancelAsk() || closeInstallDialog() || closePushPanel()) return;
      if (root.dataset.side === "open") return closeSide();
      if (!keymap.hidden) help(false);
      else if (canvas.closeMenu() || viewer.close() || find.close() || closeMonitorCard() || closeCard()) return;
      else if (route().view === "session" && !editing.size) interrupt();
    },
    help: () => help(),
    side: () => toggleSide(),
    palette: () => palette.toggle(),
  },
  () => (booted ? route().view : "boot"),
);

// Alt+[ and Alt+]: the Fleet, then the tabs, round.
function cycle(d) {
  const all = ["#fleet", ...ordered.map((m) => `#s=${m.key}`)];
  const i = all.indexOf(location.hash || "#fleet");
  go(all[i < 0 ? (d > 0 ? 0 : all.length - 1) : (i + d + all.length) % all.length]);
}

// -- composer ---------------------------------------------------------------
const input = $("input");

// A composer send gets an answer within SEND_VERDICT_MS or the box is the
// person's again (#350). Until then the box and the button are locked.
const SEND_VERDICT_MS = 3000;
const LATE = new Error("no answer yet");
const NOT_CONFIRMED = "No answer in 3 s. Send again: it will not arrive twice.";
const answered = (call) =>
  Promise.race([call, new Promise((_, reject) => setTimeout(() => reject(LATE), SEND_VERDICT_MS))]);
const sending = new Set(); // sessions whose composer send waits on its answer
// The nonce of the last send per session that got no answer yet: sending the
// same message again reuses it, and the server delivers it once.
const unconfirmed = new Map(); // key -> {what, nonce}
function nonceFor(key, what) {
  const u = unconfirmed.get(key);
  if (u?.what === what) return u.nonce;
  const nonce = crypto.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  unconfirmed.set(key, { what, nonce });
  return nonce;
}
// The lock on the shown session's box. `blocked` is renderMeta's own reason to
// disable the button (a linked server down, someone else's session).
let blocked = false;
function drawSending(why = blocked) {
  blocked = why;
  const on = !!shown && sending.has(shown);
  $("composer").classList.toggle("sending", on);
  input.readOnly = on;
  $("send").disabled = on || blocked;
}

// -- attachments: chips above the box, uploading as they arrive (attach.js) --
const waitOnline = () =>
  new Promise((resolve) => {
    const t = setInterval(() => {
      if (conn.open) {
        clearInterval(t);
        resolve();
      }
    }, 500);
  });
const atts = new Attachments($("atts"), { call: callFor, waitOnline });
// The new tab's files wait as chips until its session exists (spawnFromComposer).
const spAtts = new Attachments($("sp-atts"), { call: callFor, waitOnline, hold: true });
spAtts.show("spawn");
for (const [btn, pick, tray, key] of [
  ["attach", "attach-pick", atts, null],
  ["sp-attach", "sp-attach-pick", spAtts, "spawn"],
]) {
  $(btn).addEventListener("click", () => !offline() && $(pick).click());
  $(pick).addEventListener("change", (ev) => {
    tray.add([...ev.target.files], key ?? undefined);
    ev.target.value = "";
  });
}
// A pasted screenshot is called image.png by every browser: name it by time.
function pasted(ev) {
  const got = [...(ev.clipboardData?.files || [])];
  if (!got.length) return null;
  ev.preventDefault();
  const at = new Date().toTimeString().slice(0, 8).replaceAll(":", "");
  return got.map((f, i) => {
    const ext = (f.type.split("/")[1] || "bin").replace(/[^a-z0-9]/g, "");
    return new File([f], `pasted-${at}${i ? `-${i + 1}` : ""}.${ext}`, { type: f.type });
  });
}
input.addEventListener("paste", (ev) => {
  const files = pasted(ev);
  if (files) atts.add(files);
});
$("sp-text").addEventListener("paste", (ev) => {
  const files = pasted(ev);
  if (files) spAtts.add(files, "spawn");
});
// A file dropped anywhere on a session's page attaches to it; anywhere else
// the browser would navigate away to the file.
const carriesFiles = (ev) => ev.dataTransfer?.types.includes("Files");
const onSession = () => $("a2").dataset.view === "session";
const onSpawn = () => $("a2").dataset.view === "spawn";
document.addEventListener("dragover", (ev) => {
  if (!carriesFiles(ev)) return;
  ev.preventDefault();
  $("composer").classList.toggle("dropping", onSession());
  $("spawn").classList.toggle("dropping", onSpawn());
});
document.addEventListener("dragleave", (ev) => {
  if (ev.relatedTarget) return;
  $("composer").classList.remove("dropping");
  $("spawn").classList.remove("dropping");
});
document.addEventListener("drop", (ev) => {
  if (!carriesFiles(ev)) return;
  ev.preventDefault();
  $("composer").classList.remove("dropping");
  $("spawn").classList.remove("dropping");
  if (onSession()) atts.add([...ev.dataTransfer.files]);
  else if (onSpawn()) spAtts.add([...ev.dataTransfer.files], "spawn");
});

// -- dictation: the mic in both message boxes (dictation.js) ----------------
const mics = () => [
  [$("mic"), input],
  [$("sp-mic"), $("sp-text")],
];
const dictation = new Dictation({
  prepare: () => conn.call("dictation.prepare"),
  onState(state) {
    for (const [b, el] of mics()) b.dataset.state = dictation.target?.el === el ? state : "idle";
  },
  onLevel(x) {
    for (const [b] of mics()) b.style.setProperty("--level", Math.min(1, x * 8).toFixed(2));
  },
  onError(message) {
    toast(message, { kind: "error" });
  },
});
// A session's box holds that session's draft, so text that lands after the
// box moved to another session goes to the draft it started in.
const sessionTarget = () => ({ el: input, key: `aegis.draft.${shown}`, current: () => `aegis.draft.${shown}` });
const spawnTarget = () => ({ el: $("sp-text"), key: null, current: () => null });
if (!navigator.mediaDevices) {
  for (const [b] of mics()) {
    b.disabled = true;
    b.title = "Dictation needs https or localhost";
  }
}
$("mic").addEventListener("click", () => !offline() && dictation.toggle(sessionTarget(), "button"));
$("sp-mic").addEventListener("click", () => !offline() && dictation.toggle(spawnTarget(), "button"));
// On a touch screen Enter adds a line and the button sends: the key sits where
// a mistap lands, and half a message costs a turn.
const touch = matchMedia("(pointer: coarse)");
if (touch.matches) $("sp-text").placeholder = "What should the agent do?";

function autosize() {
  input.style.height = "auto";
  input.style.height = `${Math.min(input.scrollHeight, window.innerHeight * 0.4)}px`;
}
// A pane docked beside the transcript narrows the message box: its lines
// wrap again, so it measures again. Width only, so its own height is no loop.
let composerWidth = 0;
new ResizeObserver(([e]) => {
  if (Math.round(e.contentRect.width) === composerWidth) return;
  composerWidth = Math.round(e.contentRect.width);
  autosize();
}).observe($("tr-pane"));

function focused() {
  const r = route();
  return r.view === "session" ? sessions.get(r.id) : null;
}

// The agent's suggested next messages, from its turn_end. Redrawn only when they
// change: renderMeta runs on every patch of the open session. They sit after the
// transcript's last row, not in the composer, so they scroll away with it and
// never take reading space (#286).
// The prompt cache is cold once the session has sat idle past its harness's
// measured lifetime; the card names the thresholds, or null (cache.py).
function drawCold(s) {
  const box = $("coldnote");
  const c = s && s.cache_cold;
  const on = !!c && s.state !== "working" && (s.context_tokens ?? 0) >= c.min && nowS() - (s.last_activity || nowS()) >= c.after;
  const key = on ? `${s.log_id}:${s.context_tokens}` : "";
  if (box.dataset.key === key) return;
  box.dataset.key = key;
  box.hidden = !on;
  $("coldnote-t").textContent = on
    ? `The prompt cache has expired, so your next message rewrites all ${fmtTokens(s.context_tokens)} tokens of this context, about 20 times what a warm turn reads.`
    : "";
  if (on && transcript.following) transcript.toBottom();
}

function drawReplies(s) {
  const box = $("replies");
  const replies = s.state === "working" ? [] : s.replies || [];
  const key = JSON.stringify([s.log_id, replies]);
  if (box.dataset.key === key) return;
  box.dataset.key = key;
  box.hidden = !replies.length;
  $("replies-pills").replaceChildren(
    ...replies.map((text) => {
      const b = document.createElement("button");
      b.className = "rp";
      b.textContent = text;
      b.addEventListener("click", () => sendLine(text, false));
      return b;
    }),
  );
  // Outside the rows the transcript watches for growth: follow the end here.
  if (transcript.following) transcript.toBottom();
}

// The shown session's message written offline (outbox.js), after the last row.
const QUEUED_LABEL = {
  queued: "queued · goes out when the connection is back",
  sending: "sending…",
  stale: "The session moved on since you wrote this",
  gone: "The session is closed",
};
function drawQueued() {
  const box = $("queued");
  const d = shown && draftFor(shown);
  box.hidden = !d;
  if (!d) return;
  for (const st of ["sending", "stale", "gone", "error"]) box.classList.toggle(st, d.state === st);
  // A refusal the person should read, word for word: a quota gate, an owner.
  $("queued-lbl").textContent = d.state === "error" ? `Not sent: ${d.error}` : QUEUED_LABEL[d.state] || QUEUED_LABEL.queued;
  box.querySelector(".text").textContent = d.text;
  if (transcript.following) transcript.toBottom();
}
onDrafts(drawQueued);
loadDrafts();
setSender((key, params) => callFor(key, "session.send", params));
$("queued").addEventListener("click", (ev) => {
  const act = ev.target.closest("[data-act]")?.dataset.act;
  const key = shown;
  const d = key && draftFor(key);
  if (!act || !d) return;
  if (act === "discard") discard(key);
  if (act === "send") sendAnyway(key).catch((e) => toast(e.message, { kind: "error" }));
  if (act === "copy")
    navigator.clipboard.writeText(d.text).then(
      () => toast("Copied"),
      () => toast("Could not copy", { kind: "error" }),
    );
  if (act === "edit") {
    input.value = input.value ? `${d.text}\n\n${input.value}` : d.text;
    localStorage.setItem(`aegis.draft.${key}`, input.value);
    autosize();
    input.focus();
    discard(key);
  }
});

// On a phone Close sits in the drawer, so a session whose agent said done gets
// an Archive button after its last row, one tap from where the reading ended.
// The stylesheet shows it only in the phone layout (#333).
function drawClosing(s) {
  const box = $("closing");
  const hide = s.attention !== "done";
  if (box.hidden === hide) return;
  box.hidden = hide;
  if (transcript.following) transcript.toBottom();
}
$("closing-btn").addEventListener("click", () => {
  const s = focused();
  if (s) closeSession(s);
});

const askClose = (s) =>
  ask(`Close ${s.title || s.handle}? Its tab goes away in every browser; it stays in the archive.`, { ok: "Close" });
// The one rule for when closing asks: a done session has nothing left to lose,
// anything else may be mid-work. The Close button, /close, the Fleet's Archive
// button and the phone's Archive row all pass through it.
const confirmClose = async (s) => s.attention === "done" || (await askClose(s));

async function closeSession(s) {
  if (offline() || !(await confirmClose(s))) return;
  try {
    await callFor(s.key, "session.close");
  } catch (e) {
    $("side-error").textContent = e.message;
  }
}

// A line from the composer, or from the menu's own filter (Alt+/ over a
// draft), which leaves the composer alone. The server resolves "/" lines.
// `target` is the session the line was typed in: a send that waited on
// uploads may finish after the person moved to another tab.
async function sendLine(text, fromComposer, attachments = [], target = focused()) {
  const s = target;
  if ((!text && !attachments.length) || !s) return;
  if (text === "/help") {
    // The menu is the help: it lists every command with what it does.
    if (fromComposer) menu.setLine("/");
    menu.close();
    if (fromComposer) menu.openInline();
    else menu.openOverlay();
    return;
  }
  // /help with words opens the Help tab searched for them (help.js).
  const asked = /^\/help\s+(.+)$/s.exec(text);
  if (asked) {
    if (fromComposer) {
      input.value = "";
      autosize();
      localStorage.removeItem(`aegis.draft.${s.key}`);
    }
    menu.close();
    helpTab.ask(asked[1].trim());
    go("#help");
    return;
  }
  if (text === "/close" && !(await confirmClose(s))) return;
  // With no way to the session's server, plain text waits as its draft on this
  // device (outbox.js); a command or files still need the connection.
  if ((!conn.open || s.off) && !text.startsWith("/") && !attachments.length) {
    await queue(s.key, text, shown === s.key ? transcript.rev : -1);
    if (fromComposer) localStorage.removeItem(`aegis.draft.${s.key}`);
    if (fromComposer && shown === s.key) {
      input.value = "";
      autosize();
      menu.close();
      $("composer").classList.remove("bad");
    }
    if (shown === s.key) transcript.toBottom();
    return;
  }
  const box = $("replies");
  const was = box.hidden;
  box.hidden = true; // any send answers the turn the pills belonged to
  delete box.dataset.key; // so the next drawReplies always redraws
  const params = attachments.length ? { text, attachments } : { text };
  // A linked server older than nonces would refuse the field.
  if (fromComposer && !forKey(s.key).server) params.nonce = nonceFor(s.key, JSON.stringify([text, attachments]));
  const call = callFor(s.key, "session.send", params);
  const sent = (r) => {
    unconfirmed.delete(s.key);
    if (/^\/model\s/.test(text)) catalogs.delete(s.key); // its efforts may differ
    if (fromComposer) {
      localStorage.removeItem(`aegis.draft.${s.key}`);
      atts.clear(s.key);
    }
    if (fromComposer && shown === s.key) {
      input.value = "";
      autosize();
      // Clearing the box fires no input event; an open menu would take the next Esc.
      menu.close();
      $("composer").classList.remove("bad");
      $("send-error").textContent = "";
    }
    if (shown === s.key) transcript.toBottom();
    // /tutorial and /whats-new start a guide in a new session (help.guide):
    // open its tab, on the server that runs it, as a spawn from the new tab does.
    if (/^\/(tutorial|whats-new)(\s|$)/.test(text) && r?.log_id) go(`#s=${keyOf(forKey(s.key).server, r.log_id)}`);
  };
  if (fromComposer) {
    sending.add(s.key);
    drawSending();
    if (shown === s.key) $("send-error").textContent = "";
  }
  try {
    sent(await (fromComposer ? answered(call) : call));
  } catch (e) {
    box.hidden = was;
    if (e !== LATE) {
      toast(e.message, { kind: "error" });
      return;
    }
    // No answer yet: the box is the person's again, with the text, and says so.
    // An answer that comes later still settles it, unless they rewrote it.
    if (shown === s.key) $("send-error").textContent = NOT_CONFIRMED;
    const still = () => (shown === s.key ? input.value.trim() : localStorage.getItem(`aegis.draft.${s.key}`)?.trim()) === text;
    call.then(
      (r) => still() && sent(r),
      (e) => still() && shown === s.key && ($("send-error").textContent = `Not sent: ${e.message}`),
    );
  } finally {
    if (fromComposer) {
      sending.delete(s.key);
      drawSending();
    }
  }
}

// Sessions whose send waits on its uploads: a second Enter there does nothing.
const waiting = new Set();

// A new tab's first message when it carried files: they upload into the
// session just started, then go out with the text, as a composer send would.
async function sendFirst(key, text, files) {
  atts.add(files, key);
  waiting.add(key);
  try {
    await atts.settled(key);
  } finally {
    waiting.delete(key);
  }
  const ready = atts.ready(key);
  if (ready.error) {
    toast(ready.error, { kind: "error" });
    return;
  }
  try {
    await callFor(key, "session.send", { text, attachments: ready.ids });
    atts.clear(key);
  } catch (e) {
    toast(e.message, { kind: "error" });
  }
}
const send = async () => {
  await dictation.finish(input); // what was said goes out with the rest
  const s = focused();
  if (!s || waiting.has(s.key) || sending.has(s.key)) return;
  // The text and the session as they were at Enter, so a send that waits on
  // uploads still goes where and as it was typed.
  const text = input.value.trim();
  if (atts.list(s.key).length) {
    // Enter during an upload sends when it finishes, never without the files.
    waiting.add(s.key);
    $("send-error").textContent = "sending when the attachments finish…";
    try {
      await atts.settled(s.key);
    } finally {
      waiting.delete(s.key);
    }
    if (shown === s.key) $("send-error").textContent = "";
  }
  const ready = atts.ready(s.key);
  if (ready.error) {
    toast(ready.error, { kind: "error" });
    return;
  }
  return sendLine(text, true, ready.ids, s);
};

// Catalogs per session, fetched when the menu first opens there. The promise
// is kept, so keystrokes that arrive while it loads wait for the same call
// instead of each asking the server again.
const catalogs = new Map();
// A session's menu: its commands, and the tutorial pages /tutorial completes.
// A linked server without help.pages offers none.
async function loadCommands(key) {
  const [cat, pages] = await Promise.all([
    callFor(key, "commands.list"),
    conn.call("help.pages", {}, forKey(key).server).catch(() => []),
  ]);
  return { ...cat, tutorials: pages.filter((p) => p.kind === "tutorial") };
}
async function loadCatalog() {
  const s = focused();
  if (!s) return null;
  if (!catalogs.has(s.key)) catalogs.set(s.key, loadCommands(s.key));
  try {
    return await catalogs.get(s.key);
  } catch (e) {
    catalogs.delete(s.key); // the next open asks again
    toast(e.message, { kind: "error" });
    return null;
  }
}

const menu = new CommandMenu({
  box: $("cmd-menu"),
  rows: $("cmd-rows"),
  filter: $("cmd-filter"),
  load: loadCatalog,
  run: (line) => sendLine(line, false),
  getLine: () => input.value,
  setLine: (v) => {
    input.value = v;
    autosize();
    if (shown) localStorage.setItem(`aegis.draft.${shown}`, v);
    input.focus();
  },
  meta: () => focused(),
});

for (const [id, cmd] of [
  ["chip-model", "model"],
  ["chip-effort", "effort"],
  ["chip-perm", "permission"],
]) {
  $(id).classList.add("click");
  $(id).addEventListener("click", () => {
    // Over a draft, the menu's own filter line, as Alt+/ does, so the draft stays.
    if (input.value && !input.value.startsWith("/")) return menu.openOverlay(`/${cmd} `);
    input.value = `/${cmd} `;
    input.focus();
    menu.openInline();
  });
}

async function interrupt() {
  const s = focused();
  if (!s || s.state !== "working") return;
  try {
    await callFor(s.key, "session.interrupt");
  } catch (e) {
    toast(e.message, { kind: "error" });
  }
}

input.addEventListener("input", async () => {
  autosize();
  if (shown) localStorage.setItem(`aegis.draft.${shown}`, input.value);
  const v = input.value;
  if (v.startsWith("/") && !v.startsWith("//")) {
    if (menu.isOpen) menu.refresh();
    else await menu.openInline(); // the outline below needs the catalog it loads
  } else if (menu.isOpen) menu.close();
  const now = input.value;
  $("composer").classList.toggle("bad", now.startsWith("/") && !now.startsWith("//") && !menu.known(now));
});
input.addEventListener("keydown", (ev) => {
  if (menu.onKey(ev)) return;
  if (ev.key === "Enter" && !ev.shiftKey && !ev.isComposing && !touch.matches) {
    ev.preventDefault();
    send();
  }
});
$("send").addEventListener("click", send);
$("interrupt").addEventListener("click", interrupt);
// A nudge after an interrupt, an error or a stop: a message to a stopped
// session resumes it, so Restart is one sentence to the agent.
$("restart").addEventListener("click", () => !offline() && sendLine("Continue", false));

$("stop-session").addEventListener("click", async () => {
  if (offline()) return;
  const s = focused();
  if (!s) return;
  try {
    await callFor(s.key, "session.stop");
  } catch (e) {
    $("side-error").textContent = e.message;
  }
});

$("close").addEventListener("click", () => {
  const s = focused();
  if (s) closeSession(s);
});

// -- the panel: a drawer on a phone (base.css, max-width 760px), collapsible
// and resizable on a desktop (side.js) ------------------------------------------
const drawerMode = matchMedia("(max-width: 760px)");
initSide({ onCardOpen: () => closeMonitorCard() });
// Closing the drawer returns to the desktop state this browser keeps.
const closeSide = () => {
  if (root.dataset.side === "open") restState();
};
function toggleSide() {
  if (!drawerMode.matches) return toggleCollapsed();
  if (root.dataset.side === "open") closeSide();
  else root.dataset.side = "open";
}
// The header's height, for the drawer to start under it: it wraps to two rows
// on a phone and grows with the safe area.
const header = document.querySelector("#a2 > .tabs");
new ResizeObserver(() => root.style.setProperty("--hdr", `${header.getBoundingClientRect().height}px`)).observe(header);
// The navigator floats just above the composer, which grows with its lines.
new ResizeObserver(() => root.style.setProperty("--composer-h", `${$("composer").offsetHeight}px`)).observe($("composer"));
$("side-btn").addEventListener("click", toggleSide);
// The dimmed transcript is the session view's own ::after, so a tap on it
// lands on the view itself and goes no further.
document.querySelector(".v-session").addEventListener("click", (ev) => {
  if (ev.target === ev.currentTarget && root.dataset.side === "open") closeSide();
});

// -- rename in place -------------------------------------------------------------
const editing = new Set();

function editable(elId, field) {
  const node = $(elId);
  node.addEventListener("click", () => {
    const r = route();
    if (editing.has(field) || (r.view !== "session" && r.view !== "read")) return;
    editing.add(field);
    const old = node.textContent;
    const box = document.createElement("input");
    box.className = "rename";
    box.value = field === "title" && old === "untitled" ? "" : old;
    node.replaceChildren(box);
    box.focus();
    box.select();
    const finish = async (save) => {
      if (!editing.has(field)) return;
      editing.delete(field);
      const value = box.value.trim();
      node.textContent = old;
      if (!save || !value || value === old) return;
      try {
        await callFor(r.id, "session.rename", { [field]: value });
        node.textContent = value;
        archiveLoaded = false;
        $("side-error").textContent = "";
      } catch (e) {
        $("side-error").textContent = e.message;
      }
    };
    box.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") finish(true);
      if (ev.key === "Escape") {
        ev.stopPropagation();
        finish(false);
      }
    });
    box.addEventListener("blur", () => finish(true));
  });
}
editable("s-title", "title");
editable("s-handle", "handle");
