// The tab bar. The set of tabs is the server's open sessions, the same in
// every browser; their order is this browser's own, kept in localStorage as a
// list of log ids. Gone ids drop out, new sessions go at the end. A group (a
// session and those it started, groups.js) is drawn as one block headed by its
// oldest member, which can fold it to one tab (#586).

import { iconText, LABEL, PANE_ICON } from "./glyphs.js";
import { closeOnMiddle, markNode } from "./fleet.js";
import { blocksOf, hueOf } from "./groups.js";
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

  // `moving` (keys, in their order) to just before `beforeId`, or the end.
  move(moving, beforeId) {
    const ids = this.load().filter((x) => !moving.includes(x));
    const at = beforeId ? ids.indexOf(beforeId) : -1;
    ids.splice(at < 0 ? ids.length : at, 0, ...moving);
    this.save(ids);
  }
}

const URGENT = ["needs_you", "error", "review", "working", "waiting"];

// `actions.here` names this server; `actions.folds` is the set of folded group
// keys, `actions.onFold(key)` toggles one and `actions.saveFolds()` keeps the
// set. A folded group whose hidden member is the one in view unfolds.
export function renderTabs(list, metas, focusId, actions) {
  list.replaceChildren(
    ...blocksOf(metas, actions.here || "").map((b) => {
      const box = document.createElement("div");
      box.className = "tblock";
      box.dataset.group = b.key;
      // Every member's key, in tab order, folded ones too: what a drag moves.
      box.dataset.keys = JSON.stringify(metas.filter((m) => b.members.includes(m)).map((m) => m.key));
      if (b.members.length < 2) {
        box.append(tab(b.head, focusId, actions, "solo"));
        return box;
      }
      const hidden = b.members.slice(1);
      if (actions.folds?.has(b.key) && hidden.some((m) => m.key === focusId)) {
        actions.folds.delete(b.key);
        actions.saveFolds();
      }
      const folded = !!actions.folds?.has(b.key);
      box.classList.add("grp");
      box.style.setProperty("--g", `var(--group-${hueOf(b.key)})`);
      const mark = folded ? URGENT.find((a) => b.members.some((m) => m.mark === a)) : null;
      const head = tab(mark ? { ...b.head, mark } : b.head, focusId, actions, "head");
      const cnt = document.createElement("span");
      cnt.className = "cnt";
      cnt.textContent = folded ? `+${hidden.length}` : "−";
      cnt.title = folded ? "Unfold the group" : "Fold the group";
      cnt.classList.toggle("alert", folded && hidden.some((m) => m.mark === "needs_you"));
      cnt.addEventListener("click", (ev) => {
        ev.stopPropagation();
        actions.onFold(b.key);
      });
      head.append(cnt);
      box.append(head, ...(folded ? [] : hidden.map((m) => tab(m, focusId, actions, "member"))));
      return box;
    }),
  );
}

// One session's tab redrawn where it stands; the others keep their nodes. A
// tab inside a group is not patched (false): its head and count depend on its
// members, so the caller redraws the bar.
export function patchTab(list, m, focusId, actions) {
  const old = list.querySelector(`.tab[data-id="${CSS.escape(m.key)}"]`);
  if (!old) return true;
  if (old.closest(".tblock.grp")) return false;
  old.replaceWith(tab(m, focusId, actions, "solo"));
  return true;
}

// What is being dragged: one member inside its group, or a whole block.
let dragging = null;

// Where a drop on tab `t` at `x` puts what is dragged: the key it goes before
// (null: the end), or undefined when it may not go there.
function dropBefore(t, x) {
  const after = x > t.getBoundingClientRect().left + t.getBoundingClientRect().width / 2;
  const box = t.closest(".tblock");
  if (dragging.member) {
    if (box.dataset.group !== dragging.group || !t.classList.contains("member")) return undefined;
    if (dragging.keys.includes(t.dataset.id)) return undefined;
    if (!after) return t.dataset.id;
    const next = t.nextElementSibling?.dataset.id;
    return next ?? firstKey(box.nextElementSibling);
  }
  if (box.dataset.group === dragging.group) return undefined;
  return after ? firstKey(box.nextElementSibling) : firstKey(box);
}
const firstKey = (box) => (box ? JSON.parse(box.dataset.keys)[0] : null);

function tab(m, focusId, { onFocus, onMove, onClose }, role) {
  const t = document.createElement("div");
  t.className = `tab ${role}${m.key === focusId ? " on" : ""}${m.state === "asleep" ? " asleep" : ""}${m.off ? " off" : ""}`;
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
    name.replaceChildren(...iconText(PANE_ICON[m.kind === "term" ? "term" : "browser"], m.name));
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
    const box = t.closest(".tblock");
    const member = role === "member";
    dragging = { group: box.dataset.group, member, keys: member ? [m.key] : JSON.parse(box.dataset.keys) };
    (member ? t : box).classList.add("dragging");
  });
  t.addEventListener("dragend", () => {
    dragging = null;
    t.closest(".tblock")?.classList.remove("dragging");
    t.classList.remove("dragging");
  });
  t.addEventListener("dragover", (ev) => {
    if (!dragging || !ev.dataTransfer.types.includes("text/aegis-tab")) return;
    if (dropBefore(t, ev.clientX) === undefined) return t.classList.add("nodrop");
    ev.preventDefault();
    t.classList.add("drop");
  });
  t.addEventListener("dragleave", () => t.classList.remove("drop", "nodrop"));
  t.addEventListener("drop", (ev) => {
    ev.preventDefault();
    t.classList.remove("drop", "nodrop");
    const before = dragging && dropBefore(t, ev.clientX);
    if (before !== undefined && dragging) onMove(dragging.keys, before);
  });
  return t;
}
