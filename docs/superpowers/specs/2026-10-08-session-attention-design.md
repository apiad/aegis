# aegis: what a session needs from you, what it did, and what you have read

**Status: slice 1 implemented, 2026-10-08** (issue #171), following
`docs/superpowers/plans/2026-10-08-session-attention-slice-1.md`. Slices 2 and 3
are designed, not built. Designed with Alex in a brainstorm
with mockups, rendered on the client's own CSS from `main`. The approved screens
are in the workspace playground, not in this repo:
`.playground/aegis-recap-ui/src-transcript.html` (transcript, recap, read marks,
navigator), `.playground/aegis-recap-ui/src-glyphs.html` (Fleet cards, band,
order switch, glyphs; style B was chosen) and
`.playground/aegis-recap-ui/src-replies.html` (reply pills on the composer). Replaces, for aegis 2, the legacy
specs `2026-09-17-aegis-turn-attention-design.md` and
`2026-09-17-aegis-unified-recap-design.md`.

## What this delivers

With seven tabs open, a person can see which sessions are waiting on them, which
broke, which are waiting on CI, and which finished, without opening any of them.
Each tab and Fleet card carries one of six status marks. The agent says, in its
own words, what it is doing, what it did, and what it needs from you. When you
come back to a tab after a while, the last row of the transcript is a two-line
recap of where things stand, every agent message you have not read carries a
mark, and a navigator in the corner walks the agent messages and skips the rest.
When the agent asks a question, up to three replies it wrote in your voice sit on
the message box, and one click sends one.

The legacy tree paid a Haiku call on every turn, and another every few seconds
while a turn ran, to produce these lines. Here the agent reports its own plan
and its own turn end through two aegis tools, the server derives everything else
from facts it already holds, and Haiku runs only when a person lands on a tab
with a long unread stretch.

## Decisions

| Question | Decision | Why |
|---|---|---|
| Where "doing" and "did" come from | The agent's own `plan_update` calls | Claude Code no longer makes TodoWrite calls here (0 in the last 300 transcripts, #171), and a summariser guessing at intent cost $0.016 a turn in the legacy tree |
| Where "needs you" comes from | The agent's own `turn_end` call | Only the agent knows whether its message was a question. A model reading the transcript afterwards was the legacy answer, and it paid for every turn |
| Error, waiting, working | Mechanical, from facts the server holds | They are facts, so a model or an agent can only get them wrong |
| How hard aegis pushes the agent to report | The priming only, measured | A Stop hook would guarantee it but costs a model step every time the agent forgets, and only works for Claude Code. Revisit if `make test-live` and a week of use show poor compliance |
| When Haiku runs | When a person lands on a tab with a long unread stretch, or asks | A recap is for a person who was away. Nobody reads a recap of a turn they watched |
| Where the recap shows | The last row of the transcript | It is the first thing on screen when the tab opens |
| What "seen" belongs to | The server, shared by every browser | Reading a session on the phone should stop it pinging on the laptop |
| When a message counts as read | When it has been on screen for a second in a focused, visible tab | Opening a tab does not mean reading a long reply |
| What a read mark covers | Agent messages only (`prose` entries) | Tool calls, thinking and inbox rows are not what a person reads |
| Read mark style | A ● in the right margin when unread, a faint ✓ when read | Chosen from three mockups |
| Navigator | A pill at the bottom right: up, "2 unread · message 3 of 4", down, to latest | Chosen over a vertical stack |
| Fleet card order | A switch in the Fleet: "Needs you first" (default) or "Tab order", kept per browser | Alex wants both; the choice is a view preference like the tab order |
| Reply suggestions | Up to three, written by the agent in its `turn_end` call, shown as pills on top of the message box; a click sends the reply | The agent that wrote the options knows them, so a suggestion costs nothing extra. The legacy Haiku suggester guessed answers to open questions in 9 of its 13 suggestions |
| Glyphs | Inline SVG, filled badges, drawn in `currentColor` | Unicode marks render differently in each font; style B was chosen over an outline set |

## The status of a session

Every session has one **attention** value, computed in Python and sent on its
card. The first rule that holds wins:

1. **working**: `status` is `working`, or held messages are being flushed.
2. **error**: the last turn ended with `Result.is_error`, the status is `error`
   (an interrupt went unanswered), or the process exited mid-turn.
3. **needs_you**: the agent's `turn_end` for the last turn said `needs_you`.
   A queue worker's `needs_you` counts as `done`, because its answer goes to
   whoever enqueued it.
4. **waiting**: the session has a live monitor, an open Claude task, a held
   message, a queue task it enqueued that has not finished, or a session it
   spawned that is itself working or waiting, so a wait carries up any depth
   of `session_spawn` (#183). The card names what it waits on
   ("CI, 1 monitor").
5. **review**: the agent's `turn_end` said `review`.
6. **done**: everything else, including a turn that ended with no `turn_end`.

This order follows the legacy tree's, with one change: the agent's
`needs_you` comes from the agent, not from a classifier.

The rule is a function of the session, its fold and the monitor and queue
registries, evaluated whenever one of them changes, so a monitor that ends moves
a card from waiting to done without a new turn. A turn the harness starts on its
own, such as a Monitor wake, ends with a `result` like any other and is treated
the same way. That was legacy #111.

### When a mark shows

| attention | tab and card mark | clears |
|---|---|---|
| working | the spinning badge | when the turn ends |
| error | the error badge | when the next turn starts |
| needs_you | the needs-you badge, blinking while its message is unread and the tab is not focused | when a person sends to the session |
| waiting | the waiting badge | when the wait ends |
| review | the review badge | when its message is read |
| done | the done badge | when its message is read |

After a review or done mark clears, the tab shows the plain idle dot. A
needs_you mark stays after it is read, because the session is still blocked on
you; it only stops blinking.

## The agent's two tools

Both are operations marked for agents, so they reach Claude as
`mcp__aegis__plan_update` and `mcp__aegis__turn_end`. "Task" already names a
queue task in aegis, so the tracker is called a plan.

**`plan.update(items)`.** The session's whole plan, every call: a list of
`{text, state}` with `state` one of `pending`, `doing`, `done`, at most one
`doing`. The whole list each time is the shape TodoWrite had, which models
already use well, and it makes every call idempotent. Text is cut to 120
characters per item and 30 items.

**`turn_end(attention, line, replies)`.** Called when the agent hands the turn
back. `attention` is one of `needs_you`, `review`, `done`; `line` is one sentence
of at most 140 characters saying what the person must answer or read, or what got
done. `replies` is zero to three suggested next messages, each one line of at most
80 characters, written as the person would type them. The agent decides all of
this; no model second-guesses it. A queue worker's replies are dropped, because
nobody reads its tab.

Each handler validates its params and appends an aegis record to the session's
store: `{"kind": "plan", "items": [...]}` and
`{"kind": "turn_end", "attention": ..., "line": ..., "replies": [...]}`. The fold reads them like
any record aegis writes, so the plan and the last report survive a restart and a
refold gives the same card. The tool calls themselves still render as tool rows.
A `turn_end` belongs to the turn it was called in: the fold drops it when the
next prompt or inbox message is echoed.

The card gets three lines from the plan: **now** is the `doing` item, **did** is
the most recently finished item, and **plan 3/4** counts done over total. A
working card keeps today's live activity line (`Fold.activity()`, the running
tool call) under them. A card with a `needs_you` or `review` report shows its
`line` under the title. An error card shows the error from the store instead.

### The priming

The primer in `mcp.py` gains a paragraph, worded for the agent:

> Every turn that hands control back to the person ends the same way, in this
> order: first a call to turn_end, then your final message. No exceptions: a
> turn where you only answered or asked a question, or where the person told you
> not to run anything, still ends with turn_end, because turn_end runs nothing;
> it only labels your message on their screen. If its schema is not loaded, load
> it and plan_update with ToolSearch
> `select:mcp__aegis__turn_end,mcp__aegis__plan_update`. Pass `needs_you` when
> your message asks them anything, `review` when it gives them something to
> read, or `done` when it reports finished work, with `line` as one sentence
> saying what they must answer, what to read, or what got done. Example, after
> offering two options: turn_end(attention="needs_you", line="Ship the release
> now or wait for the review?", replies=["ship it", "wait"]). The only turns
> without turn_end are those you end to wait on a monitor or a queue task. For
> any work that is not obvious, also keep a plan with plan_update: send the
> whole list each time, mark one item `doing` while you work on it and `done`
> when it is finished.
>
> turn_end also takes up to three `replies`: messages the person might send
> next, written as they would type them, in the language they write to you in,
> lowercase and without a final period. Offer them when you laid out options, or
> when you proposed one thing and wait for a go-ahead (then a reply is their way
> of saying yes). Leave them empty when you asked an open question with many
> possible answers, or when you report finished work. An empty list is better
> than a wrong guess.

The reply rules are the findings of the legacy probe over 40 real turn ends
(`legacy/aegis/recap/__init__.py`, `SUGGESTION_RULE`): Alex's replies start
lowercase and end without a period, assent was the case the old suggester missed,
and invented answers to open questions were most of what it offered.

Compliance is measured, not assumed: `make test-live` runs a real Sonnet session
through a question turn, which must end in `turn_end` with `needs_you` and one to
three replies, and a work turn, which must end in `turn_end` with `done`. Not yet
measured live: `plan_update` compliance, a wait turn, and an open question that
offers no replies (#178). On 2026-10-08
Opus and Sonnet called turn_end on 6 of 6 question and work turns with this
wording, Haiku in about 1 of 3, so the live test runs on Sonnet. The PR that
lands the tools reports the rate over a week of Alex's sessions.

## What you have read

**Storage.** The meta gains `read_floor`, a store index: every agent message
before it is read. A message read out of order is kept in `read_ids` until the
floor passes it, so the set stays small. A session that predates this feature
starts with its floor at its current end, so nothing old turns up unread.

**Reading.** The client watches the mounted `prose` rows. A row that has been at
least half visible for one second, while the page is visible and focused, is
sent in a batched `session.read(log_id, ids)` call. That operation is for people
only. The server moves the floor, writes the meta, and publishes.

**What crosses the wire.** Python decides whether a message is unread. Each
`prose` entry carries an `unread` flag when it is published, and a read sends
upserts for the entries whose flag changed. The card carries `unread`, the
count. A browser that reloads gets the same flags from the snapshot.

**The transcript** shows:

- the margin mark on every agent message: ● in the accent colour when unread, ✓
  faint when read;
- an accent divider before the first unread entry: "new since you left · 42 min",
  measured from the last read on any browser. It stays where it is while the tab
  stays open;
- the navigator pill at the bottom right, replacing `↓ latest`: up and down move
  between agent messages, the middle reads "2 unread · message 3 of 4" and jumps
  to the first unread, and the last button goes to the latest entry.

**Keys**, added to the table in `js/keys.js` so `?` lists them: Alt+↑ and Alt+↓
move between agent messages, Alt+U goes to the first unread. Chrome on Linux
leaves these free; the plan checks them against
`chrome/browser/ui/accelerator_table.cc`, as the existing chords were.

## Reply pills

The card carries `replies` from the current `turn_end`. The session view shows
them as pills on top of the message box, under a faint "reply" label, in the order
the agent gave them. A click sends the pill's text through `session.send`,
exactly as if it had been typed, so the transcript and the agent see an ordinary
message. The pills disappear when anything is sent: the fold drops the replies at
the next `send` record, and the client hides them at once without waiting for the
patch. While pills are showing, the navigator sits above them.

Pills are click-only in this design. Chrome on Linux keeps Alt+1…9, so a key for
them needs a chord of its own, and none was chosen.

## The recap

**When.** A browser that focuses a tab calls `recap.request(log_id)`. The server
answers with nothing to do unless the session is idle or stopped, it has unread
agent messages, and the unread stretch is long: at least 2 unread messages, or
one longer than 300 words, or a last read more than 30 minutes ago. Those
thresholds are constants in the recap module, to tune with use. A sparkle button
in the navigator asks for one regardless.

**What it says.** Two fields, in the language of the person's own messages:
`context`, one sentence on what the session was doing, and `ask`, one sentence on
what it needs from you, empty when nothing. The prompt is the legacy one
(`legacy/aegis/recap/__init__.py`, `SYSTEM`) cut down to these two fields. It
reads the transcript from the last user message or the read floor, whichever is
earlier, within 3,000 tokens, plus the agent's own `turn_end` line and the plan,
which it is told to trust over its own reading.

**How it is paid for.** `.aegis.yaml` gains `recap: {agent: <name>}`, naming an
agent from `agents:` (Haiku in the Workspace). Nothing defaults: with no
`recap:`, the request answers that the recap is off and says which key turns it
on. The call is a one-shot `claude -p` with `--json-schema`,
`--setting-sources ""`, an empty working directory, stdin closed, and thinking
off. Each of those choices was measured in the legacy driver
(`legacy/aegis/drivers/claude.py`, `generate_detailed`). Its cost is added to the
session's `recap_cost_usd`, apart from the session's own cost.

**Where it lives.** The result is an aegis record,
`{"kind": "recap", "upto": <store index>, "context", "ask", "model", "cost_usd",
"duration_ms"}`, and the fold turns it into a `recap` entry. Every browser
receives that entry, so two browsers landing on the same tab pay once. The
server also holds one in-flight call per session, so two requests at the same
moment share it. A recap for the same `upto` is never made twice, except by
the refresh link. Once a person sends to the session, the fold renders that recap
folded to one line, so the history keeps what you were told when you came back.

The recap is aegis talking to the person. It never reaches the agent's context.

## The ping

**In the page.** Tab and card marks as in the table above. The Fleet band counts
sessions by attention (need you, error, review, working, waiting, done) instead of
by process state. With "Needs you first", the Fleet shows needs_you, error and
review cards under "Needs you", and the rest below.

**Outside the page.**

- The document title starts with the count of sessions that need you or errored,
  "(2) aegis", whichever view is open.
- The favicon is an SVG drawn by the client, with a dot in the needs-you colour
  while that count is not zero.
- A desktop notification fires when a session enters needs_you or error while the
  page is hidden. Its tag is the session's log id and its turn end, so one browser
  notifies once per turn end; clicking it focuses the tab. The browser asks for
  permission from a bell button in the top bar, because Chrome only asks from a
  click.

## Glyphs

The marks are SVG symbols in one client module, `js/glyphs.js`, used as
`<svg><use href="#g-need"/></svg>` and coloured by `currentColor` from the theme's
variables. The knocked-out glyph inside a badge uses `var(--bg)`, so it reads on
every theme. The set is the six attention badges (style B in the mockup), the
read and unread marks, the recap sparkle, and the navigator's chevrons and
to-latest arrow. Python still decides which mark a session has; the browser only
picks the symbol.

## Testing

Every scenario runs through the fake claude in `tests/fake_claude.py`, which can
call `/mcp` with its own token.

- **Attention.** A turn that calls `turn_end(needs_you)` gives a needs_you card. A
  monitor armed in the turn gives waiting, and its end gives done with no new
  turn. A `Result.is_error` gives error over any report. A queue worker's
  needs_you gives done. A turn with no `turn_end` gives done. A turn started by an
  inbox message ends exactly like a sent one.
- **Replies.** A `turn_end` with replies puts them on the card; a send, typed or
  from a pill, clears them; a queue worker's replies never reach the card; more
  than three, or a reply over 80 characters, is refused with a message the agent
  can act on.
- **Plan.** `plan_update` calls give now, did and the count; a refold of the store
  gives the same card; a `turn_end` from an earlier turn does not survive the next
  prompt's echo.
- **Read.** `session.read` moves the floor and keeps `read_ids` small. A second
  browser receives the flag changes and the count. A pre-existing session starts
  fully read. The session tests' rule holds: the patches add up to a fresh fold.
- **Recap.** The one-shot runner is injected, so tests never call Claude. The
  thresholds decide; two simultaneous requests make one call; a second landing on
  the same `upto` makes none; with no `recap:` the answer names the key; a send
  folds the entry.
- **Browser.** Margin marks flip after a row is on screen for a second; the divider
  sits before the first unread; the pill's count and jumps; Alt+↑, Alt+↓, Alt+U;
  the order switch persists across a reload; the title count; a blinking tab stops
  when it is focused; a reply pill sends its text and every pill disappears.
- **Live.** `make test-live` runs a real Sonnet session through a question turn,
  which must end in `turn_end` with `needs_you` and one to three replies, and a
  work turn, which must end in `turn_end` with `done`. `plan_update` compliance, a
  wait turn and an open question with no replies are not yet measured live (#178).
- **Bench.** The `unread` flag and the attention rule must not raise the cost per
  stdout line; `make bench` runs on each PR.

## Slices

Each slice is a PR that works on its own.

1. **Status.** The two agent tools and their records, the attention rule, the
   card fields, the priming, the glyph module, tab and card marks, the band, the
   order switch and the reply pills.
2. **Reading.** The read floor, `session.read`, the unread flags, margin marks,
   the divider, the navigator, the keys, the title count, the favicon and
   notifications.
3. **Recap.** `recap:` in the config, `recap.request`, the one-shot runner, the
   recap record and entry, the sparkle button.

## Out of scope

- A Stop hook that forces `turn_end`. Revisit with the measured compliance.
- A key for the reply pills.
- A paid mid-turn "doing" line.
- Title generation (#49 in the legacy tree).
- Read state per browser.
- Notifications outside the browser, such as Telegram.
