// The command palette: Ctrl+K (⌘K on a Mac) lists the actions in keys.js that
// apply to this view, filtered by title with the command menu's scorer, each
// with its keys. Enter runs the chosen one, Esc closes, ArrowUp/Down and Tab
// walk the list. It reads only the registry, so an action added there is here.
//
// Closing hands focus back to where it was before an action runs, so "Next
// row" or "Focus the message box" acts from where the person left off.

import { fuzzy } from "./commands.js";
import { ACTIONS, applies } from "./keys.js";

export class Palette {
  constructor(box, view) {
    Object.assign(this, { box, view });
    this.items = [];
    this.at = 0;
    this.back = null;
    const panel = document.createElement("div");
    panel.className = "panel";
    panel.setAttribute("role", "dialog");
    panel.setAttribute("aria-label", "Commands");
    this.q = document.createElement("input");
    this.q.id = "palette-q";
    this.q.spellcheck = false;
    this.q.autocomplete = "off";
    this.q.placeholder = "Search the commands";
    this.q.setAttribute("role", "combobox");
    this.q.setAttribute("aria-controls", "palette-rows");
    this.q.setAttribute("aria-expanded", "true");
    this.rows = document.createElement("div");
    this.rows.id = "palette-rows";
    this.rows.className = "rows";
    this.rows.setAttribute("role", "listbox");
    panel.append(this.q, this.rows);
    box.replaceChildren(panel);
    this.q.addEventListener("input", () => this.refresh());
    this.q.addEventListener("keydown", (ev) => this.onKey(ev));
    this.rows.addEventListener("mousedown", (ev) => {
      const r = ev.target.closest("[role=option]");
      if (!r) return;
      ev.preventDefault(); // the field keeps focus
      this.at = [...this.rows.children].indexOf(r);
      this.accept();
    });
    box.addEventListener("mousedown", (ev) => ev.target === box && this.close());
  }

  get isOpen() {
    return !this.box.hidden;
  }

  toggle() {
    if (this.isOpen) this.close();
    else this.open();
  }

  open() {
    this.back = document.activeElement;
    this.q.value = "";
    this.box.hidden = false;
    this.refresh();
    this.q.focus();
  }

  close() {
    if (!this.isOpen) return;
    this.box.hidden = true;
    if (this.back?.isConnected) this.back.focus({ preventScroll: true });
    this.back = null;
  }

  refresh() {
    const v = this.view();
    const q = this.q.value.trim();
    const all = ACTIONS.filter((a) => applies(a, v));
    this.items = q
      ? all
          .map((a, i) => ({ a, i, m: fuzzy(q, a.title) }))
          .filter((x) => x.m)
          .sort((x, y) => y.m.score - x.m.score || x.i - y.i)
          .map((x) => ({ a: x.a, positions: x.m.positions }))
      : all.map((a) => ({ a, positions: [] }));
    this.at = 0;
    if (!this.items.length) {
      const none = document.createElement("div");
      none.className = "empty";
      none.textContent = "No command matches";
      this.rows.replaceChildren(none);
      return;
    }
    this.rows.replaceChildren(...this.items.map((it, i) => this.row(it, i)));
    this.mark();
  }

  row({ a, positions }, i) {
    const r = document.createElement("div");
    r.className = "cmd-row";
    r.id = `palette-${i}`;
    r.dataset.action = a.id;
    r.setAttribute("role", "option");
    const nm = document.createElement("span");
    nm.className = "nm";
    const hit = new Set(positions);
    [...a.title].forEach((ch, j) => {
      if (hit.has(j)) {
        const b = document.createElement("b");
        b.textContent = ch;
        nm.append(b);
      } else nm.append(ch);
    });
    const keys = document.createElement("span");
    keys.className = "keys";
    for (const k of a.keys) {
      const kb = document.createElement("kbd");
      kb.textContent = k.label;
      keys.append(kb);
    }
    r.append(nm, keys);
    return r;
  }

  mark() {
    [...this.rows.children].forEach((r, i) => {
      r.classList.toggle("on", i === this.at);
      r.setAttribute("aria-selected", String(i === this.at));
    });
    this.q.setAttribute("aria-activedescendant", `palette-${this.at}`);
    this.rows.children[this.at]?.scrollIntoView({ block: "nearest" });
  }

  move(d) {
    if (!this.items.length) return;
    this.at = (this.at + d + this.items.length) % this.items.length;
    this.mark();
  }

  accept() {
    const it = this.items[this.at];
    if (!it) return;
    this.close();
    it.a.run();
  }

  // Every key it answers is prevented, so the listener in keys.js, which
  // skips a prevented event, never also runs Esc or Enter.
  onKey(ev) {
    if (ev.isComposing) return;
    const d = { ArrowDown: 1, ArrowUp: -1, Tab: ev.shiftKey ? -1 : 1 }[ev.key];
    if (d) this.move(d);
    else if (ev.key === "Enter") this.accept();
    else if (ev.key === "Escape") this.close();
    else return;
    ev.preventDefault();
  }
}
