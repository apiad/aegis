---
title: Artifacts
kind: howto
when: showing the person a page they can click, tune or answer, instead of asking in prose
---

An artifact is an interactive page you write for the person: a choice among
things that must be seen, values to tune, a form, a question whose answer has
structure, an explanation to play with. It lands in your transcript as a card,
runs in their browser, and their answer comes back to your inbox.

## Write and send one

1. `artifact_create` with `title` (one line, at most 140 characters), and
   optionally `caption` (one line of Markdown shown above the card) and
   `state` (JSON the page starts from, at most 64 KB). It writes a working
   skeleton and returns its `id`, its `path` and its text.
2. Edit the file at `path` with your own edit tool. Keep the skeleton's script
   tag; keep or drop its stylesheet.
3. `artifact_send` with `id`. aegis checks the page and, when the person has
   your transcript open, runs it hidden in their browser first. A page that
   throws is refused as page_error, with the message, and nothing is shown:
   fix the draft and send again. The reply's `started` is true when the page
   ran, and null when no browser was open or none answered within 3 seconds;
   the page then lands untried, and a script error reaches your inbox when
   someone opens it.
4. Only then write your message, which may refer to the card above it, and end
   the turn with `turn_end` and attention needs_you when the page asks
   something.

A send is refused when the page lost the script tag, or never calls
`aegis.submit`, `aegis.emit` or `aegis.state`, since then no answer could ever
reach you.

## What the page can call

The page talks to aegis through `window.aegis`:

- `aegis.ready((state, theme) => ...)` runs once the host has handed over the
  state and the theme (its colours and fonts);
- `aegis.state(obj)` keeps state silently, for you to read later;
- `aegis.emit(name, obj)` wakes you and leaves the page live;
- `aegis.submit(obj, label)` wakes you with the answer and closes the page; the
  card collapses to `label`;
- `aegis.onState(fn)` hears your `artifact_update`;
- `aegis.onStatus(fn)` hears the page's status, and the server's refusal of a
  submit when there is one.

Limits the checks cannot catch: an event name is one word matching
`[a-z][a-z0-9_-]{0,31}` and is never submit, error or close; state, event data
and a submit's data are at most 64 KB each; at most 20 emits a minute; a
submit's label is one line of at most 140 characters.

## Hear the answer

An answer reaches you as a user turn headed:

```text
> from artifact:<id> · submit · 2026-10-11T14:02:11Z
```

with the label and the data in a JSON code block. An emit is headed with its
event name instead of `submit`, and a script error in the page with `error`,
the message and a few stack lines. Only a page's first error wakes you. A
click while you are working is held until your turn ends.

`artifact_read` with `id` returns the page's status (draft, live, submitted or
closed), its latest state, its last 20 events, the submit's data and label,
and how many errors followed the first. It wakes nobody, so call it whenever
you need the state.

## Change or withdraw a page

- `artifact_update` with `id` pushes new `state` into a live page, changes its
  `caption`, or with `resend=true` snapshots the edited draft again and swaps
  the page, keeping its state unless you give a new one.
- `artifact_close` with `id` withdraws a live page, collapsing its card to
  `label`. A draft never sent is discarded.

## Send a file instead

When the person only needs to look at something, such as a screenshot, a
rendered PDF or a draft, `file_send` is enough. Pass `paths` (1 to 20
absolute paths, shown as one card that pages through them) and an optional
`caption`. The files are copied, so later edits are not seen, and each is at
most 100 MB. If one cannot be sent, none is. Send a file when it is an output
the person asked for or something they must see to discuss it; they see the
source code you edited as diffs already.
