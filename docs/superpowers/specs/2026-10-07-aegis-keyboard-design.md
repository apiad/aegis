# aegis 2: driving the client from the keyboard

**Status: implemented, 2026-10-07** (issue #159), following
`docs/superpowers/plans/2026-10-07-aegis-keyboard.md`. Designed with Alex, who chose
the "focus decides" model over all-Alt chords and over an explicit vim mode.

## What this delivers

Everything Alex does with the mouse in the web client, done from the keyboard:
open a session, switch tabs, focus the composer, move through the transcript a row
or a turn at a time, open a row's details, press the buttons inside a row, and move
through the Fleet cards and the archive. A `?` overlay lists every key.

Out of scope: Stop, Close and renaming get no key of their own. They stay reachable
with Tab, but ending or archiving a session from a single key press is the accident
this design avoids. The spawn form's fields are already a form and Tab walks them.

## What Chrome leaves us

Alex runs aegis in Chrome on Linux. Chromium's `chrome/browser/ui/accelerator_table.cc`
decides which keys the page never sees:

- Ctrl+T, Ctrl+W, Ctrl+N, Ctrl+Tab and their kin are the browser's. The TUI-era
  bindings (`legacy/aegis/tui/app.py:371-414`) cannot be ported.
- Alt+1…8 select Chrome's own tabs and Alt+9 its last tab, under
  `BUILDFLAG(IS_LINUX)`. aegis's Alt+1…9 has never reached the page there; only
  Alt+0 (Fleet) does. The binding stays for Firefox and other platforms.
- Alt+←/→ are back and forward. They already walk aegis's hash routes, and Alex
  uses them, so aegis does not bind them.
- Alt+D, Alt+E, Alt+F, Alt+Home and the Shift+Alt group are taken.

Free on Linux: Alt+. Alt+, Alt+[ Alt+] Alt+N. Only Shift+Alt+N is bound (split tab).

## The model: focus decides

There is no hidden mode. A key means what it means because of where the DOM focus
is, and the focus is visible:

- **Global chords** work everywhere, including while typing in the composer.
- **Plain keys** act only when the focus is not in a text field (`input`,
  `textarea`, `select`, anything `contenteditable`). This is how Gmail does it,
  and it is the only way to use j or n without stealing them from a message.
- Esc keeps its meaning: it interrupts a working session unless a title or handle
  is being edited.

### Global chords

| key | action |
|---|---|
| Alt+. | focus the composer: the session's input, or on `#new` the spawn form's directory field, where Enter submits (Enter on the profile `<select>` does not) |
| Alt+, | focus the transcript in a session or read view; the cards in Fleet |
| Alt+[ / Alt+] | previous / next tab, in this browser's order, Fleet first; wraps |
| Alt+N | new session (`#new`) |
| Alt+0…9 | unchanged: 0 is Fleet, n is the n-th tab |
| Esc | unchanged: interrupt |

### Plain keys in a session or read view, focus on the transcript

| key | action |
|---|---|
| j / ↓ | select the next row |
| k / ↑ | select the previous row |
| J / K | select the next / previous user message, one turn at a time |
| g / G | select the first / last row; G also scrolls to the bottom, so the transcript follows the tail again |
| Enter / Space | open or close the selected row's `<details>`; marks it touched, as a click does |
| o | press the selected row's first button (a file card's view) |
| Tab | native: walks the summaries and buttons from the selected row on; Enter presses them |
| i or / | focus the composer |
| n | new session |
| 1…9, 0 | the n-th tab, 0 Fleet: the same as Alt+n, for Chrome on Linux |
| ? | the key overlay |

### Plain keys in Fleet, focus on the cards

| key | action |
|---|---|
| j / k / ↓ / ↑ | select the next / previous card, then on into the archive rows |
| Enter | open the selected card's session; on an archive row, Read it |
| / | focus the archive filter |
| n, 1…9, 0, ? | as in a session |

Reopen on an archive row stays a Tab plus Enter on its button: it starts a process.

## Selection

The transcript and the Fleet each hold one selected item, by id, never by node.

`Transcript.apply()` replaces a row's node on every upsert (`old.replaceWith(n)`),
and `renderCards()` rebuilds every card on every sessions patch. A selection held
as a node would vanish the moment a tool call finished. Held as an entry id or a
log id, it is re-applied after each redraw: the new node gets the `sel` class.

Selecting scrolls the item into view with `block: "nearest"`, so moving one row
does not jump the page. A removed entry clears the selection. The first j or k
with nothing selected picks the row closest to the bottom of what is on screen,
because that is where the reader is looking.

The transcript scroller and the cards box get `tabindex="-1"`, so Alt+, can focus
them without adding them to the Tab order. The selected row shows a left rule in
the accent colour, drawn by each theme's existing accent variable, so the three
themes need no new colour.

## Where the code lives

- **`client/js/keys.js`, new.** One table: key, where it applies (global, session,
  fleet), the action's name and a one-line description. The document's single
  `keydown` listener dispatches from it. The `?` overlay is drawn from the same
  table, so the overlay cannot list a key that does nothing or miss one that does.
  The existing listener in `app.js` moves into this table.
- **`client/js/transcript.js`** gains `select(id)`, `move(delta)`, `moveTurn(delta)`,
  `selected` and `toggle()`, and re-applies the selection inside `snapshot()` and
  `apply()`.
- **`client/js/app.js`** holds the Fleet selection and re-marks it after
  `renderCards()` and `renderArchive()`; `fleet.js` is unchanged.
- **`client/js/app.js`** wires the actions to what already exists: `go()`,
  `interrupt()`, the composer, the order in `ordered`.
- **`index.html`**: the overlay's container, and the shortcut hints in the titles
  and placeholder.

The new-tab composer in PR #156 replaces the spawn form. Whichever lands second
points Alt+. at the field where Enter spawns; the rest is unaffected.

## Testing

Browser tests in `tests/test_browser.py`, against `aegis serve` with the fake
claude, one per behaviour a user would notice:

- Alt+. from the transcript puts the caret in the composer; i and / do the same.
- j, k, J, K, g and G select the expected rows; G re-follows the tail.
- A selected tool row keeps its selection when its result arrives (the upsert
  replaces the node).
- Enter opens a selected row's details and Enter again closes it.
- j typed in the composer is text, not navigation.
- Alt+[ and Alt+] cycle Fleet and the tabs and wrap; 1…9 select tabs when the
  focus is outside a text field.
- In Fleet, j then Enter opens the second card's session; / focuses the filter.
- ? opens an overlay that lists every row of the table, and Esc closes it.

Playwright's `keyboard.press` reaches the page without passing through Chrome's
accelerators, so these tests cannot see a key Chrome steals. The accelerator table
above is the evidence for that part, and the last check is Alex pressing the keys in
his own Chrome against a server started from the branch.
