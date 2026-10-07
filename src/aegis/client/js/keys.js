// The keyboard: one table of every key the client answers, and the one
// keydown listener that dispatches from it. The ? overlay is drawn from the
// same table, so it cannot list a key that does nothing.
//
// Chrome on Linux keeps Ctrl+T/W/N/Tab, Alt+1…9, Alt+←/→ and Alt+D/E/F for
// itself (chrome/browser/ui/accelerator_table.cc); the chords here are the Alt
// keys it leaves free. They match ev.code, because Alt can change ev.key.
// Plain keys act only outside text fields, as in Gmail, and the view decides
// what they do, so there is no mode to keep in your head.

const bare = (ev) => !ev.altKey && !ev.ctrlKey && !ev.metaKey;
const alt = (code) => (ev) => ev.altKey && !ev.ctrlKey && !ev.metaKey && !ev.shiftKey && ev.code === code;
const key =
  (...keys) =>
  (ev) =>
    bare(ev) && keys.includes(ev.key);

// scope: "global" acts anywhere, even while typing; "browse" outside text
// fields in any view; "session" (also the read view) and "fleet" in theirs.
export const KEYS = [
  { scope: "global", label: "Alt+.", desc: "Focus the message box", action: "composer", match: alt("Period") },
  { scope: "global", label: "Alt+,", desc: "Focus the transcript, or the Fleet cards", action: "browse", match: alt("Comma") },
  {
    scope: "global",
    label: "Alt+[  Alt+]",
    desc: "Previous / next tab, Fleet first",
    action: "cycle",
    match: (ev) => alt("BracketLeft")(ev) || alt("BracketRight")(ev),
  },
  { scope: "global", label: "Alt+N", desc: "New session", action: "spawn", match: alt("KeyN") },
  {
    scope: "global",
    label: "Alt+0…9",
    desc: "Fleet, or the n-th tab (Chrome on Linux keeps Alt+1…9)",
    action: "tab",
    match: (ev) => ev.altKey && /^Digit[0-9]$/.test(ev.code),
  },
  { scope: "global", label: "Esc", desc: "Interrupt the agent; close this list", action: "escape", match: key("Escape") },
  { scope: "browse", label: "0…9", desc: "Fleet, or the n-th tab", action: "tab", match: (ev) => bare(ev) && /^[0-9]$/.test(ev.key) },
  { scope: "browse", label: "n", desc: "New session", action: "spawn", match: key("n") },
  { scope: "session", label: "i  /", desc: "Back to the message box", action: "composer", match: key("i", "/") },
];

function typing(t) {
  return t instanceof HTMLElement && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName));
}

const PRESSABLE = new Set(["A", "BUTTON", "SUMMARY"]);

export function installKeys(actions, view) {
  document.addEventListener("keydown", (ev) => {
    if (ev.defaultPrevented || ev.isComposing) return;
    const v = view();
    const scopes = typing(ev.target) ? ["global"] : ["global", "browse", v === "read" ? "session" : v];
    const b = KEYS.find((k) => scopes.includes(k.scope) && k.match(ev));
    if (!b) return;
    // Enter and Space on a focused link, button or summary are the element's own.
    if (b.native && PRESSABLE.has(ev.target.tagName)) return;
    ev.preventDefault();
    actions[b.action](ev);
  });
}
