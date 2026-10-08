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
  if (!open) return false;
  answer(false);
  return true;
}
