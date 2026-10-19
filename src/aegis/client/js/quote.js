// What a selection says, for the composer (#479). The text of a range is
// taken from a clone with the interface parts removed: a code block's
// language label, copy buttons and marks carry data-chrome, so their words
// never reach the agent. A quote prefixes every line with "> ", and nothing
// here adds a sentence in any language: the person writes the rest.
//
// insertIntoComposer is the one way text enters the composer from a menu: it
// fires the composer's input event, so the draft is saved, a parked session
// warms and an unknown command is marked, as for typed text.

// A range's clean text: the interface parts dropped, trimmed.
export function rangeText(range) {
  return clean(range).trim();
}

// A file's or a diff's lines are spans in a grid: their numbers go, and each
// line ends with a newline. A diff draws both its layouts; the unified one,
// with its + and − marks, is the one quoted.
function clean(range) {
  const frag = range.cloneContents();
  const at = range.commonAncestorContainer;
  const inside = (at.nodeType === 1 ? at : at.parentElement)?.closest(".lines, .diff");
  let root = frag;
  if (inside) {
    root = document.createElement("div"); // what the clone's selectors need above its spans
    root.className = inside.className;
    root.append(frag);
  }
  for (const n of root.querySelectorAll("[data-chrome]")) n.remove();
  for (const d of root.querySelectorAll(".diff")) if (d.querySelector(".unified")) d.querySelector(".split")?.remove();
  if (root.matches?.(".diff") && root.querySelector(".unified")) root.querySelector(".split")?.remove();
  for (const n of root.querySelectorAll(".lines > .n, .diff .n, .diff .hunk")) n.remove();
  for (const c of root.querySelectorAll(".lines > .c, .diff .c")) c.append("\n");
  return root.textContent;
}

// The range inside `row`'s body, or null when it reaches out of it. A
// triple-click on a row's last paragraph ends at offset 0 of the next row's
// body, past only that row's time and glyph: that end is pulled back to the
// end of the body.
export function clampToRow(range, row) {
  const body = row?.querySelector(":scope > .body");
  if (!body || !body.contains(range.startContainer)) return null;
  if (body.contains(range.endContainer)) return range;
  const tail = document.createRange();
  tail.setStart(body, body.childNodes.length);
  tail.setEnd(range.endContainer, range.endOffset);
  if (clean(tail) !== "") return null;
  const r = range.cloneRange();
  r.setEnd(body, body.childNodes.length);
  return r;
}

// A range inside `root` as text offsets from its start, and back: a row
// redrawn by a streaming delta keeps the same text, longer, so the offsets
// find the selection again in the new node.
export function offsetsIn(root, range) {
  const at = (node, offset) => {
    const r = document.createRange();
    r.setStart(root, 0);
    r.setEnd(node, offset);
    return r.toString().length;
  };
  return [at(range.startContainer, range.startOffset), at(range.endContainer, range.endOffset)];
}

export function rangeIn(root, [start, end]) {
  const r = document.createRange();
  const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  let seen = 0;
  let started = false;
  for (let n; (n = w.nextNode()); ) {
    const len = n.data.length;
    if (!started && start <= seen + len) {
      r.setStart(n, start - seen);
      started = true;
    }
    if (started && end <= seen + len) {
      r.setEnd(n, end - seen);
      return r;
    }
    seen += len;
  }
  return null;
}

export function quoted(text) {
  return text
    .split("\n")
    .map((l) => `> ${l}`)
    .join("\n");
}

// prefix + text, a blank line, then the draft that was there; the caret at
// the end. The prefix opens the text: send() reads /btw only at the start.
export function insertIntoComposer(text, { prefix = "" } = {}) {
  const input = document.getElementById("input");
  const draft = input.value;
  input.value = `${prefix}${text}\n\n${draft}`;
  input.dispatchEvent(new Event("input"));
  input.focus();
  const end = input.value.length;
  input.setSelectionRange(end, end);
}
