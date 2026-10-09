// <pick-chip>: the client's select. A chip that opens a filterable list under
// it, in place of the browser's own select element, which draws its popup in
// the operating system's chrome outside the theme and answers a keystroke by
// jumping to the next option starting with that letter.
// tests/test_client_rules.py fails on a native select in the client.
//
//   <pick-chip id="sp-effort" name="effort" aria-label="Effort" free></pick-chip>
//   chip.options = [{ value, label, disabled }]; chip.value; chip.suffix
//
// Typing filters the options with the command menu's scorer; ArrowUp/Down,
// Enter and Esc are the command menu's. `free` (the model chip) lets a typed
// value through that no option names, as the first row of the list, and takes
// every keystroke as the value at once, as the text field it replaces did. A
// pick fires `input` and `change`, as a select does; keystrokes inside the
// chip stay inside it.

import { fuzzy } from "./commands.js";

export class PickChip extends HTMLElement {
  constructor() {
    super();
    this._options = [];
    this._value = "";
    this._suffix = "";
    this.rows = [];
    this.at = -1;
  }

  connectedCallback() {
    if (this.input) return;
    this.free = this.hasAttribute("free");
    this.input = document.createElement("input");
    this.input.setAttribute("role", "combobox");
    this.input.setAttribute("aria-autocomplete", "list");
    this.input.setAttribute("aria-expanded", "false");
    this.input.spellcheck = false;
    this.input.autocomplete = "off";
    const label = this.getAttribute("aria-label");
    if (label) this.input.setAttribute("aria-label", label);
    this.list = document.createElement("div");
    this.list.className = "menu";
    this.list.setAttribute("role", "listbox");
    this.list.hidden = true;
    this.append(this.input, this.list);
    this.input.addEventListener("focus", () => this.input.select());
    this.input.addEventListener("click", () => (this.isOpen ? null : this.open("")));
    this.input.addEventListener("input", (ev) => {
      ev.stopPropagation();
      if (!this.isOpen) this._before = this._value;
      if (this.free) this.set(this.input.value.trim());
      this.open(this.input.value);
    });
    this.input.addEventListener("keydown", (ev) => this.onKey(ev));
    this.input.addEventListener("blur", () => this.close());
    this.list.addEventListener("mousedown", (ev) => {
      const r = ev.target.closest("[role=option]");
      if (!r) return;
      ev.preventDefault(); // the input keeps focus
      this.at = [...this.list.children].indexOf(r);
      this.accept();
    });
    this.show();
  }

  get name() {
    return this.getAttribute("name") || "";
  }

  set name(v) {
    this.setAttribute("name", v);
  }

  get options() {
    return this._options;
  }

  set options(list) {
    this._options = list.map((o) => (typeof o === "string" ? { value: o, label: o } : o));
    if (this.isOpen) this.render(this.input.value);
    else this.show();
  }

  get value() {
    return this._value;
  }

  set value(v) {
    this._value = v ?? "";
    this.show();
  }

  // Text after the label, as `*` after the agent whose chips were changed.
  set suffix(s) {
    this._suffix = s;
    this.show();
  }

  get isOpen() {
    return !!this.list && !this.list.hidden;
  }

  label(v = this._value) {
    const o = this._options.find((x) => x.value === v);
    return o ? o.label : v;
  }

  // The chip at rest: the current value's label.
  show() {
    if (!this.input || this.isOpen) return;
    this.input.value = this.label() + this._suffix;
  }

  open(query) {
    if (!this.isOpen) this._before = this._value;
    this.list.hidden = false;
    this.input.setAttribute("aria-expanded", "true");
    this.render(query);
  }

  close() {
    if (!this.list) return;
    this.list.hidden = true;
    this.input.setAttribute("aria-expanded", "false");
    this.show();
  }

  render(query) {
    const q = query.trim();
    const hits = q
      ? this._options
          .map((o, i) => ({ o, i, m: fuzzy(q, o.label) }))
          .filter((x) => x.m)
          .sort((a, b) => b.m.score - a.m.score || a.i - b.i)
          .map((x) => ({ ...x.o, positions: x.m.positions }))
      : this._options.map((o) => ({ ...o, positions: [] }));
    const typed = this.free && q && !this._options.some((o) => o.value === q) ? [{ value: q, label: q, free: true, positions: [] }] : [];
    this.rows = [...typed, ...hits];
    this.at = hits.length ? typed.length + (q ? 0 : Math.max(0, hits.findIndex((o) => o.value === this._value))) : 0;
    this.list.replaceChildren(...this.rows.map((r, i) => this.rowNode(r, i === this.at)));
    this.list.children[this.at]?.scrollIntoView({ block: "nearest" });
  }

  rowNode(r, on) {
    const d = document.createElement("div");
    d.className = `opt${on ? " on" : ""}${r.free ? " free" : ""}`;
    d.setAttribute("role", "option");
    if (r.disabled) d.setAttribute("aria-disabled", "true");
    const hit = new Set(r.positions);
    [...r.label].forEach((ch, i) => {
      if (hit.has(i)) {
        const b = document.createElement("b");
        b.textContent = ch;
        d.append(b);
      } else d.append(ch);
    });
    return d;
  }

  move(d) {
    if (!this.rows.length) return;
    this.at = (this.at + d + this.rows.length) % this.rows.length;
    [...this.list.children].forEach((r, i) => r.classList.toggle("on", i === this.at));
    this.list.children[this.at]?.scrollIntoView({ block: "nearest" });
  }

  accept() {
    const r = this.rows[this.at];
    if (!r || r.disabled) return;
    this.commit(r.value);
  }

  // The value, told to whoever listens. The chip's text is left alone.
  set(v) {
    if (v === this._value) return;
    this._value = v;
    this.dispatchEvent(new Event("input", { bubbles: true }));
    this.dispatchEvent(new Event("change", { bubbles: true }));
  }

  commit(v) {
    this.list.hidden = true;
    this.input.setAttribute("aria-expanded", "false");
    this.set(v);
    this.show();
  }

  // Esc: what was there when the list opened.
  revert() {
    this.list.hidden = true;
    this.input.setAttribute("aria-expanded", "false");
    this.set(this._before ?? this._value);
    this.show();
  }

  onKey(ev) {
    if (ev.isComposing) return;
    if (ev.key === "ArrowDown" || ev.key === "ArrowUp") {
      ev.preventDefault();
      if (this.isOpen) this.move(ev.key === "ArrowDown" ? 1 : -1);
      else this.open("");
    } else if (ev.key === "Enter" && this.isOpen) {
      ev.preventDefault();
      this.accept();
    } else if (ev.key === "Escape" && this.isOpen) {
      ev.preventDefault();
      this.revert();
    } else if (ev.key === "Tab" && this.isOpen) {
      this.close();
    }
  }
}

customElements.define("pick-chip", PickChip);
