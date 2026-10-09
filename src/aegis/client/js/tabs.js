// The tab bar. The set of tabs is the server's open sessions, the same in
// every browser; their order is this browser's own, kept in localStorage as a
// list of log ids. Gone ids drop out, new sessions go at the end.

import { LABEL } from "./glyphs.js";
import { markNode } from "./fleet.js";

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

function tab(m, focusId, { onFocus, onMove }) {
  const t = document.createElement("div");
  t.className = `tab${m.key === focusId ? " on" : ""}${m.state === "stopped" ? " stopped" : ""}${m.off ? " off" : ""}`;
  t.classList.toggle("blink", !!m.blink);
  t.draggable = true;
  t.dataset.id = m.key;
  t.title = `${m.handle}: ${m.title || "untitled"} (${LABEL[m.attention] || m.state})`;
  const mark = markNode(m);
  const name = document.createElement("span");
  name.className = "tname";
  name.textContent = m.title || m.handle;
  const handle = document.createElement("span");
  handle.className = "srv";
  handle.textContent = m.title ? m.handle : "";
  t.append(mark, name, handle);
  // A session on a linked server carries that server's name (links.py).
  if (m.server) t.append(Object.assign(document.createElement("span"), { className: "where", textContent: m.server }));
  t.addEventListener("click", () => onFocus(m.key));
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
