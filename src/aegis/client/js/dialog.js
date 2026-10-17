// aegis's own confirm: a question over the dimmed page and two buttons, drawn
// like the ? list. The browser's own confirm dialog looks foreign in the
// installed app and blocks the page (tests/test_client_rules.py bans it). Enter
// on the focused OK is the button's own; Esc reaches cancelAsk() through the
// escape action in keys.js; a tap outside cancels.

let open = null; // the pending answer's resolve

function box() {
  let d = document.getElementById("dialog");
  if (d) return d;
  d = document.createElement("div");
  d.className = "dialog";
  d.id = "dialog";
  d.hidden = true;
  d.innerHTML =
    '<div class="panel" role="dialog" aria-modal="true"><p class="q"></p>' +
    '<div class="acts"><button class="btn cancel"></button><button class="btn primary ok"></button></div></div>';
  d.addEventListener("click", (ev) => ev.target === d && answer(false));
  d.querySelector(".ok").addEventListener("click", () => answer(true));
  d.querySelector(".cancel").addEventListener("click", () => answer(false));
  document.getElementById("a2").append(d);
  return d;
}

function answer(yes) {
  const resolve = open;
  open = null;
  box().hidden = true;
  resolve?.(yes);
}

export function ask(question, { ok = "OK", cancel = "Cancel" } = {}) {
  if (open) answer(false);
  const d = box();
  d.querySelector(".q").textContent = question;
  d.querySelector(".ok").textContent = ok;
  d.querySelector(".cancel").textContent = cancel;
  d.hidden = false;
  d.querySelector(".ok").focus();
  return new Promise((resolve) => (open = resolve));
}

export function cancelAsk() {
  if (picked) {
    pick(null);
    return true;
  }
  if (!open) return false;
  answer(false);
  return true;
}

// The same panel with a body and any number of buttons: resolves the pressed
// button's value, or null on Esc, a tap outside or another dialog. The
// untested-harness question (app.js untestedAsk) is one.
let picked = null; // the pending choice's resolve

function chooser() {
  let d = document.getElementById("chooser");
  if (d) return d;
  d = document.createElement("div");
  d.className = "dialog";
  d.id = "chooser";
  d.hidden = true;
  d.innerHTML = '<div class="panel wide" role="dialog" aria-modal="true"><p class="q"></p><div class="b"></div><div class="acts"></div></div>';
  d.addEventListener("click", (ev) => ev.target === d && pick(null));
  document.getElementById("a2").append(d);
  return d;
}

function pick(value) {
  const resolve = picked;
  picked = null;
  chooser().hidden = true;
  resolve?.(value);
}

export function choose(title, body, buttons) {
  if (picked) pick(null);
  if (open) answer(false);
  const d = chooser();
  d.querySelector(".q").textContent = title;
  d.querySelector(".b").replaceChildren(body);
  const acts = d.querySelector(".acts");
  acts.replaceChildren(
    ...buttons.map((b) => {
      const el = document.createElement("button");
      el.className = b.primary ? "btn primary" : "btn";
      el.textContent = b.label;
      el.dataset.value = b.value;
      el.addEventListener("click", () => pick(b.value));
      return el;
    }),
  );
  d.hidden = false;
  (acts.querySelector(".primary") || acts.lastChild)?.focus();
  return new Promise((resolve) => (picked = resolve));
}
