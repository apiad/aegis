// The aegis client: one home server, the servers it links, many sessions.
//
// The tabs are the open sessions of the home server and of every server it
// links (each one's `sessions` channel), the same in every browser. Their order
// and which one is focused belong to this browser: the order in localStorage,
// the focus in the URL hash. A session is known by its key: its log id at
// home, `<server>/<log_id>` on a linked server (links.py).

import { Connection } from "./protocol.js";
import { Transcript } from "./transcript.js";
import { artifactStage, btwFolds, fadeTables, fileUrl, refreshApprovals, refreshWorkflows, setApprovals, setDocker, setFileBase, setOpener, setSecrets, setWorkflows, syncDock, tickWorkflows, workflowLocked, workflowTitle } from "./entries.js";
import { setScroller } from "./highlight.js";
import { Viewer, peekRoots } from "./viewer.js";
import { Canvas } from "./canvas.js";
import { copyRow, installCopy, rowText } from "./copy.js";
import { Find } from "./find.js";
import * as artifacts from "./artifacts.js";
import { TabOrder, patchTab, renderTabs } from "./tabs.js";
import { linesOf, mine, ownerOf, setWho } from "./me.js";
import { ago, byNeed, modelLine, money, patchCard, renderArchive, renderBand, renderBandQuota, renderCards, tickPlan } from "./fleet.js";
import { age, countdown, diskRows, elapsed, hostRow, hostSeverity, mbps, netRows, providerFor, quotaSideRow, quotaTile, tile } from "./gauges.js";
import { closeCard, initSide, restState, toggleCollapsed } from "./side.js";
import { installKeys, renderKeys } from "./keys.js";
import { Palette } from "./palette.js";
import { entryIcon, glyph, gorgoneion, icon, installGlyphs, LABEL } from "./glyphs.js";
import { CommandMenu } from "./commands.js";
import * as cmenu from "./menu.js";
import { clampToRow, insertIntoComposer, quoted, rangeText } from "./quote.js";
import { closeMonitorCard, renderMonitors, tickMonitors } from "./monitors.js";
import { Settings } from "./settings.js";
import { entry, Journal, opens } from "./journal.js";
import { FilePicker, Files, filesHash, parseTarget } from "./files.js";
import { Schedules } from "./schedules.js";
import { Help } from "./help.js";
import { installBell, listenForOpen, pingToast, redrawFavicon, setTitle, updatePing } from "./ping.js";
import { ask, cancelAsk, choose } from "./dialog.js";
import { modelMeta } from "./models.js";
import "./pick.js";
import { Dictation } from "./dictation.js";
import { Attachments } from "./attach.js";
import { dur, planTimes } from "./plantime.js";
import { setToastOpener, toast } from "./toast.js";
import { closeTerm, drawCard, openTerm } from "./termview.js";
import { browserSources, closeBrowser, drawBrowser, mountBrowser, openBrowser } from "./browserview.js";
import { applyUpdate, checkForUpdate, setAutoApply, startWorker } from "./appworker.js";
import { closeInstallDialog, startInstall } from "./install.js";
import { checkRotation, closePushPanel, openPushPanel, pushSupported, setupPush } from "./pushpanel.js";
import { clearDevice, loadFleet, loadTranscript, saveFleet, saveTranscript } from "./devicestore.js";
import { markNeedsNet, offline } from "./net.js";
import { discard, draftFor, flushNow, loadDrafts, onDrafts, queue, sendAnyway, setSender } from "./outbox.js";
import { idleFor, lifecycle } from "./lifecycle.js";

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
const withKey = (m, server = null) => ({ ...lifecycle(m), server, key: keyOf(server, m.log_id), off: isOff(server) });
const order = new TabOrder();
let ordered = []; // metas in this browser's tab order
let shown = null; // the key of the session whose transcript is subscribed
// The last session shown in this browser tab, where Files' quotes go; a
// reload of the Files view keeps it.
let lastShown = sessionStorage.getItem("aegis.lastShown");
let warmed = null; // the parked (asleep, idle) session whose first keystroke asked for session.warm
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
  // Only this server's replies carry pending refs (entries.js), so a linked
  // server is never asked.
  statPaths: (entryId) => {
    const id = shown;
    return callFor(id, "path.stat", { entry_id: entryId }).then((got) => {
      if (shown !== id) throw new Error("another session is shown");
      return got;
    });
  },
});
artifacts.setup({
  call: (op, params, key) => callFor(key || shown, op, params),
  entry: (id) => transcript.entries.get(id),
  key: () => shown,
  point: (frame, params) => pagePoint(frame, params),
});
const pointing = { items: (ctx) => pointItems(ctx), send: (key, text) => toComposer(key, text) };
const viewer = new Viewer($("viewer"), { entry: (id) => transcript.entries.get(id), point: pointing });
// Open on a docked item shows its pane, so a page never runs in the viewer
// and a pane at once. An older version of a docked file opens in the viewer:
// the pane shows the newest.
// One in another browser window focuses or opens that window's tab.
// The pane id of what the viewer shows: a page's, a run's card's, or the
// shown file's when it is the newest of its key.
const itemId = (item) => {
  if (item.artifact) return item.artifact;
  if (item.workflow) return `wf-${item.workflow}`;
  const f = item.files?.[item.at];
  return f && !f.replaced ? f.key : null;
};
setOpener((item) => {
  const id = itemId(item);
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
  // A browser the session drives, docked: its own view of the stream.
  mountBrowser: (host, id) => (sessions.has(id) ? mountBrowser(host, id, termCard(id), { conn }) : null),
});
// Dock on a card or the viewer's bar. The viewer lets go of what it docks.
// The viewer lets go of what it docks or pops out.
function leaveViewer(id) {
  const item = viewer.isOpen ? viewer.items.get(viewer.key) : null;
  if (item && (item.workflow ? `wf-${item.workflow}` : item.artifact || item.files[item.at].key) === id) viewer.close();
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
$("files-btn").append(icon("folder"));
$("schedules-btn").prepend(icon("clock"));
$("send").append(icon("send"));
$("sp-go").append(icon("send"));
$("hint-tutorial-x").append(icon("close"));
$("hint-whats-new-x").append(icon("close"));
$("interrupt").append(icon("stop"));
$("replies-g").append(icon("prompt"));
$("queued-g").append(icon("prompt"));
$("working-g").append(icon("think"));
$("waking-g").append(icon("think"));
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
// Which kinds the Fleet and the archive show: all, agents or terminals. This
// browser's choice, like the order.
let fleetKind = localStorage.getItem("aegis.kind") || "all";
// Terminals and browsers are tools, not agents: the "terminals" kind shows both.
const isTool = (m) => m.kind === "term" || m.kind === "browser";
const kindShown = (m) => fleetKind === "all" || (fleetKind === "terminals") === isTool(m);
function markKind() {
  for (const b of document.querySelectorAll(".kinds button")) b.setAttribute("aria-checked", String(b.dataset.kind === fleetKind));
}
markKind();
for (const b of document.querySelectorAll(".kinds button"))
  b.addEventListener("click", () => {
    fleetKind = b.dataset.kind;
    localStorage.setItem("aegis.kind", fleetKind);
    markKind();
    render();
    loadArchive();
  });
for (const b of document.querySelectorAll("#fleet-order button"))
  b.addEventListener("click", () => {
    fleetOrder = b.dataset.order;
    localStorage.setItem("aegis.fleetOrder", fleetOrder);
    markOrder();
    render();
  });

// -- routing: #fleet, #new, #settings, #journal, #files[=<path>[:line]], #schedules, #help[=<page>], #s=<key>, #t=<key>, #read=<key>, #w=<n>:<key> --
// A help ref is URI-encoded; a hand-typed malformed one is a page name nobody has.
const unhash = (s) => {
  try {
    return decodeURIComponent(s);
  } catch {
    return s;
  }
};
function route(hash = location.hash) {
  const h = hash.slice(1);
  // A row's link (its menu's Copy link) names the entry to reveal after "&at=".
  if (h.startsWith("s=")) {
    const [id, at] = h.slice(2).split("&at=");
    return at ? { view: "session", id, at } : { view: "session", id };
  }
  if (h.startsWith("t=")) return { view: "term", id: h.slice(2) };
  if (h.startsWith("b=")) return { view: "browser", id: h.slice(2) };
  if (h.startsWith("read=")) return { view: "read", id: h.slice(5) };
  // Window n of a session's canvas, a browser tab of its own (js/canvas.js).
  const w = /^w=(\d+):(.+)$/.exec(h);
  if (w) return { view: "window", id: w[2], n: Number(w[1]) };
  if (h === "new") return { view: "spawn" };
  if (h === "settings") return { view: "settings" };
  if (h === "journal") return { view: "journal" };
  if (h === "files" || h.startsWith("files=")) return { view: "files", id: unhash(h.slice(6)) };
  if (h === "schedules") return { view: "schedules" };
  if (h === "help" || h.startsWith("help=")) return { view: "help", id: unhash(h.slice(5)) };
  return { view: "fleet" };
}

// Navigation renders in the same step as the URL changes. Setting
// location.hash would render later, on hashchange, and anything typed in
// between was wiped when the new tab's draft loaded (#128's CI failure).
// Back and Forward, and a hand-edited URL, still arrive through hashchange.
// The Files view (files.js); set once the connection exists, below.
let filesView = null;
let filePicker = null;

function go(hash) {
  // Unsaved edits in the Files view ask first (Save, Discard, Cancel).
  if (filesView?.leaving(route(hash))) {
    filesView.confirmLeave().then((ok) => ok && go(hash));
    return;
  }
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
      filesView?.online();
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
// Alt+O's picker opens a file in the Files view; Shift+Enter in a new browser tab.
filePicker = new FilePicker($("fpick"), conn, (path, line, newTab) => {
  const hash = filesHash(path, line);
  if (newTab) window.open(location.pathname + location.search + hash, "_blank");
  else go(hash);
});
// Files' pointing writes to the session last shown in this window: the Files
// view itself shows none.
filesView = new Files(conn, $("files"), {
  toast,
  go,
  onPick: () => filePicker.open(),
  point: { items: (ctx) => pointItems({ ...ctx, to: lastShown }), send: (text) => toComposer(lastShown, text) },
  // A Files row's menu (spec 06): paths are under the root, which is this
  // server's harness folder (roster.cwd).
  here: {
    insert: (path) => toComposer(lastShown, `\`${path}\``, { caret: true }),
    session: async (dir) => {
      go("#new");
      spawnGrow();
      await setSpServer(null);
      await agentsLoading; // roster.cwd comes with the roster
      $("sp-cwd").value = dir ? `${roster.cwd}/${dir}` : roster.cwd;
    },
    terminal: (dir) =>
      conn.call("term.spawn", { profile: "shell", cwd: dir || "." }).then(
        (card) => go(`#t=${keyOf(null, card.id)}`),
        (e) => toast(e.message, { kind: "error" }),
      ),
  },
});
// The Help tab (help.js), one per window like Settings. Its code blocks copy
// as the transcript's do.
// Guided tour: what /tutorial <page> does, on default_agent, for any page. A
// failure shows as an error toast, since the Help tab belongs to no session.
const helpTab = new Help(conn, $("help"), {
  go,
  onKeys: () => help(true),
  onStart: (id, tutorial) =>
    startGuide({ page: id }, "tutorial", tutorial).catch((e) => toast(e.message, { kind: "error" })),
  onAsk: (question, page) =>
    startGuide({ question, page }, "ask", false).catch((e) => toast(e.message, { kind: "error" })),
});
installCopy($("help"), copyDeps);
installCopy($("files"), copyDeps);
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
// The Schedules view (schedules.js); its button counts them, red while one is
// paused by errors.
const schedules = new Schedules(conn, $("schedules"), {
  go,
  sessions: () => local(),
  onCount: (n, red) => {
    $("schedules-n").textContent = n ? String(n) : "";
    $("schedules-n").classList.toggle("err", red);
  },
});
setSecrets((op, params) => callFor(shown, op, params));
setApprovals({
  schedule: (name) => schedules.items.get(name) || null,
  call: (op, params) => conn.call(op, params),
});
conn.subscribe(
  "schedules",
  (list) => {
    schedules.onSnapshot(list);
    refreshApprovals();
  },
  (ops) => {
    schedules.onPatch(ops);
    refreshApprovals();
  },
);

// The workflow runs (workflows.py), for their cards in the transcript and
// their canvas panes. A running card counts its times up once a second.
// `workflow-runs` has every run's summary, without the tree and the story a
// card draws: those were 96% of the bytes (#525). A card's run comes whole
// from `workflow:<run>`, subscribed while a card of it is in the page.
const wfRuns = new Map(); // run -> its summary
const wfWhole = new Map(); // run -> {w: the run whole, or null until it lands; unsub}
function wfGet(run) {
  const summary = wfRuns.get(run);
  if (!summary) return null;
  let d = wfWhole.get(run);
  if (!d) {
    d = { w: null, unsub: null };
    wfWhole.set(run, d);
    const land = (w) => {
      d.w = w;
      wfChanged(new Set([run]));
    };
    d.unsub = conn.subscribe(`workflow:${run}`, land, (ops) => {
      for (const op of ops) if (op.set) land(op.set);
    });
  }
  // Until it lands, the card draws from the summary, with no rows yet.
  return d.w || { ...summary, tree: [], story: [] };
}
// A run whose cards all left the page is no longer followed.
function wfForget() {
  for (const [run, d] of wfWhole) {
    if (document.querySelector(`.wfcard[data-run="${CSS.escape(run)}"], .wfdcard[data-run="${CSS.escape(run)}"]`)) continue;
    d.unsub();
    wfWhole.delete(run);
  }
}
setInterval(wfForget, 10000);
setWorkflows({
  get: wfGet,
  summary: (run) => wfRuns.get(run) || null,
  call: (op, params) => conn.call(op, params),
  view: (files, line) => viewer.open({ files, at: 0, remote: false, glyph: "file", line }),
});
const locked = workflowLocked;
// A patch redraws only the cards of the runs it carries.
const wfChanged = (runs) => {
  refreshWorkflows(runs);
  canvas.syncStrip(); // a docked run's tab takes its title
  // The shown session may be a node of a run that just ended: its box unlocks.
  const s = sessions.get(shown);
  if (s?.workflow && (!runs || runs.has(s.workflow.run))) renderMeta(s);
};
conn.subscribe(
  "workflow-runs",
  (list) => {
    wfRuns.clear();
    for (const w of list || []) wfRuns.set(w.run, w);
    wfChanged();
  },
  (ops) => {
    const runs = new Set();
    for (const op of ops) {
      const w = op.upsert || (op.merge && wfRuns.has(op.merge.run) && { ...wfRuns.get(op.merge.run), ...op.merge });
      if (!w) continue;
      wfRuns.set(w.run, w);
      // A whole run that is followed takes the summary's news too.
      const d = wfWhole.get(w.run);
      if (d?.w) d.w = { ...d.w, ...w };
      runs.add(w.run);
    }
    wfChanged(runs);
  },
);
setInterval(() => {
  if ([...wfRuns.values()].some((w) => w.status === "running")) tickWorkflows();
}, 1000);

// Each server's `sessions` channel: a snapshot replaces that server's metas,
// patches upsert, merge (only the fields that changed) and remove them.
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
        } else if (op.merge) {
          const k = keyOf(server, op.merge.log_id);
          const held = sessions.get(k);
          if (!held) continue;
          sessions.set(k, withKey({ ...held, ...op.merge }, server));
          changed.add(k);
        } else if (op.remove !== undefined) {
          sessions.delete(keyOf(server, op.remove));
          setChanged = true;
          // The server archives a session before it publishes the removal, so
          // this is the moment the archive gains a row, wherever it was closed.
          // A close carries the row; anything else reads the page again.
          if (op.archived) archiveGained({ ...op.archived, server: server ?? conn.server });
          else archiveLoaded = false;
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
// The copy this device kept of the session the URL opens, read while the
// socket connects. follow() waits for it before it subscribes, and read only
// once the Fleet's snapshot had arrived it held the subscription back by about
// 40 ms on every cold load (#345). Any other first view leaves it unused.
const boot = route();
let early = ["session", "window", "read"].includes(boot.view) ? { id: boot.id, copy: loadTranscript(boot.id) } : null;

loadFleet().then((saved) => {
  if (!saved || booted || refused) return;
  seenAt = saved.saved_at;
  // An older client saved stopped and parked: read them as asleep (#479).
  for (const m of saved.metas) sessions.set(m.key, { ...lifecycle(m), stale: true });
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
  // A session that went quiet or came back changes the set of tabs (#386).
  if (quietChanged()) {
    render();
    drawNav();
    return;
  }
  const r = route();
  for (const id of ids) if (sessions.has(id)) patchTab($("tablist"), sessions.get(id), tabFocus(r), tabActions);
  if (r.view === "fleet") {
    const home = [...ids].filter((id) => !sessions.get(id)?.server);
    const regroup = home.some((id) => !patchCard($("cards"), sessions.get(id), openCard, fleetOrder, closeById));
    if (regroup) renderCards($("cards"), local().filter(kindShown), openCard, fleetOrder, closeById);
    if (home.length < ids.size) renderRemotes();
    fleetMark(false);
    drawBand();
    markNeedsNet(); // the cards' Archive buttons
  } else if (r.view === "session" && ids.has(r.id)) renderMeta(sessions.get(r.id));
  else if (r.view === "term" && ids.has(r.id)) drawCard(termCard(r.id));
  else if (r.view === "window" && ids.has(r.id)) drawWindow(r);
  // A browser shows in its tab and in canvas panes: every view redraws.
  for (const id of ids) if (sessions.get(id)?.kind === "browser") drawBrowser(termCard(id));
  drawNav(); // the needs count
}

function onSessions() {
  // A terminal's card has no created_at; it starts when it started.
  const since = (m) => m.created_at ?? m.started_at ?? 0;
  const ids = order.arrange([...sessions.values()].sort((a, b) => since(a) - since(b)).map((m) => m.key));
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
  if (homeLive) updatePing([...sessions.values()].filter((m) => !m.stale && !isTool(m)), { onOpen: openSession });
}

// -- rendering -----------------------------------------------------------------
// A window tab draws one window of a session's canvas and never becomes
// another view: window 1's pages would run in it a second time.
const openSession = (id) => route().view === "window" || go(hashOf(id));
// A tab's hash: a terminal's view, or a session's.
const hashOf = (key) => ({ term: `#t=${key}`, browser: `#b=${key}` })[sessions.get(key)?.kind] || `#s=${key}`;
const tabFocus = (r) => (r.view === "session" || r.view === "term" || r.view === "browser" ? r.id : null);
// Where a browser's views find its card and the sessions it can be given to.
browserSources({
  card: (id) => (sessions.has(id) ? termCard(id) : null),
  sessions: () => local().filter((m) => !isTool(m) && m.state !== "asleep" && mine(m)),
});
// The open terminal's card, with the log id and server it is reached by.
function termCard(key) {
  const m = sessions.get(key);
  return m && { ...m, id: forKey(key).log_id };
}
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

// Quiet sessions have no tab (#386). The view leaves a session only when it was
// seen visible and then turned quiet, so a link to a quiet one still opens it,
// even when the saved Fleet or a linked server's list arrives after the page.
const tabbed = () => ordered.filter((m) => !m.quiet);
const quietSeen = new Map(); // key -> whether it was quiet when last drawn
const quietChanged = () => ordered.some((m) => !!m.quiet !== (quietSeen.get(m.key) ?? !!m.quiet));

// Archiving the open tab, here or elsewhere, leaves it the same way: to the next
// tab, not to the Fleet. The order is the one last drawn, which still holds it.
// Whether the list that would hold this key has arrived: a far session's
// server's, or this server's live one rather than the device's saved list.
function settled(key) {
  const far = forKey(key).server;
  return far ? linksLoaded && !(linkOf(far) && !remote.get(far)?.loaded) : homeLive;
}

function leaveIfGone(r) {
  const id = tabFocus(r);
  const was = [...quietSeen.keys()];
  const drawn = id && quietSeen.get(id) === false;
  const fresh = drawn && (sessions.get(id) ? r.view === "session" && sessions.get(id).quiet : settled(id));
  quietSeen.clear();
  for (const m of ordered) quietSeen.set(m.key, !!m.quiet);
  if (!fresh) return false;
  const at = was.indexOf(id);
  const next = was.slice(at + 1).concat(was.slice(0, at).reverse()).find((k) => quietSeen.get(k) === false);
  go(next ? hashOf(next) : "#fleet");
  return true;
}

// A click on a quiet card brings the session back to the tab bar, then opens it.
async function openCard(key) {
  // A locked node opens as it is, quiet: it gets its tab when the run ends.
  if (sessions.get(key)?.quiet && !locked(sessions.get(key))) {
    try {
      const back = await callFor(key, "session.unquiet");
      // Read again: a patch the server sent before the unquiet may have
      // replaced the meta meanwhile, still quiet, and the page would leave it.
      const m = sessions.get(key);
      if (m) Object.assign(m, back, { quiet: false });
    } catch (e) {
      toast(e.message, { kind: "error" });
      return;
    }
    quietSeen.set(key, false);
  }
  openSession(key);
}
const on = (server) => ordered.filter((m) => m.server === server);

let menuView = null; // the view the open menu or bar belongs to (render)
function render() {
  if (!booted) return;
  // Back or Forward away from unsaved edits: stay on the file, and ask.
  if (root.dataset.view === "files" && filesView.leaving(route())) {
    const target = location.hash || "#fleet";
    history.replaceState(null, "", filesHash(filesView.path));
    filesView.confirmLeave().then((ok) => ok && go(target));
    return;
  }
  tellView(); // the view may have changed with the same session shown
  const r = route();
  // A row's link is read once: the URL drops "&at=" on every path, so the
  // tab's hash is its own again (cycle) and a quiet session's bounce keeps
  // none. The row waits in landEntry: a render that waits for the session
  // list runs again later with the plain hash.
  if (r.at) {
    history.replaceState(null, "", `#s=${r.id}`);
    landEntry = { key: r.id, id: r.at };
  }
  // A menu or the selection bar is about the view it opened in. go() moves
  // with pushState, which fires no hashchange, so the change is caught here.
  const at = `${r.view}:${r.id ?? ""}`;
  if (at !== menuView) {
    menuView = at;
    cmenu.close();
    cmenu.unbar();
  }
  if (r.view !== "term") closeTerm();
  if (r.view !== "browser") closeBrowser();
  if (leaveIfGone(r)) return;
  // A session's link to a terminal, or the reverse, opens the right view.
  if ((r.view === "session" || r.view === "term" || r.view === "browser") && sessions.has(r.id) && `#${r.view[0]}=${r.id}` !== hashOf(r.id)) {
    history.replaceState(null, "", hashOf(r.id));
    return render();
  }
  // A reload into a closed terminal: its row comes from term.closed.
  if (r.view === "term" && !sessions.has(r.id) && !closedTerms.has(r.id) && homeLive && !closedTermsLoaded) {
    loadClosedTerms().then(render);
    return;
  }
  if (r.view === "term" && !sessions.has(r.id) && closedTerms.has(r.id)) {
    const card = { ...closedTerms.get(r.id), state: "ended", reason: "closed", archived: true };
    renderTabs($("tablist"), tabbed(), null, tabActions);
    $("tab-fleet").classList.remove("on");
    watchHost(null);
    follow(null);
    show("term");
    openTerm(card.id, card, { conn }).catch((e) => toast(e.message, { kind: "error" }));
    setTitle(`${card.name} · aegis`);
    return;
  }
  if ((r.view === "session" || r.view === "term") && !sessions.has(r.id)) {
    // A far session whose server's list has not arrived yet waits for it; a
    // home one waits for the live list, since a notification's tap or a link
    // opens a session the device's saved list may predate.
    if (!settled(r.id)) return;
    go("#fleet"); // a link to a session no tab held: gone before this page drew it
    return;
  }
  renderTabs($("tablist"), tabbed(), tabFocus(r), tabActions);
  $("tablist").querySelector(".tab.on")?.scrollIntoView({ block: "nearest", inline: "nearest" });
  $("tab-fleet").classList.toggle("on", r.view === "fleet");
  $("tab-add").classList.toggle("on", r.view === "spawn");
  $("settings-btn").classList.toggle("on", r.view === "settings");
  $("journal-btn").classList.toggle("on", r.view === "journal");
  $("files-btn").classList.toggle("on", r.view === "files");
  $("schedules-btn").classList.toggle("on", r.view === "schedules");
  if (r.view !== "schedules") schedules.close();
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
    renderCards($("cards"), local().filter(kindShown), openCard, fleetOrder, closeById);
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
  } else if (r.view === "files") {
    watchHost(null);
    follow(null);
    show("files");
    filesView.show(r.id);
    const shown = parseTarget(r.id).path || filesView.path;
    setTitle(`${shown ? shown.split("/").pop() : "Files"} · aegis`);
  } else if (r.view === "schedules") {
    watchHost(null);
    follow(null);
    show("schedules");
    if (newView) schedules.open();
    setTitle("Schedules · aegis");
  } else if (r.view === "help") {
    watchHost(null);
    follow(null);
    show("help");
    if (newView) helpTab.show(r.id);
    setTitle("Help · aegis");
  } else if (r.view === "session") {
    // A row's link: the row is revealed now when its transcript is here, else
    // once it lands. The URL drops "&at=", so the tab's hash is its own again.
    // The session shown already: its transcript is here, so the row lands
    // now, or never when it is gone. Otherwise it lands with the transcript.
    // Only on the render that read the link: a later one may come before the
    // transcript lands, and must leave the row waiting for it.
    if (r.at && landEntry?.key === r.id && shown === r.id) {
      if (transcript.entries.has(landEntry.id)) transcript.reveal(landEntry.id);
      landEntry = null;
    }
    watchHost(forKey(r.id).server || "");
    // Shown first: follow() sizes the message box, which measures 0 while hidden.
    show("session");
    follow(r.id);
    renderMeta(sessions.get(r.id));
    if (newView) {
      drawQuota();
      drawSideHost();
    }
  } else if (r.view === "browser") {
    watchHost(null);
    follow(null);
    if (!sessions.has(r.id)) return homeLive && go("#");
    show("browser");
    const card = termCard(r.id);
    openBrowser(card.id, card, { conn, server: card.server ?? null });
    setTitle(`${card.name} · aegis`);
  } else if (r.view === "term") {
    watchHost(null);
    follow(null);
    show("term");
    const card = termCard(r.id);
    openTerm(card.id, card, { conn, server: card.server ?? null }).catch((e) => toast(e.message, { kind: "error" }));
    setTitle(`${card.name} · aegis`);
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
  renderBand($("band"), { metas: local(), host, server: conn.server || localStorage.getItem("aegis.server") || "", link: stale ? seenText() : links.length ? "this server" : "", off: stale, measure: () => measureNet(null) });
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
      measure: () => measureNet(l.name),
    });
  }
}

// A speed reading now, on the server the row belongs to; the host channel
// brings the result, "measuring…" first.
function measureNet(server) {
  conn.call("net.measure", {}, server).catch((e) => toast(e.message, { kind: "error" }));
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
      attachCardMenu(cards);
      box.append(band, cards);
      host.append(box);
      const r = remote.get(l.name);
      if (r) renderBandQuota(band, { ...farQuota(r.quota), now: nowS() });
    }
    host.append(box); // keep the links' order
    const metas = on(l.name).filter(kindShown);
    if (metas.length) renderCards(box.querySelector(".cards"), metas, openCard, fleetOrder, closeById);
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
  if (h.disk) tiles.push(meter("disk", h.disk.pct)); // the fullest
  rows.push(...diskRows(h));
  const net = h.net;
  if (net) {
    const sp = net.speed;
    tiles.push(tile("net", net.ok ? `${net.rtt_ms} ms` : "down", { severity: net.ok ? "normal" : "critical" }));
    if (sp?.complete) tiles.push(tile("speed", mbps(sp.bps), { small: " Mbps" }));
  }
  rows.push(...netRows(net, Date.now() / 1000, () => measureNet(server || null)));
  $("s-host-tiles").replaceChildren(...tiles);
  $("s-host-rows").replaceChildren(...rows);
  const live = (s.server ? on(s.server) : local()).filter((m) => m.state !== "asleep" && !isTool(m));
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
  tellWatch();
}
// Which session this page shows while it is visible, for parking (park.py): a
// session someone looks at keeps its harness. A hidden page counts as away.
let watchedServer = null;
function tellWatch() {
  const key = route().view === "session" ? shown || "" : ""; // the Fleet and other views watch nothing
  const { server, log_id } = key ? forKey(key) : { server: null, log_id: null };
  if (watchedServer !== null && watchedServer !== (server || "")) {
    const off = { t: "watch", session: null, visible: false };
    if (watchedServer) off.server = watchedServer;
    conn.post(off);
  }
  watchedServer = server || "";
  const msg = { t: "watch", session: log_id, visible: !!log_id && !document.hidden };
  if (server) msg.server = server;
  conn.post(msg);
}
window.addEventListener("hashchange", tellWatch);
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
  if (sessions.has(id)) sessionStorage.setItem("aegis.lastShown", (lastShown = id));
  tellView();
  const turn = ++follows;
  const first = early;
  early = null; // read once: a later visit reads what the device holds then
  atts.show(id);
  drawQueued();
  if (!id) {
    canvas.clear();
    return viewer.show(null);
  }
  // The base before any row is drawn: a kept tab's rows mount at once, and
  // would take the last tab's server's base (#425).
  const where = forKey(id);
  setFileBase(where.server ? `/via/${where.server}` : "");
  const saved = kept.get(id);
  if (saved) {
    kept.delete(id);
    transcript.restore(saved);
  }
  // The divider is placed by the first snapshot or delta after this switch,
  // and kept across a resubscribe of the same session.
  let placed = false;
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
    (first?.id === id ? first.copy : loadTranscript(id)).then((copy) => {
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

// The sidebar's Repos row: each repo or worktree the shown session wrote to,
// from the repos:<log_id> channel (repos/service.py), which sends the whole
// list again only when it changed. A session on a linked server has none here.
let reposFor = null;
let unsubRepos = null;
function followRepos(key) {
  if (reposFor === key) return;
  reposFor = key;
  if (unsubRepos) unsubRepos();
  unsubRepos = null;
  $("s-repos-sec").hidden = true;
  if (!key || key.includes("/")) return;
  const draw = (d) => {
    if (reposFor === key) drawRepos(d);
  };
  unsubRepos = conn.subscribe(`repos:${forKey(key).log_id}`, draw, (ops) => {
    for (const op of ops) if (op.set) draw(op.set);
  });
}

const rpPlural = (n, one, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;
const rpBase = (p) => p.split("/").filter(Boolean).pop() || p;

function prLink(pr) {
  if (!pr.url) return span("rp-pr", `#${pr.n}`);
  const a = document.createElement("a");
  a.className = "rp-pr";
  a.href = pr.url;
  a.target = "_blank";
  a.rel = "noopener";
  a.textContent = `#${pr.n}`;
  a.title = pr.title || "";
  return a;
}

function repoLine(r) {
  const line = document.createElement("div");
  line.className = "rp-line";
  if (r.peers.length) {
    const dot = span("rp-dot", "");
    dot.title = r.peers.map((p) => `${p.handle} is also here`).join("\n");
    line.append(dot);
  }
  line.append(span("n", r.name), span("rp-br", r.worktree ? `⑂ ${rpBase(r.top)}` : r.branch || "detached"));
  const right = span("rp-r", "");
  if (r.removed) right.append(span("rp-gone", "removed"));
  if (r.op) right.append(span("rp-op", r.op));
  if (r.dirty_n) right.append(span("rp-dirty", `~${r.dirty_n}`));
  if (r.unpushed_n) right.append(span("rp-ahead", `↑${r.unpushed_n}`));
  for (const pr of r.prs) if (pr.state === "open") right.append(prLink(pr));
  if (r.clean) right.prepend(span("rp-clean", "✓"));
  line.append(right);
  return line;
}

function rpKv(k, v, cls = "") {
  const d = document.createElement("div");
  d.className = "kv";
  const key = span(cls, k);
  const val = typeof v === "string" ? span("", v) : v;
  d.append(key, val);
  return d;
}

function rpList(items, shown, total) {
  const ul = document.createElement("ul");
  ul.className = "rp-files";
  for (const [text, x] of items.slice(0, shown)) {
    const li = document.createElement("li");
    li.append(span("", text));
    if (x) li.append(span("x", x));
    ul.append(li);
  }
  if (total > shown) {
    const li = document.createElement("li");
    li.className = "more";
    li.textContent = `+${total - shown} more`;
    ul.append(li);
  }
  return ul;
}

function repoSection(r, ghOk) {
  const sec = document.createElement("div");
  sec.className = "csec";
  const h = document.createElement("h5");
  h.className = "rp-h";
  h.append(span("", r.name));
  if (r.worktree) h.append(span("rp-wt", "worktree"));
  h.append(span("p", r.path));
  sec.append(h);
  if (r.removed) sec.append(rpKv("state", "removed: the directory is gone"));
  else if (r.branch || r.upstream) sec.append(rpKv("branch", r.upstream ? `${r.branch} → ${r.upstream}` : `${r.branch || "detached"}, no upstream`));
  if (r.stale) sec.append(rpKv("state", "git did not answer; last known state"));
  if (r.op) sec.append(rpKv("in progress", r.op, "rp-op"));
  if (r.dirty_n) {
    sec.append(rpKv("uncommitted", rpPlural(r.dirty_n, "file"), "rp-dirty"));
    sec.append(rpList(r.dirty.map((f) => [f.path, f.stat]), 10, r.dirty_n));
  }
  if (r.unpushed_n) {
    sec.append(rpKv("unpushed", rpPlural(r.unpushed_n, "commit"), "rp-ahead"));
    sec.append(rpList(r.unpushed.map((s) => [s]), 5, r.unpushed_n));
  }
  if (r.clean) sec.append(rpKv("state", "clean, pushed", "rp-clean"));
  if (r.left_n) {
    sec.append(rpKv("left here", rpPlural(r.left_n, "commit")));
    sec.append(rpList(r.left.map((c) => [`${c.hash} ${c.subject}`]), 5, r.left_n));
  }
  for (const pr of r.prs) {
    const v = span("", "");
    v.append(prLink(pr), document.createTextNode(` ${[pr.title, pr.state, pr.ci].filter(Boolean).join(" · ")}`));
    sec.append(rpKv("PR", v));
  }
  if (r.prs.length && !ghOk) sec.append(rpKv("", "PR state unknown: gh unavailable"));
  if (r.others_n) sec.append(rpKv("others' dirt", `${rpPlural(r.others_n, "file")} this session never wrote`));
  for (const p of r.peers) {
    const d = document.createElement("div");
    d.className = "rp-peer";
    d.textContent = `● ${p.handle} is also here: ${p.where}`;
    sec.append(d);
  }
  return sec;
}

function drawRepos(d) {
  const repos = d.repos || [];
  $("s-repos-sec").hidden = !repos.length;
  if (!repos.length) return;
  $("s-repos-n").textContent = String(repos.length);
  const lines = repos.slice(0, 5).map(repoLine);
  if (repos.length > 5) lines.push(span("rp-more", `+${repos.length - 5} more`));
  $("s-repos").replaceChildren(...lines);
  const sum = (k) => repos.reduce((a, r) => a + r[k], 0);
  const open = repos.reduce((a, r) => a + r.prs.filter((p) => p.state === "open").length, 0);
  $("s-repos-sum").textContent = [
    rpPlural(repos.length, "repo"),
    `${rpPlural(sum("dirty_n"), "file")} uncommitted`,
    `${rpPlural(sum("unpushed_n"), "commit")} unpushed`,
    `${rpPlural(open, "PR")} open`,
  ].join(" · ");
  $("s-repos-all").replaceChildren(...repos.map((r) => repoSection(r, d.gh)));
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
  // A launch on a version outside the tested range shows it in the warning
  // colour (harness_versions.py, #456).
  const u = s.untested_version;
  const hEl = $("s-harness");
  hEl.textContent = (s.harness_label || s.harness || "") + (u ? ` ${u.version || "?"}` : "");
  hEl.classList.toggle("warn", !!u);
  hEl.title = u ? `${u.state === "unknown" ? "version unknown" : `${u.state} than tested`} · tested with ${u.range}` : "";
  $("s-model").textContent = s.model_id && s.model_id !== s.model ? `${modelLine(s)} → ${s.model_id}` : modelLine(s);
  $("s-effort").textContent = s.effort || "";
  $("s-perm").textContent = s.permission || "";
  $("s-server").textContent = s.server || conn.server || "this server";
  // The handle as peers address it, handle@server, and the badge a Fleet card
  // gives a queue worker.
  $("s-at").textContent = s.server || conn.server ? `@${s.server || conn.server}` : "";
  $("s-worker").hidden = !s.worker;
  $("s-worker").textContent = s.worker ? `worker · ${s.worker.queue}` : "";
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
  followRepos(s.key);
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
  drawWaiting(s);
  const mons = s.monitors || [];
  $("s-mon-sec").hidden = !mons.length;
  renderMonitors($("s-monitors"), mons);
  const working = s.state === "working";
  $("interrupt").hidden = !working;
  // A member reads another's session and changes nothing in it (tokens.py).
  const theirs = !mine(s);
  // A workflow's node is the engine's while its run lives.
  const held = locked(s);
  $("continue").disabled = working || theirs || held;
  $("restart").disabled = theirs || held || !!s.old;
  $("working").hidden = !working;
  $("stop-session").disabled = s.state === "asleep" || theirs || held;
  $("quiet-session").disabled = !["working", "waiting"].includes(s.attention) || theirs || held;
  $("interrupt").disabled = theirs || held;
  $("close").disabled = theirs || held;
  if (working && workingSince == null) workingSince = Date.now();
  if (!working) workingSince = null;
  // A linked server that is down: the transcript stays readable, nothing sends.
  const down = s.off ? linkOf(s.server) : null;
  input.disabled = $("attach").disabled = !!down || theirs || held;
  drawSending(!!down || theirs || held);
  if (s.asleep_why !== "idle" && warmed === shown) warmed = null;
  drawComposerHint(s);
  setTitle(`${s.title || s.handle} · aegis`);
}

// The sessions this one waits on (registry._waiting_for): a queue task's
// worker or a spawned child, a row each; a click opens it. A task still
// pending has no worker yet, so its row names the queue and opens nothing.
function drawWaiting(s) {
  const list = s.waiting_for || [];
  $("s-wait-sec").hidden = !list.length;
  $("s-waiting").replaceChildren(
    ...list.map((w) => {
      const key = w.key && keyOf(s.server, w.key);
      const row = document.createElement("div");
      row.className = `kv wait${key ? " go" : ""}`;
      const where = w.kind === "task" ? `queue ${w.queue} · ${w.status}` : w.status;
      row.append(span("", w.handle || "no worker yet"), span("r", where));
      if (key) {
        row.tabIndex = 0;
        row.title = `Open ${w.handle}`;
        row.addEventListener("click", () => openSession(key));
        row.addEventListener("keydown", (ev) => ev.key === "Enter" && openSession(key));
      }
      return row;
    }),
  );
}

// The composer's placeholder: whose session, whether its server is reachable,
// and whether a message resumes it. While a parked session's harness resumes
// after the first keystroke, the waking row says so in the transcript instead.
function drawComposerHint(s) {
  $("waking").hidden = !(s.asleep_why === "idle" && warmed === shown);
  const theirs = !mine(s);
  const down = s.off ? linkOf(s.server) : null;
  // Chrome counts a placeholder in scrollHeight: measure again when it changes.
  const placeholder = locked(s)
    ? `Step ${s.workflow.node} of the workflow ${workflowTitle(s.workflow.run) || s.workflow.run}. You can read it; it takes no messages until the run stops.`
    : theirs
    ? s.server
      ? `A session on ${s.server}. You can read it; acting through the link is for an admin here.`
      : `${ownerOf(s)}'s session. You can read it; only ${ownerOf(s)} can write to it.`
    : down
    ? `${s.server} is ${down.state} since ${new Date(down.since * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}; nothing can be sent until it is back.`
    : s.state === "asleep" && s.asleep_why === "idle"
      ? idleFor(s)
        ? `Asleep after ${idleFor(s)} idle. Typing wakes it.`
        : "Asleep while idle. Typing wakes it."
      : s.state === "asleep"
      ? "Asleep; your next message wakes it."
      : touch.matches
        ? "Message the agent"
        : "Message the agent. Enter sends, Shift+Enter adds a line, / for commands, Esc interrupts.";
  if (input.placeholder !== placeholder) {
    input.placeholder = placeholder;
    autosize();
  }
}

setInterval(() => {
  tickMonitors();
  if (workingSince != null) $("working-meta").textContent = `${Math.round((Date.now() - workingSince) / 1000)}s, Esc interrupts`;
  if (root.dataset.view === "fleet" && local().some((m) => m.stale)) drawBand();
  if (root.dataset.view === "fleet") for (const c of document.querySelectorAll(".card")) {
    const m = sessions.get(c.dataset.id);
    if (m && !isTool(m)) {
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
  const cancel = ev.target.closest("[data-btw-cancel]");
  if (cancel && shown) {
    callFor(shown, "btw.cancel", { id: cancel.dataset.btwCancel }).catch((e) => toast(e.message, { kind: "error" }));
    return;
  }
  const btwRow = ev.target.closest(".row.btw");
  if (btwRow && (ev.target.closest(".hd") || btwRow.classList.contains("folded"))) {
    const folded = !btwRow.classList.contains("folded");
    btwFolds.set(btwRow.dataset.btw, folded);
    btwRow.classList.toggle("folded", folded);
  }
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
    // The row comes back with the card in it; this node is replaced. Only
    // the answer says where the file sits under the root (viewer.js).
    const files = await conn.call("file.peek", { log_id: forKey(shown).log_id, entry_id: b.dataset.entry });
    for (const f of files) if (f.root_path) peekRoots.set(f.url, f.root_path);
  } catch (e) {
    b.nextElementSibling.textContent = e.message;
    b.disabled = false;
  }
});

// -- Open a path a reply named, in the viewer at its line -----------------------
$("entries").addEventListener("click", async (ev) => {
  const code = ev.target.closest(".md code.ref:not([data-kind=dir])");
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
  if (!more) loadClosedTerms();
  drawArchive();
}

// An archive row's menu: its own Read and Reopen buttons (fleet.js).
cmenu.attach(
  $("arch-list"),
  (t) => {
    const read = t.querySelector?.("button.read");
    const again = t.querySelector?.("button.reopen");
    if (!t.dataset?.id || !read) return null;
    return {
      groups: [
        [
          { id: "read", label: "Read", icon: "book", run: () => read.click() },
          again && { id: "reopen", label: "Reopen", icon: "again", run: () => again.click() },
        ].filter(Boolean),
      ],
    };
  },
  { selector: "tr[data-id]" },
);

function drawArchive() {
  renderArchive($("arch-list"), archived.filter(kindShown), {
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

// A closed session's row, which its remove carried, joins the archive where it
// sorts (archive.py's newest-first key), so a close does not read the first
// page again, about 85 KB (#525). A search is the server's to match: it reads
// the page again.
function archiveGained(m) {
  if (!archiveLoaded) return; // the next render reads the page, this row in it
  if ($("arch-q").value.trim()) {
    archiveLoaded = false;
    return;
  }
  const row = withKey(m, m.server !== conn.server ? m.server : null);
  if (archived.some((x) => x.key === row.key)) return;
  if (m.worker) archiveWorkerCount += 1;
  if (!m.worker || archiveWorkers) {
    archiveCounts[m.server] = (archiveCounts[m.server] || 0) + 1;
    if (!archiveServer || archiveServer === m.server) {
      archiveTotal += 1;
      const newer = (a, b) => a.last_activity > b.last_activity || (a.last_activity === b.last_activity && a.log_id > b.log_id);
      const at = archived.findIndex((x) => newer(row, x));
      // Past the pages shown, the next page brings it.
      if (at >= 0) archived.splice(at, 0, row);
      else if (!archiveCursor) archived.push(row);
    }
  }
  drawArchive();
}

// Closed terminals on this server (term.closed), in a section of their own
// after the archive's list. Read opens one read-only, from closedTerms.
const closedTerms = new Map(); // id -> its row
let closedTermsLoaded = false;
async function loadClosedTerms() {
  let rows = [];
  try {
    rows = await conn.call("term.closed");
    closedTermsLoaded = true;
  } catch {
    // Offline, or a server without terminals: the section stays empty.
  }
  closedTerms.clear();
  for (const t of rows) closedTerms.set(t.id, t);
  const shownRows = fleetKind === "agents" ? [] : rows;
  $("arch-terms").hidden = !shownRows.length;
  const t = document.createElement("table");
  t.className = "tbl";
  for (const row of shownRows) {
    const tr = t.insertRow();
    tr.dataset.id = row.id;
    for (const [cls, text] of [[null, row.name], ["m", row.title], ["m", ago(row.closed_at)], ["m", row.code == null ? "-" : `exit ${row.code}`]])
      Object.assign(tr.insertCell(), { textContent: text ?? "" }, cls ? { className: cls } : {});
    const read = Object.assign(document.createElement("button"), { type: "button", className: "btn", textContent: "Read" });
    read.title = "Replay what it printed";
    read.addEventListener("click", () => go(`#t=${row.id}`));
    tr.insertCell().append(read);
  }
  document.querySelector("#arch-terms .arch-term-list").replaceChildren(t);
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
  if (n) go(n.classList.contains("card") ? hashOf(fleetSel) : `#read=${fleetSel}`);
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

// Resolves once the picked server's roster has loaded.
function setSpServer(server) {
  if (server === spServer) return Promise.resolve();
  spServer = server;
  $("sp-server").value = server || "";
  $("sp-cwd").value = ""; // the other machine's directories
  delete $("sp-agent").dataset.picked;
  const loaded = loadAgents();
  drawHints();
  return loaded;
}
$("sp-server").addEventListener("change", () => setSpServer($("sp-server").value || null));
const LAST_AGENT = "aegis.lastAgent";
const PICKS = ["harness", "model", "effort", "permission"];

// The + view's Terminals group: one button per profile on the picked server,
// the built-in shell first.
function drawProfiles(list, server) {
  $("sp-terms").hidden = !list.length;
  const host = document.querySelector("#sp-terms .sp-term-list");
  host.replaceChildren(
    ...list.map((p) => {
      const b = document.createElement("button");
      b.type = "button";
      b.dataset.profile = p.name;
      b.disabled = Boolean(p.error);
      b.title = p.error || p.cmd || "your login shell";
      const name = document.createElement("b");
      name.textContent = p.name;
      const what = document.createElement("span");
      what.textContent = p.error || p.cmd || "login shell";
      b.append(name, " ", what);
      b.addEventListener("click", () =>
        conn.call("term.spawn", { profile: p.name }, server).then(
          (card) => go(`#t=${keyOf(server, card.id)}`),
          (e) => toast(e.message, { kind: "error" }),
        ),
      );
      return b;
    }),
  );
}

// A browser under the terminals: a page on the server, held by the person.
function drawBrowserEntry(server) {
  const host = document.querySelector("#sp-terms .sp-term-list");
  const b = document.createElement("button");
  b.type = "button";
  b.dataset.browser = "";
  b.title = "a browser on the server you and an agent can share";
  const name = document.createElement("b");
  name.textContent = "browser";
  const what = document.createElement("span");
  what.textContent = "a page on the server";
  b.append(name, " ", what);
  b.addEventListener("click", () =>
    conn.call("browser.spawn", { url: "about:blank" }, server).then(
      (card) => go(`#b=${keyOf(server, card.id)}`),
      (e) => toast(e.message, { kind: "error" }),
    ),
  );
  $("sp-terms").hidden = false;
  host.querySelector("[data-browser]")?.remove();
  host.append(b);
}

// The roster load in flight, so a caller can wait for the one already
// running (openSpawnWith right after a connect).
let agentsLoading = Promise.resolve();
function loadAgents() {
  agentsLoading = fetchAgents();
  return agentsLoading;
}
async function fetchAgents() {
  conn.call("term.profiles", {}, spServer).then(
    (list) => drawProfiles(list, spServer),
    () => drawProfiles([], spServer), // a server without terminals
  ).then(() => conn.call("browser.available", {}, spServer)).then(
    (a) => a.ok && drawBrowserEntry(spServer),
    () => {}, // a server from before browsers
  );
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
    ...listed.map((m) => ({ value: m.value, label: m.value, meta: modelMeta(m) })),
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

// A harness version outside the tested range (harness_versions.py): the
// server refused the launch once with what it found; ask the person whether to
// start once, allow that version for good, or cancel. Resolves "once",
// "always" or null.
function untestedAsk(d) {
  const p = (cls, text) => {
    const e = document.createElement("p");
    e.className = cls;
    e.textContent = text;
    return e;
  };
  const body = document.createElement("div");
  body.className = "untested";
  const range = d.min === d.max ? d.max : `${d.min} to ${d.max}`;
  const head = d.state === "unknown" ? `${d.label}: version unknown` : `${d.label} ${d.version}`;
  body.append(p("range", `${head} · tested with ${range}`));
  body.append(p("text", (d.message || "").split("\n")[0]));
  body.append(p("found", `found: ${d.path || "nothing on PATH"}`));
  const cmd = document.createElement("div");
  cmd.className = "cmd";
  const code = document.createElement("code");
  code.textContent = d.install;
  const copy = document.createElement("button");
  copy.type = "button";
  copy.className = "btn copy-cmd";
  copy.textContent = "Copy";
  copy.addEventListener("click", () =>
    navigator.clipboard.writeText(d.install).then(
      () => toast("Copied"),
      () => toast("Could not copy", { kind: "error" }),
    ),
  );
  cmd.append(code, copy);
  body.append(cmd);
  for (const n of d.notes || []) body.append(p("note", n));
  const buttons = [{ label: "Cancel", value: null }];
  if (d.may_always) buttons.push({ label: `Always allow ${d.version}`, value: "always" });
  buttons.push({ label: "Start once", value: "once", primary: true });
  const title = d.state === "unknown" ? `${d.label}'s version is unknown` : `${d.label} ${d.version} is untested`;
  return choose(title, body, buttons);
}

// A call that starts a harness, asked again with the person's answer when the
// server refuses it as harness_untested. Cancel resolves CANCELLED, which no
// answer can be: session.send answers a delivery with nothing at all.
const CANCELLED = Symbol("cancelled");
async function untestedRetry(call, again) {
  try {
    return await call();
  } catch (e) {
    if (e.code !== "harness_untested" || !e.data) throw e;
    const pick = await untestedAsk(e.data);
    if (!pick) return CANCELLED;
    const r = await again(pick);
    if (r?.not_saved) toast(`Started once: ${r.not_saved}`, { kind: "error" });
    return r;
  }
}

async function spawnFromComposer() {
  const a = current();
  if (!a || $("sp-go").disabled || offline()) return;
  $("sp-go").disabled = true;
  $("sp-error").textContent = "";
  await dictation.finish($("sp-text"));
  const text = $("sp-text").value.trim();
  const params = { agent: a.name, cwd: $("sp-cwd").value.trim() || null, ...overrides() };
  // With files or dictated text, the session starts empty: the files upload
  // into its inbox and go out with the text as its first message (sendFirst),
  // which carries the dictated mark a spawn's prompt has no field for.
  const dictated = spoken.has("spawn") && !spServer; // a linked server may predate the field
  const held = spAtts.list("spawn").length > 0 || dictated;
  // A / line runs as a command in the new session, as typed there (#570):
  // as spawn's prompt the harness would get it as plain text.
  const command = text.startsWith("/") && !text.startsWith("//");
  if (text && !held && !command) params.prompt = text;
  try {
    const r = await untestedRetry(
      () => conn.call("session.spawn", params, spServer),
      (untested) => conn.call("session.spawn", { ...params, untested }, spServer),
    );
    if (r === CANCELLED) return; // the typed text stays
    localStorage.setItem(LAST_AGENT, a.name);
    $("sp-text").value = "";
    spawnGrow();
    pickAgent(a.name);
    const key = keyOf(spServer, r.log_id);
    go(`#s=${key}`);
    spoken.delete("spawn");
    if (held) sendFirst(key, text, spAtts.take("spawn"), dictated);
    else if (command) {
      // The session's patch lands before spawn's reply; a far one may not have.
      const s = sessions.get(key);
      if (s) sendLine(text, false, [], s);
      else sendFirst(key, text, []);
    }
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

// The first message grows with its text until the form fills the view, and only
// then scrolls (#384): the new tab has nothing else to show. The room left
// changes with the window and with what sits around the box (files, hints, an
// error), so those are watched too. A hidden view has no height to fill.
function spawnGrow() {
  const t = $("sp-text");
  const view = t.closest(".view");
  if (!view.clientHeight) return;
  stackActs(t);
  t.style.height = "";
  const css = getComputedStyle(view);
  const pad = parseFloat(css.paddingTop) + parseFloat(css.paddingBottom);
  const room = view.clientHeight - pad - ($("spawn").offsetHeight - t.offsetHeight);
  t.style.height = `${Math.max(t.offsetHeight, Math.min(t.scrollHeight, room))}px`;
}
$("sp-text").addEventListener("input", spawnGrow);
const spawnRoom = new ResizeObserver(spawnGrow);
for (const el of [$("sp-text").closest(".view"), $("sp-atts"), $("sp-hints"), $("sp-error")]) spawnRoom.observe(el);

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
// `marks`: whether the server records this as the tutorial taken (a tutorial
// page) so the hint hides; a tour of another page or a question marks nothing.
async function startGuide(params, kind = "tutorial", marks = true) {
  const r = await untestedRetry(
    () => conn.call("help.guide", { kind, ...params }),
    (untested) => conn.call("help.guide", { kind, ...params, untested }),
  );
  if (r === CANCELLED) return null;
  if (marks) ranHint(kind);
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
$("files-btn").addEventListener("click", () => go(filesView.path ? filesHash(filesView.path) : "#files"));
$("schedules-btn").addEventListener("click", () => go("#schedules"));
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
    // The focused row's menu, else the selected transcript row's or Fleet card's.
    menuKey() {
      const at = document.activeElement?.closest?.("[data-id], .trow");
      if (cmenu.openFor(at)) return;
      const v = route().view;
      if (v === "session" || v === "read") cmenu.openFor(transcript.selected && transcript.nodes.get(transcript.selected));
      else if (v === "fleet") cmenu.openFor(fleetItems().find((x) => x.dataset.id === fleetSel));
    },
    spawn: () => offline() || go("#new"), // a new session needs the server
    settings: () => go("#settings"),
    journal: () => go("#journal"),
    files: () => filePicker.open(),
    save: () => route().view === "files" && filesView.save(),
    schedules: () => go("#schedules"),
    tab(ev) {
      const n = Number(ev.altKey ? ev.code.slice(5) : ev.key);
      if (n === 0) go("#fleet");
      else if (tabbed()[n - 1]) go(hashOf(tabbed()[n - 1].key));
    },
    workflow() {
      if (route().view !== "session") return;
      menu.setLine("/workflow ");
      input.focus();
      menu.openInline();
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
      else if (cmenu.close() || cmenu.unbar() || viewer.close() || find.close() || closeMonitorCard() || closeCard()) return;
      else if (route().view === "session" && !editing.size) interrupt();
    },
    help: () => help(),
    side: () => toggleSide(),
    quiet() {
      const b = $("quiet-session");
      if (route().view === "session" && !b.disabled) b.click();
    },
    palette: () => palette.toggle(),
  },
  () => (booted ? route().view : "boot"),
);

// Alt+[ and Alt+]: the Fleet, then the tabs, round.
function cycle(d) {
  const all = ["#fleet", ...tabbed().map((m) => hashOf(m.key))];
  const i = all.indexOf(location.hash || "#fleet");
  go(all[i < 0 ? (d > 0 ? 0 : all.length - 1) : (i + d + all.length) % all.length]);
}

// -- composer ---------------------------------------------------------------
const input = $("input");

// A composer send gets an answer within SEND_VERDICT_MS (#350). Until then the
// box and the button are locked. Plain text with no answer by then becomes the
// session's queued draft and the socket is dropped as stalled (#429); a command
// or files go back in the box, since only text waits on the device.
const SEND_VERDICT_MS = 5000;
const LATE = new Error("no answer yet");
const NOT_CONFIRMED = "No answer in 5 s. Send again: it will not arrive twice.";
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
// The drafts that got dictated text since their last send, by draft key and
// "spawn": their send tells the server, which adds a note for the agent (#579).
const spoken = new Set();
const sessionTarget = () => {
  const key = `aegis.draft.${shown}`;
  return { el: input, key, current: () => `aegis.draft.${shown}`, heard: () => spoken.add(key) };
};
const spawnTarget = () => ({ el: $("sp-text"), key: null, current: () => null, heard: () => spoken.add("spawn") });
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

// The buttons sit beside one line of text and drop under a longer one, so its
// lines get the box's whole width (#575). It measures at the narrow width every
// time: measured wide, a text that fits there would flip back and forth. A
// placeholder never stacks: an empty box keeps its height. The measure is a
// hidden copy: resizing the box itself to measure lays out the whole transcript
// again, seconds for a long message.
const ruler = document.createElement("textarea");
ruler.ariaHidden = "true";
ruler.tabIndex = -1;
ruler.style.cssText = "position:absolute;left:-9999px;top:0;visibility:hidden;height:0;padding-top:0;padding-bottom:0;border:0;overflow:hidden;box-sizing:border-box";
document.body.append(ruler);
function stackActs(t) {
  const box = t.closest(".box");
  if (!t.value || !box.clientWidth) return box.classList.remove("stacked");
  const css = getComputedStyle(t);
  for (const k of ["fontFamily", "fontSize", "fontWeight", "lineHeight", "letterSpacing", "paddingLeft", "paddingRight"]) ruler.style[k] = css[k];
  ruler.style.width = `${box.clientWidth - box.querySelector(".acts").offsetWidth}px`;
  ruler.value = t.value;
  box.classList.toggle("stacked", ruler.scrollHeight > parseFloat(css.lineHeight) * 1.5);
}

function autosize() {
  stackActs(input);
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
  const on = !!c && s.state !== "working" && !s.continued_by && (s.context_tokens ?? 0) >= c.min && nowS() - (s.last_activity || nowS()) >= c.after;
  const key = on ? `${s.log_id}:${s.context_tokens}` : "";
  if (box.dataset.key === key) return;
  box.dataset.key = key;
  box.hidden = !on;
  // A queue worker's task owns it, so it is never handed off (#354).
  $("coldnote-go").hidden = !!s.worker;
  $("coldnote-go").onclick = on ? () => handOff(s) : null;
  $("coldnote-t").textContent = on
    ? `The prompt cache has expired, so your next message rewrites all ${fmtTokens(s.context_tokens)} tokens of this context, about 20 times what a warm turn reads.`
    : "";
  if (on && transcript.following) transcript.toBottom();
}

// Continue the session's work in a fresh one (session.handoff, #354); the
// old tab is archived once the new session has answered.
async function handOff(s) {
  try {
    const r = await callFor(s.key, "session.handoff");
    go(`#s=${keyOf(s.server, r.log_id)}`);
  } catch (e) {
    toast(e.message, { kind: "error" });
  }
}

function drawReplies(s) {
  const box = $("replies");
  const working = s.state === "working";
  const replies = working ? [] : s.replies || [];
  // The agent's brief for a fresh session, offered until a handoff starts.
  const brief = !working && !s.continued_by ? s.handoff || null : null;
  const key = JSON.stringify([s.log_id, replies, brief, s.continued_by]);
  if (box.dataset.key === key) return;
  box.dataset.key = key;
  box.hidden = !replies.length && !brief && !s.continued_by;
  const chips = replies.map((text) => {
    const b = document.createElement("button");
    b.className = "rp";
    b.textContent = text;
    b.addEventListener("click", () => sendLine(text, false));
    return b;
  });
  if (brief) {
    const h = document.createElement("button");
    h.className = "rp hand";
    h.textContent = `continue fresh · ${fmtTokens(s.context_tokens)}`;
    h.addEventListener("click", () => handOff(s));
    chips.unshift(h);
  }
  $("replies-pills").replaceChildren(...chips);
  const next = s.continued_by && sessions.get(keyOf(s.server, s.continued_by));
  $("handoff-done").hidden = !s.continued_by;
  const who = next ? next.handle : "a fresh session";
  $("handoff-done").textContent = !s.continued_by
    ? ""
    : s.archived
      ? `continued in ${who}`
      : `continued in ${who}; this tab is archived when it finishes its first turn`;
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
// A queued draft's send gets the composer's deadline: with no answer the socket
// is dropped, which settles the send as unreached and the draft waits again.
setSender((key, params, { ask: asking = false } = {}) => {
  const call = callFor(key, "session.send", params);
  const late = setTimeout(() => conn.drop(), SEND_VERDICT_MS);
  // A stopped session on an untested harness: a background flush settles the
  // draft as an error; the person's Send anyway asks, and Cancel keeps it.
  return call
    .finally(() => clearTimeout(late))
    .catch(async (e) => {
      if (!asking || e.code !== "harness_untested" || !e.data) throw e;
      const pick = await untestedAsk(e.data);
      if (!pick) throw e;
      return callFor(key, "session.send", { ...params, untested: pick });
    });
});
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
  const hide = s.attention !== "done" || locked(s);
  if (box.hidden === hide) return;
  box.hidden = hide;
  if (transcript.following) transcript.toBottom();
}
$("closing-btn").addEventListener("click", () => {
  const s = focused();
  if (s) closeSession(s);
});

const askClose = (s) =>
  ask(`Archive ${s.title || s.handle}? Its tab goes away in every browser; Reopen in the archive brings it back.`, { ok: "Archive" });
// The one rule for when archiving asks: a done session has nothing left to
// lose, anything else may be mid-work. The panel's Archive, /archive and /close,
// the Fleet's Archive button, the menu and the phone's Archive row all pass
// through it.
const confirmClose = async (s) => s.attention === "done" || (await askClose(s));

async function closeSession(s) {
  if (locked(s)) return toast(`${s.handle} is a step of a running workflow; it archives when the run ends`, { kind: "error" });
  if (offline() || !(await confirmClose(s))) return;
  if (isTool(s)) {
    const { server, log_id } = forKey(s.key);
    const op = s.kind === "term" ? "term.close" : "browser.shut";
    return conn.call(op, { id: log_id }, server).catch((e) => toast(e.message, { kind: "error" }));
  }
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
  if (text === "/help" || text === "/workflow") {
    // The menu is the help: it lists every command with what it does. And
    // /workflow alone lists the saved workflows, which complete its id.
    const at = text === "/help" ? "/" : "/workflow ";
    if (fromComposer) menu.setLine(at);
    menu.close();
    if (fromComposer) menu.openInline();
    else menu.openOverlay(at);
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
  if ((text === "/archive" || text === "/close") && !(await confirmClose(s))) return;
  if (text === "/restart" && !(await confirmRestart(s))) return;
  // With no way to the session's server, plain text waits as its draft on this
  // device (outbox.js); a command or files still need the connection.
  const rev = shown === s.key ? transcript.rev : -1;
  const waitAsDraft = async (nonce) => {
    await queue(s.key, text, rev, nonce);
    if (fromComposer) {
      localStorage.removeItem(`aegis.draft.${s.key}`);
      spoken.delete(`aegis.draft.${s.key}`); // the outbox keeps no mark
    }
    if (fromComposer && shown === s.key) {
      input.value = "";
      autosize();
      menu.close();
      $("composer").classList.remove("bad");
    }
    if (shown === s.key) transcript.toBottom();
  };
  if ((!conn.open || s.off) && !text.startsWith("/") && !attachments.length) return waitAsDraft();
  const box = $("replies");
  const was = box.hidden;
  const aside = /^\/btw(\s|$)/.test(text);
  if (!aside) {
    box.hidden = true; // any send answers the turn the pills belonged to
    delete box.dataset.key; // so the next drawReplies always redraws
  }
  const params = attachments.length ? { text, attachments } : { text };
  // A linked server older than nonces would refuse the field.
  if (fromComposer && !forKey(s.key).server) params.nonce = nonceFor(s.key, JSON.stringify([text, attachments]));
  // Only to this server, like the nonce: a linked one older than the field would refuse it.
  if (fromComposer && !forKey(s.key).server && spoken.has(`aegis.draft.${s.key}`)) params.dictated = true;
  const call = callFor(s.key, "session.send", params);
  const sent = (r) => {
    unconfirmed.delete(s.key);
    if (/^\/model\s/.test(text)) catalogs.delete(s.key); // its efforts may differ
    if (fromComposer) {
      localStorage.removeItem(`aegis.draft.${s.key}`);
      spoken.delete(`aegis.draft.${s.key}`);
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
    if (e?.code === "harness_untested" && e.data) {
      // A stopped session, or a /spawn line, on an untested harness version.
      if (fromComposer) {
        sending.delete(s.key);
        drawSending();
      }
      try {
        const r = await untestedRetry(
          () => Promise.reject(e),
          (untested) => callFor(s.key, "session.send", { ...params, untested }),
        );
        if (r !== CANCELLED) sent(r);
      } catch (e2) {
        toast(e2.message, { kind: "error" });
      }
      return;
    }
    if (e !== LATE) {
      toast(e.message, { kind: "error" });
      return;
    }
    // No answer yet. Plain text waits as the session's draft under the same
    // nonce, and the stalled socket is dropped so the page reconnects; the
    // draft goes out on the reconnect and arrives once.
    if (!text.startsWith("/") && !attachments.length) {
      call.catch(() => {}); // the drop rejects it; the draft carries on
      unconfirmed.delete(s.key);
      await waitAsDraft(params.nonce);
      conn.drop();
      return;
    }
    // A command or files: the box is the person's again, with the text, and
    // says so. An answer that comes later still settles it, unless they rewrote it.
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
async function sendFirst(key, text, files, dictated) {
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
    await callFor(key, "session.send", { text, attachments: ready.ids, ...(dictated && text && { dictated }) });
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

// A session action's error: in the panel when it is the shown session's,
// else a toast (a card's or a tab's menu acts on any session).
function failed(key, e) {
  if (key === shown) $("side-error").textContent = e.message;
  else toast(e.message, { kind: "error" });
}

async function interruptKey(key) {
  const s = sessions.get(key);
  if (!s || s.state !== "working") return;
  try {
    await callFor(key, "session.interrupt");
  } catch (e) {
    toast(e.message, { kind: "error" });
  }
}
const interrupt = () => focused() && interruptKey(focused().key);

async function quietKey(key, on) {
  if (offline() || !sessions.has(key)) return;
  try {
    await callFor(key, on ? "session.quiet" : "session.unquiet");
  } catch (e) {
    failed(key, e);
  }
}

// Sleep ends the process and keeps the conversation. What would die with it
// (session.kills: background tasks, timers, processes it left running)
// is asked about first. A server older than #479 has no such op.
async function sleepKey(key) {
  const s = sessions.get(key);
  if (offline() || !s) return;
  // A check that fails cannot say nothing would die: ask then too.
  const kills = s.old || s.state === "asleep" ? [] : await callFor(key, "session.kills").catch(() => ["aegis could not check what would end with it"]);
  if (kills.length && !(await ask(`Sleep ${s.handle}? ${kills.join("; ")}.`, { ok: "Sleep" }))) return;
  try {
    await callFor(key, "session.stop");
  } catch (e) {
    failed(key, e);
  }
}

// Wake starts an asleep session's process and sends nothing (session.warm),
// asking first about an untested harness version, as a send does.
async function wakeKey(key) {
  const s = sessions.get(key);
  if (offline() || !s) return;
  try {
    // An older server's warm takes only log_id.
    const wake = s.old ? {} : { wake: true };
    await untestedRetry(
      () => callFor(key, "session.warm", wake),
      (untested) => callFor(key, "session.warm", { ...wake, untested }),
    );
  } catch (e) {
    failed(key, e);
  }
}

// Restart: a fresh process on the same conversation. It cuts a turn, so a
// working session asks first: from the menu, the panel and a typed /restart.
const confirmRestart = async (s) =>
  s.state !== "working" || (await ask(`Restart ${s.handle}? It cuts the turn it is working on.`, { ok: "Restart" }));
async function restartKey(key) {
  const s = sessions.get(key);
  if (offline() || !s) return;
  if (!(await confirmRestart(s))) return;
  try {
    await untestedRetry(
      () => callFor(key, "session.restart"),
      (untested) => callFor(key, "session.restart", { untested }),
    );
  } catch (e) {
    failed(key, e);
  }
}

// A nudge after an interrupt, an error or a sleep: a message to an asleep
// session wakes it, so Continue is one sentence to the agent.
const continueKey = (key) => !offline() && sessions.has(key) && sendLine("Continue", false, [], sessions.get(key));

// The first keystroke to a parked session starts its harness while the person
// types (session.warm); once per parked stretch (warmed, above).
function warmIfParked() {
  const s = shown && sessions.get(shown);
  if (!s || s.asleep_why !== "idle" || warmed === shown || !mine(s)) return;
  warmed = shown;
  const key = shown;
  callFor(key, "session.warm", { log_id: forKey(key).log_id }).catch(() => {
    if (warmed !== key) return;
    warmed = null;
    if (shown === key) drawComposerHint(sessions.get(key));
  });
  drawComposerHint(s);
}
input.addEventListener("input", async () => {
  warmIfParked();
  autosize();
  if (shown) localStorage.setItem(`aegis.draft.${shown}`, input.value);
  if (!input.value.trim()) spoken.delete(`aegis.draft.${shown}`); // a box emptied by hand starts over
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
$("continue").addEventListener("click", () => focused() && continueKey(focused().key));
$("restart").addEventListener("click", () => focused() && restartKey(focused().key));
$("quiet-session").addEventListener("click", () => focused() && quietKey(focused().key, true));
$("stop-session").addEventListener("click", () => focused() && sleepKey(focused().key));

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

// Rename… from a menu: the session opens and its handle turns into the
// panel's own input, once the panel shows that session.
async function renameKey(key) {
  await openCard(key);
  const s = sessions.get(key);
  for (let i = 0; i < 30 && s && $("s-handle").textContent !== s.handle; i++) await new Promise(requestAnimationFrame);
  $("s-handle").click();
}

// -- the card and tab menu (menu.js) --------------------------------------------
// What applies to the session's state, and what the viewer may do: another
// member's session offers only opening it; a guard that blocks an action
// (a workflow's step, no connection) leaves it disabled with the reason.
function sessionMenu(key) {
  const s = sessions.get(key);
  if (!s) return null;
  const opening = [
    { id: "open", label: "Open", icon: "arrow", run: () => openCard(key) },
    { id: "newtab", label: "Open in new tab", icon: "open", run: () => window.open(hashOf(key)) },
  ];
  const title = `${isTool(s) ? s.name : s.handle} · ${LABEL[s.attention] || s.state}`;
  if (!mine(s)) return { title, groups: [opening] };
  const why = locked(s)
    ? `${s.handle} is a step of a running workflow`
    : offline() || s.off
      ? "No connection to its server"
      : undefined;
  const close = [{ id: "archive", label: "Archive", icon: "archive", danger: true, disabled: why, run: () => closeSession(s) }];
  if (isTool(s)) return { title, groups: [opening, close] };
  if (s.quiet) opening.push({ id: "unquiet", label: "Unquiet", icon: "unquiet", disabled: why, run: () => quietKey(key, false) });
  else if (["working", "waiting"].includes(s.attention))
    opening.push({ id: "quiet", label: "Quiet", icon: "quiet", disabled: why, run: () => quietKey(key, true) });
  const life = [];
  if (s.state === "working") life.push({ id: "interrupt", label: "Interrupt", icon: "interrupt", disabled: why, run: () => interruptKey(key) });
  // A server older than #479 wakes only a parked session.
  if (s.state === "asleep" && (!s.old || s.asleep_why === "idle")) life.push({ id: "wake", label: "Wake", icon: "wake", disabled: why, run: () => wakeKey(key) });
  if (s.state !== "working") life.push({ id: "continue", label: "Continue", icon: "play", disabled: why, run: () => continueKey(key) });
  if (s.state !== "asleep") life.push({ id: "sleep", label: "Sleep", icon: "sleep", disabled: why, run: () => sleepKey(key) });
  if (!s.old) life.push({ id: "restart", label: "Restart", icon: "restart", disabled: why, run: () => restartKey(key) });
  if (!s.worker) life.push({ id: "handoff", label: "Hand off", icon: "handoff", disabled: why, run: () => handOff(s) });
  const rename = [{ id: "rename", label: "Rename…", icon: "pencil", disabled: why, run: () => renameKey(key) }];
  return { title, groups: [opening, life, rename, close] };
}
// The menu of a card or tab pressed; none on the space around them.
function cardMenu(t) {
  return t.dataset?.id && t.matches(".card, .tab") ? sessionMenu(t.dataset.id) : null;
}
function attachCardMenu(box) {
  cmenu.attach(box, cardMenu, { selector: ".card[data-id], .tab[data-id]" });
}
attachCardMenu($("cards"));
attachCardMenu($("tablist"));

// The New session form filled from a source session: its server, agent,
// model and folder, and a prompt the person goes on writing. The picks come
// from the source's server's roster once it has loaded, the same way on every
// server: pickAgent resets the chips to that agent, so nothing a person set in
// the form before carries over. What the form's text held stays after the prompt.
async function openSpawnWith({ server = null, agent, model, cwd, prompt }) {
  $("sp-text").value = prompt + $("sp-text").value;
  go("#new");
  spawnGrow();
  $("sp-agent").dataset.picked = agent;
  await setSpServer(server || null);
  await agentsLoading; // the boot's or a reconnect's load may still be out
  if (roster.agents.some((a) => a.name === agent)) pickAgent(agent);
  if (model && model !== "default") {
    $("sp-model").value = model;
    markDiffs();
  }
  $("sp-cwd").value = cwd || "";
}

// What a selection in the transcript offers (spec 05). The text is taken now:
// a touch can clear the selection before a button's click lands.
function selectionItems(range) {
  const text = rangeText(range);
  const s = shown && sessions.get(shown);
  if (!text || !s) return null;
  // Another member's session takes no message from this person (drawSending),
  // so the items that write to its composer are left out, as sessionMenu does.
  const own = mine(s);
  return [
    own && { id: "ask", label: "Ask", icon: "prompt", run: () => insertIntoComposer(quoted(text)) },
    own && { id: "aside", label: "Ask aside", icon: "btw", run: () => insertIntoComposer(quoted(text), { prefix: "/btw\n" }) },
    {
      id: "new",
      label: "New session",
      icon: "start",
      run: () => openSpawnWith({ server: s.server, agent: s.agent, model: s.model, cwd: s.cwd, prompt: `From ${s.handle}:\n${quoted(text)}\n\n` }),
    },
    {
      id: "copy",
      label: "Copy",
      icon: "copy",
      title: "Copy (Shift: as a quote)",
      run: (ev) => navigator.clipboard.writeText(ev?.shiftKey ? quoted(text) : text).catch((e) => toast(`Copy failed: ${e.message || e}`, { kind: "error" })),
    },
  ].filter(Boolean);
}
cmenu.bar($("entries"), selectionItems);

// What a selection or an element in a file or a page offers (spec 07): the
// bar's items with the locator in front of the quote, and Comment. `to` is the
// session whose composer gets it; with none, or another member's, New session
// alone writes it. `leave` runs first (the viewer closes over the composer).
function pointItems({ locator, text, to, comment, leave = () => {} }) {
  const s = to && sessions.get(to);
  const own = s && mine(s);
  const q = `${locator}\n${quoted(text)}`;
  return [
    own && { id: "ask", label: "Ask", icon: "prompt", run: () => (leave(), toComposer(to, q)) },
    own && { id: "aside", label: "Ask aside", icon: "btw", run: () => (leave(), toComposer(to, q, { prefix: "/btw\n" })) },
    { id: "new", label: "New session", icon: "start", run: () => newSessionWith(s, q) },
    comment && { id: "comment", label: "Comment", icon: "comment", run: comment },
    {
      id: "copy",
      label: "Copy",
      icon: "copy",
      title: "Copy (Shift: as a quote)",
      run: (ev) => navigator.clipboard.writeText(ev?.shiftKey ? q : text).catch((e) => toast(`Copy failed: ${e.message || e}`, { kind: "error" })),
    },
  ].filter(Boolean);
}

// A gesture inside a sandboxed page (pagepoint.js, through artifacts.js): the
// menu at the point it reports, or the bar over its selection, drawn here in
// the frame's place. Which file, session and tray it belongs to comes from
// where the frame is: Files' page, the viewer's, or a card's in the session.
//
// A page's script may post at any time. A press there leaves the focus in
// the frame (a right-click's mousedown gives it, and a long press focuses its
// frame), so a post while the person types in the composer or only rests the
// pointer over the page opens nothing. A page can take the focus by script,
// so that is not proof: what keeps a made-up proposal harmless is that its
// menu never takes the focus, and only a click on an item writes anything, and not in its first half second: the page chose
// where the menu or the bar opens, and could put Ask under a click aimed at
// the page. A page scrolled, Esc in it, or a selection gone
// closes only what that frame drew.
const PAGE_MENU_SETTLE_MS = 500; // a page's menu or bar takes no click this soon
let pageMenus = 0; // numbers each page menu, for its open and closed messages
function pagePoint(frame, { kind, x, y, rect, touch, locator = {}, text = "" }) {
  if (kind === "clear") {
    if (cmenu.current()?.target === frame) cmenu.close();
    return cmenu.framed(frame) && cmenu.unbar();
  }
  if (document.activeElement !== frame || !frame.checkVisibility({ visibilityProperty: true })) return;
  const files = frame.closest("#files");
  const inViewer = frame.closest("#viewer");
  const path = files
    ? filesView.path
    : frame.dataset.artifact || (inViewer && viewer.pathOf()) || decodeURIComponent(new URL(frame.src, location.href).pathname.split("/").pop());
  // The tray as it is when Comment runs: the view may have drawn a new one.
  const trayNow = () => (files ? filesView.tray : inViewer ? viewer.tray : null);
  const tray = trayNow();
  const quote = String(text).trim();
  if (!path || !quote) return;
  const where = [locator.heading && `§ ${locator.heading}`, locator.css].filter(Boolean).join(" › ");
  const items = pointItems({
    locator: `\`${path}\`${where ? ` ${where}` : ""}`,
    text: quote,
    to: files ? lastShown : shown,
    leave: inViewer ? () => viewer.close() : undefined,
    comment: tray && (() => trayNow()?.add({ locator: where || `\`${path}\``, quote, at: locator })),
  });
  const box = frame.getBoundingClientRect();
  if (kind === "select" && rect) {
    const r = new DOMRect(box.left + rect.left, box.top + rect.top, rect.right - rect.left, rect.bottom - rect.top);
    return cmenu.barAt(items, r, frame, PAGE_MENU_SETTLE_MS);
  }
  if (kind !== "menu") return;
  // Numbered: opening this one closes the one before, whose closed message
  // reaches the page after this open would, and must not undo it.
  const n = ++pageMenus;
  cmenu.open({ groups: [items] }, { x: box.left + x, y: box.top + y, touch }, frame, {
    focus: false,
    settle: PAGE_MENU_SETTLE_MS,
    onClose: () => artifacts.tell(frame, "aegis/point-closed", { n }),
  });
  artifacts.tell(frame, "aegis/point-open", { n });
}

// `text` into session `key`'s composer, going there first; Files' unsaved
// edits ask before it leaves. No session to go to: the New session form.
// True when the text landed.
async function toComposer(key, text, opts) {
  const s = key && sessions.get(key);
  if (!s || !mine(s)) return newSessionWith(null, text);
  // A window tab (#w=) follows the session too, but draws no composer.
  if (shown !== key || route().view !== "session") {
    if (filesView.leaving(route(hashOf(key))) && !(await filesView.confirmLeave())) return false;
    go(hashOf(key));
  }
  if (shown !== key || route().view !== "session") return false;
  insertIntoComposer(text, opts);
  return true;
}

// The New session form with `text` for its prompt: from session `s`'s agent,
// model and folder, or the form's own picks. The text is in the form at once.
function newSessionWith(s, text) {
  if (s) openSpawnWith({ server: s.server, agent: s.agent, model: s.model, cwd: s.cwd, prompt: `From ${s.handle}:\n${text}\n\n` });
  else {
    $("sp-text").value = `${text}\n\n${$("sp-text").value}`;
    go("#new");
    spawnGrow();
  }
  return true;
}

// A transcript row's menu (spec 06), by kind; a selection inside the row puts
// the bar's items on top. Kinds the spec names no menu for keep the browser's,
// unless a selection is in them.
function rowMenu(t, ev) {
  const id = t.dataset?.id;
  const e = id && transcript.entries.get(id);
  const s = shown && sessions.get(shown);
  if (!e || !s) return null;
  const sel = getSelection();
  const range = sel.rangeCount && !sel.isCollapsed ? clampToRow(sel.getRangeAt(0), t) : null;
  // Only a press inside the selection is about it: Chrome on macOS selects
  // the word under any right-click. The keyboard's menu (no event) takes it.
  const hit = (r) => ev.clientX >= r.left && ev.clientX <= r.right && ev.clientY >= r.top && ev.clientY <= r.bottom;
  const picked = range && (!ev || [...range.getClientRects()].some(hit)) && selectionItems(range);
  // A tool's output is a round trip away: if the person moved to another
  // session meanwhile, the quote is dropped rather than written to that
  // session's composer.
  const quote = (then) => {
    const from = shown;
    rowText(id, copyDeps).then(
      (text) => {
        if (shown !== from) return toast("The quote was dropped: you left the session it came from.");
        if (text) then(quoted(text.trim()));
      },
      (err) => toast(err.message, { kind: "error" }),
    );
  };
  const reply = { id: "reply", label: "Reply", icon: "prompt", run: () => quote((q) => insertIntoComposer(q)) };
  const fresh = {
    id: "new",
    label: "New session",
    icon: "start",
    run: () => quote((q) => openSpawnWith({ server: s.server, agent: s.agent, model: s.model, cwd: s.cwd, prompt: `From ${s.handle}:\n${q}\n\n` })),
  };
  const copy = { id: "copy", label: "Copy", icon: "copy", key: "c", run: () => copyRow(id, transcript.nodes.get(id), copyDeps) };
  // A part still streaming has an id the whole block will replace: no link.
  const link = !e.live && {
    id: "link",
    label: "Copy link",
    icon: "link",
    run: () => navigator.clipboard.writeText(`${location.origin}${location.pathname}#s=${shown}&at=${id}`).catch((err) => toast(`Copy failed: ${err.message || err}`, { kind: "error" })),
  };
  const own = mine(s); // Reply and Edit write to a composer only its owner sends from
  let groups = null;
  if (e.kind === "prose" || e.kind === "inbox") groups = [[own && reply, fresh], [copy, link]];
  else if (e.kind === "user") {
    const edit = own && { id: "edit", label: "Edit and resend", icon: "pencil", run: () => insertIntoComposer(e.md || "") };
    groups = [[edit, fresh], [copy, link]];
  } else if (e.kind === "tool") {
    const open = e.detail?.path && !s.server && { id: "open", label: "Open file", icon: "file", run: () => openToolFile(id) };
    const out = { id: "copyout", label: "Copy output", icon: "copy", key: "c", run: copy.run };
    groups = [[open, out], [own && reply, link]];
  } else if (e.kind === "file" || e.kind === "artifact") {
    const items = sentItems(t).filter(Boolean);
    if (items.length) groups = [items];
  }
  groups = groups?.map((g) => g.filter(Boolean));
  if (!groups && !picked) return null;
  return { groups: [picked || [], ...(groups || [])] };
}
cmenu.attach($("entries"), rowMenu, { selector: ".row[data-id]" });

// A sent card's menu, from the buttons its bar drew (entries.js): Dock shows
// only where the person may dock it, and the shown file's key is on it.
function sentItems(row) {
  const dock = row.querySelector(".fbar .btn.dock");
  const id = dock?.dataset.dock;
  const can = id && !dock.hidden;
  const dl = row.querySelector(".fbar a.btn.dl");
  return [
    can && { id: "dock-right", label: "Dock right", icon: "dock", run: () => canvas.dock(id) },
    can && { id: "dock-left", label: "Dock left", icon: "dock", run: () => canvas.move(id, "left") },
    id && canvas.writable && !canvas.merged() && { id: "window", label: "Open in new window", icon: "window", run: () => canvas.popOut(id) },
    dl && { id: "download", label: "Download", icon: "latest", run: () => dl.click() },
  ];
}

// A Read, Write or Edit row's file, in the viewer (file.peek, as its Show button).
async function openToolFile(id) {
  try {
    const files = await conn.call("file.peek", { log_id: forKey(shown).log_id, entry_id: id });
    for (const f of files) if (f.root_path) peekRoots.set(f.url, f.root_path);
    viewer.open({ files, at: 0, remote: false, glyph: "file" });
  } catch (err) {
    toast(err.message, { kind: "error" });
  }
}
// The transcript's own scroll listener runs first: it still follows after a
// scroll it made, or one the browser made when a remounted row shrank the list.
cmenu.follow($("tr"), () => transcript.following);
