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
  const frag = range.cloneContents();
  for (const n of frag.querySelectorAll("[data-chrome]")) n.remove();
  return frag.textContent.trim();
}

// The range inside `row`'s body, or null when it reaches out of it. A
// triple-click on a row's last paragraph ends at offset 0 of what follows,
// with no text in between: that end is pulled back to the end of the body.
export function clampToRow(range, row) {
  const body = row?.querySelector(":scope > .body");
  if (!body || !body.contains(range.startContainer)) return null;
  if (body.contains(range.endContainer)) return range;
  const tail = document.createRange();
  tail.setStart(body, body.childNodes.length);
  tail.setEnd(range.endContainer, range.endOffset);
  if (tail.toString() !== "") return null;
  const r = range.cloneRange();
  r.setEnd(body, body.childNodes.length);
  return r;
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
