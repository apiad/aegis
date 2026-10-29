// The keyboard: one registry of every action the client offers by key, and the
// one keydown listener that dispatches from it. The ? overlay and the command
// palette (palette.js) are drawn from the same registry, so neither can list an
// action that does nothing, and a new action shows in both without a second edit.
//
// Chrome on Linux keeps Ctrl+T/W/N/Tab, Alt+1…9, Alt+←/→ and Alt+D/E/F for
// itself (chrome/browser/ui/accelerator_table.cc); the chords here are the Alt
// keys it leaves free. They match ev.code, because Alt can change ev.key.
// Alt+↑/↓, Alt+U, Alt+J and Alt+P are not in Chrome's Linux accelerator table either.
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
const altShift = (code) => (ev) => ev.altKey && ev.shiftKey && !ev.ctrlKey && !ev.metaKey && ev.code === code;
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
// the key itself (which digit) or is only documented here. `nav: true` marks
// what leaves the view; it does nothing in a window tab (js/canvas.js), which
// draws one window of a canvas and would run window 1's pages a second time.
//
// A key's scope: "global" acts anywhere, even while typing; "browse" outside
// text fields in any view; "session" (also the read view) and "fleet" in theirs.
export const ACTIONS = [
  { id: "palette", title: "Search the commands", palette: false, nav: true, keys: [on("global", MAC ? "⌘K" : "Ctrl+K", mod("k"))] },
  {
    id: "composer",
    title: "Focus the message box",
    when: ["session", "spawn"],
    keys: [on("global", "Alt+.", alt("Period")), on("session", "i  /", key("i", "/"))],
  },
  { id: "commands", title: "Commands for this session", when: ["session"], keys: [on("global", "Alt+/", alt("Slash"))] },
  { id: "workflow", title: "Run a saved workflow", when: ["session"], keys: [] },
  {
    id: "browse",
    title: "Focus the transcript, or the Fleet cards",
    when: ["session", "read", "fleet"],
    keys: [on("global", "Alt+,", alt("Comma"))],
  },
  // The Fleet is the first tab.
  { id: "tabPrev", title: "Previous tab", nav: true, keys: [on("global", "Alt+[", alt("BracketLeft"))] },
  { id: "tabNext", title: "Next tab", nav: true, keys: [on("global", "Alt+]", alt("BracketRight"))] },
  // As a drag: a group's member within its group, any other tab with its group.
  { id: "tabLeft", title: "Move this tab left", when: ["session", "term", "browser"], keys: [on("global", "Alt+Shift+[", altShift("BracketLeft"))] },
  { id: "tabRight", title: "Move this tab right", when: ["session", "term", "browser"], keys: [on("global", "Alt+Shift+]", altShift("BracketRight"))] },
  { id: "needs", title: "Next session that needs you, longest waiting first", nav: true, keys: [on("global", "Alt+J", alt("KeyJ"))] },
  { id: "sessions", title: "Switch to a session", nav: true, keys: [on("global", "Alt+P", alt("KeyP"))] },
  { id: "spawn", title: "New session", nav: true, keys: [on("global", "Alt+N", alt("KeyN")), on("browse", "n", key("n"))] },
  { id: "settings", title: "Settings: .aegis.yaml", nav: true, keys: [on("global", "Alt+S", alt("KeyS"))] },
  { id: "journal", title: "Journal: what was done", nav: true, keys: [on("global", "Alt+L", alt("KeyL"))] },
  { id: "usage", title: "Usage: what work cost", nav: true, keys: [] },
  { id: "knowledge", title: "Knowledge: your skills and memories", nav: true, keys: [on("global", "Alt+I", alt("KeyI"))] },
  { id: "files", title: "Open a file under the root, newest first", nav: true, keys: [on("global", "Alt+O", alt("KeyO"))] },
  // Inside the editor Mod-s is CodeMirror's (editor.js); this is the rest of the view.
  { id: "save", title: "Save the open file", when: ["files"], keys: [on("files", MAC ? "⌘S" : "Ctrl+S", mod("s"))] },
  { id: "schedules", title: "Schedules: prompts fired on a clock", nav: true, keys: [on("global", "Alt+K", alt("KeyK"))] },
  {
    id: "dictate",
    title: "Dictate into the message box; again to stop",
    when: ["session", "spawn"],
    keys: [on("global", "Alt+M", alt("KeyM"))],
  },
  // As the Quiet button, and only when it is enabled: a working or waiting session.
  { id: "quiet", title: "Quiet: hide this tab until the turn ends", when: ["session"], keys: [on("global", "Alt+Q", alt("KeyQ"))] },
  // As the Archive button, so a working session still asks first.
  { id: "archive", title: "Archive this tab; Reopen brings it back", when: ["session"], keys: [on("global", "Alt+A", alt("KeyA"))] },
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
    nav: true,
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
  // Outside a text field: inside the find bar's box, a second press is the browser's own find.
  {
    id: "find",
    title: "Find in this transcript; press again for the browser's find",
    when: ["session", "read"],
    keys: [on("session", MAC ? "⌘F" : "Ctrl+F", mod("f"))],
  },
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
  // The browse scope covers the transcript, the Fleet and Files; in a text field the key is the browser's.
  {
    id: "menuKey",
    title: "The menu of the selected row, card or file",
    palette: false,
    keys: [
      on("browse", "Menu  Shift+F10", (ev) =>
        (bare(ev) && !ev.shiftKey && ev.key === "ContextMenu") || (bare(ev) && ev.shiftKey && ev.key === "F10"),
      ),
    ],
  },
];

// Every key, in the order the ? list shows them, each with its action.
export const KEYS = ACTIONS.flatMap((a) => a.keys.map((k) => ({ ...k, action: a })));

const VIEWS = { global: null, browse: null, session: ["session", "read"], fleet: ["fleet"], files: ["files"] };

// Whether the palette offers an action in a view.
export function applies(a, view) {
  if (a.palette === false) return false;
  if (a.when) return a.when.includes(view);
  if (!a.keys.length) return true; // a command with no key: the palette is its only way in
  return a.keys.some((k) => !VIEWS[k.scope] || VIEWS[k.scope].includes(view));
}

function typing(t) {
  return t instanceof HTMLElement && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName));
}

const HEADS = { global: "Anywhere", browse: "Outside a text field", session: "Transcript", fleet: "Fleet", files: "Files" };

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
    // In the Files view Ctrl+S saves from any field, the picker's included:
    // the browser's own Save page never opens there.
    const scopes = typing(ev.target) ? ["global", ...(v === "files" ? ["files"] : [])] : ["global", "browse", v === "read" ? "session" : v];
    const b = KEYS.find((k) => scopes.includes(k.scope) && k.match(ev));
    if (!b || (b.action.nav && v === "window")) return;
    // Enter and Space on a focused link, button or summary are the element's own.
    if (b.native && PRESSABLE.has(ev.target.tagName)) return;
    ev.preventDefault();
    b.action.run(ev);
  });
}
