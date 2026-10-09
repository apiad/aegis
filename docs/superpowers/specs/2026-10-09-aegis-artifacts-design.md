# aegis: artifacts, interactive pages an agent hands to the person

**Status: designed, 2026-10-09** (issue #217). Designed with Alex in a
brainstorm, text only. Slice 1 of the "decision panels in transcripts" section
of `2026-10-05-aegis-2-vision-design.md`. The docked, editable canvas with
native text edits and layout moves is slice 2 and has no spec yet. The plan
follows at `docs/superpowers/plans/2026-10-09-aegis-artifacts.md`.

## What this delivers

An agent writes an HTML page, calls `artifact_send`, and the page runs live in
its transcript. The person clicks, drags, types or picks, and the page sends
the result back: a submit wakes the agent with the answer and collapses the
card to one line, an event wakes the agent and leaves the page live, and a
state write is kept silently for the agent to read when it wants. The agent can
push new state into a live page, swap the page for another, or withdraw it.
The page can take the theme's colors and type, or bring its own.

Three uses drove the design: a decision ("pick one of these three layouts",
"tune the accent and the spacing, then confirm"), an explanation (a
visualization the person can play with), and a form (a question with structure
a prose reply would lose). All three are one-shot: the artifact lives for the
exchange and then is a record in the transcript.

Today `file_send` serves an HTML file in a sandboxed frame and nothing comes
back: the frame's origin is opaque, the page has no `message` listener, the
registry has no operation a frame could reach, and the inbox has no sender kind
for a page. The agent sends a file and asks in prose.

## Decisions

| Question | Decision | Why |
|---|---|---|
| What the page speaks | A small aegis script, `/static/js/artifact.js`, exposing four calls | The agent writes the page from scratch for each task; four calls fit in one primer paragraph. MCP Apps' ten-method handshake is built around a tool's result, not an agent posting a page and waiting, and no MCP App is being shipped into aegis today |
| The wire under the script | JSON-RPC 2.0 over `postMessage`, the shape MCP Apps uses | Hosting an MCP App later is a shim over the same bridge, not a rewrite |
| Where the HTML comes from | A path on disk, copied and served like `file_send` | Agents write files anyway; inline HTML in a tool call is paid for twice, in the call and in the transcript |
| Who can answer | Anyone with the session open | The vision's rule: session UI is shared, the arrangement is private |
| How the agent hears | The inbox, headed `> from artifact:<id> · …` | Held mid-turn and delivered when the turn ends, like a monitor or a handoff; nothing is injected at a tool boundary |
| State writes | Coalesced on the server to one record a second, and always before an emit, submit, close or update | A slider fires sixty times a second; the store is append-only |
| Echo | The host pushes state into a frame only when the agent wrote it | A browser's own write pushed back would loop with a page that re-sends on receipt |
| Errors in the page | One inbox message per artifact per agent turn | A loop in the page's JS costs one message, not a hundred |
| After submit or close | The card collapses to the label; Show re-mounts the frame read-only | The transcript keeps the exchange; the frame stays available to look at |
| Attention | No new rule | The agent ends its turn with `turn_end(needs_you)` after a question artifact, as after any question |

## The tools

Four agent operations, served as MCP tools under their names with the dot as
an underscore. Each touches only the caller's own session, the `own(caller)`
rule `file.send` uses. An artifact id is `art-<8 hex>`, minted like a
monitor's, and is what the inbox headers name.

```
artifact.send(path: str, caption: str | None = None, state: dict | None = None)
  -> {id, url}
```

`path` resolves like `file.send`'s: against the session's working directory,
`~` expanded. The file is copied under a fresh file id and served at
`/files/<file_id>/<name>`. Refused with `not_html` when `files.classify` does
not say `html`, and with `file.send`'s own errors otherwise. `state` is the
JSON the page receives on init; it may be anything up to 64 KB.

```
artifact.read(id: str)
  -> {status, state, events, submitted, label}
```

`status` is `live`, `submitted` or `closed`. `state` is the latest write from
the page or the agent. `events` is the last 20 emits, oldest first, each
`{name, data, ts}`. `submitted` is the submit's data and `label` its label,
both null until then. Never wakes anyone; this is the silent channel.

```
artifact.update(id: str, state: dict | None = None, path: str | None = None,
                caption: str | None = None)
  -> {id, url}
```

Pushes `state` into every live frame of the artifact, swaps the document for
the file at `path` (a new file id, the state carried over unless `state` is
also given), or changes the caption. Refused with `not_live` once the artifact
is submitted or closed.

```
artifact.close(id: str, label: str | None = None) -> "closed"
```

The agent withdraws a live artifact. The card collapses to `label`, or to
"closed by the agent".

## The page

A page includes one script and talks to `window.aegis`:

```html
<!doctype html>
<meta charset="utf-8">
<link rel="stylesheet" href="/static/css/artifact.css">
<script src="/static/js/artifact.js"></script>
<h2>Pick a layout</h2>
<div class="row">
  <button data-pick="a">A · two columns</button>
  <button data-pick="b">B · one column</button>
</div>
<script>
  aegis.ready((state, theme) => {
    for (const b of document.querySelectorAll("[data-pick]"))
      b.onclick = () => aegis.submit({layout: b.dataset.pick}, `Picked layout ${b.dataset.pick.toUpperCase()}`);
  });
</script>
```

The four calls:

- `aegis.ready(fn)` runs `fn(state, theme)` once the host has answered the
  handshake. `state` is the agent's initial state, or the latest one after a
  reload. `theme` is the map of CSS variables.
- `aegis.state(obj)` replaces the artifact's state. Silent. The script
  coalesces calls within one animation frame; the server coalesces further.
- `aegis.emit(name, obj)` wakes the agent with the event and leaves the
  artifact live. `name` is one word, `[a-z][a-z0-9_-]*`, at most 32
  characters, and never `submit`, `error` or `close`.
- `aegis.submit(obj, label)` wakes the agent with the answer and ends the
  artifact. `label` is one line of at most 140 characters; it is what the
  collapsed card shows.

What the script does besides: it reports the document's height through a
`ResizeObserver` on `documentElement` so the frame grows with the page; it
forwards `window.onerror` and `unhandledrejection` to the host; it applies the
theme variables to the frame's `:root` on init and on every theme switch, so a
page written with `var(--accent)` follows the theme; and once the host says the
artifact is no longer live it turns `submit`, `emit` and `state` into no-ops
that log a warning, and sets `data-status` on `<html>` so a page can gray
itself out. Opened in its own tab with no host, `ready` fires with the empty
state after a short wait and the other calls log to the console, so the raw
Open link still shows the document.

`/static/css/artifact.css` is optional. It styles the body, headings, buttons,
inputs, labels, `.row` and `.card` from the theme variables (`--bg`, `--ink`,
`--accent`, `--surface`, `--rule`, `--font-ui`, `--font-mono`, `--r`), so a
page that links it looks like a part of aegis without writing CSS. A page that
wants its own look does not link it.

## The bridge

`client/js/artifacts.js` holds one `message` listener on `window` and a map
from a frame's `contentWindow` to `{log_id, artifact_id, frame}`. A message is
accepted only when `event.source` is a mapped window; that binds every message
to one artifact, so a page can never name another. The page posts to `*`,
because its origin is opaque and it cannot know the host's; the host posts
back to the frame's `contentWindow` with target `*` for the same reason, and
the content of those messages is state, theme and status, nothing secret.

Messages, JSON-RPC 2.0:

| From | Method | Params | Answer |
|---|---|---|---|
| page | `ui/initialize` | `{}` | `{artifact, state, theme, status}` |
| page | `aegis/state` (notification) | `{state}` | |
| page | `aegis/emit` (notification) | `{name, data}` | |
| page | `aegis/submit` (notification) | `{data, label}` | |
| page | `aegis/size` (notification) | `{height}` | |
| page | `aegis/error` (notification) | `{message, stack}` | |
| host | `aegis/state` (notification) | `{state}` | |
| host | `aegis/theme` (notification) | `{theme}` | |
| host | `aegis/status` (notification) | `{status}` | |

`ui/initialize` keeps MCP Apps' name because it is the one message both
protocols have and the one a compatibility shim would start from; the others
are aegis's. Nothing in the bridge is a tool call: the page reaches the server
only through the four person operations below, with the artifact id the host
holds, never one the page sent.

The theme map is read once per switch from `getComputedStyle` on the page's
root, for the variables the base stylesheet declares, and pushed to every
mounted frame. The size cap is 70 % of the viewport's height, after which the
frame scrolls inside.

## The person operations

Four operations in `app.py`, callable from the websocket and not by agents,
each taking `log_id` and `artifact_id`:

- `artifact.state(state)`: replaces the state. Over 64 KB is refused with
  `too_large`; the bridge reports the refusal to the page as the request's
  error and nothing reaches the agent.
- `artifact.emit(name, data)`: records the event and wakes the agent. Over 64
  KB refused; a bad name refused with `bad_name`; the 21st emit within a minute
  from one artifact refused with `rate_limited`.
- `artifact.submit(data, label)`: records the submit, sets the status, wakes
  the agent. Refused with `not_live` after the first; the bridge then tells the
  page the status so it disables itself.
- `artifact.error(message, stack)`: wakes the agent once per artifact per
  agent turn; later errors in the same turn are counted and dropped.

Each is refused with `no_artifact` for an id the session does not have, and
`not_live` for a submitted or closed one, except `error`, which a read-only
frame may still raise.

## Records and the fold

Five aegis records, `src: aegis`, in the session's store:

| Record | Fields | Effect in the fold |
|---|---|---|
| `artifact` | `artifact_id, file_id, name, caption, state` | creates the entry, status `live` |
| `artifact_state` | `artifact_id, state, by` (`page` or `agent`) | replaces the entry's state |
| `artifact_event` | `artifact_id, name, data` | appends to the entry's events, keeping the last 20 |
| `artifact_submit` | `artifact_id, data, label` | status `submitted`, sets `submitted` and `label` |
| `artifact_close` | `artifact_id, label` | status `closed`, sets `label` |

An `artifact.update` with a new path writes a second `artifact` record with
the same `artifact_id` and the new file; the fold swaps the document and keeps
status, events and state. A caption change alone is the same record with the
old file. The entry's state and events travel as detail (`transcript/wire.py`),
fetched when the row mounts; the row itself carries status, label, caption,
name and URL. Every decision about the card is made in the fold: the preview
is always `html` and the frame's sandbox is always `allow-scripts`, so the
client reads neither from the file.

State coalescing lives in `Session`: a page's write replaces an in-memory
latest and marks it dirty; a record is written when one second has passed
since the last state record, and always before an `artifact_event`,
`artifact_submit`, `artifact_close` or an agent's update, and at shutdown. An
`artifact.read` answers from memory, so it is never behind. The fold therefore
sees every write that mattered and at most one a second of the rest; a reload
or a refold agrees with the live view at every record, which the delta tests
check at every cut as they do for every other kind.

The in-memory latest and the error-per-turn set live on the `Session` and are
not in the meta: boot reads only metas, and after a restart the latest is the
last record, which is at most a second old.

## Waking the agent

Through `Session.deliver`, so a message mid-turn is held and lands at the
turn's end with the others, and a stopped session is resumed for it:

````
> from artifact:art-3f9a12c0 · submit · 2026-10-09T11:02:14
Picked layout B
```json
{"layout": "b", "accent": "#d08a1e"}
```
````

An emit puts its name where `submit` is, and its data in the block. An error
puts `error` there, then the message and the first five lines of the stack.
The body is a code block so the agent reads it as data; labels and data are
never rendered as HTML anywhere.

The primer gains one paragraph after the `file_send` one: when to send an
artifact instead of asking in prose (a choice among things that have to be
seen, values that have to be tuned, a question whose answer has structure, an
explanation that is better played with), the four calls, the header the answer
arrives under, and that the turn still ends with `turn_end(needs_you)`.
`artifact_send`'s docstring carries the skeleton above, so an agent that has
only the tool list can still write a page.

## The card

A new entry kind, `artifact`, rendered in `entries.js` beside `file`:

- Live: the caption as Markdown, a bar with the artifact glyph, the name, the
  status and Open (the raw document in a new tab), then the frame, with
  `sandbox="allow-scripts"`, `loading="lazy"`, its height following the page's
  reports up to the cap.
- Submitted or closed: one line, the glyph, the label and when, and Show,
  which re-mounts the frame with status `submitted` so the page can gray itself
  out, and whose `submit` the host refuses.

The frame is mounted only while its row is, like every row (`transcript.js`),
so an artifact scrolled far up is unmounted and re-inits from the server's
state when it comes back. A row's patch with a new state from the agent is
pushed to its mounted frame; a patch whose state came from a page is not.
`artifacts.js` registers the frames `entries.js` mounts and unregisters them
when the row is removed, and `app.js` tells it on a theme switch.

## Security

Unchanged from `file_send`: the document is served with
`Content-Security-Policy: sandbox allow-scripts` and framed with
`sandbox="allow-scripts"`, so its origin is opaque and the websocket's origin
check refuses it. New: the page reaches the server only through the bridge,
only through the four person operations, only for the artifact the host bound
its window to. The server caps every payload and rate-limits emits. Agent text
is untrusted as before: captions render as Markdown with raw HTML off, labels
and data as text and code.

## Tests

- `tests/test_artifacts.py`: the five records fold to the entry at every cut
  and the delta equals the fold (the `test_wire.py` fixtures gain an artifact
  scenario); coalescing writes one record a second and one before each
  boundary; refusals (`not_html`, `too_large`, `bad_name`, `rate_limited`,
  `not_live`, `no_artifact`); one error message per artifact per turn; the
  header and body format; `artifact.read` answers from memory.
- Browser (`tests/test_browser_*.py`, headless Chromium against a real
  `aegis serve`): the fake claude sends a fixture page that calls `ready`,
  writes state, emits and submits; the test clicks inside the frame, reads the
  inbox turn the fake claude receives, sees the card collapse to the label,
  reloads and sees the same card, switches the theme and sees the frame's
  variables change.
- `make test-live`: a real Claude session asked to offer two layouts in an
  artifact and to report the pick.
- `make bench` in the PR body, as for every change.

## Out of this slice

The docked canvas and its native edits (text in spans, moving layout pieces),
with its versioned layout, is slice 2. Also out: two people editing one live
artifact at once (last writer wins here), MCP Apps compatibility, files from
the person to an agent, an agent reading another session's artifacts, and
artifacts on the Fleet view.
