// A browser (#564): the page the server's Chromium draws, streamed as JPEG
// frames into a canvas, with a bar over it. The person's pointer, wheel and
// keys go back as CDP input events while they hold the page; while the agent
// holds it the bar shows the agent's last action and the canvas only watches.
// A view builds its own DOM, so one browser can show in its tab and in a
// pane of a session's canvas (canvas.js) at once, each view subscribed.

import { toast } from "./toast.js";
import { icon, iconText } from "./glyphs.js";

const refused = (e) => toast(e.message, { kind: "error" });
// CDP's modifier bits.
const mods = (ev) => (ev.altKey ? 1 : 0) | (ev.ctrlKey ? 2 : 0) | (ev.metaKey ? 4 : 0) | (ev.shiftKey ? 8 : 0);
const BUTTON = ["left", "middle", "right"];
const NAV_ICON = { back: "left", forward: "right", reload: "again" };
const views = new Set(); // every mounted view, for card updates

// The card of a browser by id, and the person's sessions to give one to; app.js sets both.
let cardOf = () => null;
let sessionsOf = () => [];
export function browserSources({ card, sessions }) {
  cardOf = card;
  sessionsOf = sessions;
}
export const browserCard = (id) => cardOf(id);

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
}
function button(cls, text, label) {
  const b = el("button", cls, text);
  b.type = "button";
  if (label) b.title = b.ariaLabel = label;
  return b;
}

// Mount a view of browser ``id`` in ``host``; returns {draw(card), dispose()}.
export function mountBrowser(host, id, card, { conn, server = null }) {
  const v = { id, conn, server, card, queue: [], sending: false, decoding: false, pending: null, moveFrame: 0, lastMove: null };
  const bar = el("header", "br-bar");
  const name = el("span", "br-name");
  const nav = ["back", "forward", "reload"].map((a) => {
    const b = button("br-nav", "", a[0].toUpperCase() + a.slice(1));
    b.append(icon(NAV_ICON[a]));
    b.dataset.action = a;
    b.addEventListener("click", () => call(v, "browser.nav", { action: a }));
    return b;
  });
  const url = el("input", "br-url");
  Object.assign(url, { type: "text", spellcheck: false, autocomplete: "off", ariaLabel: "Address" });
  url.setAttribute("autocapitalize", "off");
  const holder = el("span", "br-holder");
  const take = button("btn br-take", "Take over");
  const back = button("btn acc br-giveback", "Give back");
  const giveWrap = el("span", "br-give-wrap");
  const give = button("btn br-give", "Give to…");
  const giveList = el("span", "br-give-list");
  giveList.hidden = true;
  giveWrap.append(give, giveList);
  const reopen = button("btn br-reopen", "Reopen");
  const close = button("btn br-close", "Close");
  bar.append(name, ...nav, url, holder, take, back, giveWrap, reopen, close);
  const ask = el("div", "br-ask");
  const askText = el("span", "br-ask-text");
  const askBack = button("btn acc br-ask-back", "Give back");
  ask.append(askText, askBack);
  const act = el("div", "br-act");
  const stage = el("div", "br-stage");
  const screen = el("canvas", "br-screen");
  screen.tabIndex = 0;
  screen.ariaLabel = "The page";
  const wait = el("div", "br-wait", "waiting for the first frame…");
  stage.append(screen, wait);
  const root = el("div", "br-view");
  root.append(bar, ask, act, stage);
  host.replaceChildren(root);
  Object.assign(v, { name, nav, url, holder, take, back, give, giveList, reopen, close, ask, askText, act, stage, screen, wait });

  url.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter") {
      ev.preventDefault();
      call(v, "browser.nav", { url: url.value });
      screen.focus();
    } else if (ev.key === "Escape") {
      url.value = v.card.url || "";
      url.blur();
    }
  });
  take.addEventListener("click", async () => {
    await call(v, "browser.take");
    fit(v);
  });
  back.addEventListener("click", () => call(v, "browser.handback"));
  askBack.addEventListener("click", () => call(v, "browser.handback"));
  reopen.addEventListener("click", () => call(v, "browser.reopen"));
  close.addEventListener("click", () => call(v, "browser.shut"));
  give.addEventListener("click", () => {
    const list = sessionsOf();
    giveList.replaceChildren(
      ...(list.length
        ? list.map((s) => {
            const b = button("", s.handle);
            b.addEventListener("click", () => {
              giveList.hidden = true;
              call(v, "browser.give", { session: s.log_id });
            });
            return b;
          })
        : [el("span", "", "No session of yours is open.")]),
    );
    giveList.hidden = !giveList.hidden;
  });
  listen(v);

  draw(v, card);
  wait.hidden = card.state !== "running";
  if (card.state === "running" && card.holder === "person") requestAnimationFrame(() => fit(v));
  v.unsub = conn.subscribe(
    `browser:${id}`,
    (data) => paint(v, data),
    (ops) => ops.forEach((f) => paint(v, f)),
    () => {},
    () => null,
    server,
  );
  views.add(v);
  return {
    draw: (c) => draw(v, c),
    dispose() {
      views.delete(v);
      v.unsub?.();
      v.gone = true;
    },
  };
}

// A card changed: every view of that browser redraws.
export function drawBrowser(card) {
  for (const v of views) if (v.id === card.id) draw(v, card);
}

// Only the newest frame is decoded: a frame that arrives while one decodes
// replaces the one waiting, so a slow device skips frames instead of lagging.
function paint(v, f) {
  if (v.gone || !f?.frame) return;
  if (v.decoding) {
    v.pending = f;
    return;
  }
  v.decoding = true;
  const img = new Image();
  img.onload = img.onerror = () => {
    if (!v.gone && img.naturalWidth) {
      const c = v.screen;
      if (c.width !== img.naturalWidth || c.height !== img.naturalHeight) {
        c.width = img.naturalWidth;
        c.height = img.naturalHeight;
      }
      c.getContext("2d").drawImage(img, 0, 0);
      // The page size this frame shows, in CSS pixels: a click maps to it.
      v.shown = { width: f.w || img.naturalWidth, height: f.h || img.naturalHeight };
      c.style.aspectRatio = `${v.shown.width} / ${v.shown.height}`;
      v.wait.hidden = true;
    }
    v.decoding = false;
    const next = v.pending;
    v.pending = null;
    if (next) paint(v, next);
  };
  img.src = `data:image/jpeg;base64,${f.frame}`;
}

function draw(v, card) {
  if (!card) return;
  // Control just reached the person: the view they can see takes the page's
  // size, as Take over does (fit skips a hidden or tiny view).
  const handed = v.card?.holder === "agent" && card.holder === "person" && card.state === "running";
  v.card = card;
  if (handed) requestAnimationFrame(() => fit(v));
  const running = card.state === "running";
  const person = card.holder === "person";
  v.name.replaceChildren(...iconText("globe", card.name));
  if (document.activeElement !== v.url) v.url.value = card.url || "";
  v.url.readOnly = !(running && person);
  v.holder.textContent = !running ? card.state : person ? "you" : "agent";
  v.holder.className = `br-holder ${running ? card.holder : "ended"}`;
  v.take.hidden = !running || person;
  v.back.hidden = !running || !person || !card.session;
  v.give.hidden = !running || !person || Boolean(card.session);
  v.reopen.hidden = running;
  for (const b of v.nav) b.disabled = !(running && person);
  v.ask.hidden = !(person && card.ask);
  v.askText.textContent = card.ask ? `The agent asks: ${card.ask}` : "";
  v.act.textContent = !running ? `stopped: ${card.reason || ""}` : !person && card.last_action ? `agent: ${card.last_action}` : "";
  v.screen.classList.toggle("locked", !(running && person));
  if (!v.shown) v.screen.style.aspectRatio = `${card.width} / ${card.height}`;
  if (!running) v.wait.hidden = true;
}

// -- input ------------------------------------------------------------------

const canAct = (v) => !v.gone && v.card.state === "running" && v.card.holder === "person";

function push(v, ev) {
  if (!canAct(v)) return;
  v.queue.push(ev);
  if (!v.sending) flush(v);
}

// One call at a time, in order, each carrying what queued meanwhile.
async function flush(v) {
  v.sending = true;
  while (v.queue.length && !v.gone) {
    const events = v.queue.splice(0, 200);
    try {
      await v.conn.call("browser.input", { id: v.id, events }, v.server);
    } catch (e) {
      v.queue.length = 0;
      refused(e);
    }
  }
  v.sending = false;
}

// A point on the canvas in the page's CSS pixels, as the drawn frame shows
// the page: a resize's new card can arrive after its first frames.
function at(v, ev) {
  const r = v.screen.getBoundingClientRect();
  const { width, height } = v.shown || v.card;
  return { x: Math.round(((ev.clientX - r.left) * width) / r.width), y: Math.round(((ev.clientY - r.top) * height) / r.height) };
}

function listen(v) {
  const s = v.screen;
  const mouse = (type, ev) => ({ t: "mouse", type, ...at(v, ev), button: BUTTON[ev.button] || "left", buttons: ev.buttons, count: ev.detail || 1, mods: mods(ev) });
  s.addEventListener("pointerdown", (ev) => {
    if (!canAct(v)) return;
    ev.preventDefault();
    s.focus();
    s.setPointerCapture(ev.pointerId);
    push(v, mouse("mousePressed", ev));
  });
  s.addEventListener("pointerup", (ev) => canAct(v) && push(v, mouse("mouseReleased", ev)));
  // Moves go out at most once a frame: hover menus need them, a stream of them does not.
  s.addEventListener("pointermove", (ev) => {
    if (!canAct(v)) return;
    v.lastMove = { t: "mouse", type: "mouseMoved", ...at(v, ev), button: ev.buttons ? "left" : "none", buttons: ev.buttons, mods: mods(ev) };
    if (v.moveFrame) return;
    v.moveFrame = requestAnimationFrame(() => {
      v.moveFrame = 0;
      if (v.lastMove) push(v, v.lastMove);
      v.lastMove = null;
    });
  });
  s.addEventListener(
    "wheel",
    (ev) => {
      if (!canAct(v)) return;
      ev.preventDefault();
      push(v, { t: "wheel", ...at(v, ev), dx: ev.deltaX, dy: ev.deltaY, mods: mods(ev) });
    },
    { passive: false },
  );
  s.addEventListener("contextmenu", (ev) => ev.preventDefault());
  // Keys typed on the focused canvas are the page's, not aegis's shortcuts.
  for (const type of ["keydown", "keyup"]) {
    s.addEventListener(type, (ev) => {
      if (!canAct(v) || ev.isComposing) return;
      ev.preventDefault();
      ev.stopPropagation();
      const printable = ev.key.length === 1 && !ev.ctrlKey && !ev.metaKey;
      const text = type === "keyup" ? "" : printable ? ev.key : ev.key === "Enter" ? "\r" : "";
      push(v, { t: "key", type: type === "keyup" ? "keyUp" : text ? "keyDown" : "rawKeyDown", key: ev.key, code: ev.code, keyCode: ev.keyCode, text, mods: mods(ev) });
    });
  }
  s.addEventListener("paste", (ev) => {
    if (!canAct(v)) return;
    ev.preventDefault();
    push(v, { t: "text", text: ev.clipboardData.getData("text") });
  });
}

const call = (v, op, extra = {}) => v.conn.call(op, { id: v.id, ...extra }, v.server).catch(refused);

// The page takes the size of the view that takes it, so nothing is scaled down.
function fit(v) {
  const r = v.stage.getBoundingClientRect();
  if (r.width < 240 || r.height < 240) return;
  return call(v, "browser.view", { width: Math.min(3840, Math.floor(r.width)), height: Math.min(2400, Math.floor(r.height)) });
}

// -- the browser's own tab (app.js) ----------------------------------------

let tab = null; // {id, view}

export function openBrowser(id, card, { conn, server = null }) {
  if (tab?.id === id) return tab.view.draw(card);
  closeBrowser();
  tab = { id, view: mountBrowser(document.getElementById("br-tab"), id, card, { conn, server }) };
}

export function closeBrowser() {
  tab?.view.dispose();
  tab = null;
  document.getElementById("br-tab").replaceChildren();
}
