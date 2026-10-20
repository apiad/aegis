// Copy buttons: on the person's messages, every tool row, every code block and
// every table (#247, #323). An agent's reply has no button of its own: its code
// blocks and tables carry theirs, and `c` copies the whole reply.
//
// A message copies its Markdown as the entry holds it, a block its code, a tool
// its whole output, all as plain text. A table copies two things at once: its
// Markdown lines, for a chat or an editor, and its cells as an HTML table, for a
// spreadsheet or a document. A tool entry holds only the tail of its output
// (describe.output_tail), so its button asks the server for all of it
// (transcript.output); a linked server too old to answer gets the tail. The
// renderers in entries.js draw the buttons; one listener on the list serves them
// all, so a row a patch replaces needs no wiring, and one on the viewer serves the
// buttons in the Markdown it draws.
//
// The buttons are not Tab stops: Tab walks the rows' summaries and their own
// buttons, and one per message would put a stop before every row. The
// keyboard copies with `c` on the selected row (copyRow, keys.js).

import { icon } from "./glyphs.js";

// The kinds whose row is a message, copied as the Markdown it was written in.
const MESSAGES = new Set(["user", "prose", "inbox"]);
const SHOWN_MS = 1400;

// `what` names the text it copies: "message", "code", "table" or "output".
export function copyButton(what) {
  const b = document.createElement("button");
  b.type = "button";
  b.className = "copy";
  b.tabIndex = -1;
  b.dataset.copy = what;
  b.dataset.chrome = ""; // not part of the text a selection quotes (quote.js)
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
    const what = b.dataset.copy;
    run(b, what === "code" ? codeOf(b) : what === "table" ? tableOf(b) : rowText(b.closest(".row")?.dataset.id, deps));
  });
}

// The `c` key: the selected row's message or output. A reply has no button
// to confirm on, so its glyph shows the confirmation.
export function copyRow(id, node, deps) {
  const b = node?.querySelector(":scope > .body > .copy, :scope .line > .copy");
  if (b) {
    run(b, rowText(id, deps));
    return true;
  }
  const g = node?.querySelector(":scope > .g");
  if (!g || !MESSAGES.has(deps.entry(id)?.kind)) return false;
  clearTimeout(g._copyTimer);
  put(rowText(id, deps)).then(
    () => (g.dataset.copied = "done"),
    () => (g.dataset.copied = "err"),
  );
  g._copyTimer = setTimeout(() => delete g.dataset.copied, SHOWN_MS);
  return true;
}

function codeOf(b) {
  const code = b.closest(".fence")?.querySelector("pre > code") ?? b.closest("pre")?.querySelector("code");
  return code?.textContent ?? "";
}

// A table as the agent wrote it, for a chat or an editor, and as cells, for a
// spreadsheet or a document.
function tableOf(b) {
  const blk = b.closest(".tblk");
  const [a, z] = blk.dataset.lines.split("-").map(Number);
  const src = b.closest(".md")?._src ?? "";
  const t = blk.querySelector("table").cloneNode(true);
  for (const td of t.querySelectorAll("[data-label]")) td.removeAttribute("data-label");
  return { text: src.split("\n").slice(a, z).join("\n").replace(/\n+$/, ""), html: t.outerHTML };
}

// A row's message, or a tool's whole output; the row menu's Reply quotes it too.
export async function rowText(id, deps) {
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

// Text, or {text, html} written as one item with both types; a browser
// without ClipboardItem gets the text.
async function put(v) {
  v = await v;
  if (typeof v === "string") return navigator.clipboard.writeText(v);
  if (window.ClipboardItem && navigator.clipboard.write) {
    return navigator.clipboard.write([
      new ClipboardItem({
        "text/plain": new Blob([v.text], { type: "text/plain" }),
        "text/html": new Blob([v.html], { type: "text/html" }),
      }),
    ]);
  }
  return navigator.clipboard.writeText(v.text);
}

async function run(b, value) {
  clearTimeout(b._copyTimer);
  try {
    await put(value);
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
