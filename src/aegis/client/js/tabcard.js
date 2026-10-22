// The tab card (#586). Hovering a session tab opens that session's Fleet card
// under it, with the tab menu's items as icon buttons, so the bar shows what a
// tab is and acts on it without a right-click. The pointer may cross from the
// tab onto the card. A key, a press elsewhere or a change of view closes it.
import { icon } from "./glyphs.js";

export const OPEN_MS = 300;
export const CLOSE_MS = 220;

// `meta(key)` is the session's meta, `card(m)` its Fleet card, `items(key)` the
// tab menu ({title, groups}) or null.
export function attachTabCard(list, { meta, card, items }) {
  const box = Object.assign(document.createElement("div"), { className: "tabcard" });
  box.hidden = true;
  document.body.append(box);
  let key = null;
  let tab = null;
  let openT = null;
  let closeT = null;

  const hide = () => {
    clearTimeout(openT);
    clearTimeout(closeT);
    box.hidden = true;
    key = tab = null;
  };
  const draw = () => {
    const m = key && meta(key);
    if (!m || !tab?.isConnected) return hide();
    const acts = Object.assign(document.createElement("div"), { className: "acts" });
    const menu = items(key);
    for (const group of menu?.groups || []) {
      for (const it of group) {
        const b = Object.assign(document.createElement("button"), { type: "button", title: it.label });
        b.dataset.id = it.id;
        b.setAttribute("aria-label", it.label);
        b.append(icon(it.icon));
        if (it.danger) b.classList.add("danger");
        if (it.disabled) {
          b.disabled = true;
          b.title = it.disabled;
        }
        b.addEventListener("click", (ev) => {
          ev.stopPropagation();
          hide();
          it.run();
        });
        acts.append(b);
      }
    }
    box.replaceChildren(card(m), acts);
    box.hidden = false;
    const r = tab.getBoundingClientRect();
    box.style.top = `${r.bottom + 6}px`;
    box.style.left = `${Math.max(8, Math.min(r.left, innerWidth - box.offsetWidth - 8))}px`;
  };
  const show = (t) => {
    clearTimeout(closeT);
    clearTimeout(openT);
    openT = setTimeout(() => {
      key = t.dataset.id;
      tab = t;
      draw();
    }, OPEN_MS);
  };
  const leave = () => {
    clearTimeout(openT);
    clearTimeout(closeT);
    closeT = setTimeout(() => {
      if (!box.matches(":hover") && !tab?.matches(":hover")) hide();
    }, CLOSE_MS);
  };

  list.addEventListener("pointerover", (ev) => {
    const t = ev.target.closest(".tab[data-id]");
    if (t && ev.pointerType === "mouse" && t.dataset.id !== key) show(t);
    else if (t) clearTimeout(closeT);
  });
  list.addEventListener("pointerleave", leave);
  list.addEventListener("focusin", (ev) => {
    const t = ev.target.closest(".tab[data-id]");
    if (t) show(t);
  });
  list.addEventListener("focusout", leave);
  box.addEventListener("pointerenter", () => clearTimeout(closeT));
  box.addEventListener("pointerleave", leave);
  // A press on the card opens its session, as on the Fleet; a button acts.
  addEventListener("keydown", (ev) => {
    if (!box.hidden && !["Shift", "Control", "Alt", "Meta"].includes(ev.key)) hide();
  }, true);
  addEventListener("pointerdown", (ev) => {
    if (!box.hidden && !box.contains(ev.target)) hide();
  }, true);
  addEventListener("hashchange", hide);
  // The tab redrawn under an open card, or its session gone: redraw or close.
  new MutationObserver(() => {
    if (box.hidden || !key) return;
    tab = list.querySelector(`.tab[data-id="${CSS.escape(key)}"]`);
    draw();
  }).observe(list, { childList: true, subtree: true });
  return { hide };
}
