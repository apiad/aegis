# aegis: artifacts, interactive pages an agent hands to the person

**Status: implemented, 2026-10-09** (issue #217), following
`docs/superpowers/plans/2026-10-09-aegis-artifacts.md`. Designed with Alex in a
brainstorm, text only. Slice 1 of the "decision panels in transcripts" section
of `2026-10-05-aegis-2-vision-design.md`. The docked, editable canvas with
native text edits and layout moves is slice 2 and has no spec yet.

## What this delivers

An agent asks aegis for a page, edits it, and sends it. The page runs live in
its transcript. The person clicks, drags, types or picks, and the page sends
the result back: a submit wakes the agent with the answer and collapses the
card to one line, an event wakes the agent and leaves the page live, and a
state write is kept silently for the agent to read when it wants. The agent can
push new state into a live page, re-send the page after editing it, or
withdraw it. The page can take the theme's colors and type, or bring its own.

A page that does not start never reaches the transcript: the send checks it,
runs it in a hidden frame in the person's browser, and answers the agent with
the error, so the agent iterates until a working page lands and the person
only ever sees that one.

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
| What the page speaks | A small aegis script, `/static/js/artifact.js`, exposing four calls | The agent writes the page for each task; four calls fit in one primer paragraph. MCP Apps' ten-method handshake is built around a tool's result, not an agent posting a page and waiting, and no MCP App is being shipped into aegis today |
| The wire under the script | JSON-RPC 2.0 over `postMessage`, the shape MCP Apps uses | Hosting an MCP App later is a shim over the same bridge, not a rewrite |
| Where the HTML starts | `artifact_create` writes the skeleton to a file aegis owns and returns its path; every other tool takes the id | The agent never types the boilerplate, the skeleton is the one CI tests, and a tool that takes a path can be given the wrong one |
| What lands | Only a page that passed the static checks and started in a browser | A broken card in the transcript costs the person a look and the agent a turn; a tool error costs one edit |
| Who can answer | Anyone with the session open | The vision's rule: session UI is shared, the arrangement is private |
| How the agent hears | The inbox, headed `> from artifact:<id> · …` | Held mid-turn and delivered when the turn ends, like a monitor or a handoff; nothing is injected at a tool boundary |
| Order in the transcript | Artifact first, then the prose that refers to it | The prose is written once, after the page is proven; a card above its discussion reads like a figure. The caption is the lead line, and a lesson can carry its text inside the page |
| State writes | Coalesced on the server to one record a second, and always before an emit, submit, close or update | A slider fires sixty times a second; the store is append-only |
| Echo | The host pushes state into a frame only when the agent wrote it | A browser's own write pushed back would loop with a page that re-sends on receipt |
| Errors in the page | One inbox message per landed page, re-armed by a resend; later errors are counted and shown by `artifact_read` | A loop in the page's JS costs one message, not a hundred, and not one per turn for as long as the card is mounted (the first cut, once per turn, woke the agent after every turn it took) |
| After submit or close | The card collapses to the label; Show re-mounts the frame read-only | The transcript keeps the exchange; the frame stays available to look at |
| Attention | No new rule | The agent ends its turn with `turn_end(needs_you)` after a question artifact, as after any question |

## The tools

Five agent operations, served as MCP tools under their names with the dot as
an underscore. Each touches only the caller's own session, the `own(caller)`
rule `file.send` uses. An artifact id is `art-<8 hex>`, minted like a
monitor's, and is what the inbox headers name. No tool takes a path.

```
artifact.create(title: str, caption: str | None = None, state: dict | None = None)
  -> {id, path, html}
```

Writes the skeleton below to `<state>/artifacts/<id>/index.html`, the draft,
with `title` as its heading, and returns the path the agent edits with its own
Edit tool and the file's text, so the agent needs no Read before its first
edit. `caption` is the Markdown line shown above the card. `state` is the JSON
the page receives on init, up to 64 KB.

```
artifact.send(id: str) -> {id, url, started: bool | None}
```

Lands the draft: the static checks, a snapshot of the draft under a fresh file
id served at `/files/<file_id>/<name>`, then the probe (below). Only when the
probe passes, or no browser is open to run it, is the `artifact` record
written and the card shown. A failed send raises and lands nothing, so the
agent edits the draft and sends again. `started` is `true` when a browser ran
the page, `null` when none was open.

```
artifact.read(id: str)
  -> {status, state, events, submitted, label, errors}
```

`status` is `draft`, `live`, `submitted` or `closed`. `state` is the latest
write from the page or the agent. `errors` counts the script errors the server
heard after the first, which woke the agent (a browser forwards only a mounted
frame's first error, so a loop in one frame costs one wake and one count at
most). `events` is the last 20 emits, oldest first,
each `{name, data, ts}`. `submitted` is the submit's data and `label` its label,
both null until then. Never wakes anyone; this is the silent channel.

```
artifact.update(id: str, state: dict | None = None, caption: str | None = None,
                resend: bool = False)
  -> {id, url, started}
```

Pushes `state` into every live frame of the artifact, changes the caption, or
with `resend` snapshots the edited draft again and swaps the document, through
the same checks and probe as a send, with the state carried over unless
`state` is also given. Refused with `not_live` once the artifact is submitted
or closed.

```
artifact.close(id: str, label: str | None = None) -> "closed"
```

The agent withdraws a live artifact. The card collapses to `label`, or to
"closed by the agent". A draft that was never sent is closed the same way and
leaves no record.

## Getting the first version right

Three layers, cheapest first.

**The skeleton is tested.** The browser tests fill the draft `artifact.create`
wrote, replacing only its two placeholder lines, so the real skeleton runs in
CI on every push and goes red if it stops working. With `artifact.css` doing the styling, an agent that starts from it
and only adds its controls has little to get wrong. The skeleton:

```html
<!doctype html>
<html lang="en">
<meta charset="utf-8">
<title>Pick a layout</title>
<link rel="stylesheet" href="/static/css/artifact.css">
<script src="/static/js/artifact.js"></script>
<body>
<h2>Pick a layout</h2>
<!-- controls go here -->
<script>
  aegis.ready((state, theme) => {
    // wire the controls; answer with aegis.submit / aegis.emit / aegis.state
  });
</script>
</body>
</html>
```

**Static checks at send.** Before anything is served: a draft that does not
load `/static/js/artifact.js` is refused with `no_script`, and one whose text
calls none of `aegis.submit`, `aegis.emit` or `aegis.state` with `no_answer`,
each with a one-line hint. These are the two mistakes that leave a page that
looks fine and can never answer; the unedited skeleton fails the second one on
purpose, so its comment names no call. Nothing else is linted: browsers parse
anything, and external scripts such as d3 from a CDN are allowed.

**The probe.** The server publishes an `artifact.probe` request on the
session's transcript channel, carrying the snapshot's URL and a probe id. Every
browser with the session open mounts the page in a hidden sandboxed frame,
outside the transcript, and answers `artifact.probed` with the outcome of
`ui/initialize`: started, or the first error the page raised before it, with
its message and stack. The first answer wins; later ones and answers to an
unknown probe id are dropped; the frame is removed either way. The server waits
three seconds. The page posts `ui/initialize` after its inline scripts ran, so
an error in them arrives instead of the handshake; an error inside `ready`
arrives just after it, so the browser waits a short grace (250 ms) after the
handshake before reporting started. Started: the record is written and the
tool returns. Error: the
snapshot is deleted, nothing is recorded, and the tool raises `page_error` with
the message and the first five lines of the stack, so the agent sees
"ReferenceError: d3 is not defined at index.html:14" in its own turn and fixes
the draft before it asks the person anything. No browser open, or none answered within the three seconds (a background tab
the browser throttles): nothing was proved, the record is written with
`started: null`, and the first browser to mount it reports an error, if any,
to the inbox.

The failed sends show as the harness's own tool rows with an error mark, which
is the right trace of the iteration; the transcript shows one card.

## The page

The page talks to `window.aegis`:

- `aegis.ready(fn)` runs `fn(state, theme)` once the host has answered the
  handshake. `state` is the agent's initial state, or the latest one after a
  reload. `theme` is the map of CSS variables.
- `aegis.state(obj)` replaces the artifact's state. Silent. The script
  coalesces calls within one animation frame; the server coalesces further.
- `aegis.emit(name, obj)` wakes the agent with the event and leaves the
  artifact live. `name` is one word, `[a-z][a-z0-9_-]*`, at most 32
  characters, and never `submit`, `error` or `close`.
- `aegis.submit(obj, label)` wakes the agent with the answer and ends the
  artifact. `label` is one line of at most 140 characters, which the script
  enforces by cutting (so a long label never makes Submit silently fail); it
  is what the collapsed card shows.
- `aegis.onState(fn)` runs `fn(state)` each time the agent pushes state with
  `artifact_update`.

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
page that keeps the skeleton's link looks like a part of aegis without writing
CSS. A page that wants its own look drops the link.

## The bridge

`client/js/artifacts.js` holds one `message` listener on `window` and a map
from a frame's `contentWindow` to `{log_id, artifact_id, frame}`, probe frames
included. A message is accepted only when `event.source` is a mapped window;
that binds every message to one artifact, so a page can never name another.
The page posts to `*`, because its origin is opaque and it cannot know the
host's; the host posts back to the frame's `contentWindow` with target `*` for
the same reason, and the content of those messages is state, theme and status,
nothing secret.

Messages, JSON-RPC 2.0:

| From | Method | Params | Answer |
|---|---|---|---|
| page | `ui/initialize` | `{}` | `{artifact, state, theme, status}` |
| page | `aegis/state` (request) | `{state}` | `"ok"`, or the refusal as the error |
| page | `aegis/emit` (request) | `{name, data}` | `"ok"`, or the refusal as the error |
| page | `aegis/submit` (request) | `{data, label}` | `"ok"`, or the refusal as the error |
| page | `aegis/size` (notification) | `{height}` | |
| page | `aegis/error` (notification) | `{message, stack}` | |
| host | `aegis/state` (notification) | `{state}` | |
| host | `aegis/theme` (notification) | `{theme}` | |
| host | `aegis/status` (notification) | `{status}` | |

`ui/initialize` keeps MCP Apps' name because it is the one message both
protocols have and the one a compatibility shim would start from; the others
are aegis's. Nothing in the bridge is a tool call: the page reaches the server
only through the person operations below, with the artifact id the host holds,
never one the page sent. In a probe frame the bridge answers `ui/initialize`
with status `probe`, forwards the first `aegis/error` as the probe's outcome,
and drops everything else.

The theme map is read once per switch from `getComputedStyle` on the page's
root, for the variables the base stylesheet declares, and pushed to every
mounted frame. The size cap is 70 % of the viewport's height, after which the
frame scrolls inside.

## The person operations

Five operations in `app.py`, callable from the websocket and not by agents,
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
- `artifact.error(message, stack)`: wakes the agent once per landed page
  (a resend re-arms it); later errors are counted, never delivered, and the
  count is in `artifact.read`'s `errors`.
- `artifact.probed(probe_id, started, message?, stack?)`: the outcome of a
  probe frame; answers to a probe the server no longer waits on are dropped.

Each is refused with `no_artifact` for an id the session does not have, and
`not_live` for a submitted or closed one, except `error`, which a read-only
frame may still raise.

## Records and the fold

Five aegis records, `src: aegis`, in the session's store. A draft is not a
record: it is a file under `<state>/artifacts/<id>/` and an entry in the
session's in-memory map, so a draft never sent leaves nothing in the
transcript, and a restart forgets it (the board is rebuilt from the fold,
where a draft never was). The folder is never wiped at boot: it also holds
the working copy of every landed page, which a resend after a restart reads.

| Record | Fields | Effect in the fold |
|---|---|---|
| `artifact` | `artifact_id, file_id, name, caption, state, started` | creates the entry, status `live` |
| `artifact_state` | `artifact_id, state, by` (`page` or `agent`) | replaces the entry's state |
| `artifact_event` | `artifact_id, name, data` | appends to the entry's events, keeping the last 20 |
| `artifact_submit` | `artifact_id, data, label` | status `submitted`, sets `submitted` and `label` |
| `artifact_close` | `artifact_id, label` | status `closed`, sets `label` |

A resend writes a second `artifact` record with the same `artifact_id` and the
new file; the fold swaps the document and keeps status, events and state (a
second `artifact` record never touches state). A caption change alone is the
same record with the old file. The entry's events travel as lazy detail
(`transcript/wire.py`); its state rides the wire with the row, because the
frame needs it the moment it mounts and it is capped at 64 KB. The row also
carries status, label, caption, name and URL. Every
decision about the card is made in the fold: the preview is always `html` and
the frame's sandbox is always `allow-scripts`, so the client reads neither from
the file.

State coalescing lives in `Session`: a page's write replaces an in-memory
latest and marks it dirty; a record is written when one second has passed
since the last state record, and always before an `artifact_event`,
`artifact_submit`, `artifact_close` or an agent's update, and at shutdown. An
`artifact.read` answers from memory, so it is never behind. The fold therefore
sees every write that mattered and at most one a second of the rest; a reload
or a refold agrees with the live view at every record, which the delta tests
check at every cut as they do for every other kind.

The in-memory latest, the drafts, the pending probes and the error-per-turn
set live on the `Session` and are not in the meta: boot reads only metas, and
after a restart the latest is the last record, which is at most a second old.

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
puts `error` there, then the message and the first five lines of the stack;
the bridge forwards only the first error a mounted frame raises, and the server
wakes only once per landed page.
The body is a code block so the agent reads it as data; labels and data are
never rendered as HTML anywhere.

## Telling the agent

The primer gains one paragraph after the `file_send` one. It says when to send
an artifact instead of asking in prose: a choice among things that have to be
seen, values that have to be tuned, a question whose answer has structure, an
explanation that is better played with. It gives the order of work: create,
edit the draft, send until it lands, and only then write the message, which
refers to the card above it, and end the turn with `turn_end(needs_you)` when
the artifact asks something. The caption is the one line the person reads
before the card, and a lesson that needs its text beside its controls puts the
text inside the page. It names the four calls and the header the answer
arrives under. The prose comes after the card because it is written once, when
the page is proven, and nothing written before a failed send is wasted.
`artifact_create`'s docstring carries the four calls, so an agent that has only
the tool list can still write a page; `meta` lists the tools as it does today.
Both harnesses get everything through MCP.

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
state when it comes back. A patch to a live artifact's row updates the row in
place and keeps its frame (`transcript.js` asks the renderer first), because a
remount would reload the page on every state write; only a change of status
or caption remounts. A patch with a new state from the agent is pushed to the
frame; a patch whose state came from a page is not. `artifacts.js` finds a
frame by the artifact id its row carries, mounts and removes probe frames on
`artifact.probe`,
and `app.js` tells it on a theme switch.

## Security

Unchanged from `file_send`: the document is served with
`Content-Security-Policy: sandbox allow-scripts` and framed with
`sandbox="allow-scripts"`, so its origin is opaque and the websocket's origin
check refuses it. New: the page reaches the server only through the bridge,
only through the person operations, only for the artifact the host bound its
window to. The server caps every payload and rate-limits emits. Agent text is
untrusted as before: captions render as Markdown with raw HTML off, labels and
data as text and code. The draft file is written by the server under the state
root and read back only through the static checks and the snapshot; its name
is fixed, so no path from the agent is ever opened.

## Tests

- `tests/test_artifacts.py`: `create` writes the skeleton and returns it; the
  five records fold to the entry at every cut and the delta equals the fold
  (the `test_wire.py` fixtures gain an artifact scenario); a resend swaps the
  file and keeps the rest; coalescing writes one record a second and one
  before each boundary; the static checks (`no_script`, `no_answer`); a failed
  probe deletes the snapshot and records nothing; no browser gives
  `started: null`; refusals (`too_large`, `bad_name`, `rate_limited`,
  `not_live`, `no_artifact`); one error message per artifact per turn; the
  header and body format; `artifact.read` answers from memory; a draft never
  sent is gone after a restart.
- Browser (`tests/test_browser_*.py`, headless Chromium against a real
  `aegis serve`): the fake claude creates an artifact, the test fills the
  draft from the skeleton with a button, sends, sees the probe pass and the
  card appear; clicks inside the frame, reads the inbox turn the fake claude
  receives, sees the card collapse to the label, reloads and sees the same
  card, switches the theme and sees the frame's variables change; a draft
  with a thrown error at load fails the send with `page_error` and shows no
  card.
- `make test-live`: a real Claude session asked to offer two layouts in an
  artifact and to report the pick.
- `make bench` in the PR body, as for every change.

## Out of this slice

The docked canvas and its native edits (text in spans, moving layout pieces),
with its versioned layout, is slice 2. Also out: two people editing one live
artifact at once (last writer wins here), MCP Apps compatibility, files from
the person to an agent, an agent reading another session's artifacts,
artifacts on the Fleet view, and a server-side headless browser for the probe
when no browser is open.
