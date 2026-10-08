# aegis on a phone: a lighter wire, a narrow layout, an installable app

**Status: slices 1 and 2 built, 2026-10-08** (the quota fix in #192, the lighter wire for #189); slices 3 to 5 designed, not built. Designed with Alex in a brainstorm
with screenshots of the real client at phone width. The measurement scripts, the
prototype stylesheet and the screenshots are in the workspace playground, not in
this repo: `.playground/aegis-mobile/measure.py` and `cuts.py` (wire),
`narrow.css` and `proto.py` (layout), `two.py` (two devices on one server).

## What this delivers

Alex uses aegis from his phone mostly to triage: see which sessions need him,
read the last turn, answer with a tap or a short message. Sometimes he
brainstorms or researches from it. The phone reaches the VPS's aegis over mobile
data, from Chrome on Android, installed on the home screen. iPhone support waits
for someone to ask.

After this work:

- Returning to a tab, or to the app after the phone put it in the background,
  costs only the turns that happened meanwhile, not the whole transcript again.
- A transcript snapshot carries no tool output, tool arguments or thinking text
  until a row is opened, which is 75% of its bytes today.
- Below 760 px the same page wraps: tabs on their own row, the side panel as a
  drawer, the Fleet band in one column, 44 px touch targets.
- aegis installs from Chrome's menu, opens without a token in the URL, and is
  protected by its own token alone, with no proxy password in front.

The gain on the wire is the same for every browser, desktop included: it is one
client and one socket.

## What was measured

### The wire

All 80 transcripts in the workspace state, folded read-only with
`fold_records`. Sizes are JSON bytes before compression. The socket compresses
(uvicorn runs `websockets` with permessage-deflate on), and zlib brings these
snapshots to 28% of their raw size.

| | median | p90 | max | max compressed |
|---|---|---|---|---|
| Snapshot today | 58 KB | 518 KB | 846 KB | 233 KB |
| Without tool args, tool tails and thinking text | 16 KB | 144 KB | 393 KB | 108 KB |
| Only the last 100 entries | 58 KB | 93 KB | 131 KB | 37 KB |
| Both | 16 KB | 34 KB | 79 KB | 24 KB |

Where the bytes go, over all 10,861 entries: `detail.tail` 42%, `detail.args`
32%, `md` 12.5%, everything else under 3% each. Tool entries are 8.9 MB of the
11.1 MB. Patches replayed record by record add up to 17.3 MB, more than the final
snapshots, because a tool entry is upserted again when its result lands.

In headless Chromium against an isolated server, opening a tab received 326 KB,
switching to a second tab 330 KB, and switching back to the first 326 KB again:
`follow()` in `js/app.js` unsubscribes and resubscribes, and every subscribe is
a full snapshot. The phone drops the socket whenever the app goes to the
background, and the reconnect resubscribes the same way.

### The layout at 390 px

Headless Chromium at 390 × 844 with a touch profile, and at 820 and 1366 px:

- The session tabs had zero width: `.tablist` shrinks to nothing next to the
  Fleet tab, the server badge and the theme picker, which cover it. A tap on a
  tab cannot reach it.
- Below 760 px `.side` is `display:none` and nothing opens it, so context, cost,
  monitors, Stop and Close are unreachable.
- The quota rows break at any width under 1100 px, laptops included:
  `#a2 .band .q{grid-column:1/-1}` (`css/base.css:372`) was meant for the quota
  column, and every quota bar also has the class `q`, so each bar spans the row
  and its percentage drops under it, cut off on the left.
- `#a2{height:100vh}` measures past Chrome's URL bar on a phone.
- A reply chip is about 29 px tall; Android's guideline is 48 dp and Apple's
  44 pt. A tap sends the chip at once.
- The transcript itself reads well at 390 px. The 820 px tablet layout works.

### Two devices on one server

`tests/test_web.py::test_two_clients_see_the_same_patches` covers two sockets.
In the browser, a laptop page (1366 px) and a phone page (390 px, touch) on one
isolated server with the fake claude passed all seven checks of `two.py`: the
phone sees a tab the laptop spawned, each sees the other's messages live, the
phone sees a turn the laptop started as working and interrupts it, and Close on
the phone removes the laptop's tab. Drafts and tab order stay per browser, as
DESIGN.md says. Nothing needs to change for a second device; a browser test of
this shape joins the suite.

### PWA

The client has no manifest and no service worker. It keeps the token in
`sessionStorage`, which a launch from the home screen does not have, so an
installed app would open on "No token". dev.apiad.net sits behind Caddy's
`basicauth` as well as the token.

## Decisions

| Question | Decision | Why |
|---|---|---|
| What the phone is for | Triage first, some brainstorming and research | Alex's answer. The Fleet's needs-you order and the reply chips matter most |
| How to cut the wire | Collapsed detail is fetched on open, and a resubscribe resumes from a revision (option A) | Resuming fixes the cost paid dozens of times a day, on every return from the background. A windowed snapshot (B) and an IndexedDB cache (C) wait for a measurement on the phone |
| Which detail is lazy | Everything a closed `<details>` shows: tool args, tool tails, diffs, thinking text, a user entry's template, a system entry's tail | It is the part nobody reads until they open the row |
| Tool rows that failed | Start closed, like every tool row | Alex's call. The red status and the one-line result on the closed row show the failure |
| Phone layout | The desktop markup, adapted by CSS below 760 px, plus a drawer button | Alex wants the desktop experience and no second set of HTML. A prototype of about 45 lines of CSS covers every screen |
| Switching sessions on a phone | The existing tab row, wrapped onto its own line and scrolled sideways | Follows from the decision above |
| The side panel on a phone | The existing `<aside>` as a drawer from the right, opened by ☰ | Same markup, every field reachable |
| Touch targets | 44 px under `(pointer: coarse)`, at any width | A touch laptop gets them without the phone layout |
| Enter on a touch screen | Adds a line; the send button sends | A mistapped Enter sends half a message, and that costs a turn |
| Interrupt | An icon-only button next to send, shown while a turn runs; the text Stop under the composer goes away | Alex's call. A phone has no Esc |
| Side panel actions | Stop (ends the process), Restart (sends "Continue"), Close | Alex's call. A message to a stopped session already resumes it |
| The lock | aegis's token alone, as an HttpOnly cookie; Caddy's password comes off dev.apiad.net | Alex's call. One lock, and the installed app needs no URL token |
| Service worker | None until push | Chrome installs from its menu with a manifest alone since Chrome 108 on Android. A caching worker is the likeliest way to serve a stale client after a deploy |
| Confirmations and alerts | aegis's own dialog, never `confirm()`, `alert()` or `prompt()` | Alex's call. A native dialog looks foreign in the installed app and blocks the page; the client calls `confirm()` twice today, both before Close |
| Push notifications | A playground spike after the rest is deployed, then its own spec | Alex wants the design decided on working code |

## Design

### 1. The lighter wire

**What crosses for an entry.** The fold still computes every entry in full, so
Python still decides everything about it. The transcript channel sends a
projection: the entry without the fields a closed `<details>` shows, plus
`detail.more: true` when it dropped any, plus `rev`. One function in
`transcript/` makes the projection, and both the snapshot and the `upsert`
patches go through it, so the live stream shrinks with the snapshot.

**`rev`.** Every entry carries `rev`, the store index `i` of the record that last
changed it. Revisions come from the store, so a refold and a server restart give
the same numbers.

**Fetching detail.** A new operation, `transcript.detail(log_id, ids)`, returns
the full detail of the named entries from the in-memory fold. The client calls
it when a row opens, through a click, Enter or expand-all, and caches the answer
by entry id and `rev`. An upsert with a newer `rev` for an open row fetches
again, which is what happens when a tool's result lands while its call is open.

**Resuming.** A `sub` message may carry `since`. The transcript channel answers
`since=R` with a snapshot marked as a delta: the projected entries whose `rev` is
above R, and the ids removed after R. The fold keeps a log of removals with the
record index that caused each, for any entry id: pending prompts are replaced by
their echo, and OpenCode's streaming entries are dropped when the turn's final
text arrives (`_drop_live`). A `since` above the store's last index gets a full
snapshot. Other channels ignore `since`, so `sessions`, `quota` and `host` are
unchanged, and the protocol gains one optional field that any channel may
honour, which keeps "the client knows no subsystem by name".

**The client.** `Transcript` keeps the entries and the highest `rev` of the last
8 tabs visited in this page load, and drops the least recently used. `follow()`
and the resubscribe after `welcome` send `since`. A delta is applied as one batch
of upserts and removes, so the mounted rows and the scroll position stay. A gap
in patch numbers resubscribes with `since`.

**The invariant.** DESIGN.md's "The patches add up to the entries" gains a second
clause: a delta from any revision R, applied to the entries as of R, equals a
fresh fold. The session tests already check the first clause after every
scenario, and they check the second from several cut points.

**Left out.** A windowed snapshot with paging (B), a persistent IndexedDB cache
(C), and fetching untruncated tool output: an opened row shows the same 40-line,
8 KB tail it shows today.

### 2. The narrow layout

One block of CSS below 760 px, one block under `(pointer: coarse)`, and a few
lines of JS. No element changes shape on desktop.

**Header.** `.tabs` wraps. Fleet, +, the server dot and the theme picker stay on
the first row; `.tablist` takes the second row at full width and scrolls
sideways. Tabs hide their handle and cut their title at 16 characters, so three
fit at 390 px. The `?` button and the version tag hide: there is no keyboard to
explain, and the version is in the drawer.

**Fleet.** `.band` becomes one column and the gauges `62px minmax(0,1fr)
max-content`. The cards are already one column at that width.

**Session.** The transcript takes the full width. A ☰ button, shown only in the
session view, toggles `data-side=open` on `#a2`; the `<aside>` becomes a fixed
drawer from the right, `min(330px, 88vw)` wide, over a dimmed transcript.
Tapping the dimmed part, tapping ☰ again, or switching tabs closes it. Monitor
cards already open on click. Reply chips stack one per row.

**Composer and side panel actions, at every width.** The text Stop button under
the composer goes away. Next to send sits an icon-only interrupt button (■),
shown while a turn runs, calling `session.interrupt` as Esc does. The side panel
has Stop (`session.stop`, as today), Restart, which sends "Continue" through
`session.send`, and Close.

**Touch, under `(pointer: coarse)`.** Reply chips, send, interrupt and buttons at
least 44 px; tabs and tool row summaries at least 40 px. Enter in the composer
adds a line, and send sends. The new-session form keeps Enter as it is, because
it already has its own send button and a one-line first message is common.

**Dialogs, at every width.** A small module, `js/dialog.js`, draws a confirm
dialog in the page the way the `?` key map is drawn: a panel over a dimmed page,
the question, and two buttons. It returns a promise of true or false; Enter
confirms, Esc and a tap outside cancel, and under `(pointer: coarse)` the buttons
are 44 px. The two `confirm()` calls before Close (`js/app.js:778` for `/close`
and `:896` for the button) use it. A test, next to `tests/test_imports.py`, fails
if any file in `client/js/` calls `confirm(`, `alert(` or `prompt(`, so the rule
holds without anyone remembering it; `.rift.yaml` is not the place, because CI
cannot run rift.

**Plumbing.** `100vh` becomes `100dvh`. The viewport tag gets
`interactive-widget=resizes-content`, so Chrome on Android shrinks the page when
the keyboard opens and the composer stays above it. Padding respects
`env(safe-area-inset-*)`. The new-session form sits at the top on a phone.

**The quota fix.** `.band .q` becomes `.band > .q`. It ships first, alone,
because laptops hit it today.

**Interaction with session attention slices 2 and 3.** The unread navigator pill
in `2026-10-08-session-attention-design.md` sits at the bottom right with the
jump pill. On a phone both must clear the stacked reply chips, which the jump
pill already does through `#a2 .col:has(#replies:not([hidden])) .jump`.

### 3. The installable app and the lock

**The cookie.** `GET /?token=<t>` is handled by the server: a valid token sets an
HttpOnly, `SameSite=Strict` cookie with a one-year `Max-Age` and redirects to
`/`. The cookie is `Secure` when the request came through an `--origin` that is
https. Cookies are not scoped by port, so two servers on one host would overwrite
each other's cookie; its name carries a short hash of the state root. The socket
handshake accepts the cookie; a `hello` carrying the token still works, for the
tests, the fake claude and the bench. The client no longer reads or stores the
token.

**Logging in.** A browser without a valid cookie gets the page with one field,
"Paste the token", which posts to `POST /login` and gets the same cookie. A
rotated token file invalidates every cookie and brings the field back.

**Sent files.** `files.py` serves HTML and SVG sandboxed because their script
could read the token from `sessionStorage`. No script can read an HttpOnly
cookie, and a sandboxed frame has an opaque origin, so the socket's origin check
refuses it even though the browser would send the cookie. The sandbox stays.

**The manifest.** `/manifest.webmanifest`: `display: standalone`, `start_url:
/`, a name that carries the server ("aegis · vps"), Ink's background and theme
colours, and 192 px, 512 px and maskable icons drawn in code from the ⬡ glyph.

**dev.apiad.net.** After the release that carries this slice, the `basicauth`
block comes off the Caddy site and the `/mcp` 404 stays. The Caddyfile diff is
shown to Alex before `sudo` applies it.

### 4. Push

A spike in `.playground/` once slices 1 to 3 are deployed, on Alex's phone
against the VPS, and then its own spec. The brainstorm's starting point: push on
a transition into `needs_you` or `error`, tagged by `log_id`, suppressed by the
service worker when a focused window shows that session, opted into per device
from the Fleet band, encrypted with `cryptography` (RFC 8291) and sent with
httpx, and a service worker that handles `push` and `notificationclick` and
caches nothing.

## Slices

Each is an issue, a worktree from `origin/main` and a PR that meets AGENTS.md's
definition of done.

1. **The quota fix.** One selector, a browser test at 1000 px that no gauge
   child sits outside its row.
2. **The lighter wire** (section 1), and **the narrow layout** (section 2), in
   parallel. They share only `js/app.js`; the second to merge rebases.
3. **The cookie lock and the manifest** (section 3).
4. **Deploy to dev.apiad.net**: a release, the Caddy change, and Alex's phone over
   mobile data. Here we measure whether option A is enough or whether the
   windowed snapshot is worth adding.
5. **The push spike**, then its spec.

## Testing

- **Wire.** Session tests check that a delta from several cut points, applied to
  the entries at that point, equals a fresh fold, including removals of pending
  and streaming entries. A browser test opens a tab, switches away and back, and
  asserts the second visit received under a few KB. `transcript.detail` returns
  what the fold holds. `scripts/bench.py` gains two rows, snapshot bytes and
  return-to-tab bytes.
- **Layout.** A browser test at 390 × 844 with touch taps a tab, opens and closes
  the drawer, taps a reply chip, checks that Enter adds a line, and confirms a Close
  through the aegis dialog, and asserts no element is wider than the viewport, the check that would have caught both the
  zero-width tabs and the quota rows. The two-device check from `two.py` becomes
  a browser test.
- **Lock.** A browser without a token sees the login field, pastes the token,
  reloads and stays signed in; a second server on another port keeps its own
  cookie; a wrong cookie gets 4401 on the socket; the manifest and its icons
  load with the right content types.
- **Not covered by tests.** The phone itself: installing from Chrome's menu, the
  keyboard resizing the page, and the bytes over mobile data are checked by hand
  in slice 4 and reported as checked by hand.

## Rules this adds to DESIGN.md

- A delta from any revision, applied to the entries as of that revision, equals a
  fresh fold.
- The wire carries no collapsed detail; a row fetches it when it opens.
- The browser holds the token only as an HttpOnly cookie.
- The client asks through its own dialog, never the browser's.
