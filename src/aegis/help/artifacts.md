---
title: Artifacts
kind: howto
when: showing the person a page they can click, tune or answer, instead of asking in prose
hints:
  - Ask an agent to show you a page to choose from instead of a question in prose. #write-and-send-one
  - A page an agent sent can stay docked beside the transcript while you work. #pages-that-stay-in-view
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
   ran, and null when it landed untried; a script error then reaches your
   inbox when someone opens it. `watching` says why it went untried: false
   when no browser had your transcript on screen, so the person sees the card
   when they open your tab, and true when a browser had it but did not answer
   within 3 seconds.
4. Only then write your message, which may refer to the card above it, and end
   the turn with `turn_end` and attention needs_you when the page asks
   something.

Lay every page out for a desktop and a phone alike. The person may open it on
a phone, and on a desktop every artifact's bar has a Phone width button that
narrows the frame to 390 pixels, so they will see both. Let the layout reflow
with the width (flex-wrap, a grid with `auto-fit`, a media query that stacks
side-by-side columns), keep controls large enough to tap, and never rely on a
fixed width or on hover alone.

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
  submit when there is one;
- `aegis.dictate(field, fn)` dictates into a textarea or text input, and a second
  call for the same field stops it (see below);
- `aegis.upload(file, onProgress)` sends a file to the session's inbox and wakes
  you with its path (see below).

Limits the checks cannot catch: an event name is one word matching
`[a-z][a-z0-9_-]{0,31}` and is never submit, error, close or upload; state, event data
and a submit's data are at most 64 KB each; at most 20 emits a minute; a
submit's label is one line of at most 140 characters.

## Take dictation or a file

A page can take long spoken answers, one field per question, and files of up
to 100 MB:

```html
<label>What went wrong? <textarea id="q1"></textarea></label>
<button id="mic">Dictate</button>
<input id="audio" type="file" accept="audio/*">
<button id="send">Send</button>
<script>
  aegis.ready((state) => {
    q1.value = state.q1 ?? "";
    q1.addEventListener("input", () => aegis.state({ q1: q1.value }));
    mic.onclick = () => aegis.dictate(q1, (s) => (mic.textContent = s === "idle" ? "Dictate" : "Stop"));
    audio.onchange = async () => {
      const f = await aegis.upload(audio.files[0], (sent, size) => (mic.title = `${sent} of ${size}`));
      audio.after(`Sent ${f.name}`);
    };
    send.onclick = () => aegis.submit({ q1: q1.value }, "Answered");
  });
</script>
```

`aegis.dictate` must run inside a click in the page: a call with no click is
refused and the person sees why. The function hears `loading`, `listening`,
`finishing` and `idle`, also on the field's `data-dictation`. The microphone and
the engine run in aegis's page and only the text reaches yours, as `input` events
on the field, so save it with `aegis.state` as above: words still in flight when
the page leaves the screen are lost, because the microphone stops with it. aegis
draws its own bar over the page while it listens, with a Stop button. A submit while
dictation runs stops it and is refused, with the reason on the card, because
its data was read before the last words landed: the person sends again.
The answer you get is the next submit or event, with a note that part of it was
dictated and may hold misheard words. `artifact_read` has no such note, so
treat text from a dictating page as a draft there too.

`aegis.upload` takes a File or a Blob and resolves to `{name, path, mime, size}`,
or rejects with an error whose `code` says why. Each file is a turn of yours
headed `> from artifact:<id> · upload`, listing its path, and an `upload` entry
in `artifact_read`'s events; it counts against the 20 events a minute. The page
stays live, and the file is not subject to the 64 KB limits.

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

## What the person can point at

The person can select text in your page, or right-click or long-press an
element, and ask you about it or leave comments on it (Files help, "Pointing
at lines and comments"). The message reaches you as a quote under a locator:
the page's id, the nearest heading, and the element's id or a short CSS path,
such as `` `art-1a2b3c4d` § Options › #plan-b ``. Headings and ids on the
parts the person may talk about make those locators read well. The same holds
for an HTML file you send, named by its file name. The text comes from the
page's own report of what was selected or pressed, so a page decides what
Ask and Copy carry. A right-click with Shift
opens the browser's own menu inside the page.

## Send a file instead

When the person only needs to look at something, such as a screenshot, a
rendered PDF or a draft, `file_send` is enough. Pass `paths` (1 to 20
absolute paths, shown as one card that pages through them with ‹ › or a
sideways swipe on a touch screen, going round from the last to the first) and an optional
`caption`. The files are copied, so later edits are not seen until you send
the file again, and each is at most 100 MB. If one cannot be sent, none is. Send a file when it is an output
the person asked for or something they must see to discuss it; they see the
source code you edited as diffs already.

## Pages that stay in view

A card scrolls away as the transcript grows. To keep a page or a file on
screen beside the transcript, dock it: pass `dock` to `artifact_send` or
`file_send`, or to `workflow_arm` for a workflow's card. The person can dock any card too, and move, resize and undock
what you docked. `dock` takes one of:

- left or right: a new column at that edge of window 1;
- left:, right:, above: or below: and a pane's id: next to a pane already
  docked;
- window: and a number, or window:new: another browser window of the canvas
  (see below).

A page's pane id is its `id`. A sent file's is its key, which `layout_read`
lists with each pane's kind and title; the transcript's is transcript.
Several files in one `file_send` stack in one column, in the order given, from `dock` down. A
pane already docked moves. A `dock` that does not fit is refused as
bad_params before anything is sent, and the message says why and which panes
there are. If the person changed the canvas while the send was under way
and it no longer fits, the page or the files are still sent and the reply
says `"docked": false` with the reason.

`layout_read` returns the whole canvas: its windows, each a list of columns
with a width share, each column a stack of panes with a height share. It
wakes nobody. `layout_set` with `windows` replaces the canvas, in the same
shape; widths and heights are optional, and missing ones are split equally.

The limits are 4 windows, 4 columns in a window and 3 panes in a column, and
the transcript stays in window 1.

Window 1 is the session's own browser tab. Any other window is a separate
browser tab, which you cannot open, since browsers open tabs only from a
click. It is opened from the item's card, which says in window 2 · open, or
from window 1's chip for that window, which also brings its panes back.
Until it is open, what you put there is off the desktop's screen. So place
panes in window 1, and use another window only when the person asked for a
second screen.

A page leaves the canvas when it is submitted or closed. A file sent again
from the same path is its next version: the earlier card says it was
replaced, and a pane showing the file shows the new version where it is, so
a second `file_send` needs no `dock`.

## Emit when the person lets go

Each emit wakes you for a turn, and a page may emit at most 20 events a
minute. A slider that emits on input emits at every step of a drag and
reaches that limit in seconds, each step a turn of yours. Emit on change,
which fires once when the person lets go of the control, and keep the value
in between with `aegis.state` if you need it.

## Example: controls beside the document they shape

The person tunes a report's margin and watches the PDF change:

1. `artifact_create` a page whose control emits on change:

   ```html
   <label>Margin <input id="m" type="range" min="10" max="40"></label>
   <script>
     aegis.ready((state) => {
       m.value = state.margin ?? 20;
       m.addEventListener("change", () => aegis.emit("margin", { margin: Number(m.value) }));
     });
   </script>
   ```

2. `artifact_send` it with `dock="right"`.
3. Render the PDF and `file_send` it with `dock="below:<the page's id>"`.
4. When a turn headed `> from artifact:<id> · margin` arrives, render the PDF
   again with the new margin and `file_send` the same path. The pane under
   the controls shows the new version.
