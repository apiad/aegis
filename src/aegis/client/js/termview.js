// A terminal's tab (spec section 06): xterm.js where a session has its
// transcript, a box under it that sends whole lines, a row of keys a phone
// keyboard lacks, and a panel with the process. One terminal is mounted at a
// time; switching tabs disposes it, and coming back replays the snapshot.

import { mountTerminal } from "./terminal.js";
import { iconText } from "./glyphs.js";
import { toast } from "./toast.js";

const $ = (id) => document.getElementById(id);
const KEYS = { esc: "\x1b", tab: "\t", "ctrl-c": "\x03" };
// A call the person made that the server refused, such as a paste past 1 MB
// ("busy") or Restart while the program still runs ("running").
const refused = (e) => toast(e.message, { kind: "error" });

let live = null; // {id, ctl, history, at, conn, server, card}

export async function openTerm(id, card, { conn, server = null }) {
  if (live?.id === id) return drawCard(card);
  closeTerm();
  const mine = { id, ctl: null, history: [], at: 0, conn, server, card };
  live = mine;
  drawCard(card);
  const ctl = await mountTerminal($("term-host"), {
    id,
    conn,
    server,
    readOnly: card.archived === true,
    onError: refused,
  });
  if (live !== mine) return ctl.dispose();
  mine.ctl = ctl;
}

export function closeTerm() {
  live?.ctl?.dispose();
  live = null;
  $("term-host").replaceChildren();
  $("term-keys").hidden = true;
}

export function drawCard(card) {
  if (!live || card.id !== live.id) return;
  live.card = card;
  document.querySelector("#v-term .term-name").replaceChildren(...iconText("terminal", card.name));
  const state = card.state === "exited" ? `exited (${card.code})` : card.state === "ended" ? `ended: ${card.reason}` : "running";
  document.querySelector("#v-term .term-state").textContent = state;
  const rows = [
    ["command", card.title],
    ["pid", card.pid ?? "-"],
    ["cwd", card.cwd],
    ["started", card.started_at ? new Date(card.started_at * 1000).toLocaleString() : "-"],
    ["restart", card.restart],
    ["owner", card.owner],
  ];
  const dl = document.querySelector("#v-term .term-proc");
  dl.replaceChildren(
    ...rows.flatMap(([k, v]) => {
      const dt = document.createElement("dt");
      const dd = document.createElement("dd");
      dt.textContent = k;
      dd.textContent = String(v ?? "-");
      return [dt, dd];
    }),
  );
  $("term-restart").hidden = card.archived === true || card.state === "running";
  $("term-close").hidden = card.archived === true;
}

// keys.js calls these from the term scope.
export function sendBox() {
  const box = $("term-input");
  if (!live?.ctl || !box.value) return;
  live.ctl.sendLine(box.value);
  live.history.push(box.value);
  live.at = live.history.length;
  box.value = "";
}

export function boxHistory(step) {
  const box = $("term-input");
  if (!live || (box.value && live.at === live.history.length)) return false;
  live.at = Math.max(0, Math.min(live.history.length, live.at + step));
  box.value = live.history[live.at] ?? "";
  return true;
}

export function leaveDirect() {
  live?.ctl?.leave();
}

function key(name) {
  const ctl = live?.ctl;
  if (!ctl) return;
  if (name === "done") return ctl.leave();
  if (name === "ctrl") return ctl.latchCtrl();
  if (name === "up" || name === "down") {
    const c = name === "up" ? "A" : "B";
    return ctl.sendKeys(ctl.appCursor ? `\x1bO${c}` : `\x1b[${c}`);
  }
  ctl.sendKeys(KEYS[name]);
}

// Buttons take pointerdown and prevent its default, so the phone keyboard stays open.
$("term-keys").addEventListener("pointerdown", (ev) => {
  const b = ev.target.closest("[data-key]");
  if (!b) return;
  ev.preventDefault();
  key(b.dataset.key);
});
document.addEventListener("termmode", (ev) => {
  document.querySelector("#v-term .term-mode").textContent = ev.detail.direct ? "direct" : "line mode";
  $("term-keys").hidden = !(ev.detail.direct && matchMedia("(pointer: coarse)").matches);
});
$("term-box").addEventListener("submit", (ev) => {
  ev.preventDefault();
  sendBox();
});
// The box's own keys, as the composer answers its own (app.js): keys.js acts
// only on its global chords inside a text field.
$("term-input").addEventListener("keydown", (ev) => {
  if (ev.isComposing) return;
  if (ev.key === "Enter" && !ev.shiftKey) {
    ev.preventDefault();
    sendBox();
  } else if ((ev.key === "ArrowUp" || ev.key === "ArrowDown") && !ev.shiftKey) {
    if (boxHistory(ev.key === "ArrowUp" ? -1 : 1)) ev.preventDefault();
  } else if (ev.key === "Escape") {
    ev.preventDefault();
    ev.target.blur();
  }
});
$("term-restart").addEventListener("click", () => {
  if (live) live.conn.call("term.restart", { id: live.id }, live.server).catch(refused);
});
$("term-close").addEventListener("click", () => {
  if (live) live.conn.call("term.close", { id: live.id }, live.server).catch(refused);
});