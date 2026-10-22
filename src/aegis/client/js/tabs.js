// The tab bar. The set of tabs is the server's open sessions, the same in
// every browser; their order is this browser's own, kept in localStorage as a
// list of log ids. Gone ids drop out, new sessions go at the end.

import { LABEL } from "./glyphs.js";
import { closeOnMiddle, markNode } from "./fleet.js";
import { shownOwner } from "./me.js";

export class TabOrder {
  constructor(key = "aegis.tabs") {
    this.key = key;
  }

  load() {
    try {
      const v = JSON.parse(localStorage.getItem(this.key) || "[]");
      return Array.isArray(v) ? v : [];
    } catch {
      return [];
    }
  }

  save(ids) {
    localStorage.setItem(this.key, JSON.stringify(ids));
  }

  arrange(openIds) {
    const open = new Set(openIds);
    const kept = this.load().filter((id) => open.has(id));
    const known = new Set(kept);
    const ids = [...kept, ...openIds.filter((id) => !known.has(id))];
    this.save(ids);
    return ids;
  }

  move(id, beforeId) {
    const ids = this.load().filter((x) => x !== id);
    const at = beforeId ? ids.indexOf(beforeId) : -1;
    ids.splice(at < 0 ? ids.length : at, 0, id);
    this.save(ids);
  }
}

export function renderTabs(list, metas, focusId, actions) {
  list.replaceChildren(...metas.map((m) => tab(m, focusId, actions)));
}

// One session's tab redrawn where it stands; the others keep their nodes.
export function patchTab(list, m, focusId, actions) {
  list.querySelector(`.tab[data-id="${CSS.escape(m.key)}"]`)?.replaceWith(tab(m, focusId, actions));
}

function tab(m, focusId, { onFocus, onMove, onClose }) {
  const t = document.createElement("div");
  t.className = `tab${m.key === focusId ? " on" : ""}${m.state === "asleep" ? " asleep" : ""}${m.off ? " off" : ""}`;
  t.classList.toggle("blink", !!m.blink);
  t.draggable = true;
  t.dataset.id = m.key;
  const said = `${m.handle}: ${m.title || "untitled"} (${LABEL[m.attention] || m.state})`;
  const mark = markNode(m);
  const term = m.kind === "term" || m.kind === "browser";
  if (term) {
    // A terminal is named by its name; its command is in the title.
    t.title = said;
    const name = Object.assign(document.createElement("span"), { className: "tname" });
    name.textContent = `${m.kind === "term" ? "▸_" : "◫"} ${m.name}`;
    t.append(mark, name);
  } else {
    // The whole handle, and the title under it cut to the handle's width (#586).
    t.setAttribute("aria-label", said);
    const lines = Object.assign(document.createElement("span"), { className: "l2" });
    lines.append(
      Object.assign(document.createElement("span"), { className: "hn", textContent: m.handle }),
      Object.assign(document.createElement("span"), { className: "tt", textContent: m.title || "" }),
    );
    t.append(mark, lines);
  }
  // Whose it is, when it is not this browser's (tokens.py).
  const owner = shownOwner(m);
  if (owner) t.append(Object.assign(document.createElement("span"), { className: "who", textContent: owner }));
  // A session on a linked server carries that server's name (links.py).
  if (m.server) t.append(Object.assign(document.createElement("span"), { className: "where", textContent: m.server }));
  t.addEventListener("click", () => onFocus(m.key));
  closeOnMiddle(t, () => onClose(m.key));
  t.addEventListener("dragstart", (ev) => {
    ev.dataTransfer.setData("text/aegis-tab", m.key);
    ev.dataTransfer.effectAllowed = "move";
    t.classList.add("dragging");
  });
  t.addEventListener("dragend", () => t.classList.remove("dragging"));
  t.addEventListener("dragover", (ev) => {
    if (ev.dataTransfer.types.includes("text/aegis-tab")) {
      ev.preventDefault();
      t.classList.add("drop");
    }
  });
  t.addEventListener("dragleave", () => t.classList.remove("drop"));
  t.addEventListener("drop", (ev) => {
    ev.preventDefault();
    t.classList.remove("drop");
    const id = ev.dataTransfer.getData("text/aegis-tab");
    if (id && id !== m.key) onMove(id, m.key);
  });
  return t;
}
