// Copy buttons: on every message, every tool row and every code block.
//
// Each copies the raw text, never the rendered HTML: a message's Markdown as
// the entry holds it, a block's code, a tool's whole output. A tool entry holds
// only the tail of its output (describe.output_tail), so its button asks the
// server for all of it (transcript.output); a linked server too old to answer
// gets the tail. The renderers in entries.js draw the buttons; one listener on
// the list serves them all, so a row a patch replaces needs no wiring.
//
// The buttons are not Tab stops: Tab walks the rows' summaries and their own
// buttons, and one per message would put a stop before every row. The
// keyboard copies with `c` on the selected row (copyRow, keys.js).

import { icon } from "./glyphs.js";

// The kinds whose row is a message, copied as the Markdown it was written in.
const MESSAGES = new Set(["user", "prose", "inbox"]);
const SHOWN_MS = 1400;

// `what` names the text it copies: "message", "code" or "output".
export function copyButton(what) {
  const b = document.createElement("button");
  b.type = "button";
  b.className = "copy";
  b.tabIndex = -1;
  b.dataset.copy = what;
  b.title = `Copy the ${what}`;
  b.setAttribute("aria-label", b.title);
  b.append(icon("copy"), document.createElement("span"));
  return b;
}

// deps: entry(id) -> the entry; output(id) -> Promise of a tool's whole
// output; detail(id) -> Promise of the whole entry, for the tail.
export function installCopy(list, deps) {
  list.addEventListener("click", (ev) => {
    const b = ev.target.closest("button.copy");
    if (!b) return;
    ev.preventDefault(); // in a tool's summary line the click must not open the row
    run(b, b.dataset.copy === "code" ? codeOf(b) : rowText(b.closest(".row")?.dataset.id, deps));
  });
}

// The `c` key: the selected row's message or output, confirmed on its button.
export function copyRow(id, node, deps) {
  const b = node?.querySelector(":scope > .body > .copy, :scope .line > .copy");
  if (!b) return false;
  run(b, rowText(id, deps));
  return true;
}

function codeOf(b) {
  const pre = b.closest("pre");
  return pre.querySelector("code")?.textContent ?? "";
}

async function rowText(id, deps) {
  const e = deps.entry(id);
  if (!e) throw new Error("the row is gone");
  if (MESSAGES.has(e.kind)) return e.md || "";
  try {
    return await deps.output(id);
  } catch {
    const whole = await deps.detail(id);
    return whole?.detail?.tail || "";
  }
}

async function run(b, text) {
  clearTimeout(b._copyTimer);
  try {
    await navigator.clipboard.writeText(await text);
    show(b, "done", "copied", "Copied");
  } catch (err) {
    show(b, "err", "failed", `Copy failed: ${err.message || err}`);
  }
  b._copyTimer = setTimeout(() => show(b, null, "", `Copy the ${b.dataset.copy}`), SHOWN_MS);
}

function show(b, state, word, title) {
  if (state) b.dataset.state = state;
  else delete b.dataset.state;
  b.lastElementChild.textContent = word;
  b.title = title;
  b.setAttribute("aria-label", title);
}
