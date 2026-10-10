// The aegis client: one home server, the servers it links, many sessions.
//
// The tabs are the open sessions of the home server and of every server it
// links (each one's `sessions` channel), the same in every browser. Their order
// and which one is focused belong to this browser: the order in localStorage,
// the focus in the URL hash. A session is known by its key: its log id at
// home, `<server>/<log_id>` on a linked server (links.py).

import { Connection } from "./protocol.js";
import { Transcript } from "./transcript.js";
import { artifactStage, fileUrl, setFileBase } from "./entries.js";
import { copyRow, installCopy } from "./copy.js";
import { Find } from "./find.js";
import * as artifacts from "./artifacts.js";
import { TabOrder, patchTab, renderTabs } from "./tabs.js";
import { ago, byNeed, money, patchCard, renderArchive, renderBand, renderBandQuota, renderCards, tickPlan } from "./fleet.js";
import { age, countdown, elapsed, hostRow, hostSeverity, providerFor, quotaSideRow, quotaTile, tile } from "./gauges.js";
import { closeCard, initSide, restState, toggleCollapsed } from "./side.js";
import { installKeys, renderKeys } from "./keys.js";
import { Palette } from "./palette.js";
import { glyph, icon, installGlyphs, LABEL } from "./glyphs.js";
import { CommandMenu } from "./commands.js";
import { closeMonitorCard, renderMonitors, tickMonitors } from "./monitors.js";
import { Settings } from "./settings.js";
import { entry, Journal, opens } from "./journal.js";
import { installBell, redrawFavicon, setTitle, updatePing } from "./ping.js";
import { ask, cancelAsk } from "./dialog.js";
import "./pick.js";
import { Dictation } from "./dictation.js";
import { dur, planTimes } from "./plantime.js";

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
  { value: "syalia", label: "Syalia" },
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
  loadDetail: (ids) => {
    const id = shown;
    return callFor(id, "transcript.detail", { ids }).then((got) => (shown === id ? got : []));
  },
});
artifacts.setup({ call: (op, params, key) => callFor(key || shown, op, params), entry: (id) => transcript.entries.get(id), key: () => shown });
const copyDeps = {
  entry: (id) => transcript.entries.get(id),
  output: (id) => callFor(shown, "transcript.output", { id }).then((r) => r.text),
  detail: (id) => callFor(shown, "transcript.detail", { ids: [id] }).then((got) => got[0]),
};
installCopy($("entries"), copyDeps);
const find = new Find($("find"), transcript, (q) => callFor(shown, "transcript.search", { q }).then((r) => r.ids));
$("find-prev").append(icon("up"));
$("find-next").append(icon("down"));
$("find-close").append(icon("close"));
installGlyphs();
// The navigator: previous / next agent message, the position, and the latest.
$("nav-recap").append(icon("sparkle"));
$("nav-up").append(icon("up"));
$("nav-down").append(icon("down"));
$("jump").append(icon("latest"));
$("bell").append(icon("bell"));
$("settings-btn").prepend(icon("gear"));
installBell($("bell"));
$("nav-up").addEventListener("click", () => transcript.message(-1));
$("nav-down").addEventListener("click", () => transcript.message(1));
$("nav-pos").addEventListener("click", () => transcript.firstUnread());
$("nav-recap").addEventListener("click", () => askRecap(true));
// Every redraw of the transcript re-marks the selection, which asks for the
// navigator, so it is drawn at most once a frame: position() walks every entry.
let navFrame = 0;
function drawNav() {
  if (!navFrame) navFrame = requestAnimationFrame(drawNavNow);
}
// Shown with any entry, so the latest button is there before the first agent
// message; with none, the position is empty and the arrows are off. Unchanged
// values are not written back.
let navDrawn = {};
function drawNavNow() {
  navFrame = 0;
  const { index, total, unread } = transcript.position();
  const now = {
    hidden: !transcript.entries.size,
    text: total ? `${unread ? `${unread} unread · ` : ""}message ${index} of ${total}` : "",
    off: !total,
  };
  if (now.hidden !== navDrawn.hidden) $("nav").hidden = now.hidden;
  if (now.text !== navDrawn.text) $("nav-pos").textContent = now.text;
  if (now.off !== navDrawn.off) $("nav-up").disabled = $("nav-down").disabled = now.off;
  navDrawn = now;
}
// The fold level: this browser's choice, for every tab. z steps through
// everything shown, the work folded, and all but the messages.
const FOLDS = ["Everything shown", "Tool calls and thinking folded", "Only messages shown"];
$("nav-fold").append(icon("fold"));
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

// -- routing: #fleet, #new, #settings, #journal, #s=<key>, #read=<key> ------------------
function route() {
  const h = location.hash.slice(1);
  if (h.startsWith("s=")) return { view: "session", id: h.slice(2) };
  if (h.startsWith("read=")) return { view: "read", id: h.slice(5) };
  if (h === "new") return { view: "spawn" };
  if (h === "settings") return { view: "settings" };
  if (h === "journal") return { view: "journal" };
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

// A line at the foot of the page for a key that had nothing to do.
let noteTimer = null;
function note(text) {
  const n = $("note");
  n.textContent = text;
  n.hidden = false;
  clearTimeout(noteTimer);
  noteTimer = setTimeout(() => (n.hidden = true), 2000);
}

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
const conn = new Connection(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`, {
  onState(state, server) {
    $("conn-dot").className = `dot ${state === "open" ? "ready" : state === "connecting" ? "ghost" : "err"}`;
    $("conn-text").textContent =
      state === "open" ? server : state === "connecting" ? "connecting" : state === "closed" ? "disconnected, retrying" : state;
    if (state === "unauthorized") showLogin();
    if (state === "version") show("boot", "This page and the server speak different protocol versions. Reload the page.");
    if (state === "open") {
      // Whether this browser runs on the server's desktop (Open natively).
      if (conn.native) root.dataset.native = "";
      else delete root.dataset.native;
      loadAgents();
      loadVersion();
    }
  },
});
const settings = new Settings(conn, $("settings"), { themes: THEMES, setTheme });
let landEntry = null; // {key, id}: reveal this entry once its transcript lands
const journal = new Journal(conn, $("journal"), {
  onOpen: (r) => {
    landEntry = r.source ? { key: r.log_id, id: r.source } : null;
    go(r.open ? `#s=${r.log_id}` : `#read=${r.log_id}`);
  },
});
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
      else booted = true;
      onSessions();
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
        updatePing([...sessions.values()], { onOpen: openSession });
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
  },
  (ops) => {
    for (const op of ops) if (op.set) quota = op.set;
    drawQuota();
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
  } else if (r.view === "session" && ids.has(r.id)) renderMeta(sessions.get(r.id));
}

function onSessions() {
  const ids = order.arrange([...sessions.values()].sort((a, b) => a.created_at - b.created_at).map((m) => m.key));
  ordered = ids.map((id) => sessions.get(id));
  render();
  updatePing([...sessions.values()], { onOpen: openSession });
}

// -- rendering -----------------------------------------------------------------
const openSession = (id) => go(`#s=${id}`);
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
  const r = route();
  if (r.view === "session" && !sessions.has(r.id)) {
    const far = forKey(r.id).server;
    // A far session whose server's list has not arrived yet: wait for it.
    if (far && (!linksLoaded || (linkOf(far) && !remote.get(far)?.loaded))) return;
    go("#fleet"); // closed here or elsewhere
    return;
  }
  renderTabs($("tablist"), ordered, r.view === "session" ? r.id : null, tabActions);
  $("tablist").querySelector(".tab.on")?.scrollIntoView({ block: "nearest", inline: "nearest" });
  $("tab-fleet").classList.toggle("on", r.view === "fleet");
  $("tab-add").classList.toggle("on", r.view === "spawn");
  $("settings-btn").classList.toggle("on", r.view === "settings");
  $("journal-btn").classList.toggle("on", r.view === "journal");
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
    if (newView) drawQuota();
    if (!archiveLoaded) loadArchive();
    setTitle("Fleet · aegis");
  } else if (r.view === "spawn") {
    watchHost(null);
    follow(null);
    show("spawn");
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
  renderBand($("band"), { metas: local(), host, server: conn.server, link: links.length ? "this server" : "" });
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

  // With a share of the window, context leads and the quota follows; without
  // one, the quota's windows take the row and context moves to the foot.
  const lead = pct != null || !windows.length;
  const shown = windows.slice(0, lead ? 2 : 3);
  const left = !windows.length ? "" : lead ? cost : `${tokens ? `${tokens} ctx · ` : ""}${cost}`;
  const next = shown.find((w) => w.resets_at != null);
  const right = next ? `↻ ${countdown(next.resets_at - now)}` : "";
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
  $("s-foot-r").textContent = right;
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
  const rows = p.state === "failed" ? [] : p.windows.map((w) => quotaSideRow(p, w, now));
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

function follow(id) {
  if (shown === id) return;
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
  if (!id) return;
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
  unsubTranscript = conn.subscribe(
    `transcript:${where.log_id}`,
    (data) => {
      if (data.since !== undefined) transcript.resume(data);
      else transcript.snapshot(data);
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
      transcript.apply(ops);
    },
    undefined,
    // Holding nothing, a full snapshot mounts only the last rows; a delta
    // from -1 would mount every row through apply().
    () => (transcript.rev >= 0 ? transcript.rev : null),
    where.server,
  );
  menu.close();
  $("input").value = localStorage.getItem(`aegis.draft.${id}`) || "";
  autosize();
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
    line.append(span("g", r.glyph), span("x", (r.tag ? `${r.tag}: ` : "") + r.text), span("t", r.time));
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
  drawSideJournal(s.key);
  drawReplies(s);
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
  $("restart").disabled = working;
  $("working").hidden = !working;
  $("stop-session").disabled = s.state === "stopped";
  if (working && workingSince == null) workingSince = Date.now();
  if (!working) workingSince = null;
  // A linked server that is down: the transcript stays readable, nothing sends.
  const down = s.off ? linkOf(s.server) : null;
  input.disabled = $("send").disabled = !!down;
  // Chrome counts a placeholder in scrollHeight: measure again when it changes.
  const placeholder = down
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
  setTitle(`${working ? "● " : ""}${s.title || s.handle} · aegis`);
}

setInterval(() => {
  tickMonitors();
  if (workingSince != null) $("working-meta").textContent = `${Math.round((Date.now() - workingSince) / 1000)}s, Esc interrupts`;
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
  }
}, 1000);

// -- version: the running aegis and the latest release ------------------------
// Asked again every hour, so a tab left open learns about a new release; the
// server caches PyPI's answer for that long anyway.

function span(cls, text) {
  const el = document.createElement("span");
  el.className = cls;
  el.textContent = text;
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
  const mark = v.status === "current" ? [span("ok", "✓ current")] : behind ? [span("upd", "↑ update")] : [];
  $("ver-latest").replaceChildren(span("v", v.latest || "unknown"), ...mark);
  $("ver-line").replaceChildren(
    span("", "aegis"),
    span("v", shown),
    ...(dev ? [span("tag", "dev")] : behind ? [span("upd", "↑ update")] : v.status === "current" ? [span("ok", "✓")] : []),
  );
  $("ver-sec").hidden = false;

  const top = $("ver-top");
  top.replaceChildren(span("", shown), ...(dev ? [span("tag", "dev")] : behind ? [span("upd", "↑")] : []));
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
  try {
    const r = await callFor(id, "recap.request", { force });
    if (r.status === "off" || r.status === "failed") why = r.why;
    else if (r.status === "busy") why = "the agent is still working; ask again when it is done";
  } catch (e) {
    why = e.message;
  }
  if (force && why && shown === id) $("send-error").textContent = why;
}
$("entries").addEventListener("click", (ev) => {
  if (ev.target.closest("[data-recap=force]")) askRecap(true);
});
// Coming back to the page is landing again; the server decides.
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible" && shown) askRecap(false);
});

// -- Open natively on a sent file's card ------------------------------------
$("entries").addEventListener("click", async (ev) => {
  const b = ev.target.closest(".fbar .native");
  if (!b) return;
  b.disabled = true;
  try {
    await conn.call("file.open", { file_id: b.dataset.fileId, name: b.dataset.name });
  } catch (e) {
    $("send-error").textContent = e.message;
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

// -- archive -------------------------------------------------------------------
let archived = [];
let archiveCursor = null; // the next page's cursor; null: nothing more
let archiveTotal = 0; // the first page's count of everything matching
let archiveServer = null; // the server filter: null for every server
let archiveCounts = {}; // the first page's count per server
let archiveOffline = []; // linked servers that were down for the first page
let archiveLoaded = false;
let archiveTimer = null;

// The first page, or with `more` the next one after what is shown. A button
// asks for more, never scrolling: the page grows only when a person asks.
async function loadArchive(more = false) {
  archiveLoaded = true;
  const q = $("arch-q").value.trim();
  const params = q ? { query: q } : {};
  if (archiveServer) params.server = archiveServer;
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
    }
  } catch (e) {
    if (!more) [archived, archiveCursor, archiveTotal] = [[], null, 0];
  }
  renderArchive($("arch-list"), archived, {
    onReopen: reopen,
    onRead: (id) => go(`#read=${id}`),
  });
  $("arch-more").hidden = !archived.length;
  $("arch-count").textContent = `Showing ${archived.length} of ${Math.max(archiveTotal, archived.length)}`;
  $("arch-next").hidden = !archiveCursor;
  drawArchiveServers();
  fleetMark(false);
}

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
    $("sp-error").textContent = e.message;
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
  if (!a || $("sp-go").disabled) return;
  $("sp-go").disabled = true;
  $("sp-error").textContent = "";
  await dictation.finish($("sp-text"));
  const text = $("sp-text").value.trim();
  const params = { agent: a.name, cwd: $("sp-cwd").value.trim() || null, ...overrides() };
  if (text) params.prompt = text;
  try {
    const r = await conn.call("session.spawn", params, spServer);
    localStorage.setItem(LAST_AGENT, a.name);
    $("sp-text").value = "";
    pickAgent(a.name);
    go(`#s=${keyOf(spServer, r.log_id)}`);
  } catch (e) {
    $("sp-error").textContent = e.message;
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
$("sp-text").addEventListener("keydown", (ev) => {
  if (ev.key === "Enter" && !ev.shiftKey && !ev.isComposing) {
    ev.preventDefault();
    spawnFromComposer();
  }
});

// -- tab bar and keys -----------------------------------------------------------
$("tab-fleet").addEventListener("click", () => go("#fleet"));
$("tab-add").addEventListener("click", () => go("#new"));
// The ? list: drawn once from the key table.
const keymap = $("keymap");
renderKeys(keymap);
const help = (open = keymap.hidden) => (keymap.hidden = !open);
$("keys-btn").addEventListener("click", () => help());
$("settings-btn").addEventListener("click", () => go("#settings"));
$("journal-btn").addEventListener("click", () => go("#journal"));
keymap.addEventListener("click", (ev) => ev.target === keymap && help(false));
const palette = new Palette($("palette"), () => route().view);

// What each key in keys.js does. `input` and `editing` are declared below;
// a key is pressed only after this module has run.
installKeys(
  {
    composer() {
      const v = route().view;
      if (v === "session") input.focus();
      // The new tab's message box: Enter there spawns and sends.
      else if (v === "spawn") $("sp-text").focus();
    },
    dictate() {
      const v = route().view;
      if (!navigator.mediaDevices || (v !== "session" && v !== "spawn")) return;
      dictation.toggle(v === "spawn" ? spawnTarget() : sessionTarget(), "key");
    },
    browse() {
      const v = route().view;
      if (v === "session" || v === "read") {
        $("tr").focus({ preventScroll: true });
        transcript.pick();
      } else if (v === "fleet") {
        $("cards").focus({ preventScroll: true });
        if (!fleetSel) fleetMove(1);
      }
    },
    // In byNeed's order, from the tab after this one; from the top when this one
    // is not in the list, as after reading a review, which drops it.
    needs() {
      const list = byNeed(ordered);
      if (!list.length) return note("Nobody needs you");
      const r = route();
      const i = r.view === "session" ? list.findIndex((m) => m.key === r.id) : -1;
      const id = list[(i + 1) % list.length].key;
      if (list[i]?.key === id) return transcript.firstUnread(); // the only one, and open
      landUnread = id;
      go(`#s=${id}`);
    },
    tabPrev: () => cycle(-1),
    tabNext: () => cycle(1),
    next: () => transcript.move(1),
    prev: () => transcript.move(-1),
    turnNext: () => transcript.moveTurn(1),
    turnPrev: () => transcript.moveTurn(-1),
    first: () => transcript.edge(false),
    last: () => transcript.edge(true),
    messagePrev: () => transcript.message(-1),
    messageNext: () => transcript.message(1),
    firstUnread: () => transcript.firstUnread(),
    toggle: () => transcript.toggle(),
    find: () => find.open(),
    press: () => transcript.press(),
    copy: () => transcript.selected && copyRow(transcript.selected, transcript.nodes.get(transcript.selected), copyDeps),
    foldLevel: () => foldLevel((transcript.foldLevel + 1) % 3),
    walk() {},
    fleetNext: () => fleetMove(1),
    fleetPrev: () => fleetMove(-1),
    fleetOpen,
    filter: () => $("arch-q").focus(),
    spawn: () => go("#new"),
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
      if (cancelAsk()) return;
      if (root.dataset.side === "open") return closeSide();
      if (!keymap.hidden) help(false);
      else if (find.close() || closeMonitorCard() || closeCard()) return;
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
    $(dictation.target?.el === input ? "send-error" : "sp-error").textContent = message;
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
$("mic").addEventListener("click", () => dictation.toggle(sessionTarget(), "button"));
$("sp-mic").addEventListener("click", () => dictation.toggle(spawnTarget(), "button"));
// On a touch screen Enter adds a line and the button sends: the key sits where
// a mistap lands, and half a message costs a turn.
const touch = matchMedia("(pointer: coarse)");

function autosize() {
  input.style.height = "auto";
  input.style.height = `${Math.min(input.scrollHeight, window.innerHeight * 0.4)}px`;
}

function focused() {
  const r = route();
  return r.view === "session" ? sessions.get(r.id) : null;
}

// The agent's suggested next messages, from its turn_end. Redrawn only when they
// change: renderMeta runs on every patch of the open session. They sit after the
// transcript's last row, not in the composer, so they scroll away with it and
// never take reading space (#286).
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

const askClose = (s) =>
  ask(`Close ${s.title || s.handle}? Its tab goes away in every browser; it stays in the archive.`, { ok: "Close" });
// The one rule for when closing asks: a done session has nothing left to lose,
// anything else may be mid-work. The Close button, /close and the Fleet's
// Archive button all pass through it.
const confirmClose = async (s) => s.attention === "done" || (await askClose(s));

async function closeSession(s) {
  if (!(await confirmClose(s))) return;
  try {
    await callFor(s.key, "session.close");
  } catch (e) {
    $("side-error").textContent = e.message;
  }
}

// A line from the composer, or from the menu's own filter (Alt+/ over a
// draft), which leaves the composer alone. The server resolves "/" lines.
async function sendLine(text, fromComposer) {
  const s = focused();
  if (!text || !s) return;
  if (text === "/help") {
    // The menu is the help: it lists every command with what it does.
    if (fromComposer) menu.setLine("/");
    menu.close();
    if (fromComposer) menu.openInline();
    else menu.openOverlay();
    return;
  }
  if (text === "/close" && !(await confirmClose(s))) return;
  $("send-error").textContent = "";
  const box = $("replies");
  const was = box.hidden;
  box.hidden = true; // any send answers the turn the pills belonged to
  delete box.dataset.key; // so the next drawReplies always redraws
  try {
    await callFor(s.key, "session.send", { text });
    if (/^\/model\s/.test(text)) catalogs.delete(s.key); // its efforts may differ
    if (fromComposer) {
      input.value = "";
      localStorage.removeItem(`aegis.draft.${s.key}`);
      autosize();
      // Clearing the box fires no input event; an open menu would take the next Esc.
      menu.close();
      $("composer").classList.remove("bad");
    }
    transcript.toBottom();
  } catch (e) {
    $("send-error").textContent = e.message;
    box.hidden = was;
  }
}

const send = async () => {
  await dictation.finish(input); // what was said goes out with the rest
  return sendLine(input.value.trim(), true);
};

// Catalogs per session, fetched when the menu first opens there. The promise
// is kept, so keystrokes that arrive while it loads wait for the same call
// instead of each asking the server again.
const catalogs = new Map();
async function loadCatalog() {
  const s = focused();
  if (!s) return null;
  if (!catalogs.has(s.key)) catalogs.set(s.key, callFor(s.key, "commands.list"));
  try {
    return await catalogs.get(s.key);
  } catch (e) {
    catalogs.delete(s.key); // the next open asks again
    $("send-error").textContent = e.message;
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
    $("send-error").textContent = e.message;
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
$("restart").addEventListener("click", () => sendLine("Continue", false));

$("stop-session").addEventListener("click", async () => {
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
