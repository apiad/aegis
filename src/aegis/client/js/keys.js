// The keyboard: one registry of every action the client offers by key, and the
// one keydown listener that dispatches from it. The ? overlay and the command
// palette (palette.js) are drawn from the same registry, so neither can list an
// action that does nothing, and a new action shows in both without a second edit.
//
// Chrome on Linux keeps Ctrl+T/W/N/Tab, Alt+1…9, Alt+←/→ and Alt+D/E/F for
// itself (chrome/browser/ui/accelerator_table.cc); the chords here are the Alt
// keys it leaves free. They match ev.code, because Alt can change ev.key.
// Alt+↑/↓, Alt+U and Alt+J are not in Chrome's Linux accelerator table either.
// Plain keys act only outside text fields, as in Gmail, and the view decides
// what they do, so there is no mode to keep in your head.
//
// The palette is Ctrl+K, and ⌘K on a Mac. Chrome binds Ctrl+K to "search from
// the address bar" (IDC_FOCUS_SEARCH), which is not in its reserved list
// (BrowserCommandController::IsReservedCommandOrKey), so the page's
// preventDefault wins, as it does on GitHub and Slack. On a Mac Ctrl+K deletes
// to the end of the line in a text field, so there the chord is ⌘K alone.

const MAC = /Mac|iPhone|iPad/.test(navigator.platform);
const bare = (ev) => !ev.altKey && !ev.ctrlKey && !ev.metaKey;
const alt = (code) => (ev) => ev.altKey && !ev.ctrlKey && !ev.metaKey && !ev.shiftKey && ev.code === code;
const mod = (k) => (ev) =>
  (MAC ? ev.metaKey && !ev.ctrlKey : ev.ctrlKey && !ev.metaKey) && !ev.altKey && !ev.shiftKey && ev.key.toLowerCase() === k;
const key =
  (...keys) =>
  (ev) =>
    bare(ev) && keys.includes(ev.key);
const on = (scope, label, match, extra) => ({ scope, label, match, ...extra });

// An action: its id (the name app.js runs it by), its title, the keys that run
// it, and `when`, the views where the palette offers it. Without `when` that is
// wherever its keys act. `palette: false` keeps out of the palette what needs
// the key itself (which digit) or is only documented here.
//
// A key's scope: "global" acts anywhere, even while typing; "browse" outside
// text fields in any view; "session" (also the read view) and "fleet" in theirs.
export const ACTIONS = [
  { id: "palette", title: "Search the commands", palette: false, keys: [on("global", MAC ? "⌘K" : "Ctrl+K", mod("k"))] },
  {
    id: "composer",
    title: "Focus the message box",
    when: ["session", "spawn"],
    keys: [on("global", "Alt+.", alt("Period")), on("session", "i  /", key("i", "/"))],
  },
  { id: "commands", title: "Commands for this session", when: ["session"], keys: [on("global", "Alt+/", alt("Slash"))] },
  {
    id: "browse",
    title: "Focus the transcript, or the Fleet cards",
    when: ["session", "read", "fleet"],
    keys: [on("global", "Alt+,", alt("Comma"))],
  },
  // The Fleet is the first tab.
  { id: "tabPrev", title: "Previous tab", keys: [on("global", "Alt+[", alt("BracketLeft"))] },
  { id: "tabNext", title: "Next tab", keys: [on("global", "Alt+]", alt("BracketRight"))] },
  { id: "needs", title: "Next session that needs you, longest waiting first", keys: [on("global", "Alt+J", alt("KeyJ"))] },
  { id: "spawn", title: "New session", keys: [on("global", "Alt+N", alt("KeyN")), on("browse", "n", key("n"))] },
  { id: "settings", title: "Settings: .aegis.yaml", keys: [on("global", "Alt+S", alt("KeyS"))] },
  {
    id: "dictate",
    title: "Dictate into the message box; again to stop",
    when: ["session", "spawn"],
    keys: [on("global", "Alt+M", alt("KeyM"))],
  },
  { id: "side", title: "Show or hide the session panel", keys: [on("global", "Alt+B", alt("KeyB"))] },
  {
    id: "foldLevel",
    title: "Fold: everything shown, then tool calls and thinking, then all but the messages",
    when: ["session", "read"],
    keys: [on("global", "Alt+Z", alt("KeyZ")), on("session", "z", key("z"))],
  },
  {
    id: "tab",
    title: "Fleet, or the n-th tab (Chrome on Linux keeps Alt+1…9)",
    palette: false,
    keys: [
      on("global", "Alt+0…9", (ev) => ev.altKey && /^Digit[0-9]$/.test(ev.code)),
      on("browse", "0…9", (ev) => bare(ev) && /^[0-9]$/.test(ev.key)),
    ],
  },
  { id: "escape", title: "Interrupt the agent; close a dialog or a list", keys: [on("global", "Esc", key("Escape"))] },
  { id: "help", title: "Every key, in one list", keys: [on("browse", "?", key("?"))] },
  { id: "next", title: "Next row", keys: [on("session", "j  ↓", key("j", "ArrowDown"))] },
  { id: "prev", title: "Previous row", keys: [on("session", "k  ↑", key("k", "ArrowUp"))] },
  { id: "turnNext", title: "Next message of yours", keys: [on("session", "J", key("J"))] },
  { id: "turnPrev", title: "Previous message of yours", keys: [on("session", "K", key("K"))] },
  { id: "messagePrev", title: "Previous agent message", keys: [on("session", "Alt+↑", alt("ArrowUp"))] },
  { id: "messageNext", title: "Next agent message", keys: [on("session", "Alt+↓", alt("ArrowDown"))] },
  { id: "firstUnread", title: "First unread agent message", keys: [on("session", "Alt+U", alt("KeyU"))] },
  { id: "first", title: "First row", keys: [on("session", "g", key("g"))] },
  { id: "last", title: "Last row, and follow the tail", keys: [on("session", "G", key("G"))] },
  {
    id: "toggle",
    title: "Open or close the row's details",
    keys: [on("session", "Enter  Space", key("Enter", " "), { native: true })],
  },
  { id: "press", title: "Press the row's first button", keys: [on("session", "o", key("o"))] },
  { id: "copy", title: "Copy the row's message or output", keys: [on("session", "c", key("c"))] },
  // Documents the browser's own Tab; it never matches.
  {
    id: "walk",
    title: "Walk the buttons from the selected row on",
    palette: false,
    keys: [on("session", "Tab", () => false)],
  },
  { id: "fleetNext", title: "Next card, then the archive", keys: [on("fleet", "j  ↓", key("j", "ArrowDown"))] },
  { id: "fleetPrev", title: "Previous card", keys: [on("fleet", "k  ↑", key("k", "ArrowUp"))] },
  {
    id: "fleetOpen",
    title: "Open the session; Read an archived one",
    keys: [on("fleet", "Enter", key("Enter"), { native: true })],
  },
  { id: "filter", title: "Filter the archive", keys: [on("fleet", "/", key("/"))] },
];

// Every key, in the order the ? list shows them, each with its action.
export const KEYS = ACTIONS.flatMap((a) => a.keys.map((k) => ({ ...k, action: a })));

const VIEWS = { global: null, browse: null, session: ["session", "read"], fleet: ["fleet"] };

// Whether the palette offers an action in a view.
export function applies(a, view) {
  if (a.palette === false) return false;
  if (a.when) return a.when.includes(view);
  return a.keys.some((k) => !VIEWS[k.scope] || VIEWS[k.scope].includes(view));
}

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
      r.insertCell().textContent = k.action.title;
    }
    panel.append(t);
  }
  box.replaceChildren(panel);
}

const PRESSABLE = new Set(["A", "BUTTON", "SUMMARY"]);

// runs: what each action does, by id; each becomes its action's `run`. An
// action without one, or one for no action, is a mistake caught at boot.
export function installKeys(runs, view) {
  const ids = new Set(ACTIONS.map((a) => a.id));
  const missing = [...ids].filter((id) => !runs[id]);
  const extra = Object.keys(runs).filter((id) => !ids.has(id));
  if (missing.length || extra.length) throw new Error(`keys.js: no run for ${missing}; no action for ${extra}`);
  for (const a of ACTIONS) a.run = runs[a.id];
  document.addEventListener("keydown", (ev) => {
    if (ev.defaultPrevented || ev.isComposing) return;
    const v = view();
    const scopes = typing(ev.target) ? ["global"] : ["global", "browse", v === "read" ? "session" : v];
    const b = KEYS.find((k) => scopes.includes(k.scope) && k.match(ev));
    if (!b) return;
    // Enter and Space on a focused link, button or summary are the element's own.
    if (b.native && PRESSABLE.has(ev.target.tagName)) return;
    ev.preventDefault();
    b.action.run(ev);
  });
}
