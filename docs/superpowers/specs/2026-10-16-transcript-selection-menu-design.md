# Transcript selection menu

## Why

Assistant replies are rendered as selectable Markdown, but selecting words has
no Aegis-specific affordance. The browser's native selection is useful for
copying, yet a person who wants to ask about a passage must manually move it to
the composer and write the question.

## Scope

When a non-empty range stays inside one assistant response (`.row.prose .body`),
the client shows a compact, viewport-fixed toolbar with three actions:

1. **Add to chat** appends the selected text to the current draft.
2. **More details** appends `Tell me more about this:` followed by the text.
3. **Ask in side chat** appends the same prompt prefixed with `/btw`.

All actions focus the composer but leave sending to the person. The menu is
not shown for user messages, tool output, thinking rows, empty selections or a
range crossing two replies.

## Design

`client/js/selection.js` owns selection detection, menu positioning and the
short-lived selected-text snapshot. It listens to the document's native
`selectionchange`, filters the range to one response body, and places a fixed
toolbar below the range (above it when there is no room). A menu `mousedown`
prevents the browser from clearing the selection before its click handler runs.

The module closes on transcript scroll, resize, unrelated pointer down and
after an action. `app.js` owns composer mutation and draft persistence so the
selection feature does not duplicate send or `/btw` behavior. The fixed menu
also avoids being clipped by the transcript's scroll container and remains
usable with virtualized transcript rows.

## Out of scope

Keyboard navigation between the composer and transcript, selection shortcuts,
direct submission, and a whole-reply copy action are separate follow-ups.

## Verification

`tests/test_selection_browser.py` selects an assistant reply in Chromium,
checks menu labels and viewport placement, exercises all three actions, checks
focus and draft composition, and confirms a user-message selection does not
open the menu.
