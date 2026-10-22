// A browser's tab (#564): the page the server's Chromium draws, streamed as
// JPEG frames into a canvas, with a bar over it. The person's pointer, wheel
// and keys go back as CDP input events while they hold the page; while the
// agent holds it the bar shows the agent's last action and the canvas only
// watches. One browser is mounted at a time, as with terminals.

import { toast } from "./toast.js";

const $ = (id) => document.getElementById(id);
const refused = (e) => toast(e.message, { kind: "error" });
// CDP's modifier bits.
const mods = (ev) => (ev.altKey ? 1 : 0) | (ev.ctrlKey ? 2 : 0) | (ev.metaKey ? 4 : 0) | (ev.shiftKey ? 8 : 0);
const BUTTON = ["left", "middle", "right"];

let live = null; // {id, conn, server, card, unsub, queue, sending, decoding, pending}
let sessionsOf = () => [];

export function openBrowser(id, card, { conn, server = null, sessions = () => [] }) {
  sessionsOf = sessions;
  if (live?.id === id) return drawBrowser(card);
  closeBrowser();
  const mine = { id, conn, server, card, queue: [], sending: false, decoding: false, pending: null };
  live = mine;
  drawBrowser(card);
  $("br-wait").hidden = card.state !== "running";
  if (card.state === "running" && card.holder === "person") requestAnimationFrame(fit);
  mine.unsub = conn.subscribe(
    `browser:${id}`,
    (data) => paint(mine, data),
    (ops) => ops.forEach((f) => paint(mine, f)),
    () => {},
    () => null,
    server,
  );
}

export function closeBrowser() {
  live?.unsub?.();
  live = null;
  const c = $("br-screen");
  c.getContext("2d").clearRect(0, 0, c.width, c.height);
}

// Only the newest frame is decoded: a frame that arrives while one decodes
// replaces the one waiting, so a slow device skips frames instead of lagging.
function paint(mine, f) {
  if (live !== mine || !f?.frame) return;
  if (mine.decoding) {
    mine.pending = f;
    return;
  }
  mine.decoding = true;
  const img = new Image();
  img.onload = img.onerror = () => {
    if (live === mine && img.naturalWidth) {
      const c = $("br-screen");
      if (c.width !== img.naturalWidth || c.height !== img.naturalHeight) {
        c.width = img.naturalWidth;
        c.height = img.naturalHeight;
      }
      c.getContext("2d").drawImage(img, 0, 0);
      $("br-wait").hidden = true;
    }
    mine.decoding = false;
    const next = mine.pending;
    mine.pending = null;
    if (next) paint(mine, next);
  };
  img.src = `data:image/jpeg;base64,${f.frame}`;
}

export function drawBrowser(card) {
  if (!live || card.id !== live.id) return;
  live.card = card;
  const running = card.state === "running";
  const person = card.holder === "person";
  $("br-name").textContent = `◫ ${card.name}`;
  const url = $("br-url");
  if (document.activeElement !== url) url.value = card.url || "";
  url.readOnly = !(running && person);
  const chip = $("br-holder");
  chip.textContent = !running ? card.state : person ? "you" : "agent";
  chip.className = `br-holder ${running ? card.holder : "ended"}`;
  $("br-take").hidden = !running || person;
  $("br-giveback").hidden = !running || !person || !card.session;
  $("br-give").hidden = !running || !person || Boolean(card.session);
  $("br-reopen").hidden = running;
  for (const b of document.querySelectorAll("#v-browser .br-nav")) b.disabled = !(running && person);
  $("br-ask").hidden = !(person && card.ask);
  $("br-ask-text").textContent = card.ask ? `The agent asks: ${card.ask}` : "";
  $("br-act").textContent = !running ? `stopped: ${card.reason || ""}` : !person && card.last_action ? `agent: ${card.last_action}` : "";
  const c = $("br-screen");
  c.classList.toggle("locked", !(running && person));
  c.style.aspectRatio = `${card.width} / ${card.height}`;
  if (!running) $("br-wait").hidden = true;
}

// -- input ------------------------------------------------------------------

function canAct() {
  return live && live.card.state === "running" && live.card.holder === "person";
}

function push(ev) {
  if (!canAct()) return;
  live.queue.push(ev);
  if (!live.sending) flush(live);
}

// One call at a time, in order, each carrying what queued meanwhile.
async function flush(mine) {
  mine.sending = true;
  while (mine.queue.length && live === mine) {
    const events = mine.queue.splice(0, 200);
    try {
      await mine.conn.call("browser.input", { id: mine.id, events }, mine.server);
    } catch (e) {
      mine.queue.length = 0;
      refused(e);
    }
  }
  mine.sending = false;
}

// A point on the canvas in the page's CSS pixels.
function at(ev) {
  const r = $("br-screen").getBoundingClientRect();
  const { width, height } = live.card;
  return { x: Math.round(((ev.clientX - r.left) * width) / r.width), y: Math.round(((ev.clientY - r.top) * height) / r.height) };
}

const screen = $("br-screen");
let moveFrame = 0;
let lastMove = null;
screen.addEventListener("pointerdown", (ev) => {
  if (!canAct()) return;
  ev.preventDefault();
  screen.focus();
  screen.setPointerCapture(ev.pointerId);
  push({ t: "mouse", type: "mousePressed", ...at(ev), button: BUTTON[ev.button] || "left", buttons: ev.buttons, count: ev.detail || 1, mods: mods(ev) });
});
screen.addEventListener("pointerup", (ev) => {
  if (!canAct()) return;
  push({ t: "mouse", type: "mouseReleased", ...at(ev), button: BUTTON[ev.button] || "left", buttons: ev.buttons, count: ev.detail || 1, mods: mods(ev) });
});
// Moves go out at most once a frame: hover menus need them, a stream of them does not.
screen.addEventListener("pointermove", (ev) => {
  if (!canAct()) return;
  lastMove = { t: "mouse", type: "mouseMoved", ...at(ev), button: ev.buttons ? "left" : "none", buttons: ev.buttons, mods: mods(ev) };
  if (moveFrame) return;
  moveFrame = requestAnimationFrame(() => {
    moveFrame = 0;
    if (lastMove) push(lastMove);
    lastMove = null;
  });
});
screen.addEventListener(
  "wheel",
  (ev) => {
    if (!canAct()) return;
    ev.preventDefault();
    push({ t: "wheel", ...at(ev), dx: ev.deltaX, dy: ev.deltaY, mods: mods(ev) });
  },
  { passive: false },
);
screen.addEventListener("contextmenu", (ev) => ev.preventDefault());
// Keys typed on the focused canvas are the page's, not aegis's shortcuts.
for (const type of ["keydown", "keyup"]) {
  screen.addEventListener(type, (ev) => {
    if (!canAct() || ev.isComposing) return;
    ev.preventDefault();
    ev.stopPropagation();
    const printable = ev.key.length === 1 && !ev.ctrlKey && !ev.metaKey;
    const text = type === "keyup" ? "" : printable ? ev.key : ev.key === "Enter" ? "\r" : "";
    push({
      t: "key",
      type: type === "keyup" ? "keyUp" : text ? "keyDown" : "rawKeyDown",
      key: ev.key,
      code: ev.code,
      keyCode: ev.keyCode,
      text,
      mods: mods(ev),
    });
  });
}
screen.addEventListener("paste", (ev) => {
  if (!canAct()) return;
  ev.preventDefault();
  push({ t: "text", text: ev.clipboardData.getData("text") });
});

// -- the bar ------------------------------------------------------------------

const call = (op, extra = {}) => live && live.conn.call(op, { id: live.id, ...extra }, live.server).catch(refused);

// The page takes the size of the pane that takes it, so nothing is scaled down.
function fit() {
  const r = $("br-stage").getBoundingClientRect();
  if (r.width < 240 || r.height < 240) return;
  return call("browser.view", { width: Math.min(3840, Math.floor(r.width)), height: Math.min(2400, Math.floor(r.height)) });
}

$("br-url").addEventListener("keydown", (ev) => {
  if (ev.key === "Enter") {
    ev.preventDefault();
    call("browser.nav", { url: ev.target.value });
    screen.focus();
  } else if (ev.key === "Escape") {
    ev.target.value = live?.card.url || "";
    ev.target.blur();
  }
});
for (const b of document.querySelectorAll("#v-browser .br-nav")) b.addEventListener("click", () => call("browser.nav", { action: b.dataset.action }));
$("br-take").addEventListener("click", async () => {
  await call("browser.take");
  fit();
});
$("br-giveback").addEventListener("click", () => call("browser.handback"));
$("br-ask-back").addEventListener("click", () => call("browser.handback"));
$("br-reopen").addEventListener("click", () => call("browser.reopen"));
$("br-close").addEventListener("click", () => call("browser.shut"));
$("br-give").addEventListener("click", () => {
  const list = sessionsOf();
  const menu = $("br-give-list");
  menu.replaceChildren(
    ...(list.length
      ? list.map((s) => {
          const b = document.createElement("button");
          b.type = "button";
          b.textContent = s.handle;
          b.addEventListener("click", () => {
            menu.hidden = true;
            call("browser.give", { session: s.log_id });
          });
          return b;
        })
      : [Object.assign(document.createElement("span"), { textContent: "No session of yours is open." })]),
  );
  menu.hidden = !menu.hidden;
});

export { fit as fitBrowser };
