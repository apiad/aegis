# aegis 2: a command palette over one registry of actions

**Status: implemented, 2026-10-09** (issue #248), building on
`2026-10-07-aegis-keyboard-design.md`.

## What this delivers

Ctrl+K (⌘K on a Mac) opens a palette over the page. It lists the actions that
apply to the view you are in, each with its keys; typing narrows them with the
command menu's scorer (`fuzzy` in `js/commands.js`); ArrowUp/Down and Tab walk the
list; Enter runs the chosen action; Esc, the chord again or a click outside closes
it. Closing hands focus back to where it was before the action runs, so "Next row"
or "Focus the message box" acts from where you left off.

## One registry

`js/keys.js` held a table of keys, each naming an action that app.js ran by name.
It is now a table of actions: `{ id, title, keys, when, palette }`, where each key
is `{ scope, label, match }`. `installKeys(runs, view)` gives each action its
`run` from app.js and throws at boot when an action has no run or a run has no
action. The keydown listener, the `?` list and the palette all read this one
array, so a new action is one entry here and one run in app.js, and it appears in
all three.

The actions that were pairs on one key row (Alt+[ / Alt+], J / K, g / G,
Alt+↑ / Alt+↓) are two actions each, so every action in the palette runs without
the key event that would have told it which way to go. Only Alt+0…9 still reads
its event, for the digit, and stays out of the palette (`palette: false`), as do
the documented Tab and the palette's own chord.

`when` names the views where the palette offers an action. Without it, that is
wherever the action's keys act: anywhere for a global or browse key, the
transcript (session and read) or the Fleet for theirs. It is set where a global
chord does nothing in some views: the composer, the command menu and dictation.

## Why Ctrl+K

Chromium's `accelerator_table.cc` binds Ctrl+K to `IDC_FOCUS_SEARCH`, and
`BrowserCommandController::IsReservedCommandOrKey` reserves only closing and
opening tabs and windows, restoring a tab, cycling tabs and exit. Ctrl+K is not
among them, so the page's `preventDefault` wins, as on GitHub and Slack, and in an
installed app window no key is reserved at all. Nothing in the client bound a Ctrl
chord before. On a Mac, Ctrl+K deletes to the end of the line in a text field, so
there the chord is ⌘K alone. The chord is global: it opens the palette while you
type in the message box.

## Left out

- Jumping to a session by name. The palette lists actions, not tabs.
- A button for the palette. The `?` list names its chord.
- Recently used actions first. The order is the registry's, or the score's.

## Testing

- `test_ctrl_k_opens_the_palette_which_filters_runs_and_closes`: open, the rows
  are the registry's for the view, keys shown, filter, Enter runs, Esc closes
  without the Esc action and gives focus back, arrows wrap, the chord toggles.
- `test_every_key_and_every_action_reach_the_list_and_the_palette`: every key
  is in the `?` list under its action's title, and every action not kept out
  is offered in the Fleet or a session.
- `test_only_keys_js_listens_for_keys_on_the_page` (`tests/test_client_rules.py`):
  no other module adds a key listener to the document or the window, so a
  page-wide key outside the registry fails the suite.
