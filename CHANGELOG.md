# Changelog

All notable changes to Aegis are documented here.
The format follows Keep a Changelog; this project uses SemVer (0.x).

## [Unreleased]

### Fixed

- **A session no longer thinks forever after its turn ends.** Claude puts
  things on the wire between turns that belong to no turn: `commands_changed`
  when skills reload, a bare re-`init`, `hook_started`, `task_updated`,
  `task_notification`. aegis asked only whether the harness queue was
  non-empty, so the idle watcher promoted one of those lone notices into an
  unsolicited turn — and the drain then parked forever on a read waiting for
  a `Result` that was never coming. The session sat in `working`, rendering
  as an agent thinking with no event ever arriving, until someone noticed and
  pressed `Esc`.

  The predicate now asks the question it meant to ask: **is a turn waiting to
  be drained**, not is the queue non-empty. Only events that can occur inside
  a turn — assistant text and thinking, tool use and results, plan updates,
  the `Result` itself — promise a `Result`, and only those promote. Telemetry
  that merely rides along with a turn (`thinking_tokens`, `compact_boundary`)
  is deliberately excluded: it cannot start a turn on its own, and the real
  turn's events arrive right behind it. The end-of-stream sentinel still
  promotes, so a dead harness surfaces as `error` rather than going quietly
  idle on a subprocess that no longer exists.

  Nothing is dropped. Out-of-band notices stay queued and drain with the next
  turn, where they render as nothing — which is what they rendered as before.

  Found in `plush-pearl`'s log: a turn ended cleanly at 13:06:55, a
  `commands_changed` landed 67 seconds later, and the session never moved
  again. Eleven sessions in the on-disk history end on exactly that shape —
  a tail of pure system notices after the final `Result`. Regression tests in
  `tests/test_claude_idle_promotion.py` drive the real `ClaudeSession` queue
  on purpose: the fake in `test_core_session.py` drains its list and returns,
  so it cannot block, and could never have caught this.

- **A rename no longer strands the session's monitors and reminders.**
  `SessionManager.rename_handle` migrated the inbox and the lock claims and
  stopped there, even though it owns the monitor and reminder planes too
  (`attach_monitor_manager` / `attach_reminder_service`). `AegisApp` migrates
  all of them, so the two rename paths disagreed and only one of them left
  you stranded.

  Both planes key their records by the handle that armed them, and that key
  is what the wake is delivered to. A monitor left under the old name is not
  merely invisible in a UI scoped by `for_handle` — it keeps watching, and
  when its condition trips it delivers to a handle no session answers to, so
  the agent waits forever on a callback that already went somewhere else.

  Caught live: a session renamed mid-run kept its monitor under the old
  handle, and `aegis_monitors(from_handle=<new>)` came back empty while the
  monitor was still watching under `<old>`.

- **A monitor now refuses a handle it could never wake.** `start_monitor`
  already refuses a *condition* that can never trip — "checked here, not only
  at the MCP surface, so no caller can route around it". The handle is the
  same defect one field over, and it was not checked at all: `_fire` delivers
  to `from_handle`, so a stale one meant the monitor polled for its whole
  timeout, tripped, delivered into the void, and the agent waited forever on
  a callback that had already gone nowhere.

  This is not an exotic mistake. **An agent is never told when the operator
  renames it** — no message announces it, and its system prompt still carries
  the handle it was born with — so it goes on passing the name it remembers.
  Caught exactly that way on 2026-08-12: a session renamed at 13:22:14 armed
  monitors at 13:27:31 and 13:31:54 under the handle it no longer had. Both
  armed happily and watched for minutes in silence.

  The refusal names the live handles and points at `aegis_list_sessions`, so
  a renamed agent can find itself instead of guessing. Inert when the session
  manager cannot answer (no manager, or an empty session list — a stub or a
  boot-time race), so it bites in production without vetoing the suite.

## [0.33.0] - 2026-08-11

### Added

- **The `F3` side dashboard — the pane's whole right-hand column, instead of
  four strips fighting for one row.** Press `F3` (or type `/tasks`) and a
  full-height sidebar carries `SESSION`, `CONTEXT`, `PLAN`, `QUEUES`,
  `MONITORS`, `REPOS` and `SYSTEM`; the main column becomes transcript and
  input. Press it again and the pane is exactly what it was.

  The status bar had run out of room. Eight segments compete for one line,
  and each new thing worth watching — a plan, a queue depth, a monitor's
  progress — arrived as another collapsed strip above the input, each doing
  its own truncation. The column's vertical axis is free, which is the whole
  argument for it: sections are ordered by **volatility**, highest first,
  because the panel scrolls and what you see without scrolling should be what
  moves. An empty section renders nothing at all, not a heading over a blank.

  **`F3` toggles a mode, and the mode is app-wide.** One flag fans out to
  every pane, and a tab opened later comes up in the mode rather than
  collapsed beside its siblings — which is what makes a fan-out readable,
  since the point is comparing agents. It shipped per-pane and that was
  wrong: switching tabs changed the layout under you.

  Every row is fitted to the column, top tier down, at 26 / 33 / 40 / 60
  cells — one invariant in `tests/test_sidebar_render.py` pins it, because
  the body is a `Static` in a `VerticalScroll` and an over-long row does not
  clip, it wraps and pushes everything below it off the panel.

- **The sidebar's `SYSTEM` block now says when and where.** Under the
  CPU/RAM/disk meters it carries three more rows: the date, time, zone and
  locale; the directory this aegis is rooted at (`CWD ~/Workspace/repos/aegis`
  — home collapsed to `~`, narrowing from the head, since in a path you
  already live in the prefix is the part you can reconstruct); and the build
  actually running (`aegis 0.32.0+b78cb3d`, off `version.BUILD`, which
  latches at import for exactly this reason — under an editable checkout
  "what am I running" and "what is on disk" diverge the moment a commit
  lands beneath a live TUI).

  Sidebar-only: a one-row status bar has no space for four more segments,
  and the column's vertical axis is free — which is what the panel is for.
  Ordered by volatility like every other section, one level down: the
  meters move every tick, the clock every minute, the last two never. The
  repaint rides the existing app tick (`set_system` already fires every
  second and lands on `_refresh_sidebar`), so the clock stays current
  without a timer of its own.

- **`/spawn <agent> <prompt>` now carries where you were standing.** Typed
  from a live pane, the new agent's opening turn gets the same three things
  `@peer` sends: provenance of *place* ("the operator started you from tab
  `alpha` (opus)"), a bounded tail of that pane's transcript, and a pointer
  to `aegis_read_peer("alpha")` for the rest. So `/spawn opus please verify
  this test` now means something — the new agent can see which test, and go
  read the conversation if the tail is not enough.

  It closes the last row of the context-carrying table. `aegis_handoff`
  carries nothing and you retype it; `@peer` carries a bounded slice to an
  idle peer; `/fork` carries the entire conversation for about a dollar;
  `/spawn` carried nothing at all, which is why a fresh agent's first four
  turns went to grepping for a referent that was one log read away.

  One thing inverts on purpose. `@peer` tells its target *not* to start long
  work — it is spending someone else's idle turn. A spawn is the opposite:
  paying for a new agent is exactly buying one that goes and does the thing,
  so the composed body says do the work, and offers `aegis_handoff` back to
  the source as the way home.

  The tail is assembled at the **teaser** budget (2k tokens), not
  `read_peer`'s own 24k default. Measured on a real 410KB transcript, the
  wide budget turned a 3-turn window into 95,346 characters of preamble in
  front of the three words the operator typed — and the turn bound does not
  save you, because one long in-flight turn is a single turn. `read_peer`
  grew `budget_tokens` / `item_chars` on both bridges for this, pinned by
  `test_read_peer_takes_the_same_window_knobs_on_both_bridges`.

  The preamble rides on the tail: a pane whose first input is the `/spawn`
  itself, a damaged log, or a bridge without `read_peer` all fall back to
  the bare prompt, because provenance pointing at a transcript nobody can
  read buys the new agent a failed tool call and a paragraph of confusion.
  `aegis_spawn` over MCP is unchanged — an agent calling it is told to write
  a self-contained payload and has the context to do so. Spec:
  `docs/superpowers/specs/2026-08-10-aegis-spawn-with-provenance-design.md`.

- **`REPOS` in the `F3` sidebar — which repos the agents are writing to,
  on what branch, and whether more than one is in there.** aegis runs many
  agents over one checkout and nothing on screen said where they were
  standing; two agents in one repo is the collision `src/aegis/locks/`
  exists to prevent, and you found out at `git diff`, hours later.

  ```
  REPOS                              2
  ● aegis        main ~6 ↑6  calm-hopper
  ● Workspace    main ~2
  ```

  `●` is you, `·` is peers only, amber is more than one live writer. `~n`
  is uncommitted files — how you spot the seven an agent left behind in a
  repo it stopped working in an hour ago — and `↑n` is unpushed commits,
  which is the "a VPS job clones `origin` and silently gets the old tree"
  failure made visible. A detached `HEAD` or a rebase in flight replaces
  the branch, in the error colour.

  **Membership is writes only.** A repo enters when an agent runs `Write` /
  `Edit` / `NotebookEdit` (ACP is recognised by tool *kind*, since every
  harness on that seam picks its own titles) and stays for the life of that
  session. Reads do not promote — otherwise every repo an agent merely
  grepped shows up and the section stops meaning *work is happening here*.
  Bash is missed on purpose: guessing write targets out of a shell command
  is the heuristic the mandatory-claims spec already declined to dress up
  as complete, and a row that appeared because something misread a `>`
  inside a quoted string would make the whole section untrusted.

  One `git status --porcelain=v2 --branch` per repo returns branch,
  upstream, ahead/behind and the dirty list together. It runs off the UI
  thread behind a 5s TTL, **only while the sidebar is open**, and a paint
  never waits on it: a new row shows its branch immediately from
  `.git/HEAD` (one file read) and fills in counts on the next tick. Repos
  on a remote host are listed and never probed — the same path names a
  different tree there, so `git status` locally would be a silently wrong
  answer rather than an error.

  TUI only; the web client renders no `REPOS`, the same debt the sidebar
  itself and the live task list already owe.

- **A compaction counter, `✂n`, beside the context gauge.** When the harness
  drops older context to keep going, that is the single most consequential
  thing that can happen to a long session and nothing on screen said it had.
  Claude Code reports it exactly — a `system` / `compact_boundary` event
  carrying `trigger`, `pre_tokens` and `post_tokens`, which aegis now parses
  into a typed event — so the counter is a count, not an inference. Yellow at
  one, red at two or more, and each boundary re-baselines the gauge to the
  post-compaction size, without which `ctx` would sit at ~100% for the rest
  of the turn.

  There is deliberately **no heuristic fallback for ACP harnesses**. The
  obvious one — a large drop in reported context — was tried and measured
  against the local corpus: it fires 1,272 times against 17 real
  compactions, ~1.3% precision, and no online variant beat 12%. Nearly half
  its detections are subagent context switches and 98% recover inside the
  same turn. So on a harness with no protocol signal the counter stays
  absent, which is the honest reading.

- **A monitor now tells an agent what else it is watching.** A monitor
  outlives the process it watches: kill that process — a PID sweep, a
  superseding run — and the monitor keeps polling for a marker nothing will
  ever write, then wakes the agent with a stale verdict or times out much
  later. Nothing said so, and the one tool that would show it,
  `aegis_monitors()`, is a call an agent has no reason to make.

  So the roster goes where the agent is already looking. `aegis_monitor`
  returns `also_watching`, every monitor callback appends a *"Still watching
  (N)"* block, and both print full ids with percent beside elapsed — that
  pair is what exposes an orphan, since a monitor frozen at 60% for nineteen
  minutes is plainly watching a corpse. `aegis_monitors()` also takes an
  optional `from_handle`, because unscoped it lists every peer's monitors
  with nothing marking ownership, and that is the state the roster now sends
  agents to inspect.

  **`aegis_monitor_cancel` says what it killed.** `{ok: true, state:
  "cancelled"}` asked the agent to take on faith that it hit the monitor it
  meant, and ULIDs differ in a handful of characters. The result now names
  the description of what died and hands back the remaining roster, counted
  — cancelling is when an agent is pruning, which makes it the best of the
  three moments to show the rest of the pile. Neither path sends an inbox
  wake: the agent just made that decision and does not need reminding of it.

  Paid for on 2026-08-10: a session killed a chained pytest by PID, which
  also killed the shell waiting to write the marker file. Its monitor sat at
  60% forever, the agent armed two more, was woken by one of them and never
  noticed — Alex had to point at it twice from outside. Either touch point
  alone would have caught it.

- **A call into the aegis layer now looks like one.** Every `aegis_*` tool
  renders with a glyph from the layer's own family, in the layer's own
  colour, on a line that names the counterpart — `⇄ weary-turing · "the
  render is yours"`, `⊙ exclusive · src/aegis/mcp/ · 3 paths`. Before this,
  all 72 of them fell through `describe_tool`'s unknown-tool branch and
  rendered as *the first stringy argument, truncated* — for a handoff, the
  caller's own handle, which says nothing about the call. Nineteen glyphs
  name semantic acts rather than tools; read-the-room calls (`list_sessions`,
  `claims`, every `config_*`) share one pale `∘` so polling never wears the
  conversation's weight. A coverage test over `server.list_tools()` fails
  the build if a newly-added tool has no descriptor.
- **`aegis comms list`** reads back who talked to whom. A FastMCP
  `on_call_tool` middleware appends one envelope per call — `from`, typed
  `to`, family, verb, thread, outcome, duration — to a daily JSONL under
  `.aegis/state/comms/`. `thread` adopts the substrate's own ids
  (`task_id`, `monitor_id`, `claim_id`, …), so an `enqueue` and the callback
  that lands in another agent's inbox minutes later share one. Filter with
  `--handle` (matches either end), `--thread`, `--family`, `--since`.

### Changed

- **`F3` is one mode for the whole app, not a per-tab widget.** The
  dashboard sidebar shipped scoped to the active pane, which meant
  switching tabs changed the layout under you and every new tab landed
  collapsed beside its open siblings. `F3`, `/tasks` and the pane's own
  toggle now flip a single app-level flag that fans out to every pane,
  and a pane mounted later adopts it.

### Fixed

- **The context gauge measured the wrong thing, and read over 100% on most
  agentic turns.** `ctx` is meant to answer "how full is the model's window",
  but it was fed the turn's *accumulated* true input — and an agentic turn is
  many round trips, so the figure grew with every tool call. Replayed over
  the 381 local session logs (6,871 turns), **4,256 of them — 61.9% — render
  above 100% today**, the worst at **92,956%** across 1,138 sub-turns. The
  gauge now takes the **peak single sub-turn**, which is the quantity that
  actually has to fit in the window: replaying the shipped code over the same
  corpus leaves **1 turn in 7,042 above 100%**, and reintroducing the
  one-line bug takes it back to 4,271. Cumulative accounting (cost, session
  input) is unaffected — it was never the same number.

  ACP sessions get a real window too: `context_size` off the harness's own
  context update overrides the bundled model registry, which is what closes
  the last outlying turn.

- **Every `Ctrl+Q` printed a `LookupError` crash dump, and the tab roster
  silently stopped being saved.** The debounce timer behind the roster write
  is armed from `AppBridge` methods that run in MCP handler tasks
  (`aegis_title`, `aegis_rename`, spawn, close) — and a Textual `Timer`
  copies the context that armed it, then reads `active_app` on its first
  tick. There is none in a handler task, so the timer died immediately and
  nothing retrieved the exception until shutdown awaited the dead task.

  The dump at quit was the visible half. The invisible half is worse: the
  flush never ran, so the timer handle stayed set, so every later debounced
  roster write for the rest of the session was dropped on the early return —
  and a crash then lost the tab roster entirely, since only the synchronous
  write in `action_quit` survived. Fixed by arming the timer inside the app's
  context, verified by hand on a fresh instance (agent calls `aegis_title`,
  then `Ctrl+Q` → rc=0, no traceback) and not only in the suite.

- **The sidebar no longer crashes the app when the context fills up.** The
  `ctx N (P%)` gauge and the `✂N` compaction counter colour themselves once
  the window is half full, and they wrote that colour as Textual's
  theme-variable markup (`[$warning]…[/$warning]`). The status bar is a
  Textual `Static` and reads it; the F3 sidebar parses the *same strings*
  with `rich.text.Text`, and Rich does not accept `[$warning]` as an opening
  tag while it does accept `[/$warning]` as a closing one — so it raised
  `MarkupError` and took the TUI down. Nothing carried a tag below 50%,
  which is why this only ever fired on a long session. `render_tiers` now
  takes the palette, the way `sysmeter` and the strips already do, and
  writes a palette hex closed by the bare `[/]` — the one dialect both
  parsers accept. A caller with no palette gets no markup at all, which
  also fixes the web client showing a literal `[$warning]✂1[/$warning]`
  where the segment should be (it sets the string as `textContent`).

- **A queue callback carries the worker's last *message*, not its last
  chunk.** Assistant text arrives as a token stream — one message is many
  events, which is the entire reason `render.coalesce_chunks` exists — and
  the queue captured "the last `AssistantText`" by overwriting on each one.
  So a worker that signed off with *"Fixed the deadlock in storage.py;
  suite is green."* reported back to its producer as **`"green."`**. Short
  single-chunk replies came through intact, which is why this survived: the
  bug is invisible exactly until the worker has something substantial to
  say. Chunks now accumulate by `message_id`, with the same run rule
  `coalesce_chunks` uses — adjacent events with equal ids are one message
  (equal includes both `None`, the pre-slice-2 claude case), and any other
  event ends the run, without which an id-less driver would concatenate the
  worker's whole monologue.

  The same capture also folded in **subagent narration**. `capture_next_reply`
  states the rule — "a peer that runs a `Task` must not fold its subagent's
  commentary into the answer the operator reads" — and the queue was the one
  capture path in the codebase that never applied it, so a worker that
  dispatched a `Task` could report its subagent's chatter back to the
  producer as its own answer. Events carrying a `parent_tool_use_id` are now
  skipped (skipped, not treated as an intervening event — ending the run
  there would truncate the worker's own message whenever a subagent spoke
  mid-stream), which also keeps the queue dashboard's tail in the worker's
  own voice.

- **A queue callback carries what the worker actually said.** The task
  result *is* the worker's final assistant text — that is the contract
  `aegis_enqueue` sells ("phrase the payload so the worker's natural final
  answer is the thing you want back"). Two paths that end a worker threw
  it away:

  - **`cancel()`** sent the producer the literal string `"cancelled"`. A
    worker that had done twenty minutes of work and said so was reduced to
    one word, and the producer had no way to know anything had happened.
    The body now leads with the outcome and carries the last message under
    it, and the text is recorded as the task's `result` so
    `aegis_task_status` shows it too.
  - **`_mark_interrupted()`** (boot replay after a crash) sent only the
    restart notice. The `deferred` record now persists the worker's last
    text — the one point at which a *live* worker's words reach disk — so
    a worker that was waiting when the process died still reports back.

  A worker that ends having emitted no assistant text at all (tool calls
  only) used to produce an **empty** callback body, which in an inbox is
  indistinguishable from a message that failed to render. It now says what
  happened. Nothing invents a quote for a worker that said nothing: each
  path passes its own honest note for the empty case ("never dispatched",
  "the worker had not said anything yet", "nothing of the worker survived
  the restart").

- **A queue worker that armed a waker no longer dies at its turn
  boundary.** Ending a turn is how an agent *waits* — the monitor briefing
  says so outright ("returns `{monitor_id}` immediately; END YOUR TURN") —
  but `QueueManager._finalize` read any turn boundary as completion:
  marked the task `completed`, sent the producer a callback, and closed the
  session.

  Paid for on 2026-08-10 in `repos/ainbox`. A worker took the warden
  write-lock-deadlock task, did the work, armed a monitor on the test
  suite, said *"Waiting on the warden suite — I'll report when it lands"*
  and ended its turn. It was closed on the spot. The monitor's wake had
  nowhere to land, so the suite result was never read; the producer's
  callback was that sentence, which is a promise and not a result; the task
  read `completed`; and the actual work — a `beaver-db` pin bump, a
  `Storage.health` probe, a `/healthz` route and its integration test — sat
  **uncommitted in a shared checkout** that Alex and other agents were
  writing to.

  `_finalize` now asks whether the worker is still waiting on something
  before finalizing anything. If it is, the task stays in flight, no
  callback is sent, the session stays alive, and a `deferred` record goes
  in the queue log. The waker fires, the worker takes its reporting turn,
  and *that* boundary finalizes normally.

  Deferring conditions are exactly the **self-terminating** ones — live
  monitors (they have timeouts), pending reminders (they have fire times),
  unconsumed inbox messages (they resolve at the next turn boundary), an
  armed loop. Held **file claims deliberately do not defer**: a claim is
  released only by the agent holding it, so a worker that forgot would pin
  a `max_parallel` slot until the process died. `aegis_close` is right to
  refuse on one — a human is asking and can go look — and the substrate is
  right not to.

  The conditions are not a second copy: the fact-gathering that
  `aegis_close` did inline moved to `close_guard.gather_facts`, and both
  callers now read the same planes. `still_working_reasons` is the
  substrate's half of `refuse_reasons`, with the ownership questions
  dropped because nobody is asking permission.

## [0.32.0] - 2026-08-07

### Added

- **Session titles — a label beside the handle, so ten tabs stop reading
  `lucid-knuth`.** A session now carries a `title` saying what it is
  *doing*, distinct from the handle, which stays what it always was:
  identity — `from_handle` on every MCP call, the inbox routing key, half
  the log id. Conflating the two was the actual bug behind the old
  "rename yourself once your purpose settles" workaround.
  **A session titles itself.** When its first turn finishes, aegis makes
  one cheap structured call to summarize what you asked for and titles the
  session with the result — once per session, not per turn, because a
  label that churns is worse than one that doesn't. Point the existing
  `text_generation:` key at a small profile to keep it off your main
  model's bill. Generation is best-effort by contract: a bad day at the
  endpoint leaves the session untitled and cannot disturb the turn.
  Set one by hand with **`/title <text>`**; bare **`/title`** regenerates
  from where the conversation is *now* (told the previous title, and given
  the transcript tail rather than the opening request — the case where the
  original title has stopped being true), and **`/title --clear`** drops
  it. Agents name themselves with **`aegis_title`** or
  **`aegis_rename(…, title=…)`**.
  Writes are ordered `human > agent > auto`: an agent cannot overwrite
  what you typed, and the refusal says so instead of failing quietly. That
  ordering is also the entire concurrency story — a title that loses simply
  never lands, so there is no request-id bookkeeping anywhere.
  Titles ride the append-only `SessionMeta` record the transcript already
  uses for renames, so they survive a restart, and **`Ctrl+R`** shows and
  filters on them — where they earn the most, a history row having a whole
  line to spend. The active session's title also sits in the status line.
  Not in the tab bar, deliberately: four tabs already measure 127 cells
  before any title, past a 120-column terminal, and a title takes that
  to 190–210.

### Fixed

- A `/rename` silently blanked the session's title: the header it appends
  re-derives every field, and wrote `title=""` over what you had set.
- A resumed session forgot its title *and its `title_source`*, so after any
  restart an agent could overwrite a title the operator had set by hand —
  the precedence rule was only ever as strong as one uptime.

## [0.31.0] - 2026-08-06

### Added

- **The live task list — a plan is now session state, not a run of
  anonymous tool calls.** `TaskCreate` / `TaskUpdate` / `TodoWrite` and
  ACP's plan updates all fold into one cumulative plan, and a tracker on
  the session times each task as it runs. The time is *working* time, not
  wall clock: it accrues only while the session is mid-turn, so a task left
  in progress overnight reports the minute of work it actually got rather
  than nine hours of idling — and the in-progress circle spins on exactly
  that condition, so the rotation is a literal rendering of the clock
  running.
  Three surfaces read the one tracker. An always-on **strip** above the
  status bar carries a circle per task, the count, and what is running now.
  **`F3`** (or `/tasks`) opens a **dock** beside the transcript with a row
  per task and its working time, subagent plans nested underneath — which
  is what makes a fan-out legible, since it shows which of several parallel
  agents is still grinding. And the plan reaches other agents: the tab bar
  carries a `3/8`, `aegis_list_sessions` rolls it up on `SessionInfo.plan`,
  and the new **`aegis_peer_plan`** drills into a peer's full list, so an
  agent deciding who to hand work to learns not just that a peer is busy
  but how far along it is and what it is on.

  The plan **survives a restart**: a resumed session replays its own
  transcript through the tracker and comes back with the tasks *and* their
  banked working time intact, rather than blank until the agent next
  happens to list them. A log that stops mid-turn always replays to idle,
  so an interrupted session cannot return claiming hours on its first
  paint. A task that never started reads `—`, never `0:00` — the two mean
  different things and must not look alike.

## [0.30.0] - 2026-08-05

### Added

- **SSH execution hosts — run a harness on another machine.** A new
  `hosts:` config section, and `host` becomes a third orthogonal spawn axis
  beside agent profile and harness: any harness on any host, resolved per
  spawn and never persisted, exactly like the `model`/`effort` overrides.
  Reach it from `Ctrl+N` (a host tier ahead of the usual ones),
  `/spawn main@vps:/srv/app`, or `aegis_spawn(..., host="vps")` so an agent
  can place a peer itself. The point is not remote *access* — it is that
  the agent's `Bash`/`Read`/`Edit` run natively over there instead of one
  `ssh` invocation per command. One SSH ControlMaster per host is shared by
  every session on it, and a reverse tunnel carries the local MCP plane
  across, so a remote agent is an ordinary peer: same handles, same
  handoffs, same canvases. This is neither `--remote` (the TUI attaching to
  a remote serve) nor `remotes:` (federated serves) — here there is one
  aegis, local, and only the subprocess is elsewhere.
  Paths become host-scoped, which fixes a silent wrong answer:
  `/home/apiad/Workspace/src/foo.py` exists on both machines and is a
  *different file*, so `Ctrl+click` on a remote pane now offers
  `vps:/…/foo.py` rather than opening the local one, file claims carry
  their host, and the tab is marked `@vps`. A dropped link reports itself
  instead of leaving a dead tab looking idle, and `/reconnect` rebuilds the
  harness on the same host, resuming the same conversation in the same tab.
  `aegis config host add|remove|list` writes it all scriptably.
  A host runs its harness under a login shell by default (`login_shell`,
  on unless you turn it off): a non-interactive `ssh host cmd` never
  sources your profile, so a harness installed in `~/.local/bin` — where
  the Claude Code installer puts it — would not be on `PATH` at all, and
  on a machine carrying both a system and a user install it is the login
  shell that resolves the newer one.
  Docs: [Execution hosts](https://apiad.github.io/aegis/hosts/); spec:
  `docs/superpowers/specs/2026-08-04-aegis-ssh-execution-hosts-design.md`;
  procedure: `know-how/ssh-execution-hosts.md`.

- **`Ctrl+click` a `Read`/`Write`/`Edit` block to open that file (TUI).** The
  gesture prose blocks already had for backtick tokens now works on the tool
  call itself — and it needs no fuzzy matching, because the call already named
  the exact path. `Read` lands on its `offset`, `Edit` on the line its
  `old_string` starts at, which is resolved by reading the file at click time
  rather than at render time: the edit has by then removed its own anchor, so
  the answer is only correct if it is computed late. When neither the anchor
  nor its first line is still findable, the file opens at the top rather than
  on a guessed line. Plain click still expands the args; harnesses that report
  a file through ACP `locations` instead of a `file_path` get the gesture too.

## [0.29.0] - 2026-08-01

### Added

- **Reorderable tabs (TUI).** `Ctrl+Shift+←/→` carries the active tab one slot
  along the bar, and a tab can be dragged there with the mouse. Order is
  clamped at the ends rather than wrapped — a tab teleporting from first to
  last reads as a bug — and it survives a restart, because the roster
  snapshot writes each tab's `order` from its list position.

  Tab order *is* `app._panes` order (the ContentSwitcher keys on pane id, not
  on child order), so the whole feature is a list splice plus a repaint. The
  drag deliberately takes no mouse capture: cells are positional, so the cell
  the pointer is over already *is* the drop target, and Textual's own routing
  does the hit-testing. Each crossing emits one `TabBar.Reordered` hop, which
  is what makes the tab appear to follow the pointer.

- **`@peer` — ask an idle agent, from where you're standing.** Type
  `@lucid-knuth is this schema right?` in any pane. An idle peer answers, and
  the answer lands as a real turn in *its* transcript and a transient block in
  *yours* — your own agent neither sees it nor pays for it.

  It fills the hole between `aegis_handoff` (free, carries no context, you
  retype everything) and `/fork` (~$1, carries the whole conversation, spawns a
  new agent): a bounded slice of where you are standing, delivered to an agent
  that already exists.

  **Idle-only is the domain, not a limitation.** The case for the feature is
  extracting value from a warm context that is currently producing nothing; a
  busy peer inverts it, since it is already producing value and cutting in
  costs you the thing you were asking for. So the guard reads the *target* and
  never the source — which makes `@peer` legal while your *own* tab is
  mid-turn, and that is the point: you spend a long turn's dead time asking
  someone who is free.

  - `@handle …` is sugar for `/peer handle …`, routed as a `classify_input`
    rewrite so the dispatcher, the effect channel, the palette and the web seam
    all carry it unchanged. `@@` is the literal-`@` escape, mirroring `//`;
    only a leading `@` addresses, so `a@b.com` mid-line stays prose.
  - The peer is sent a **teaser** — 2k tokens of your transcript tail against
    `/btw`'s 32k — assembled with `btw.window.assemble`. It costs a log read
    and no model call, which is what lets the design push a pointer rather than
    a summary. Its honest header ("6 of 143 events") goes to the peer
    deliberately: the failure mode here is not laziness but that a model cannot
    detect a gap, and a stated boundary turns an undetectable absence into a
    legible one.
  - `aegis_read_peer(handle, turns=12)` is the pull half, at 24k. It unlocks
    nothing — the logs are plain JSONL in the project root and every agent has
    Read and Bash — but it fixes *addressing*: a log id carries the session's
    **birth** handle and is never renamed, so current-handle → file is not
    derivable by an agent that only knows who it is talking to.
  - `--cc` delivers the peer's answer into your own conversation as a real
    turn. Decided by the operator at send time, never by the peer at reply
    time: the peer cannot judge relevance to a conversation it holds a 3-turn
    window of, and being agreeable it would guess yes.
  - Busy peers are marked in the completion palette rather than hidden, so the
    idle-only constraint is visible at type-time instead of arriving as a
    refusal at send-time.

  Spec: `docs/superpowers/specs/2026-07-31-aegis-at-mention-peer-ask-design.md`

- **`/btw` — a side note that doesn't cost a conversation.** `/btw <question>`
  answers from the current pane's own transcript tail and disappears: a
  bounded window of the log, one throwaway model call, an inline block. It is
  legal mid-turn, where `/fork` is not — `/btw` never touches the harness
  session, it reads aegis's own log and makes an independent call, so a live
  turn's dangling `tool_use` cannot follow it.

  Notes are transient by construction. A note goes into the pane's history so
  scrolling keeps it, and is never appended to the session log — so side notes
  do not compound, and the fifth `/btw` still sees real conversation rather
  than four of its own prior musings.

  - The window assembler fills **newest-first** under a 32k budget, and its
    header states what it dropped ("6 of 143 events") to the model and to the
    reader alike. Filling from the front would have dropped the turn that
    prompted the question, and `/btw` would confidently answer one nobody
    asked.
  - It runs **off the input handler**. Awaiting a 12–17s call inside a Textual
    message handler held the pane's whole message pump: no working indicator,
    no tool spinners, no input. A deferred command now mounts a placeholder
    that echoes your question, dispatches on a worker, and rewrites that block
    in place — so the note stays where you asked it while the agent's output
    streams past underneath.
  - **`Esc` cancels a running note**, ahead of clearing a half-typed line and
    well ahead of interrupting the turn: the spinning block is the thing on
    screen billing by the second. Cancelling leaves a tombstone rather than
    removing the block, and the wording comes from the command, so `@peer`
    says "stopped waiting" rather than the lie that anything was cancelled.
  - `/btw` and `@peer` answers **render as markdown on a surface of their
    own** — a raised panel plus a thin left bar. Making the answer beautiful
    made it camouflage: for `@peer` especially, a real turn from a different
    session must not read as your own agent talking.

- **`/fork` — branch a conversation into a worker that already knows.**
  `/fork [prompt] [--slug S] [--model M] [--effort E]` branches the current
  pane; `aegis_fork(target_handle, …)` forks an idle peer. A slash command is
  served by aegis rather than sent to the agent, so the pane is idle and there
  is no half-written turn to branch from — which is exactly why self-fork over
  MCP is refused with a pointer at `/fork` rather than a generic reason.

  The parent is untouched by being forked: its `session_id` is unmoved and its
  log byte-identical, both asserted, because both would break silently. A fork
  costs roughly $1, and that measurement lives in the MCP docstring where an
  agent deciding whether to fan out will actually read it.

- **A one-shot `generate()` seam on drivers** — no session, no MCP, no tools.
  `supports_oneshot` + `generate()`, plus a `text_generation:` config key that
  decides which model pays for aegis's own small calls.

  It exists because `claude -p` with default flags is not a generator, it is
  an agent. Same window, same question, haiku: the default run spent 21.9s and
  $0.0633 on 53,593 input tokens going looking for files a side note has no
  business reading, then answered "I cannot verify". With `--system-prompt`
  and `--tools ""` it took 8.5s and $0.0044 on 2,361 tokens and answered
  correctly. Shedding the tool schemas sheds the urge to use them.

- **`aegis_monitor` refuses `pgrep -f` in a condition, and says what to use
  instead.** A condition runs in a shell whose own command line contains the
  pattern, so `pgrep -f 'pytest -q'` matches itself — it is true when nothing
  is running, and `! pgrep -f …` is false forever. The monitor never trips and
  times out instead, which reads as a hung process rather than a broken
  condition. Now a deterministic guard, with the fix in the message: a
  completion marker in a log, or `! kill -0 <pid>`.

### Fixed

- **`WorkflowEngine.send()` silently returned `""`.** `workflow/runner.py` has
  reached for `session_send_and_await` on the bridge via `getattr(…, None)`
  since the workflow scaffold landed, and no production class ever defined it —
  so every `engine.send()` fell through to fire-and-forget and returned an
  empty string instead of the agent's reply. `SessionManager` now implements
  it. Found while building `@peer`, which is its first real caller.

- **Monitors were invisible in any tab born from a spawn or a fork.** The
  strip is composed once, in `ConversationPane.compose()`, and only when the
  caller passed a `monitor_manager` — which three of six construction sites
  did not, including the one behind `aegis_spawn`, `/spawn`, group spawn and
  every queue worker. The monitor always worked; only the row was missing,
  which is why it survived so long. (The strip is composed at construction, so
  this does not repair tabs in an already-running aegis — restart, and boot
  restore rebuilds them.)

- **`Ctrl+R` crashed partway down a long session list.** Handles come from a
  finite pool and only avoid *live* ones, so two logs routinely share one; the
  history modal keyed its options by handle, and the second collision raised
  `DuplicateID` out of `on_mount`. On a real 268-session state dir the first
  collision sat at index 93 — everything older than that was unreachable, and
  it degrades as more handles recycle. Keyed by `log_id` now, which is minted
  once at spawn and never reused. Selection lookups moved with it: keying by
  handle also reopened the *first* row with that handle rather than the
  highlighted one.

- **Replay rendered the agent talking to itself.** Reopening a conversation
  showed answers with no questions: the user's own turns are in every
  transcript (claude runs with `--replay-user-messages`) but had no typed
  event, so they fell through to `Unknown` and were dropped. `isReplay` is the
  predicate — measured over 269 transcripts, it marks genuine user turns and
  nothing else, where matching `type:user` alone would have rendered skill
  bodies and subagent prompts as things you said. Retroactive: 374 user turns
  recover from six real logs without a migration.

- **The mounted transcript window is bounded when you scroll up.** The `N_MAX`
  check was nested inside the stick-to-bottom branch, so reading back through
  a thread while an agent worked mounted every new block and never evicted —
  measured climbing past 650, and unbounded on a resumed session. The window
  grows a second edge and evicts from whichever end is furthest from the
  viewport, so eviction can no longer fight `_load_older`. This closes the
  last open finding from 0.28.0's audit.

- **Assistant prose and folded tool blocks are selectable.** Textual extracts
  a selection only from widgets that render a `Text`; a `Markdown` and a
  folded `tool_use`+`tool_result` pair are not, so they returned nothing.
  Blocks already carry a plain-text payload for click-to-copy, and the
  selection now uses it.

### Performance

Continuing 0.28.0's audit — the same scenario, the reflow tax rather than the
repaint tax. Textual rebuilds the whole compositor map on any layout change,
and its cost is linear in the number of mounted widgets, so the wins here are
about *not asking for layout* and about halving what a mounted block costs.

- **Transcript cells are no longer wrapped in a second widget.**
  `CopyableBlock` composed a child `Static`, so every block counted twice.
  At a full 300-block window: one reflow 308 → 140 ms, a keystroke in the
  input box 226 → 151 ms, one scroll line 261 → 136 ms. The marginal cost of
  a mounted block falls 0.86 → 0.33 ms.

- **Four places stopped asking for a layout pass when nothing moved.**
  `StatusBar.update()` defaulted to `layout=True`, firing one full-screen
  reflow per streamed delta; eviction and back-fill pruned and mounted one
  block at a time, each a reflow; streaming repaints are now throttled to
  20/s against the record-is-truth contract. Layout refreshes over 40 deltas:
  116 → 29. A/B against v0.28.1 at 300 blocks: reflow 236 → 110 ms, keystroke
  245 → 136 ms, scroll line 286 → 131 ms, 100 gapped deltas 5,614 → 2,723 ms.

- **The 10 Hz timers stopped asking too** — the working indicator and each
  running tool block, both repainting for the whole duration of every turn.
  Over one second of a real turn (indicator running, three tools in flight):
  140 layout passes → 18, or ~880 ms of layout per turn-second down to
  ~113 ms. That is what the 69% idle CPU on a live TUI actually was.

- **Reopening a session reads its transcript off the event loop.** On the
  largest real log — 24.8 MB, 17,990 records — decoding is ~500 ms, and it ran
  on the loop, so the UI froze mid-reopen. Both async call sites now thread
  it: the `Ctrl+R` reopen, and the boot restore, where it runs once per
  restored tab and the freezes add up.

### Internal

- **The hermetic suite is hermetic again.** Two `lovelaice` files spawn a real
  `lovelaice-acp` subprocess and call a real model over the network, but
  carried only `skipif` gates and not `pytest.mark.live` — so `pytest -m "not
  live"` had been running two network-dependent tests all along, and failed
  the day the endpoint went down.

## [0.28.1] - 2026-07-29

### Performance

- **A background tab no longer repaints its transcript.** It painted every
  streamed delta into a widget that isn't on screen — the same render cost as
  the foreground tab, paid once per open tab on the single UI thread. The
  record stays current on every delta (it is the source of truth, and the
  transcript never lies about what arrived); only the widget waits, and
  `on_show` reconciles it.

  Median of three A/B runs, 10 tabs ingesting 1,200 events: wall 7.60s →
  6.42s, 158 → 187 events/s, loop lag p50 124.5 → 78.5 ms, p95 857 → 665 ms.

  Modest next to 0.28.0's wins, because what remains in that scenario is
  mounting the widgets rather than repainting them. Closing that — and the
  still-open "window is unbounded while scrolled up" from 0.28.0 — needs the
  same change: a second window edge, so a pane can hold history it has not
  mounted.

## [0.28.0] - 2026-07-29

### Performance

Five measured audits of "sluggish with many tabs and long histories". Same
benchmark, same machine, before and after — ratios are the result, the box
was loaded so the absolute figures are pessimistic:

| scenario | before | after |
|---|---|---|
| per-event cost, fresh tab | 0.73 ms | 0.46 ms |
| per-event cost, 100 blocks deep | 3.42 ms | 0.42 ms |
| per-event cost, 300 blocks (the cap) | 10.4 ms | 0.46 ms |
| 10 tabs ingesting 1,200 events | 14.5 s | 4.3 s |
| worst-case UI stall, 10 tabs | 1,913 ms | 563 ms |
| reopening a 5,000-event conversation | 4.58 s | 0.073 s |
| `Ctrl+R` on a 619 MB corpus | 14.3 s | 0.04 s |

- **Per-event cost no longer grows with transcript depth.** `refresh_metrics`
  ran after every ingested event and found its status bar with
  `self.query(StatusBar)` — a deep, uncached, CSS-matching walk over every
  descendant of the pane, which is to say over the transcript. Four of the
  five audits found this independently. The status bar and working indicator
  are held directly now.
- **Resuming a conversation renders only what it shows.** Replay called
  `render_event` on every event in the log to mount ten blocks, and rendering
  assistant text constructs a `rich.Markdown`, which parses in its
  constructor. Records carry their source events and render on
  materialization; scroll-up was already the natural place for that.
- **Boot no longer scales with tab count.** Resumed tabs are mounted hidden,
  and now defer their replay until first shown.
- **`Ctrl+R` is off the event loop and cached.** The scan ran on the loop and
  re-read the whole corpus every time; it now runs on a thread and keeps a
  `(size, mtime)`-keyed sidecar index. A corrupt, stale, or unwritable index
  costs a re-scan, never a wrong listing.
- **The tab bar repaints only cells that changed** (40 panes flipping state
  together was 1.4 s of frozen UI), **the file index publishes while it
  walks** (the picker was empty for 17–43 s on a 60k-file tree) **and inserts
  with `bisect`** rather than re-sorting per filesystem event, **the session
  log holds its fd** (270 µs → 50 µs per event, re-validated on turn barriers
  so a `doctor --repair` rewrite can't be written into an orphaned inode),
  **closing a tab stops decoding the whole transcript** to answer two
  booleans, **roster writes are debounced**, **background bells are
  rate-limited**, and **terminal tabs freeze their ticker when hidden**.
- `LOAD_BATCH` is 40, not 100: mounting costs ~3.7 ms/block, so a scroll-up
  load was a 370 ms hitch.

Known limit, deliberately left: the mounted window is still unbounded while
you are scrolled up. Two attempts depended on layout timing — one fought
`_load_older` for the blocks it had just mounted, the other evicted nothing.
The real fix needs a second window edge and a remount-on-return path. Its
cost was mostly the DOM walk above, which is gone.

### Added

- **`aegis doctor --archive`** gzips closed transcripts older than
  `--archive-days` (90 by default) in place. Nothing pruned the state dir
  before, and it grows ~9 MB/day. Compress rather than delete: a transcript
  is the only copy of a conversation. Archived logs stay readable and
  resumable, only closed ones are touched, live handles are skipped, and a
  failed compression leaves the original alone.

## [0.27.0] - 2026-07-29

### Added

- **Alt+click a transcript block to open it natively.** Third gesture on a
  block: click copies, `ctrl+click` opens it in aegis's file tab, `alt+click`
  hands it to the desktop's own handler — your editor, image viewer, or
  browser — the way double-clicking would. Same token resolution as
  `ctrl+click`, including the chooser when a block names several files, plus
  URLs, which aegis has nothing to do with and a browser does. A `.desktop`
  file is refused (the handler would *run* it, and the path came out of agent
  output), and remote sessions say local-only rather than opening whatever
  sits at that path on your machine.

  Alt rather than shift: VTE terminals reserve shift to bypass mouse
  reporting, so a shift+click never reaches the application at all. Textual
  reports Alt as `meta` (SGR bit 8).

- **`aegis_close(handle, from_handle)`** — an agent can reap the workers it
  spawned, which keeps the tab bar honest. Refused unless it spawned the
  target *and* the target is demonstrably finished: not mid-turn, no live
  monitors, no pending reminders, nothing undelivered in its inbox, no queue
  task running, no armed loop, no file claims held. A refusal reports every
  unmet condition at once, so the caller knows what to wait for instead of
  re-calling to discover the next one. Group membership is not yet checked.

## [0.26.0] - 2026-07-29

### Added

- **The done line says how long ago the turn ended** —
  `── done in 12.3s · 4¢ · 4m ago ──` — so a tab you come back to tells you
  whether you are reading something fresh or something that has been sitting
  for an hour. Refreshed on the one-second tick for the pane you're looking
  at, and again on show. Only the newest terminator carries an age; the
  previous one drops it rather than freezing at a number that stops being
  true. (The web client shows the same line but has no per-event timestamps
  to compute an age from, so it is unchanged for now.)
- **`aegis_monitor` takes a `cwd`.** Conditions still default to the project
  root, but an agent working inside `repos/<name>` can now say so — a
  relative path resolves against the root. A condition that silently resolves
  the wrong directory never trips, so the monitor used to sit there until it
  timed out instead of failing; a `cwd` that doesn't exist is now rejected up
  front.

### Fixed

- **Pending reminders survive a rename.** A future-time reminder is addressed
  to the handle that left it, so one armed before a session renamed itself
  fired at a name nothing answered to — the same defect fixed for monitors in
  0.25.0. Loops were already safe (their state lives on the session) and are
  now covered by a test.
- The pane's dispatch observer no longer raises when its batch lands after
  the pane is pruned. Being an observer, the failure surfaced only as a
  logged ERROR on teardown.

## [0.25.0] - 2026-07-29

### Added

- **Click a tab to switch to it.** The tab bar's cells are clickable and route
  through the same activation path as `Ctrl+1..9` / `Ctrl+Tab`, so a click also
  clears the unseen mark and focuses the input. Hovering underlines the tab.

### Fixed

- **Monitors stay visible when a session renames itself.** The strip captured
  its handle at compose time, so any monitor a renamed agent armed afterwards
  was filtered out and never appeared. Monitors armed *before* a rename were
  worse: their completion wake went to a handle nothing answered to. They now
  move with the session, alongside the existing inbox and locks renames.
- **Two teardown crashes.** Pruning a widget clears its component styles while
  the compositor can still render it a tick later, which panicked the app
  through `TextArea`'s render path (closing the last tab did exactly that).
  And messages dispatched during teardown hit a bare
  `query_one(ContentSwitcher)` in `_active` / `_refresh_tabbar` /
  `_write_snapshot`, panicking on `NoMatches` — they now treat a missing DOM
  as nothing to do, which also stops a half-dismantled roster from reaching
  `workspace.json`.
- **The file indexer no longer leaks an inotify instance per app.** Its
  watchdog observer was released only in `action_quit`, so any other exit path
  kept it — 128 per user is the kernel's budget.

### Changed

- **Monitors stack, one per row**, instead of sharing a single line where a
  long description pushed the next monitor's bar off the edge.
- **Agents are asked for a `progress` condition on every monitor.** The tool
  docstring, briefing, and per-session priming now treat it as the default
  rather than an option, with recipes for deriving one — it is the difference
  between an ETA and an anonymous spinner.

### Internal

- Every test runs in its own project directory. The suite shared the repo's
  real `.aegis/state`, so one test's saved workspace was resumed by the next
  test's app — and by the next run, since nothing cleaned it up. A leftover
  terminal entry alone could hang an unrelated test. Also cuts `test_tui.py`
  from 307s to 39s.

## [0.24.1] - 2026-07-29

### Fixed

- **Session history is now `Ctrl+R`** (was `Ctrl+H`, which never fired). Most
  terminals send Ctrl+H as `\x08`, which the xterm parser reports as
  `backspace` — the binding was dead everywhere except terminals speaking the
  kitty keyboard protocol. `Ctrl+H` stays bound as a hidden alias for those.

## [0.24.0] - 2026-07-28

### Session history (`Ctrl+H`)

- **New: a persistent, cross-process record of your sessions.** `Ctrl+H` opens
  a modal listing every user-initiated agent session — open or closed, this
  launch or a previous one. Enter does the right thing per row: jump to a live
  tab, **resume** a closed one with full conversation continuity (Claude / any
  resume-capable driver), or open a fresh session with the recorded profile.
  Filter as you type; Up/Down to navigate.
- Backed by two new event variants (`SessionMeta` header + `SessionClosed`
  marker) written into the existing per-session `.jsonl` log. The header is
  written lazily on the first user message (so its preview is populated) and
  only for user-initiated TUI tabs — queue workers, group members, and
  workflow agents are excluded. A header with no close marker reads back as an
  inferred crash.

### TUI performance

- **Streaming no longer re-parses the whole message per token.** Assistant text
  streams as plain `Text` and is parsed to Markdown once when the turn settles
  — ~90× less render work on a long message, and it no longer ran in background
  panes. Streamed text now explicitly follows the bottom while you're at the
  tail (and never yanks you down when you've scrolled up).
- **Background panes freeze their spinner timers.** A hidden tab's
  WorkingIndicator + per-tool spinners no longer tick at 10 Hz into the shared
  message pump; they resume on show. Multiple busy tabs no longer compound.

## [0.23.0] - 2026-07-28

### Harnesses are a registry; models and effort are per-session

- **New: a top-level `harnesses:` section in `.aegis.yaml`.** A harness is a
  named provider entry — a driver plus its credentials/endpoint — declared
  once and referenced by agents. `openrouter: {driver: lovelaice, base_url:
  …, api_key_file: …}` lets you point the same driver at two different
  endpoints (an OpenRouter and a local Ollama, say), which the old
  one-provider-per-agent shape couldn't express. The four driver strings
  (`claude-code`, `gemini`, `opencode`, `lovelaice`) auto-register as
  implicit harnesses, so every existing config loads unchanged.
- **New: pick model and effort when you open a tab.** The agent picker is now
  two-tier — named presets on top (unchanged), registered harnesses below.
  Choosing a harness opens a model picker (the live catalogue, with free-text
  fallback) and, for claude-code, an effort picker. The pick is a transient
  agent; it isn't written to `.aegis.yaml`.
- **`aegis_spawn` and `/spawn` take `model` / `effort` / `prompt` overrides**,
  layered over a named profile without persisting. Queues, schedules, and
  groups are untouched — they still resolve named profiles.
- **New: `aegis config harness {add,list,remove}`** authors the registry from
  the shell; the TUI's Add-agent modal is harness-aware.

### OpenCode is first-class — including its free models

- **`agent.model` now selects the OpenCode model.** `opencode acp` takes no
  model flag, so the driver injects `OPENCODE_CONFIG_CONTENT`; the free tier
  (`opencode/deepseek-v4-flash-free`, `north-mini-code-free`, and the other
  `-free` models) is reachable from aegis for the first time. Verified against
  the real CLI, and per-session MCP injection still works alongside it.

### Personas: an optional system prompt per agent

- **New: an agent may carry a `prompt:` file** — a persona (a reviewer, a
  Spanish writer, a terse ops agent). It's read at spawn and injected as a
  system prompt that composes with, never replaces, the aegis handle primer.
  Claude appends it after the primer; ACP drivers prepend it on the first
  turn (verified: a real OpenCode agent obeys the persona).

## [0.22.0] - 2026-07-24

### Live Claude quota in the status bar

- **New: the status bar shows how much of your Claude subscription is spent.**
  `⧗ 5h 64% · wk 7%` sits beside the metrics whenever a Claude agent is open —
  the quota is an account property, so a background worker burning the window
  while you sit in another tab is exactly the case this catches. No Claude
  agent open means no segment and no network call at all.
- Past 80% the window that is running out grows a reset countdown
  (`⧗ 5h 87% ⟶2h14m`), because "when does it reset" is only a question once
  the number is high. Amber and red follow the API's own severity rather than
  a threshold invented here.
- A failing fetch says so rather than going quiet: `⧗ quota — auth expired`,
  `— no credentials`, `— rate limited`, `— unreachable`. A single failed poll
  keeps the last numbers, dimmed, with their age; only sustained failure drops
  them. A 429 additionally parks the poller for five minutes, and an explicit
  `/usage quota` will not override that backoff.
- **New: `/usage quota`** prints every window the API reports — percent,
  severity, reset time and countdown — forcing a fresh read. Works in the web
  client too.
- Remote mode (`aegis --remote`) shows no quota: the agent runs on the daemon
  host and spends that host's account, not yours.

### The status bar fits the terminal

- **The bar no longer clips.** It was composing ~226 columns unconditionally
  and letting Textual cut whatever fell off the right. Segments now carry
  progressively narrower forms and a priority, and the bar degrades from the
  bottom until it fits — dropping what never changes (build string, model name,
  system stats) before what does (state, loop, quota, tokens and cost).
- Metrics narrows in four stages, shedding the tool counter and throughput
  first, then the cached and reasoning shares, keeping tokens, cost and turn
  time to the end. `ctx 88.2K (44%)` becomes `ctx 44%` when space is tight —
  the percentage is the part you act on.

### Interrupts stop being destructive

- **A monitor no longer cuts a busy agent's turn.** It used to interrupt any
  real turn so the notice "landed immediately" — but the agent is usually
  still finishing the very turn that armed the monitor, so a fast `done`
  condition threw away the tail of that turn (its closing message included)
  for nothing. The callback is now buffered and chained at the turn boundary,
  the same path a queue result or a handoff takes. `aegis_monitor(...,
  interrupt=True)` opts back in when the news genuinely can't wait — matching
  `aegis_handoff(..., interrupt=True)`.
- **An interrupt now drains what was queued behind the turn it cut.** A
  cancelled turn never reaches `_chain_if_pending`, so monitor callbacks,
  queue results and chips buffered while it ran used to strand until some
  unrelated future poke. `AgentSession.interrupt()` dispatches them as the
  next turn. Only the inbox tier — Esc still means stop for reminders and
  `/loop`. Callers that deliver their own message right after (send-with-
  interrupt, `aegis_handoff(interrupt=True)`, monitors that opted in, queue
  cancel) pass `drain=False` so everything goes out as one turn.
- `AegisApp.interrupt(handle)` now awaits the pane's interrupt worker instead
  of firing it off. It was returning before the turn was actually cut, so a
  peer's follow-up delivery could land against a still-working session.

### `/loop` — repeat an instruction until the agent says it's done

- **New: `/loop <instruction>` arms a looping instruction on a session.** The
  instruction is re-delivered every time the session would otherwise settle
  idle, until the agent judges it satisfied and reaps it with
  `aegis_loop_stop`. It sits at the *lowest* rung of
  `AgentSession._chain_if_pending` — below inbox messages, below the
  spontaneous-event drain, below reminders — so handoffs, monitor callbacks and
  anything you type preempt the next iteration rather than starving behind it.
- **A loop yields to an armed `aegis_monitor`.** While a monitor is watching
  the handle the loop does not fire and its counter does not advance. Without
  that gate, `/loop run the tests` plus a monitor is a spin loop: the agent
  burns whole turns asking "done yet?" while the monitor waits to wake it.
- Five ways out: `aegis_loop_stop` (the intended one), the iteration cap
  (default 20, `--max N`) which reports as *capped* rather than completed,
  `/loop stop`, Esc — interrupt means stop, or the loop re-fires the moment the
  interrupted turn ends — and a harness error, so a broken session can't spin
  on its own error. `/loop` alone shows status; the status bar carries
  `⟳ loop 3/20`.
- Loops are session-scoped and in-memory. They do not survive a restart, by
  design: auto-firing a restored loop would mean a cold TUI starts spending
  tokens at boot without anyone asking it to.
- Agent-armed loops (an `aegis_loop` tool) are deliberately **not** in this
  release. When they land they'll sit behind the same human-approval gate
  dynamic workflows use.

### Fixed

- **Turn-end `aegis_remind` never found its session in the TUI.**
  `ReminderService._session_for` looks up `getattr(sm, "get", None)`; in the
  TUI the session manager is the `AegisApp`, which had no `get()` (nor does
  Textual's `App`), so every turn-end reminder answered "no live session".
  Only `aegis serve` — the path the feature was smoke-tested on — worked.
- **Background-mounted panes stacked on top of the active tab.** Textual's
  `ContentSwitcher` hides children only at its own mount or on a `current`
  old→new transition, so a bare `cs.mount()` left the new pane visible.
  Affected agent-spawned sessions (`aegis_spawn`, queue workers), terminals
  restored on reload, and restored file tabs — which were also stealing
  `cs.current` from the resumed active tab.

### Added

- The running build (`aegis 0.21.0+<sha>`) now shows in the TUI status bar,
  resolved at import so it reports the code this process actually loaded
  rather than whatever the checkout has moved on to.

### Self-reminders — `aegis_remind`

- **New: an agent can leave a note for its future self, delivered back to its
  own inbox.** Two timings, one tool. `aegis_remind(from_handle, note)` (no
  `after`) is a **turn-end** reminder: it comes back as the session's very
  last turn once everything else settles — the new lowest-priority tier in
  `AgentSession._chain_if_pending`, strictly *behind* buffered inbox messages
  (monitor / queue / handoff callbacks) and behind any spontaneous
  harness-event drain. If the turn ends but the inbox still holds items, those
  are consumed first; the reminder is the last thing.
- `aegis_remind(..., after="20m")` (seconds or a duration string like `30s` /
  `2h` / `1h30m`) is a **future-time** reminder: a lightweight `ReminderService`
  timer drops the note into the inbox at that time, where it behaves as an
  ordinary message (waking the agent if idle). `aegis_reminders` /
  `aegis_reminder_cancel` list and cancel pending future-time reminders.
  Reminders are in-memory (not persisted across a `serve` restart).

## [v0.21.0] - 2026-07-22

### Reasoning-token accounting — real counts + `% think`

- **Fixed: every Claude 'thought' block showed `~1 tok`.** The compact thought
  summary estimated tokens from `len(text)//4`, but Claude redacts the
  reasoning text (it streams empty), so the estimate always floored to 1.
  Claude *does* stream the real running estimate via `system/thinking_tokens`
  events — which the parser dropped as `Unknown`. Those are now parsed into a
  typed `ThinkingTokens(estimated, delta)` event; the per-block total is
  stamped onto `AssistantThinking.token_estimate` and used by every renderer
  (TUI live pane, static/replay, web, server-side HTML), falling back to the
  length heuristic only for harnesses that stream the reasoning text instead.
- **Status line shows the reasoning share of output.** Cumulative thinking
  tokens accumulate into `SessionMetrics` and render as a breakdown of the
  output segment: `↓73.9K (80% think)`. Shown only for harnesses that report
  it. The cost-view `thinking_tokens` stays 0 — reasoning is billed inside
  output, so counting it again would double-bill.
- **`ThinkingTokens` are transient.** Hundreds stream per turn, so they're
  skipped by the session-log observer and the web fan-out (persisting them
  would bloat the log and drift the event-seq index); the cumulative estimate
  rides on the persisted `AssistantThinking` block and the state-frame metrics.

### Monitors — the aegis monitor is the authoritative waker

- **Fixed: a monitor watching a native background task could stall the wake
  ~1 min ('interrupted').** When a monitor's `done` watched a Claude
  `run_in_background` task's output file, it raced that task's own completion
  notification: the monitor saw the session `working` (actually the harness
  draining its OWN notification as an unsolicited turn) and interrupted it
  mid-resume, wedging the wake behind an extra replay cycle. The monitor no
  longer interrupts an unsolicited-turn drain.
- **The monitor now leads.** While a monitor watches a handle, the session
  holds back promotion of the harness's own spontaneous events into a
  competing unsolicited turn — they queue and fold into the turn the monitor's
  delivered message drives. Result: a single authoritative wake, so pairing a
  monitor with a native background task is safe. `aegis_meta` guidance updated
  to say the monitor is the preferred waker.

## [v0.20.0] - 2026-07-21

### Working-indicator lifecycle + compact 'thought' blocks

- **Fixed: the working spinner could linger after Escape-interrupt.**
  `session.interrupt()` emits `(ready, finished=False)`, but the pane only
  removed the spinner on `finished=True` — so it was orphaned whenever no
  trailing `Result` arrived. The indicator is now reconciled to the live
  state (visible iff the agent is working), so interrupt clears it
  deterministically.
- **Fixed: the spinner didn't return on a self-woken / chained turn.** When a
  harness emitted events after `Result` (a background Monitor firing, an inbox
  wake), a lingering/frozen indicator made `_start_indicator()` no-op.
  `WorkingIndicator.start()` is now idempotent (cancels prior timers) and
  `_start_indicator()` always (re)starts a live one.
- **Reasoning blocks render as a compact summary.** A streamed thinking block
  now shows `💭 thought · 0:42 · ~1.2k tok` (elapsed + approximate token count,
  ~4 chars/token — the harness doesn't report thinking tokens) instead of a
  wall of reasoning text. The full reasoning stays in the block's copy payload.
  Replay matches (minus the duration, which history doesn't record).

### Process monitors — `aegis_monitor` (poll, visualize, auto-wake)

- **Wait on a long process without polling.** A new MCP tool `aegis_monitor`
  replaces the agent's `while …; sleep; tail` pattern. aegis does *not* own the
  process — the agent launches it (or it already runs, like a dev server) and
  hands aegis bash conditions evaluated on an interval in the session cwd:
  `done` (exit 0 ⇒ complete), optional `fail` (exit 0 ⇒ failed), optional
  `progress` (echoes 0–100 for a bar + ETA). A `timeout_s` backstop is terminal.
- **Auto-wake on the outcome.** On any terminal state the agent is woken via its
  inbox — the same `InboxRouter.deliver` path as queue callbacks — **interrupting
  its current turn if busy** so the notice lands immediately; an idle agent is
  woken directly. The agent fires the monitor, ends its turn, and continues when
  it completes — no turns burned polling.
- **TUI strip.** A `MonitorStrip` above the status bar (mirrors `QueueStrip`)
  shows one row per live monitor: `pytest ▓▓▓░░ 62% · ETA 0:18`, or
  `dev server ⣾ 0:42 watching` when there's no progress. Drops off on completion.
- **Surface.** `aegis_monitor`, `aegis_monitors` (list), `aegis_monitor_cancel`
  (no agent callback on cancel); monitors auto-reap on session close. The agent
  priming instructs *always* using this over sleep/tail loops. Backed by a new
  `MonitorManager` (poll loop + interrupt-if-working delivery), memory-only in
  v1. Remote mode shows the TUI host's monitors; web-client parity is a follow-up.

### Status-bar system meter

- **CPU / RAM / disk at a glance.** The status bar gains a
  `CPU 23% · RAM 38% · DSK 71%` segment — all system-wide percentages, sampled
  once per app tick (not per pane) from the local host. `DSK` reports the
  filesystem holding the project root (the disk agents write into). A metric
  turns amber past 90% so a hot machine catches the eye. Backed by `psutil`;
  remote mode shows the TUI host's figures (per-remote stats deferred).

## [v0.19.0] - 2026-07-20

### Dynamic Workflows — Track 2 JSON DSL

- **Agent-authorable dynamic workflows as a validated JSON document.** The
  safe/data counterpart to Track-1 durable `@workflow` Python: a JSON spec
  describing a fan-out/pipeline orchestration of **real aegis agents across
  harnesses**, licensed by schema validation (a malformed spec is rejected at
  the tool boundary; a valid spec is safe by construction). Where a single
  in-harness feature cannot, a dynamic workflow spawns any profile / hands to a
  live session / delegates to a queue, is **durable across process restarts and
  hosts**, and can **pause for the operator** mid-run.
- **Node families.** `sequence` + `agent` (with `target` = `spawn` | `session`
  | `queue`); `map` (bounded-concurrency fan-out via an `asyncio.Semaphore`) +
  `parallel` (barrier); `loop` (hard-bounded `max_rounds`) + `if` (branch
  routing) with typed `shell` (true iff exit 0) and `judge` (agent-call)
  predicates; a TUI-only `human` node via `ask_human` (an `enum` schema becomes
  selectable options).
- **Data flow.** `refs` selectors and templates resolve against a run-scoped
  `Store`; `agent` nodes take `inputs` substitution and a JSON-Schema `schema`
  that coerces structured output (prompt-engineered + `jsonschema`-validated,
  one bounded reparse-retry).
- **Durability.** Per-node checkpoint/resume — on resume only the in-flight
  node re-runs; `loop`/`if` decisions replay deterministically.
- **Semantic validator.** Rejects id collisions, selectors referencing an id
  not declared earlier in document order, an `agent` node with no `target` when
  there is no default agent, and unknown `spawn.profile` / `queue.queue`
  references — before the workflow runs.
- **Cost gate + MCP surface.** `aegis_run_dynamic_workflow` validates, gates,
  and launches; a plan-preview reports the projected agent count (a labelled
  static upper bound); the gate is operator-implicit or prompts the agent above
  a cost threshold (`dynamic_workflow_autoapprove_agents` config key). This is
  also the first landing of the Track-1 gating rule the design called out as
  missing.
- Spec: `docs/superpowers/specs/2026-07-17-aegis-json-dsl-dynamic-workflows-design.md`;
  plan: `docs/superpowers/plans/2026-07-17-aegis-json-dsl-dynamic-workflows.md`.

### `aegis usage` — session usage & cost analytics

- **Read-only usage-and-cost dashboard over the session logs.** Aggregates the
  per-tab event streams already persisted to `.aegis/state/sessions/<handle>.jsonl`
  into a dashboard plus deeper cuts — temporal (usage over time), per-session,
  and per-tool — with segment-aware cost and token-split math and price
  resolution against the model registry.
- **`/usage` slash command (TUI + web).** Reuses the shared renderer so the
  in-app command and the `aegis usage` CLI surface identical figures.
- Spec: `docs/superpowers/specs/2026-07-17-aegis-usage-command-design.md`;
  plan: `docs/superpowers/plans/2026-07-17-aegis-usage-command.md`.

## [v0.18.0] - 2026-07-17

### Slash commands 2D — command palette (drop-up typeahead)

- **Inline drop-up completion panel.** Typing `/` raises a panel above the
  input with fuzzy-matched commands and their one-line summaries; past the
  verb it completes subverbs and **live argument values** — agent slugs (with
  `harness · model · permission`), session handles (`agent · state`), queue /
  group / schedule / terminal names, theme ids, and flag names. A ghost usage
  hint shows the remaining arguments. Up/Down navigate, Tab/Enter accept, Esc
  dismisses; Enter with the panel closed submits as before.
- **One engine, two frontends.** A pure `complete(text, bridge) -> Completions`
  in the harness-agnostic commands core drives both the TUI (`CommandPalette`
  widget mounted above the input, with a `GrowingInput.key_interceptor` hook)
  and the web client (a `complete` WS RPC feeding a drop-up `<div>`), so both
  show identical candidates.
- **Completer seam.** `Arg` gains an optional `completer` (a static tuple or a
  `(bridge) -> choices` callable, each choice a value or `(value, detail)`
  pair). Subverbs are the first positional's completer; dynamic values are a
  later positional's bridge-driven completer. A new `fuzzy` scorer ranks
  matches. `complete()` never raises — a throwing completer contributes no
  items.

### Slash commands 2C — prompt commands + plugin `@command`

- **User-authored prompt commands.** `.aegis/commands/<name>.md` files register
  as `source=user` commands: frontmatter carries `description` / `argument-hint`,
  and the body is a template with `$1..$9` / `$ARGUMENTS` substitution, `@file`
  includes, and embedded `` !`shell` `` expansion (args-first, so a `$1` inside
  an `@file` or `` !`…` `` resolves before the include/shell runs). Expansion
  rides the `CommandResult.effect` `{"kind":"deliver"}` channel, so both the TUI
  and web seams send the rendered text to the agent as a normal message —
  Claude-Code slash-command parity.
- **Plugin `@command` decorator.** A `@command` decorator sits beside
  `@workflow` / `@hook` / `@tool`; commands defined in a plugin are
  auto-registered (`source=plugin`) on the plugin import sweep. An example ships
  in the bundled plugin.
- **Source precedence.** Both loaders plug into 2A's `source`-tagged registry,
  now with a full precedence rule in `register()` — **builtin > user > plugin** —
  so a user file shadows a plugin command of the same name but can never override
  a protected builtin. The 2D palette color-codes the three sources.
- **Boot-load, no live watch.** Prompt + plugin commands load at TUI `on_mount`
  and at `serve` boot; there is no filesystem watch in this slice.

### Slash commands 2B — full builtin coverage

- **Operator-useful builtins over the `AppBridge`.** New commands drive the
  meta-harness from the keyboard: `/groups` (list / status / dissolve),
  `/schedules` (list / show / enable / disable / remove / logs), `/terminals`
  (list / new / run / close), `/rename`, `/close`, `/themes`, and `/clear`.
  Agent management folds into `/agents` (`add` / `remove`); queue listing
  lands on `/queues` (**renamed from `/queue`** — 2A was unreleased, so no
  alias).
- **Conventions.** Collection nouns are plural (`/agents`, `/groups`,
  `/schedules`, `/terminals`, `/themes`, `/queues`); a bare noun-command is
  equivalent to its `list`.
- **Effect channel.** `CommandResult` gains an optional `effect` dict the
  frontend seams apply after rendering the block — `/themes <name>` switches
  the live theme (TUI `app.theme`, web stylesheet + localStorage) and
  `/clear` cosmetically wipes the transcript, leaving a marker that shows the
  context tokens still in play (the agent's context is untouched). Threaded
  through both the TUI pane and the web client.
- **Builtins are a package.** `commands/builtins.py` became a `builtins/`
  package (one module per command family). Added a `list_groups` method to the
  groups bridge.
- **Deferred / dropped.** `/model` and `/effort` (mid-session model/effort
  change) are deferred to a 2B.1 session-mutation slice — they require a
  resume-restart, not a thin call. `/handoff` is intentionally not a command
  (redundant with switching tabs for the operator; agent→agent handoff stays
  the MCP tool); `/config` is dropped (agent verbs live on `/agents`).

### Slash commands 2A — parser + resolution core

- **Declarative typed arguments.** Commands now declare an `ArgSpec`
  (positionals — required / optional / greedy — plus boolean and valued
  `--flags`); `dispatch()` parses the input against it and hands the handler a
  validated `Args`, replacing per-handler `.split()`. Flags are recognized
  anywhere among the non-greedy positionals (a boolean flag may lead or
  trail); a trailing greedy positional stops flag parsing so free-text
  (prompts) survives verbatim, `--x` and quotes included.
- **Protected builtins + command sources.** The registry tags each command
  with a `source` (`builtin` / `user` / `plugin`); a non-builtin command that
  collides with a builtin name is rejected (`CommandCollision`). `/help`
  groups by source. This is the seam the 2C user/plugin loaders plug into.
- **`//` literal-slash escape.** Typing `//foo` delivers a literal `/foo`
  message to the agent instead of running a command, via a shared
  `classify_input()` helper used by both the TUI and web seams.
- **`/queues new` persistence.** `/queues new <name> [agent]` writes to
  `.aegis.yaml` (comment-preserving, same path as `aegis config add-queue`)
  and hot-registers; `--ephemeral` keeps the old session-only behavior.
  (2B renamed `/queue` → `/queues`.)
- **Web parity.** The web `deliver` RPC routes `/command` through the same
  `dispatch()` and returns a `command_result` frame the client renders as a
  transcript block; `//` unescapes there too. The slash surface now works
  identically from the TUI and web input boxes.
- Spec/plan:
  `docs/superpowers/specs/2026-07-17-aegis-slash-commands-2a-parser-resolution-design.md`,
  `docs/superpowers/plans/2026-07-17-aegis-slash-commands-2a.md`.

## [0.17.0] - 2026-07-16

### Removed

- **The Telegram frontend is gone** (breaking). The bot bridge, its command
  surface, and the `telegram_*` config keys were removed; the web client is
  the remote surface now. Earlier entries below that describe Telegram
  features are retained as the historical record of what shipped at the time.

### Slash commands + shell escape (TUI input)

- **`!<command>` shell escape.** Typing `!cmd` in the input runs `cmd` in a
  local shell (in the session's project root) and injects `$ cmd` plus its
  combined stdout/stderr into the conversation as your next message, so the
  agent sees the result. Output is capped, merges stderr, notes a non-zero
  exit, and times out after 60s. A bare `!` is a no-op.
- **`/<command>` slash commands (Phase 1).** Control commands aegis executes
  itself — a human-facing front-end over the same `AppBridge` surface agents
  drive through MCP; they never reach the harness. Results render as a
  `/`-glyph transcript block (red on failure); unknown commands point at
  `/help`. Builtins: `/help`, `/sessions`, `/agents`, `/spawn <agent>
  [prompt]`, `/queue new <name> [agent]`, `/enqueue <queue> <payload>`. The
  registry + pure `dispatch()` are harness-agnostic so the web client can
  reuse them. Spec:
  `docs/superpowers/specs/2026-07-16-aegis-slash-commands-design.md`.
- **Input-prefix accents.** While the input starts with `!` / `/`, its
  outline **and text** turn magenta / bright blue so a shell escape or command
  reads as distinct from a plain message (green) at a glance. Precedence:
  recording > shell-escape > slash-command > working > idle.

### Live terminals — correctness fixes

- **OSC 133 parser handles ST-terminated sequences**, not just BEL. Modern
  shell integrations (starship, VTE, Ghostty) terminate `]133;C` with ST
  (`ESC \`); the old BEL-only scan ran past it and **swallowed the following
  `]133;D` exit marker**, so `aegis_term_run` never saw command-end and always
  timed out on those machines. The parser now strips *all* OSC sequences (VTE
  / title / cwd noise no longer leaks into captured output), recognizes the
  `C` (output-start) marker, and returns an **ordered** output/event stream so
  a same-read `B…C…output…D` no longer wipes the output.
- **Marker-injection fallback** when shell integration is unavailable: aegis
  injects its own `printf` boundary markers so command-boundary + exit
  detection still work instead of hanging (the fallback the spec promised but
  never wired).
- **Bounded default `run()` timeout (120s)** so an interactive/never-returning
  command can't hang the per-terminal lock forever; a reader-loop crash now
  finalizes the pending command instead of stranding it.
- **Cleaner capture**: the echoed command line + prompt redraw are stripped
  (reset at the command-start / output-start markers).
- **Stops clobbering your shell**: array-aware prepend into `PROMPT_COMMAND`
  (starship / atuin / direnv / VTE render through it); zsh uses the
  `precmd_functions` / `preexec_functions` arrays instead of replacing hooks.
- **Multi-line commands rejected** with a clear error (each newline is a fresh
  prompt cycle → multiple `D` markers → corrupted correlation).

### Status-line tok/s meter

- The status line now carries a rolling **`⚡ N tok/s`** generation-speed
  segment (right after the `↓output` tokens): token-weighted output
  tokens/sec over the last 5 completed turns (`SessionMetrics.recent_tps()`,
  fed from a `(output, turn_seconds)` ring buffer on `commit`). Only real
  generation turns count — tool-only or instant turns are skipped — and the
  segment is hidden until the first such turn completes. Shows in both the TUI
  and the web status bar (both render from `SessionMetrics.render`).

### TUI input handling + handoff interrupt

- Richer chat-input gestures in `GrowingInput`: `Alt+Enter` (and `Ctrl+Enter`
  where the terminal distinguishes it) **send-with-interrupt** — cut the live
  turn and send the message now instead of queuing it behind the turn; `Esc`
  **clears a non-empty input** first and only interrupts the turn when the
  input is empty; `Up`/`Down` **recall previously-sent messages**
  (boundary-aware — active only at the first/last line so multi-line editing is
  intact — per-pane, session-lifetime, draft-preserving). `Enter` still
  enqueues; `Shift+Enter` / `Ctrl+J` still insert a newline (`Alt+Enter` moved
  off newline duty).
- The input outline now echoes the agent state dot: a **vivid green** outline
  when the agent is idle (live — your message acts immediately) versus a
  **subdued** outline while it is working (your message queues behind the
  turn), so you can tell at a glance whether you are writing against a live or
  busy agent. Voice recording still overrides with the amber outline.
- `aegis_handoff` gains `interrupt: bool = False`. `interrupt=True` cuts a busy
  peer's in-progress turn (via the new `AppBridge.interrupt(handle)`) before
  delivering, so the handoff lands as the peer's next turn instead of buffering
  — for blocking corrections a peer needs *now*. Returns `interrupted & landed
  at <target>` in that case. Default is byte-for-byte the old behavior.

### Web protocol v2 — compact-by-default + central persistence

- Events stream **compact-by-default**: heavy bodies are truncated on the
  wire (`ToolResult` clipped to a head, `ToolUse.raw_input` dropped,
  `AssistantThinking` body emptied) with a `truncated` flag; full detail is
  fetched on demand via the new `get_event` RPC. Foundation for the mobile /
  flaky-connection web PWA.
- `aegis serve`/web now persists sessions to JSONL like the TUI does
  (`SessionManager.attach_persistence`), so `seq` is a real disk line index in
  every frontend and web sessions survive a `serve` restart.
- `hello` advertises `protocol_version: 2` + `capabilities: ["compact"]`;
  `TOOL_RESULT_HEAD_LINES` / `TOOL_INPUT_HEAD_LINES` join the constants block.
- The web client renders transcripts **client-side** from the compact `event`
  payload (`renderEvent.js`, mirroring `render_html.py`); the server no longer
  ships a rendered `html` blob per event. Truncated blocks (tool input/output,
  thinking) expand on tap via `get_event`, cached per tab. This completes the
  wire diet — tool-heavy turns stream a fraction of the previous bytes.
- The web client is now an installable **PWA**: `manifest.webmanifest` + icon,
  a service worker that precaches the app shell and serves it cache-first
  (cache-busted per `server_version`), and a reconnecting banner for flaky
  links. Launches instantly and works installed; live actions require the
  connection (no offline outbox in v1).
- Mobile-first web layout: below 640px the client shows a session **list** and
  a full-screen **conversation** view (same DOM/state), with the composer
  pinned above the keyboard and a back control. **Swipe** left/right moves
  between agents. Desktop is unchanged; dashboards are desktop/TUI-only on
  mobile v1.

## [0.16.0] - 2026-06-26

### Unified input queue + click-to-dequeue chips

Every inbound message — text typed into the box and agent handoffs —
now flows through one inbox queue.

- `AgentSession.deliver` returns a `Delivery(landed | queued, depth)`
  receipt: `landed` when the agent was idle (consumed into a turn now),
  `queued` with its position when mid-turn. A new `on_dispatch` observer
  fires when a buffered batch starts its turn.
- Text-box input is no longer blocked while the agent works. It's
  delivered as a headerless `sender=user` message (a plain user turn)
  and, when the agent is mid-turn, shows as a click-to-dequeue **chip**
  above the input box (`PendingStrip`/`Chip`). Clicking a chip cancels
  that message before it reaches the agent. The input stays enabled —
  you can keep typing.
- `aegis_handoff` to a busy peer no longer rejects — it queues and
  returns `landed at <t>` or `queued for <t> (position N)`.

### Transcript windowing for long sessions

The TUI ConversationPane now keeps a bounded set of mounted blocks:
evicts the top when the count exceeds `N_MAX`, restores older blocks on
debounced scroll-up with anchor preservation, and trims the initial
replay so resumed long sessions start snappy. Sticky-bottom tracking
gates auto-scroll.

### pre_spawn hooks + socks-proxy plugin

- New `pre_spawn` hook event lets hooks rewrite a harness subprocess's
  argv/env before exec (wired in the Claude and ACP drivers).
- `socks-proxy` plugin tunnels harness subprocesses through
  `proxychains4`, built on the new `pre_spawn` seam.

### memory-system plugin (v0.1.0)

Second canonical plugin under `plugins/memory-system/`. Hermes-inspired
persistent memory:

- Per-project `.aegis/memory/` with `SOUL.md`, `USER.md`, and a
  `MEMORY.md` index over typed entries (`user` / `feedback` / `fact` /
  `reference`).
- `pre_turn` hook injects SOUL + USER + index + judgment primer on
  turn 0; top-5 entry teasers (name + description, 1000-word cap) on
  later turns.
- Five `@tool`s: `memory_add`, `memory_replace`, `memory_remove`,
  `memory_search`, `memory_read`.
- `dream` `@workflow` -- three-stage consolidate + synthesize pass over
  the last 7 days of `.aegis/state/sessions/`. Writes new entries +
  a dated `dreams/dream-YYYY-MM-DD.md` narrative log. Defaults to a
  Haiku-backed `dreamer` agent.
- Install optionally drops a daily 3am cron via
  `aegis.scheduler.push.write_atomic` (overlay file at
  `.aegis/schedules/memory-dream.yaml`); cron fires while `aegis serve`
  runs.

Proves the v1 plugin substrate generalizes beyond `skill-system` --
every primitive shape (`@hook`, `@tool`, `@workflow`) is exercised
end-to-end.

## [0.15.1] - 2026-05-29

The 0.15.0 release commit bumped `pyproject.toml` but didn't
regenerate `uv.lock`, so CI and the release workflow both failed on
`uv sync --locked` and nothing reached PyPI. This patch syncs the
lockfile to match the project version. No source-code or behavior
changes — 0.15.0 and 0.15.1 are byte-identical except for the lock.

## [0.15.0] - 2026-05-28

Two big lines land together: the **plugin substrate v1** (hooks +
tools + plugin install/uninstall + registry resolution + a canonical
`skill-system` plugin) and the **driver visibility parity arc** —
seven slices that close the gap on what Claude / Gemini / OpenCode
each publish on the wire, surfaced through one canonical event surface
the renderer treats identically.

### Plugin substrate (v1)

Five-slice plan landed end-to-end: hooks, tools, plugin
install/uninstall lifecycle, registry resolution, and a canonical
`skill-system` plugin.

- **Hooks** (`@hook`). `pre_turn` modifies the user message before it
  reaches the harness (`prepend_system`, `rewrite_user`, `block`);
  `post_turn`, `session_start`, `session_end` are observer hooks
  with per-hook timeout and JSONL logging. All four fire at the right
  point in `AgentSession`'s turn loop — `session_start` once at the
  top of the first turn, `session_end` on `close()`. Composition rules
  thread multiple `pre_turn` results deterministically.
- **Tools** (`@tool`). Decorator + registry with reserved-name guard,
  invocation wrapper that handles timeout / sync-or-async / JSONL
  logging, and FastMCP registration so every `@tool` lights up as an
  MCP tool the spawned agent can call.
- **Plugin lifecycle.** `plugin.toml` manifest parser,
  `InstallContext` for `_install.py` hooks, local-path install with
  rollback on failure, lockfile write/read, uninstall flow that calls
  `_uninstall.py` and strips config. `aegis plugin
  install/uninstall/list/show` typer subapp drives it from the CLI.
- **Registry resolution.** `gh:owner/repo[#path]` and `file://`
  registry URL parsers, `git archive` HTTPS fetch, install wired
  through registries; `aegis plugin update` + `aegis plugin search`
  round out the surface.
- **Canonical `skill-system` plugin** at `plugins/skill-system/`
  registers a `pre_turn` hook that lazy-loads filesystem skills + a
  `load_skill` MCP tool. Live integration test drives a real claude
  subprocess through the install → invoke loop.

### Driver visibility — parity slice 7 (SystemInit enrichment)

`SystemInit` carries optional `model`, `permission_mode`, `version`,
`available_commands` fields. Both substrates populate at boot:

- Claude `parse()` reads `system.init.model`,
  `system.init.permissionMode`, `system.init.claude_code_version`, and
  `system.init.slash_commands[].name`.
- `AcpSession.start()` emits a `SystemInit` immediately after
  `new_session` returns, with the agent name + version from
  `InitializeResponse.agent_info`. `AvailableCommandsUpdate` arrives
  later in the ACP timeline and surfaces as a follow-on `SystemInit`
  with only `available_commands` populated; consumers see two
  `SystemInit`s and can merge.

State `event_codec` round-trips every new field with
backward-compatible defaults.

This closes the 7-slice driver-visibility parity arc. The canonical
event surface now exposes every signal both substrates publish — what
the model is doing, what tools it invoked with what arguments on which
files with what diff, the running plan, mid-turn cost / mode / title
telemetry, and end-of-turn cost / stop_reason / per-model attribution.

### Driver visibility — parity slice 6 (mid-turn ContextUpdate)

New event types `ContextUpdate` + `CostUsage` are the canonical home
for mid-turn ACP telemetry that doesn't belong in the transcript:

- `UsageUpdate` (cost amount + context used/size) →
  `ContextUpdate(cost=CostUsage(…))`
- `CurrentModeUpdate` → `ContextUpdate(mode=…)`
- `SessionInfoUpdate` → `ContextUpdate(title=…)`

The renderer returns `None` for `ContextUpdate` so the pane skips it;
downstream subscribers (status bar, metrics observers) consume through
the standard event-observer surface — that wiring lands in a follow-on.
Claude has no equivalent — claude reports cost / size only at turn end
on `Result` (slice 5).

State `event_codec` round-trips cost/mode/title independently with
backward-compatible defaults.

### Driver visibility — parity slice 5 (Result enrichment)

`Result` event gains optional `stop_reason`, `ttft_ms`, `num_turns`,
`cost_usd`, `model_usage`, `permission_denials` fields. Each driver
populates whatever it surfaces (claude → all except cost is from
result.total_cost_usd; ACP → stop_reason from PromptResponse, cost
accumulated from mid-turn UsageUpdate.cost.amount, model_usage from
gemini's field_meta.quota.model_usage list).

The terminator line in the transcript grows from
`── done in 2.5s ──` to
`── done in 2.5s · $0.0042 · max_tokens ──` when the new fields fire.
Cost is shown only when > 0 (so claude-subscription turns stay
quiet) and `stop_reason` is shown only when non-default (so the
happy `end_turn` path stays silent).

State `event_codec` round-trips every new field with
backward-compatible defaults; legacy Result records still decode.

### Driver visibility — parity slice 4 (file-diff preview)

`ToolResult` now carries an optional `diff: (path, old_text, new_text)`
tuple that drivers populate for edit-shaped tool calls.

- ACP side: `session_update` walks `ToolCallProgress.content` for a
  `FileEditToolCallContent` block and captures `(path, old_text,
  new_text)` from it.
- Claude side: `ParserState` gains a `tool_diffs` dict; the `Edit`
  tool_use parser captures `(file_path, old_string, new_string)`, the
  `Write` parser captures `(file_path, "", content)` since Write
  overwrites. The matching tool_result attaches the cached diff.
- `render_event` shows a small unified preview when `diff` is
  populated and the call succeeded — trimmed common prefix/suffix,
  capped at 6 visible rows with a "… N more" footer, red `-` gutters
  for removed lines and green `+` for added. Failed edits still show
  the single-line `error …` form.

Real-CLI smoke against an opencode write of a 5-line file shows the
full added content live in the transcript:

```
✏️ write
  ┌ target.out
  │ + alpha
  │ + beta
  │ + gamma
  │ + delta
  │ + epsilon
  └
```

State `event_codec` round-trips the diff with backward-compatible
defaults.

### Driver visibility — parity slice 3 (AgentPlan blocks)

New canonical event `AgentPlan` + `PlanEntry` dataclass unifies
claude's `TodoWrite` tool input and ACP's `AgentPlanUpdate` notification.

- The claude `parse()` intercepts `tool_use(name="TodoWrite")` and
  emits `AgentPlan(entries=…)` instead of a generic `ToolUse`. Each
  entry's `content` + `status` (`pending` / `in_progress` /
  `completed`) flows through; priority defaults to `medium` (claude
  doesn't expose one).
- The ACP driver's `session_update` handler maps `AgentPlanUpdate` →
  `AgentPlan` with priority preserved.
- `render_event` grows a plan branch: `📋 Plan — N/M done` header
  followed by one row per entry with status glyphs (● completed,
  ◐ in_progress, ○ pending). High-priority entries bold their
  content; low-priority dim. Empty plans render `📋 (no plan)`.
- State `event_codec` round-trips with the natural shape; legacy
  records still decode.

Real-CLI smoke against an opencode planning turn: 4 distinct
`AgentPlan` events emitted (0/3 → 1/3 → 2/3 → 3/3 progress) with
proper glyphs interleaved with tool calls. The model's plan
revisions are now visible in real time instead of buried inside a
`⏺ TodoWrite(…)` blob.

### Driver visibility — parity slice 2 (chunk coalescing by message_id)

`AssistantText` / `AssistantThinking` now carry `message_id` (claude
from `assistant.message.id`, ACP from each chunk's `message_id`). A
new pure helper `aegis.render.coalesce_chunks` merges adjacent
same-`(type, message_id)` chunks into one event; any non-chunk event
(`ToolUse`, `ToolResult`, `Result`, …) breaks the run.

`replay_blocks` pipes its events through the coalescer before
rendering. An opencode session that persists ~116 thought-chunk
events under one message_id now renders as a single ✻ block on
resume instead of 116 separate lines. Live pane streaming was
unchanged — `_stream_append` already coalesced by kind for the
in-flight path.

Visible measurement (real opencode acp turn, file-read + write +
report):

- Raw events: **80**
- After coalesce: **9**

State `event_codec` round-trips `message_id` with backward-compatible
defaults; legacy persisted records still decode.

### Driver visibility — parity slice 1

Tool calls now read identically across all three drivers (claude
stream-json, gemini --acp, opencode acp) — every tool call carries
its semantic `kind`, `tool_call_id`, structured `raw_input`, and file
`locations`, and `ToolResult` correlates back via `tool_call_id` so
`kind` is available downstream. The TUI renderer picks a glyph by
kind (📖 read, ✏️ edit, ⌬ execute, 🔎 search, ✻ think, 🌐 fetch, ➡️
move, 🗑 delete, 🔄 switch_mode, ⏺ fallback) and shows a path tail
hint (`foo.py:42`) instead of the bare tool name.

Bug fixes:

- ACP failed tool calls were silently rendered as green ok lines —
  `is_error` is now derived from `ToolCallProgress.status == "failed"`.
- Gemini's PromptResponse usage was always zero because Gemini puts
  token counts in `field_meta.quota.token_count` (wire alias `_meta`),
  not in the canonical `usage` field — `AcpSession.send` now falls
  back to `field_meta.quota` when `usage` is None.

Spec: `docs/superpowers/specs/2026-05-28-aegis-driver-visibility-parity-design.md`.
Slice 2 (chunk aggregation by `message_id`) is next.

## [0.14.0] - 2026-05-28

### Workspace recovery: complete

`aegis` now restores the full previous workspace on relaunch — every
ConversationPane (claude-code via `claude --resume`; gemini and opencode
via ACP `loadSession`), every TerminalTab (re-spawned as a fresh shell
over its existing ledger), and every FileTab (re-opened at the saved
path). Both `Ctrl+Q` and crash exits persist a final snapshot so any
session_id latched mid-turn reaches disk and the next boot can resume.

Key wiring:

- `AegisApp.on_mount` loads `~/.aegis/state/workspace.json` once and
  threads the snapshot through `_resume_agent_tabs`, `_resume_terminals`,
  and `_resume_files` — so the default-spawn that fires when no agent
  tabs were resumable no longer clobbers terminals / files (a
  pre-existing bug that meant terminal-resume never actually worked).
- New `_boot_done` guard suppresses snapshot writes during the on_mount
  sequence so `self.theme = …` triggering `watch_theme → _refresh_tabbar`
  can't overwrite the saved roster before resume runs.
- `action_quit` now writes a final snapshot before teardown.
- `AcpDriver` advertises `supports_resume = True`; `AcpSession.start()`
  calls `conn.load_session(session_id=…)` instead of `new_session(…)`
  when a resume id is set. If the spawned agent doesn't implement
  `loadSession`, the resumed tab surfaces a clear ⚠ banner.
- New `WorkspaceFile(path, order, created_at)` schema entry; file
  tabs persist via `_write_snapshot` (filtering FileTab panes) and
  restore via the existing `_open_file_tab` path. Dirty buffers and
  cursor positions intentionally NOT preserved — file tabs are
  viewers, not long-lived sessions.

### Model registry: YAML-backed + auto-refresh

The hardcoded `aegis.budget.prices.PRICES` dict and the substring-pattern
`context_window_for` function are gone — both now derive from a single
canonical YAML at `src/aegis/data/models.yaml`, served by a new
`aegis.models` registry module. At CLI boot, `aegis` fires a best-effort
background fetch of
`https://raw.githubusercontent.com/apiad/aegis/main/src/aegis/data/models.yaml`
into `~/.cache/aegis/models.yaml` (24h TTL). The cache wins over the
bundled file on next load — so updating prices or adding new models is
a single PR to `main`, no release required. Cache failures (404, HTML
body, partial download) never corrupt the local copy: the fetcher
parse-validates before atomic replace, and a corrupt cache silently
falls back to the bundled YAML.

Public surface:

- `aegis.models.get_prices(provider, model)` — exact + alias match,
  raises `UnknownPriceError` on miss (preserves the legacy
  `prices.lookup` contract).
- `aegis.models.get_context_window(harness, model)` — exact, then
  alias, then `context_window_patterns` substring fallback, then
  provider default; 0 for unknown providers.
- `aegis.models.models_for(provider)` — `(name, label)` pairs powering
  the picker.
- `aegis.budget.prices` is a thin backward-compat shim over the
  registry; existing callers (`cost.compute`, queue manager, budget
  evaluator) keep working unchanged.

### Registry-backed model picker in `AddAgentModal`

The Add-Agent modal's model field is now a `Select` populated from
`aegis.models.models_for(<provider>)`. Switching providers repopulates
the dropdown; picking `<custom>` reveals an Input for any arbitrary
model name. `ModelEntry` gains an `aliases` list (so `claude-opus-4-7`
and `opus` resolve to the same prices) and an optional `label` for the
"opus → claude-opus-4-7" picker subtitle.

### Refresh tooling

- `scripts/refresh-models.py` regenerates `models.yaml` from
  `https://models.dev/api.json` (the catalog OpenCode itself consults
  per opencode.ai/docs/models). Curation lives at the top of the script
  (CLAUDE_CODE / GEMINI / OPENCODE lists). `--diff` previews,
  `--apply` writes.
- `aegis models refresh` synchronously refetches the GitHub raw URL +
  reloads the in-memory registry (use when you don't want to wait for
  the 24h background TTL). `aegis models clear` deletes the local
  cache. `aegis models list [provider]` prints exactly what aegis
  currently sees.

### Model catalog corrections

The bundled catalog regenerated from models.dev surfaces several
inaccuracies in the prior hardcoded table:

- **Claude Opus 4.7 is $5 / $25 per MTok**, not the legacy Opus 4.1
  $15 / $75. A 3× cost-reporting error in earlier 0.13.x sessions.
- **Claude Sonnet 4.6 has a 1M context window**, not 200k.
- **Gemini lineup:** `gemini-3-pro-preview`, `gemini-3.5-flash`,
  `gemini-2.5-pro`, `gemini-2.5-flash`, `gemini-3.1-flash-lite` —
  model IDs match what `ai.google.dev` publishes.
- **OpenCode** entries now use the `<vendor>/<model-id>` form opencode
  writes in its own config, sourced from the same models.dev provider
  IDs: `anthropic/claude-{opus-4-7, sonnet-4-6, haiku-4-5}`,
  `google/gemini-{3-pro-preview, 3.5-flash, 2.5-pro, 2.5-flash}`,
  `moonshotai/{kimi-k2.6, kimi-k2-thinking, kimi-k2-0905-preview}`,
  `minimax/MiniMax-{M2.7, M2.1, M2}`, `deepseek/deepseek-{v4-pro,
  v4-flash, chat, reasoner}`, `alibaba/{qwen3.7-max, qwen3-coder-plus,
  qwen3.6-plus}`.

The pre-existing bare `kimi-k2.6` slug is preserved as an alias of
`moonshotai/kimi-k2.6` so existing `.aegis.yaml` files don't break.

Parser hardening: `cache_hit` / `cache_write` are now optional in the
YAML (many providers don't publish them); missing fields default to 0
and `thinking` falls back to `output`. Pre-fix, parsing raised
`KeyError` on Moonshot / MiniMax / DeepSeek rows that omit
`cache_write`.

### Live cost segment in the status line

The status line gains a USD cost segment between the ctx % and the
tool counter, recomputed every render from the running token tallies
against the rate card. Adaptive formatting keeps it short:

```
↑12.0k (45% cached) ↓3.1k · ctx 12k (6%) · 12.3¢ · ⚒ 4 · 3s / 1m12s
↑1.2M (60% cached) ↓45k · ctx 1.2M (60%) · $5.43 · ⚒ 28 · 4s / 18m02s
```

Sub-cent → `X.Y¢`, 1¢–99¢ → `N¢`, ≥$1 → `$X.XX`. Unknown model or
lookup failure drops the segment silently.

`SessionMetrics` gains `c_cache_write` (tracking `cache_creation`
tokens separately from `cache_read`, billed at different rates) plus
`provider` / `model` strings that drive the lookup, and five
`@property` accessors mapping internal counters to the attribute
names `aegis.budget.cost.compute()` reads — so the same registry
path powers both per-turn budget enforcement and the live status
line.

### Fixes

- **ACP usage mapping.** Gemini and OpenCode sessions were rendering
  0 / 0 / 0 / 0 for every status-line metric: the driver populated
  `Result.input_tokens` / `Result.output_tokens` as bare fields from
  the legacy `field_meta["quota"]["token_count"]` path but never set
  `Result.usage`, and `SessionMetrics.commit` reads `ev.usage`
  exclusively. The ACP SDK has had a structured
  `PromptResponse.usage` (`acp.schema.Usage`) with
  `input_tokens` / `output_tokens` / `cached_read_tokens` /
  `cached_write_tokens` / `thought_tokens` since the protocol added
  it — the driver now reads it directly and builds a `TokenUsage`
  with `thought_tokens` folded into `output` (every provider aegis
  surfaces today bills thinking at the output rate).

## [0.13.0] - 2026-05-27

### MCP config-edit surface

Spawned agents can now mutate `.aegis.yaml` through the same
comment-preserving, validated, atomic-write path the `aegis config`
CLI uses — 12 new MCP tools surfaced uniformly to every spawned
harness. Additive paths hot-register on the live `QueueManager` /
agent map / plugin loader so the "create queue → enqueue to it" loop
works within a single `aegis serve` session; removes persist to YAML
but defer effect to next restart, signalled via the return-value
`restart_required_for` field.

Read tools (4):

- `aegis_config_show` — full parsed `.aegis.yaml`; telegram token
  redacted to `<set>`/`<unset>`.
- `aegis_config_list_agents` — `[{slug, harness, model, effort,
  permission}, …]`.
- `aegis_config_list_queues` — `[{name, agent, max_parallel,
  budgets}, …]`.
- `aegis_config_list_schedules` — `[{name, cron, enabled, workflow},
  …]`.

Write tools (8) — all wrapping the corresponding `aegis.config.edit`
helper:

- `aegis_config_add_agent(slug, harness, model, effort?, permission?)`
  — **live** (registers on the agent map).
- `aegis_config_remove_agent(slug)` — persisted; restart required.
- `aegis_config_add_queue(name, agent, max_parallel, budgets?)` —
  **live** (registers on `QueueManager`).
- `aegis_config_remove_queue(name)` — persisted; restart required.
- `aegis_config_add_plugin_dir(path)` — **live** (re-runs
  `import_plugins` so any new `@workflow` registers immediately).
- `aegis_config_remove_plugin_dir(path)` — persisted; restart
  required.
- `aegis_config_set_schedule_enabled(name, enabled)` /
  `aegis_config_toggle_schedule_enabled(name)` — live; the existing
  `ReloadWatcher` picks the change up.

Every write returns
`{ok, live, restart_required_for, [note]}`. Validation failures
(unknown harness, duplicate slug, queue referencing a missing agent)
bubble up as `{error: ...}` with the same wording the human sees at
`aegis config …`. A per-server `asyncio.Lock` serializes writes; the
existing `_atomic_write` (tempfile + rename) keeps the on-disk file
well-formed under concurrent calls. Out of scope in v1:
`set_telegram`, `set_default_agent`, fully-live removes, dry-run
mode, and groups/remotes (no `aegis.config.edit` helpers yet).

Spec: `docs/superpowers/specs/2026-05-27-mcp-config-edit-design.md`.
Plan: `docs/superpowers/plans/2026-05-27-mcp-config-edit.md`.

### Live context-size meter

The status-line metrics widget gained a `ctx Nk (P%)` segment showing
the most recent turn's authoritative `true_input` against the model's
context window, alongside the existing cumulative `↑` total. While a
turn is in flight, `p_in` (the monotonic max of streamed assistant
usages) drives the live value; at turn end the committed
`result.usage.true_input` takes over. Window comes from a hardcoded
`context_window_for(harness, model)` map — Claude Opus 4.x at 1M,
Sonnet/Haiku 4.x at 200k, Gemini at 1M, anything with `1m` in the
model name at 1M, OpenCode 200k. Unknown harness suppresses the
segment.

## [0.12.0] - 2026-05-27

### BREAKING

- **`.aegis.py` removed.** `.aegis.yaml` is now the single config
  substrate. Migration: rewrite your imperative `Agent(...)` /
  `queues = {...}` / `telegram_token = ...` lines as YAML sections
  (see [Configuration](docs/configuration.md)). `find_project_root`
  keys off `.aegis.yaml`; any `.aegis.py` in the tree is ignored.
- **`aegis init` retired.** Bootstrap paths now: launch `aegis` in
  an empty directory (the TUI opens the ConfigPanel — press `a` to
  add an agent), or use the scriptable CLI verbs (`aegis config
  agent add <slug> --provider <…> --model <…>` writes a minimal
  `.aegis.yaml`).

### `aegis config` CLI surface

Scriptable, idempotent subcommands for every authorable section of
`.aegis.yaml`. Each writing verb routes through ruamel.yaml so
existing comments and key order are preserved, validates the
prospective body via `yaml_loader.load_config` before persisting, and
fails loud on invalid input (the on-disk file is unchanged):

- `aegis config show [--json]`
- `aegis config agent list / add <slug> --provider --model
                                       [--effort] [--permission] /
                            remove <slug>`
- `aegis config queue list / add <name> --agent --max-parallel
                                       [--budget …]+ /
                            remove <name>`
- `aegis config telegram show / set [--token --chat-id --auto-prompt
                                    + matching --clear-* variants]`
- `aegis config default-agent <slug>`
- `aegis config plugin-dir list / add / remove`

`--budget` format: `usd:1.00:1h` or `output_tokens:500000:1h`
(repeatable).

### TUI ConfigPanel

New tab type alongside `ConversationPane` / `FileTab` / `TerminalTab`.
Stacks four sections — default-agent + agents table, queues table,
telegram block (token redacted), plugin_dirs list — and re-reads
`.aegis.yaml` on each refresh.

- **Boot-into-panel.** Launching `aegis` in a directory with no
  `.aegis.yaml` no longer refuses to start. The TUI mounts the panel
  as the only tab, status bar nudges you to add an agent.
- **Mid-session.** `F2` opens (or focuses) the panel from any other
  tab. `Ctrl+,` was the original binding but most terminals don't
  deliver it distinctly from `,`.
- **AddAgentModal.** Press `a` on the panel → modal with
  slug/provider/model/effort/permission fields, validates through
  the same `add_agent` helper the CLI uses, refreshes on save.

### File picker — keyboard nav + bypass on unique match

- Up/Down/PgUp/PgDn move the highlight while focus stays in the
  Input (priority bindings).
- Top match is always preselected after each filter pass — Enter
  opens it without arrow keys.
- Escape is now a priority binding so the Input can't swallow it.
- Indexer poll is one-shot; was running every 150ms forever and
  clobbering your typed query.
- Ctrl+click on a backtick token bypasses the picker entirely when
  the token resolves to a unique indexed file (otherwise falls back
  to the prefilled picker, whose dismiss path now actually opens
  the file — the previous `push_screen` call dropped the result).

### File viewer — cancel-edit confirm bar

Escape in edit mode with unsaved modifications now shows a
`⚠ unsaved edits — [d] discard / [esc] keep editing` bar and parks
the TextArea read-only so the bar's keystrokes don't get typed into
the buffer. Clean buffer still exits edit mode silently.

## [0.11.2] - 2026-05-26

### File picker improvements

- Background `FileIndexer` (watchdog + `os.walk`) starts on app load — picker
  opens instantly instead of blocking on `rglob`. Ships its own comprehensive
  ignore list (`.git`, `__pycache__`, `.venv`, `node_modules`, `*.pyc`, etc.);
  does not parse `.gitignore`. Live-updates as agents create or delete files.
- `FilePickerModal` reads from `FileIndexer` when available; falls back to
  synchronous walk in test environments without a full `AegisApp`.
- `CopyableBlock`: click = copy text (restored); ctrl+click = open file from
  backtick token. Multiple tokens → `_TokenChooser` lets you pick which one.
  Tooltip updated to `"click to copy | ctrl+click to open file"` when tokens
  are present.

## [0.11.1] - 2026-05-26

### File viewer/editor

- `FileTab` — new TUI tab type for viewing and lightly editing any file with
  syntax highlighting (tree-sitter via `textual[syntax]`).
- Ctrl+O opens a fuzzy `FilePickerModal` with typeahead over the current
  working directory (up to 5000 entries).
- Clicking any backtick-wrapped token in an agent response opens the file
  picker pre-filled with that token.
- MCP tool `aegis_view_file(path)` lets agents surface a file to the operator
  mid-task; focuses an existing tab if the same path is already open.
- VIEW mode (default): read-only; 2s mtime polling auto-reloads on disk
  changes.
- EDIT mode (`e`): writable; disk changes show a warning bar with `[r]` reload
  / `[k]` keep options; Ctrl+S saves; Esc returns to VIEW.

## [0.11.0] - 2026-05-26

### Telegram renderer + correctness (buckets B+D from the v0.10 critique)

- Replace MarkdownV2-escape-everything render path with HTML parse mode.
  Worker replies with fenced code, bold, italic, blockquotes, links now
  render natively instead of as literal backslashes.
- Greedy chunker; replies >3 parts spill to a `.md` attachment with a
  500-char peek caption (uses new `sendDocument`).
- Status message becomes a live per-turn ticker — edits on tool-use
  boundaries instead of every 2s. Tool-call activity is now visible.
- Multi-observer migration: TUI and Telegram both register via
  `add_event_observer` / `add_state_observer`; two frontends can
  observe the same session without clobbering.
- New `add_close_observer` on `AgentSession`; `_active` clears on any
  session-close path.
- Telegram update offset persists across restart.
- Tactical fixes: send_message=None guard, refresh-loop exceptions
  caught and logged.

### New dependency
- `markdown-it-py>=3.0`

## [0.10.0] - 2026-05-26

### Added
- **Telegram substrate command surface.** Nine new chat commands
  reach every existing substrate from the phone:
  - `/queue list` + `/queue show <name>` — local-only (no cross-host
    queue endpoint yet).
  - `/schedule list [@peer]` + `/schedule show <name> [@peer]` +
    `/schedule run <name>` (local-only fire-now).
  - `/budget list [@peer]` + `/budget show <queue> [@peer]`.
  - `/peers` — list configured remotes with reachability probe.
  - `/help` + `/help <name>` — registry-driven.
- **Command registry** in `src/aegis/telegram/commands.py`. The five
  existing verbs (`/new`, `/close`, `/interrupt`, `/agents`,
  `/sessions`) migrated into the same registry; single source of
  truth for `/help`.
- **`@<peer>` cross-host syntax** parsed by the dispatcher. Each
  handler decides whether to honor it; commands that don't support
  cross-host return a clear error.
- **Plain-text output by default; tabular data in fenced code
  blocks** for proper monospace alignment on mobile. No
  MarkdownV2-escape gymnastics in any new command.

### Changed
- `TelegramFrontend.__init__` grows `bridge` and `cfg` positional
  params. Existing `aegis serve` wire-up updated; no external API
  change.

## [0.9.0] - 2026-05-26

### Added
- **Per-queue budgets.** Each queue may declare one or more
  `(constraint, window)` ceilings (USD or output-token) over a
  rolling window. New `aegis_enqueue` calls are rejected with a
  structured error when admitting the task would push the queue
  over any of the declared budgets; ALL budgets must allow. Rejection
  names every blocked constraint and an `unblock_at` ETA.
- **Cost accounting.** Existing per-queue JSONL audit now carries a
  `cost` field on every `completed` and `failed` record:
  `{usd, input_tokens, output_tokens, cache_hit_tokens,
  cache_write_tokens, thinking_tokens}` computed from
  `SessionMetrics` (committed c_in/c_out/c_cached counters) +
  a static per-(provider, model) price table at
  `src/aegis/budget/prices.py`. Unknown models record
  `cost: {error: "unknown_model"}` without crashing the finalizer.
  Failed workers count toward budget — they burned tokens too.
- **`BudgetExceeded` typed exception** for the workflow engine:
  `engine.enqueue` raises with the full Decision attached so
  workflow Python can choose a retry strategy.
- **`aegis_budget_status` MCP tool** with `target=None` local and
  `target="<peer>"` cross-host via the new `GET /remote/v1/budget`
  and `GET /remote/v1/budget/<queue>` HTTP endpoints.
- **`aegis budget` CLI** — `list` (one-line summary per queue) and
  `show <queue>` (full Decision with per-budget rows). `--remote
  <peer>` on both.

The TUI strip + dashboard band described in the spec are
**deferred to v0.9.1**.

Spec: `docs/superpowers/specs/2026-05-25-aegis-per-queue-budgets-design.md`.

## [0.8.1] - 2026-05-25

### Fixed

Three issues in the v0.8.0 wire-callback path that together prevented
callbacks from working in any documented configuration:

- **`RemotePlaneSpec` now carries a `peer_name` field.** v0.8.0
  `cli.py` read it via `getattr(..., None) or "this-serve"`, but the
  dataclass had no such field — so every outbound callback identified
  the sender as the literal string `"this-serve"`, which no real
  receiver's `remotes:` map names. Round-trip was 100% miss.
- **`aegis_enqueue(target=…)` now defaults `callback` to False**
  (matching v0.7 fire-and-forget semantics). v0.8.0 made it default
  True, which silently broke pre-existing agent prompts that called
  the tool against a remote target without specifying callback —
  those calls began returning an error when this serve had no
  `remote_plane` configured. The signature also widened from
  `callback: bool = True` to `callback: bool | None = None` so the
  default can be context-sensitive (True for local, False for
  remote).
- **Loud rejection at MCP-tool boundary** when `callback=True` is
  set on a remote target and any of `remote_plane.peer_name` /
  `remotes[target].peer_name` / `remote_plane` block is missing.
  v0.8.0 silently sent `callback_to=None` on the wire in those cases
  and the receiver's observer dropped without error.

Also:

- **Callback observer now holds a strong reference** to every
  in-flight `asyncio.create_task` and discards on completion via
  `add_done_callback(set.discard)`. v0.8.0 fire-and-forget tasks
  could be garbage-collected mid-await under burst load, dropping
  callbacks without a log line. (Python docs explicitly warn about
  this pattern.)

Deployment behavior: fire-and-forget enqueues continue to work
unchanged. Callback-using deployments need `remote_plane.peer_name`
set in `.aegis.yaml` on the caller's side (and the matching
`remotes.<peer>.peer_name` on the caller's side already; symmetric
on the receiver). The MCP-tool error returned when something is
missing now points at the exact missing field.

## [0.8.0] - 2026-05-25

### Added
- **Wire callbacks for remote queues.** `aegis_enqueue(target=…, callback=True)` now actually delivers the worker's final message to the originating agent's inbox once the remote task terminates. Symmetric peers config (both sides define each other in `remotes:`); RemoteSpec gains an optional `peer_name` field that controls the `callback_to` round-trip. Best-effort, no retry, log+drop on miss; receiver's queue JSONL records every callback attempt.
- **Remote schedule control plane.** Five new endpoints under `/remote/v1/schedule` (PUT push, GET list/show, DELETE remove, GET logs); five matching `aegis_schedule_*` MCP tools (push/list/show/remove/logs, each with optional `target=` for cross-host); CLI `aegis schedule push --to <peer>` and `--remote <peer>` flag on inspection verbs. Pushed schedules land in the receiver's `.aegis/schedules/<name>.yaml` overlay folder with a `# pushed_from:` provenance comment; the v0.6 hot-reload watcher picks them up and they become indistinguishable from native schedules. Source classification (`inline` / `overlay` / `pushed`) is surfaced in list + show responses.

Spec: `docs/superpowers/specs/2026-05-25-aegis-remote-callbacks-schedule-control-design.md`.

## [0.7.1] - 2026-05-25

### Changed
- **Remote-plane public surface rewritten** to drop the
  Telegram-as-default-return-channel framing that crept in from the
  design spec. The remote plane has no built-in return channel; the
  `callback_note` string returned to the calling agent now reads
  *"no wire return channel in v1; completion behavior is whatever
  the receiving serve is configured to do"*. README, docs/remote.md,
  docs/index.md, docs/roadmap.md, docs/configuration.md, and the
  `aegis_enqueue` docstring rewritten in the same voice. Example
  URLs are now neutral tailnet IPs.
- No code-behavior changes — only one user-visible string (the
  `callback_note`) and the `aegis_enqueue` docstring. The wire
  protocol, queue semantics, and config schema are unchanged from
  0.7.0.

## [0.7.0] - 2026-05-25

### Added
- **Remote plane.** Server-to-server enqueue over HTTP. `aegis serve`
  exposes a second HTTP plane (distinct from the loopback MCP plane),
  bound to whatever address you want it reachable from, that other
  `aegis serve` instances can POST into. `aegis_enqueue` grows an
  optional `target=` parameter that routes the call to a configured
  remote's `/remote/v1/enqueue`; the remote enqueues into its own
  `QueueManager` (recorded with `enqueued_by="remote:<from>"`) and
  runs the worker on its own filesystem under its own agent profiles.
  In v1 there is **no wire return channel** — completion behavior is
  whatever the receiving serve is configured to do on queue
  completion; the calling aegis is not notified over the wire. Two
  new top-level sections in `.aegis.yaml`: `remotes` (outbound peers;
  `url` plus optional `token`; per-name overlay files at
  `.aegis/remotes/<name>.yaml` with fail-loud collision detection)
  and `remote_plane` (inbound bind address + optional
  `accept_tokens` bearer allowlist + optional `accept_from`
  source-IP allowlist; gates compose with AND; default off). All
  failure paths return clear, distinguishable error dicts to the
  calling agent — no silent fallback to local enqueue. Recommended
  deployment binds the plane to a private overlay network (Tailscale,
  Headscale, WireGuard, VPN) so the network itself acts as the
  outermost trust boundary; tokens and IP allowlists are
  defense-in-depth knobs on top. Docs: `docs/remote.md`.

## [0.6.0] - 2026-05-25

### Added
- **Agent groups.** Sixth coordination primitive: named committees
  of agents with one in-flight broadcast slot, a four-field broadcast
  contract (`objective`, `output_format`, `tool_guidance`,
  `boundaries`), `wait_all` and `wait_any` waiters (the latter with
  passive loser cancellation via `group:<name>/cancel:<id>` inbox
  envelopes), four built-in reducers (`concat`, `join_by_handle`,
  `last_wins`, `majority_vote`) plus `register_reducer` for custom
  reductions, append-only JSONL audit per group under
  `.aegis/state/groups/<name>.jsonl` with on-boot replay that ignores
  torn trailing lines. Nine MCP tools (`aegis_group_spawn`,
  `aegis_group_spawn_mixed`, `aegis_group_broadcast`,
  `aegis_group_wait_all`, `aegis_group_wait_any`, `aegis_group_status`,
  `aegis_group_dissolve`, `aegis_group_rename`,
  `aegis_group_move_member`). Mirror surface on `WorkflowEngine`
  (`spawn_group` / `broadcast` / `wait_all` / `wait_any` /
  `dissolve_group` / `rename_group` / `move_member`) plus the
  `engine.ephemeral_group(profiles=[…])` context manager for
  one-shot committees. YAML configuration: `groups:` section in
  `.aegis.yaml` with `defaults:` and `presets:`, drop-in overlays at
  `.aegis/groups/<name>.yaml`, preset-name collisions fail loud.
  `aegis_group_spawn_mixed(preset=...)` resolves presets from
  config. TUI surface: `GroupTabState` with aggregate-state emoji
  (`✓` / `⏳` / `⚠` / `⛔`) and `GroupDashboard` render with three
  panels (Members, Current broadcast, Recent broadcasts).
- **Scheduler substrate.** Cron-style scheduled workflow execution
  inside `aegis serve`. Declarative in `.aegis.yaml` under a top-level
  `schedules:` section; drop-in overlays under `.aegis/schedules/<name>.yaml`
  merge into the table with fail-loud conflict detection. Each entry
  declares `workflow`, `args`, a trigger (`cron` or `fire_at`), a
  `lifecycle` (`forever`, `once`, `{fires: N}`, `{until: <iso>}`),
  `on_overlap` (`skip` / `queue` / `kill`), and optional `notify` /
  `timeout` / `enabled` knobs. A single asyncio tick loop walks the
  table every 60 s, dispatches eligible entries through the workflow
  runner, and appends lifecycle events (`fire_requested` /
  `fire_completed` / `fire_failed`) to `.aegis/state/schedules/<name>.jsonl`.
  A derived snapshot at `.aegis/state/schedules.snapshot.json` carries
  the next-fire-time + in-flight flag per schedule for dashboards.
  On-boot replay rebuilds `fire_count` from the JSONL, closes dangling
  `fire_requested` records as `failed:interrupted`, and flags
  past-due fires for a single backfill.
- **Built-in workflows.** `prompt(agent, text)` spawns an agent, sends
  one message, closes; `enqueue(queue, payload, callback=false)` is
  the canonical scheduler→queue handoff.
- **`aegis schedule` CLI.** `list / show / run / enable / disable / logs`.
  `enable` / `disable` go through a comment-preserving ruamel.yaml
  editor so operator-curated YAML survives automation.
- **Hot reload.** A watchdog observer over `.aegis.yaml` and the
  overlay folders re-reads the config on every edit and atomic-swaps
  the running scheduler's schedule table. Parse errors keep the prior
  config intact and append a `reload_failed` record to
  `.aegis/state/aegis_events.jsonl`.

Spec: `docs/superpowers/specs/2026-05-25-aegis-scheduler-design.md`.

## [0.5.1] - 2026-05-23

### Fixed
- `tests/test_cli.py::test_version_flag_prints_and_exits` and
  `tests/test_cli_clean_flag.py::test_clean_flag_shows_in_help` both
  failed on CI for the v0.5.0 tag (the former hard-coded the prior
  version string; the latter assumed no ANSI escapes in Typer/Rich
  help output, which CI runners trigger via `FORCE_COLOR=1`).
  v0.5.0 was tagged but never published to PyPI as a result — 0.5.1
  is the first release of the 0.5.x line.

## [0.5.0] - 2026-05-23

### Added
- **Live terminals.** Fifth coordination primitive: a real PTY-backed
  shell (bash or zsh) that any agent or Alex can spawn, run commands
  on, send raw keystrokes to, read history from, and subscribe to.
  Command boundaries are detected from [OSC 133 shell-integration
  markers](https://gitlab.freedesktop.org/Per_Bothner/specifications/blob/master/proposals/semantic-prompts.md);
  every finalized command is appended to a JSONL ledger and fires
  a `✉ from term:<name>` inbox notification (with cmd / exit code /
  duration / stdout tail) to every subscriber except the writer.
  Eight MCP tools (`aegis_term_spawn / list / run / keys / read /
  subscribe / unsubscribe / close`). TUI surface: `Ctrl+E` opens
  a `term:<name>` tab with per-command blocks; the input bar has
  `run` (Enter submits a command) and `raw` (`Ctrl+K` toggles —
  every keystroke goes straight to the PTY) modes. State at
  `.aegis/state/terminals/<name>/` (meta.json + ledger.jsonl +
  raw.log + shell rcfile); `aegis --resume` re-spawns saved
  terminals as fresh shells over their existing ledger, and any
  commands that were in flight are marked `killed_by_restart: true`.
  Spec: `docs/superpowers/specs/2026-05-21-live-terminals-design.md`.
  Docs: `docs/terminals.md`.
- **Workflow catalog v1.** Four seed workflows under `aegis.workflows`:
  `brainstorm_to_spec` (interactive Q/A → spec doc), `execute_plan`
  (parse plan markdown → dispatch implementer per task with durable
  resume), `review_branch` (parallel multi-reviewer fan-out → markdown
  report), `tdd_cycle` (three-phase predicate-driven loop). Engine
  gains `ask_human`, `spawn`/`close`, `checkpoint`/`resume_state`,
  `bash_predicate`, `parallel`, `config`, `host`, `workflow_id`.
  Runner becomes a long-lived class owning background workflow tasks
  with a JSONL ledger at `.aegis/state/<id>/`; `aegis_run_workflow`
  MCP tool is now non-blocking. New tools `aegis_workflow_status` and
  `aegis_workflow_cancel`; new CLI commands `aegis workflow status`
  and `aegis workflow cancel`. Spec:
  `docs/superpowers/specs/2026-05-22-workflow-catalog-design.md`.
  Docs: `docs/workflows.md`.
- **Session persistence.** `aegis` resumes the last workspace by default;
  `aegis --clean` opts out. Per-tab event logs + workspace.json live under
  `.aegis/state/`. Tabs whose drivers don't support session resume
  (currently Gemini, OpenCode) are skipped with a startup banner.
- **Shared canvas.** Third coordination primitive after queues and
  inbox handoffs: a markdown file multiple agents can read, write
  sections of, and subscribe to. Writes fire `✉ from canvas:<name>`
  inbox notifications to every other subscriber with diff math + a
  preview — same delivery channel as queue callbacks and handoffs,
  zero new TUI. Six MCP tools (`aegis_canvas_open / read /
  write_section / append_to_section / subscribe / unsubscribe /
  list`); section ownership is by convention only in v1, ledger
  records who wrote what. State at `.aegis/state/canvases/<name>/`;
  the markdown file lives wherever the caller points it. Spec:
  `docs/superpowers/specs/2026-05-21-shared-canvas-design.md`. Docs:
  `docs/canvas.md`.

## [0.4.0] - 2026-05-21

### Added
- **Queue dashboard.** Always-on one-line strip above every
  conversation's status bar (per-queue depth + most recent worker;
  adaptive format for 1 / 2–3 / 4+ queues) plus a `Ctrl+D` modal
  dashboard with `QUEUES / IN-FLIGHT / QUEUED / RECENT` bands and an
  inline `DetailPanel` showing payload, lifecycle, and a live
  assistant-text tail. `↑↓` move, `>` jumps to the worker's tab,
  `Esc` closes. Backed by a new `QueueDigest` aggregator subscribed
  to a push-based `QueueManager.subscribe()` hook (committed-state
  observability; observer exceptions never poison the substrate).
- **Inbox visibility in the TUI.** When a handoff, queue callback,
  Telegram message, or any other inbox message lands on an agent, the
  pane mounts a distinct `✉` block in the transcript before the agent
  reacts — sender / task / status / timestamp header plus up to 4 body
  lines (truncation footer if longer). New
  `AgentSession.on_inbox` observer slot fires synchronously on every
  `deliver()`, idle or mid-turn. Pure renderer
  `render.render_inbox_block(msg, colors)`.

### Fixed
- App-level `escape` priority binding no longer swallows modal-dismiss
  presses — `action_interrupt` dismisses a pushed `ModalScreen`
  before falling through to pane interrupt. Previously, pressing
  `Esc` to close the agent picker or queue dashboard was a silent
  no-op.
- Queue strip no longer sits flush against the model/permission
  status line — 1-row transparent margin separates the two panel
  bands.

## [0.3.0] - 2026-05-21

First public PyPI release as `aegis-harness`. Distribution name is
`aegis-harness`; the importable package is still `aegis`.

### Added
- **Multi-provider parity via ACP.** Gemini and OpenCode drivers rewritten
  on the official Agent Client Protocol Python SDK
  (`agent-client-protocol >= 0.10`). Multi-turn, streaming, cancellation,
  and per-session MCP injection are now identical across `claude-code`,
  `gemini`, and `opencode`.
- **Per-provider config classes** (`ClaudeCode`, `GeminiCLI`, `OpenCode`)
  in `aegis.config`. Legacy flat `Agent(harness=..., model=..., ...)`
  shape still works via a back-compat validator.
- **Task queues + workflows.** `aegis_enqueue` / `aegis_task_status` MCP
  tools, `QueueManager` (FIFO + max-parallel + substrate-deterministic
  dispatch + JSONL replay), `InboxRouter` with universal sender tagging,
  `@workflow` decorator + `WorkflowEngine` runtime, `aegis workflow
  list/run` CLI, `aegis_run_workflow` MCP tool.
- **Headless mode.** `aegis serve` runs SessionManager + MCP plane without
  a TUI, with an optional Telegram front-end (`/new`, `/close`,
  `/interrupt`, `/<handle> …`, bare-text routing). Configured via
  `telegram_token` / `telegram_chat_id` / `auto_add_to_telegram_prompt`
  in `.aegis.py`. systemd unit template at `scripts/aegis-serve.service`.
- **`aegis init` wizard.** Rich-powered interactive wizard that detects
  installed agent CLIs, walks through agent + queue setup, and refuses
  to clobber an upstream `.aegis.py` without `--force`.
- **TUI polish.** Per-block click-to-copy with hover tooltip, inline
  `WorkingIndicator` (spinner + rotating verb + elapsed timer) mounted
  inside the transcript, glued `ToolUse`↔`ToolResult` blocks,
  max-variety alliterating handle generation (no laureate or adjective
  reuse, letter cycling).
- **OIDC release workflow.** `.github/workflows/release.yml` publishes
  to PyPI on `v*` tag push using PyPI trusted publishing — no token
  stored in the repo.
- **Expanded docs.** New pages for Drivers, Queues, Workflows, the MCP
  plane, and an auto-generated API reference via mkdocstrings.

### Changed
- Distribution renamed from `aegis` to `aegis-harness` (the name `aegis`
  was already taken on PyPI). Import path is unchanged.
- README + docs site rewritten for the multi-provider surface; old
  Phase 1/1.5/2 framing replaced with a current-capability summary.
- Removed `legacy/` (sidelined FastMCP prototype) and `notes/`
  (scratch markdown). Git history preserves both.

### Fixed
- ACP driver: workaround for an upstream SDK race in `Connection.__init__`
  that was killing every Gemini/OpenCode session on startup.
- ACP driver: measure `duration_ms` locally in `send()` (the final
  status line was always showing 0.0s).

## [0.2.0] - 2026-05-18

### Added
- MCP plane (slice 1): a shared FastMCP HTTP server owned by aegis;
  spawned agents are injected strict + primed and get an `aegis_meta`
  orientation tool.
- MCP plane (slice 2): `aegis_list_sessions` / `aegis_list_agents` /
  `aegis_handoff` (fire-and-forget inter-agent context transfer);
  per-pane self-reported handle baked into the priming so each agent
  knows who it is and passes that as `from_handle`.

### Fixed
- Driver: large `tool_result` payloads (e.g. reading a SOUL.md-sized
  file) no longer silent-hang a turn. `create_subprocess_exec` now
  uses a 16 MiB `StreamReader` buffer (root cause: 64 KiB default was
  too small for legitimate lines), and `_pump_stdout` has a
  `try/finally` so the stream-closed sentinel always fires. Tool-result
  display is capped at 100 chars. Regression tests cover both
  guarantees.

## [0.1.0] - 2026-05-18

First tagged release — a usable, personal-infrastructure-grade meta-harness.

### Added
- CLI driver: runs Claude Code via `claude -p` stream-json (bidirectional,
  no log scraping); agent profiles from a Python `.aegis.py`.
- Full-screen Textual TUI replacing the line REPL.
- Multi-tab: N independent agent sessions, a sideways-scrolling tab bar,
  per-tab agent profiles, an `AgentPicker` modal, generated handles
  (`adjective-laureate`), cross-tab signalling (state dot + sticky `*` +
  bell).
- Theme engine (Textual-native) with the default **Ink** theme; themes are
  drop-in.
- Live status-line metrics: true input (incl. cache) with cached %, output,
  tool calls, turn / session time; provisional while streaming, exact at
  turn end.
- Lazy session start (harness spawns on first message, not tab open).
- `aegis --version`.

### Notes
- Not general-public-ready; runs from source via `uv`, drives a local
  `claude` CLI. The earlier FastMCP workflow-engine prototype is preserved
  under `legacy/`, unbuilt.
