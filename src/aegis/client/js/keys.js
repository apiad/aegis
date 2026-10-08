// The keyboard: one table of every key the client answers, and the one
// keydown listener that dispatches from it. The ? overlay is drawn from the
// same table, so it cannot list a key that does nothing.
//
// Chrome on Linux keeps Ctrl+T/W/N/Tab, Alt+1…9, Alt+←/→ and Alt+D/E/F for
// itself (chrome/browser/ui/accelerator_table.cc); the chords here are the Alt
// keys it leaves free. They match ev.code, because Alt can change ev.key.
// Alt+↑/↓, Alt+U and Alt+J are not in Chrome's Linux accelerator table either.
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
  { scope: "global", label: "Alt+/", desc: "Commands for this session", action: "commands", match: alt("Slash") },
  { scope: "global", label: "Alt+,", desc: "Focus the transcript, or the Fleet cards", action: "browse", match: alt("Comma") },
  {
    scope: "global",
    label: "Alt+[  Alt+]",
    desc: "Previous / next tab, Fleet first",
    action: "cycle",
    match: (ev) => alt("BracketLeft")(ev) || alt("BracketRight")(ev),
  },
  {
    scope: "global",
    label: "Alt+J",
    desc: "Next session that needs you, longest waiting first",
    action: "needs",
    match: alt("KeyJ"),
  },
  { scope: "global", label: "Alt+N", desc: "New session", action: "spawn", match: alt("KeyN") },
  {
    scope: "global",
    label: "Alt+0…9",
    desc: "Fleet, or the n-th tab (Chrome on Linux keeps Alt+1…9)",
    action: "tab",
    match: (ev) => ev.altKey && /^Digit[0-9]$/.test(ev.code),
  },
  { scope: "global", label: "Esc", desc: "Interrupt the agent; close a dialog or this list", action: "escape", match: key("Escape") },
  { scope: "browse", label: "0…9", desc: "Fleet, or the n-th tab", action: "tab", match: (ev) => bare(ev) && /^[0-9]$/.test(ev.key) },
  { scope: "browse", label: "n", desc: "New session", action: "spawn", match: key("n") },
  { scope: "browse", label: "?", desc: "This list", action: "help", match: key("?") },
  { scope: "session", label: "j  ↓", desc: "Next row", action: "next", match: key("j", "ArrowDown") },
  { scope: "session", label: "k  ↑", desc: "Previous row", action: "prev", match: key("k", "ArrowUp") },
  { scope: "session", label: "J  K", desc: "Next / previous message of yours", action: "turn", match: key("J", "K") },
  {
    scope: "session",
    label: "Alt+↑  Alt+↓",
    desc: "Previous / next agent message",
    action: "message",
    match: (ev) => alt("ArrowUp")(ev) || alt("ArrowDown")(ev),
  },
  { scope: "session", label: "Alt+U", desc: "First unread agent message", action: "firstUnread", match: alt("KeyU") },
  { scope: "session", label: "g  G", desc: "First row / last row, and follow the tail", action: "edge", match: key("g", "G") },
  {
    scope: "session",
    label: "Enter  Space",
    desc: "Open or close the row's details",
    action: "toggle",
    match: key("Enter", " "),
    native: true,
  },
  { scope: "session", label: "o", desc: "Press the row's first button", action: "press", match: key("o") },
  // Documents the browser's own Tab; it never matches.
  { scope: "session", label: "Tab", desc: "Walk the buttons from the selected row on", action: "none", match: () => false },
  { scope: "session", label: "i  /", desc: "Back to the message box", action: "composer", match: key("i", "/") },
  { scope: "fleet", label: "j  ↓", desc: "Next card, then the archive", action: "fleetNext", match: key("j", "ArrowDown") },
  { scope: "fleet", label: "k  ↑", desc: "Previous card", action: "fleetPrev", match: key("k", "ArrowUp") },
  {
    scope: "fleet",
    label: "Enter",
    desc: "Open the session; Read an archived one",
    action: "fleetOpen",
    match: key("Enter"),
    native: true,
  },
  { scope: "fleet", label: "/", desc: "Filter the archive", action: "filter", match: key("/") },
];

function typing(t) {
  return t instanceof HTMLElement && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName));
}

const HEADS = { global: "Anywhere", browse: "Outside a text field", session: "Transcript", fleet: "Fleet" };

export function renderKeys(box) {
  const panel = document.createElement("div");
  panel.className = "panel";
  for (const [scope, head] of Object.entries(HEADS)) {
    const t = document.createElement("table");
    const th = document.createElement("th");
    th.colSpan = 2;
    th.textContent = head;
    t.insertRow().append(th);
    for (const k of KEYS.filter((x) => x.scope === scope)) {
      const r = t.insertRow();
      r.className = "k";
      r.insertCell().textContent = k.label;
      r.insertCell().textContent = k.desc;
    }
    panel.append(t);
  }
  box.replaceChildren(panel);
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
