# Aegis — Tasks / Next

Working roadmap for what's next. Shipped history lives in `CHANGELOG.md`;
the public roadmap is `docs/roadmap.md`. This file is the scratch /
priority list — keep it terse and current.

Current release: **v0.32.0** (2026-08-07).

## Resolved — the June 2026 billing scare

Both ⚠️ deadlines below have passed and neither action is needed. Kept as a
record so nobody re-arms them off the old spec files.

### ~~Before June 15 — Claude driver: `claude -p` → REPL mode~~ — MOOT

**Anthropic cancelled the billing split before it took effect.** `claude -p`
and Agent SDK usage both still draw from the Pro/Max subscription pool; there
is no separate credit to migrate away from. Never executed — there is no
`drivers/claude_repl.py` on disk, and there should not be.

- Superseded by `docs/superpowers/specs/2026-07-30-aegis-claude-agent-sdk-driver-design.md`,
  which reworks the Claude driver for capability rather than billing.
- Dead spec/plan: `docs/superpowers/{specs,plans}/2026-05-27-aegis-claude-repl-driver-*`

### ~~Before June 18 — `GEMINI_API_KEY` in the Gemini agent profile~~ — OVERTAKEN

Never implemented (no `api_key` on `GeminiCLI` in `config/__init__.py`), and
overtaken by events: Gemini CLI subscription access died 2026-06-18 and the
`gemini --acp` driver is dead weight for subscription users. See *Backlog →
Subscription-backed models*, which supersedes this. Do the API-key field only
if someone actually wants to pay Google AI Studio rates.

### After June 1 billing transition — Copilot ACP driver

GitHub Copilot CLI supports ACP since Jan 2026: `copilot --acp` (stdio).
Driver is a four-line `AcpDriver` shim — same shape as `GeminiDriver`.
Auth goes through `gh auth login` (no separate token management).

## Recently shipped

### File browser tab *(shipped 2026-08-20)*

`Ctrl+O` opens a persistent **FileBrowserTab** instead of the fullscreen
`FilePickerModal`. Browse mode is a recency-sorted list (new
`FileIndexer.paths_by_mtime()`) with a fuzzy filter; selecting a file switches
the same tab into view mode — a full `FileTab` editor with `b` to go back — and
a toggleable `DirectoryTree` sidebar on the right selects files straight into
view mode. Multiple browser tabs coexist.

Two contracts worth knowing, both already paid for:

- **`AegisApp.set_sidebar_mode` fans out through
  `getattr(pane, "set_task_dock", None)`**, so a composite tab must expose
  `set_task_dock` — not `set_sidebar_mode` — or `F3` silently skips it.
- **Escape does not arrive as a key event.** The app binds
  `Binding("escape", "interrupt", priority=True)`, and a priority app binding
  is checked before the focused widget, so a `key_escape` method on a tab
  never runs inside the real app (probed on Textual 8.2.6: the app action
  fires, the widget method does not). A tab that owns escape implements the
  duck-typed `escape_handled() -> bool` rung `action_interrupt` calls above
  the pane ladder, and returns `False` when it wants nothing, so the rungs
  below still run. Printable keys are unaffected — `b` bubbles normally even
  with a read-only `TextArea` focused.

**Spec gaps closed 2026-08-26 (`a044635`).** Four things the spec asked for
and the first pass did not ship: `Esc` back to browse, cursor restored to the
file that was open (and held there across the 2s poll rather than walking to
the top), the 200-row cap reporting how many rows it dropped, and the poll
returning early in view mode instead of rebuilding a hidden list. The escape
work also revived `FileTab`'s own escape — exit edit mode, exit preview,
answer the discard prompt — which the priority binding had made dead inside
`AegisApp` since long before this tab existed. 12 tests, each mutation-checked.

- Spec: `docs/superpowers/specs/2026-08-20-aegis-file-browser-tab-design.md`
- Plan: `docs/superpowers/plans/2026-08-20-aegis-file-browser-tab.md` (6 tasks, TDD)
- Commits: `240c415`…`0b010a4`. `src/aegis/tui/file_browser_tab.py`,
  `tests/test_file_browser_tab.py`.
- **Outstanding: the web client has no file browser.** Joins the standing
  TUI-first debt (sidebar, REPOS, live task list, session title) — one web
  slice, not five.

### Context gauge accuracy + compaction detection *(shipped 2026-08-10)*

The ctx% gauge reads >100% on most agentic turns because
`SessionMetrics.commit()` uses `Result.usage.true_input`, which accumulates
across every sub-turn. And Claude's auto-compaction fires invisibly.

**The 2026-08-09 draft was written from two session logs and half of it did not
survive contact with the corpus.** Re-running its hypotheses over all 381 local
logs (6,871 turns):

- **Gauge fix confirmed, and understated.** 4,256/6,871 turns (61.9%) render
  >100% today; worst is **92,956%** (1,138 sub-turns). Replaying the proposed
  fix leaves **1 turn in 6,871** over 100%. Land it as written.
- **The >50% drop heuristic is refuted.** It fires **1,272 times against 17
  real compactions** — ~1.3% precision. 47% of its detections carry a
  `parent_tool_use_id` (subagents); 98% recover within the same turn. No online
  variant beats 12% precision. The draft's own two evidence sessions never
  compacted: both are Opus (1M window, not the 200k it assumed) with zero
  `compact_boundary` events, so its two "observed compactions" at 124k and 163k
  are subagent context switches.
- **Claude emits `system`/`compact_boundary`** carrying `trigger`, `pre_tokens`,
  `post_tokens`, `cumulative_dropped_tokens`. 17 in the corpus, one per affected
  session, all `trigger: auto`, all firing at `pre_tokens` ≈ the 1M ceiling.
  Exact, no thresholds. It currently parses as `Unknown`, so `events.py` does
  need a change after all.

Three traps the plan already carries: `core/session.py` has **two** identical
event loops and both need every routing change; `render_tiers()` must keep
returning **four** tiers (`StatusBar._tiers` reads a fifth element as a fifth
tier, so the colour goes in as Rich markup, which `fit.plain_width` strips
before measuring); and the registry provider key is `claude-code`, where any
model containing `opus` resolves to a 1M window.

**Shipped** in `dea1704`…`0133e3e` (8 tasks, TDD). Acceptance gate — replaying
the *shipped* `SessionMetrics` over all 381 logs: **1 turn in 7,042 above 100%**
(an OpenCode session whose window the new ACP `context_size` override now
supplies), and **17 `compact_boundary` events, one per affected session**.
Mutation-checked both ways: reintroducing the one-line `commit()` bug takes the
gate to **4,271 turns over 100%** and rc=1. Full hermetic suite 2,986 passed.

Adding `CompactBoundary` to the `Event` union tripped
`test_renders_to_nothing_matches_render_event_for_every_event_type`, which
exists to force a decision about both render functions for any new type. The
decision: renders to nothing, like `ContextUpdate` — the boundary drives the
status bar, not the transcript.

- Spec: `docs/superpowers/specs/2026-08-09-context-gauge-and-compaction-design.md`
  (revised; keeps the rejected heuristic and its numbers so it is not
  reintroduced)
- Plan: `docs/superpowers/plans/2026-08-10-context-gauge-and-compaction.md`
- Deliberately not done: compaction detection for ACP harnesses (no protocol
  signal, and the heuristic is worse than showing nothing), and the
  `── compacted ──` transcript separator — now a one-event change off
  `CompactBoundary` if it turns out to be wanted.

### SSH execution hosts *(specced + planned + built 2026-08-04)*

`hosts:` config; `host` as a third orthogonal spawn axis. `Ctrl+N` host
tier, `/spawn main@vps:/path`, `aegis_spawn(host=…)`, `/reconnect`,
host-scoped claims and file affordances, `aegis config host …`. Live
tests run against `ssh localhost`, including a real `claude` over the
link and a mutation-checked reverse-tunnel test.

Not durable by design — that stays `aegis --remote ssh://vps:8080`.

Follow-ups worth considering, none blocking:

- **A token on the MCP plane.** The reverse-tunneled port is
  unauthenticated on the remote loopback, so any user on that box can
  drive the local aegis. Fine on a personal VPS, a blocker on a shared one.
- **Filesystem bridge.** Fetch remote files over the control connection so
  ctrl+click, the indexer and the `@`-picker work against a remote pane.
  Its own spec.
- **Stage the launch command as a remote script**, to keep the primer and
  persona out of the remote `ps`.
- **Host-aware terminals** (`aegis_term_spawn(host=…)`) — separate
  substrate, separate treatment.

- Spec: `docs/superpowers/specs/2026-08-04-aegis-ssh-execution-hosts-design.md`
- Plan: `docs/superpowers/plans/2026-08-04-aegis-ssh-execution-hosts.md`
- Know-how: `know-how/ssh-execution-hosts.md`

### F3 side dashboard *(shipped 2026-08-07)*

`F3` toggles a mode, not a widget. Open, a full-height sidebar carries the
plan, the queues, the monitors and all eight status-bar segments, and the
main column is transcript and input; closed, the pane is what it was.
`PlanDock` is gone — the sidebar's PLAN section calls `render_plan_dock`
verbatim rather than re-implementing rows, which is what keeps the circle
spacing and the `width - 9` label budget from being re-paid.

Three traps worth knowing before you add a fifth collapsed surface or a
seventh section:

- The mode switch is a single `-sidebar` class and a CSS `display: none`,
  so a surface that sets `display` **imperatively** silently wins over it.
  `PlanStrip` did, and moved to the `-empty` class idiom its two sibling
  strips already used. The toggle test asserts on real widget visibility,
  never on the class, and is mutation-checked.
- **The column's bounds are the frame, so they carry the padding on top of
  the content budget** (`SIDEBAR_MIN/MAX = 26/60 + 2 * SIDEBAR_PAD_X`).
  Charging the rows for the chrome took an 80-col terminal below the
  widest system segment, and `fit_rows` responds to "no tier fits" by
  dropping the segment — the section disappears rather than getting
  shorter, which does not look like a width bug.
- **A section composes a renderer; it does not inherit its framing.**
  `render_plan_dock` was free-standing, so it opens with its own
  `tasks d/t` line and newline-terminates its last row. Dropped straight
  in that duplicated the heading's counter and put two blank rows after
  PLAN where every sibling has one. Both are trimmed at the composition
  site in `_plan()`, not in the renderer, which has its own contract in
  `tests/test_plan_render.py`.
- **A formatter shared with a strip has not been fitted to a column.**
  `format_q` and `format_mon` were made public so both surfaces would
  share them, but they were written for a full-pane strip that does its
  own fitting. In 26 cells one monitor rendered 68 and wrapped to three
  rows. Both now take an optional width; `format_mon` degrades through a
  tier ladder (bar, then ETA, then the description, floored at 14 cells)
  rather than cutting the row from the right, which would throw away
  everything the row exists to show.
- **A right-aligned field pads but does not truncate.** `:>6` spends
  seven cells on a seven-cell string, so `fmt_working`'s old `H:MM:SS`
  wrapped any row whose task had worked an hour. The hour form is `1h06`
  — under budget, and not misreadable as the `1:06` minute form.

The invariant that catches most of this class is one line and lives in
`tests/test_sidebar_render.py`: **every rendered row fits the width, at
26/33/40/60.** The column's body is a Static inside a `VerticalScroll`,
so an over-long row does not clip — it wraps, and the sections below it
go off the panel.

**Outstanding, deliberately: the web client renders no sidebar.** Same
call as the live task list's Task 12 and the session-title web gap below —
three TUI-first features now owe the PWA the same debt, and `AGENTS.md`
calls the two UIs co-equal. Worth doing as one web slice rather than three.
The `REPOS` section below joins the same debt: four now, one slice.

- Spec: `docs/superpowers/specs/2026-08-07-aegis-f3-side-dashboard-design.md`
- Plan: `docs/superpowers/plans/2026-08-07-aegis-f3-side-dashboard.md`

### REPOS in the sidebar *(TUI complete 2026-08-10; web outstanding)*

Which repos the live agents are writing to, on what branch, with dirty and
ahead/behind counts, and a per-pane mark separating this agent from peers.
Membership is learned from write tools only and keyed on `(host, root)`, so
the same path on two machines is two rows rather than a phantom collision.

Two things a contributor will otherwise break, both already paid for:

- **The narrowest row tier truncates rather than being dropped.**
  `fit_rows` answers "no tier fits" by omitting the segment, so without a
  guaranteed-fitting floor a repo *disappears* because its name was long —
  and a missing row reads exactly like a repo nobody touched.
- **The recording hook is in `AgentSession._fire_event`, which the replay
  walk does not call.** Move it somewhere the replay reaches and a resumed
  session repopulates the board from its whole transcript, reporting agents
  standing in repos nobody is in.

**Outstanding: the web client renders no `REPOS`.** Rolls into the sidebar
web slice above.

- Spec: `docs/superpowers/specs/2026-08-10-aegis-sidebar-repos-section-design.md`
- Plan: `docs/superpowers/plans/2026-08-10-aegis-sidebar-repos-section.md`

### Live task list *(TUI complete 2026-08-06; web outstanding)*

Plan state is first-class session state — parsed from every harness, timed
in working seconds, and rendered on the strip, the `F3` dock, the tab bar
and the coordination plane (`SessionInfo.plan`, `aegis_peer_plan`).

Two defects that unit tests could not see, both found by driving a real
plan through a real pane and both fixed with mutation-checked tests:

- The surfaces did not fit their width — the "one-line" strip wrapped to
  two lines (it took no width and never truncated), and every dock row was
  width+1 while the widget subtracted its padding twice, so the errors
  cancelled at some widths and left a dead column at others. `e056127`.
- The plan did not survive a restart. The tracker is per-process and the
  only path back was a reactive `TaskList` result, so the strip stayed
  blank until the agent happened to list its tasks. A resumed session now
  replays its transcript through the tracker, recovering banked working
  time exactly — the property Task 4 built the tracker for and nothing had
  ever used. `acfdbc4`.

**Outstanding, deliberately:**

- **The web strip and slide-over (plan Task 12) — not built.** Deferred at
  Alex's direction to finish the TUI first. `AGENTS.md` calls the TUI and
  the PWA co-equal, so this is the honest debt of the feature:
  `renderEvent.js:planHtml` already renders `AgentPlan` rows, so only the
  live surfaces are new.
- **Group-dashboard roll-up (plan Task 11's second half) — skipped.**
  `DashboardSnapshot` has no live data path, so a `member_detail` helper
  would be dead code. Needs that data path first; own slice.
- **`TaskList`-triggered rehydration still loses banked time.** The
  restart path is now exact, but the older `TaskList`-result path
  (`1cabfb6`) restores rows with their clocks at `—`. Worth unifying on
  the replay path.

- Spec: `docs/superpowers/specs/2026-08-05-aegis-live-task-list-design.md`
- Plan: `docs/superpowers/plans/2026-08-05-aegis-live-task-list.md`

## Active

### Per-session MCP identity — make `from_handle` a transport fact

*Surfaced 2026-08-11 while shipping the comms format. Not a regression: the
gap predates it, the ledger only made it visible.*

**The MCP server cannot tell which agent is calling it.** `AegisMCP` is
co-resident and shared — every session on this aegis reaches the same HTTP
port — so there is no per-connection identity to read a handle from. That is
why `from_handle` is a *parameter* in the first place, baked into each
agent's primer system prompt and passed back by convention.

Three consequences, all live today:

- An agent can pass a handle that is not its own, by mistake or otherwise.
  Nothing checks it.
- Tools that do not take `from_handle` (`aegis_list_sessions`,
  `aegis_claims`, `aegis_canvas_list`, `aegis_meta`, every `config_*`)
  cannot be attributed at all.
- The comms ledger therefore records `from: ""` for those, and
  `aegis comms list` prints `(unattributed)`. `CommsMiddleware` deliberately
  does not guess — a fabricated attribution in an audit record is worse than
  an honest gap — but a ledger whose whole point is *who talked to whom* has
  a hole in the *who*.

**Shape of the fix:** mint a per-session token at spawn and inject it
alongside the primer (`mcp_config_json` already writes per-invocation MCP
config via `--mcp-config`, and ACP carries `mcp_servers` in `new_session`),
then resolve `from_handle` server-side from the token rather than trusting
the argument. The parameter can stay for one release as a fallback so
nothing breaks, then become advisory.

**Payoff beyond attribution:** `aegis_claim` / `aegis_release` /
`aegis_close` / `aegis_loop_stop` all gate on `from_handle` matching, and
each currently trusts the caller for it.

Touches: `src/aegis/mcp/{runtime,server}.py`, `mcp_config_json`, the primer
in `PRIMING`, `src/aegis/comms/middleware.py` (drop the `args.get`
best-effort read). Spec:
`docs/superpowers/specs/2026-08-11-aegis-comms-format-design.md`
(*`from` is best-effort, and says so*).

### Terminals — redesign from scratch *(defect found 2026-08-10; deliberately not patched)*

**A live shared terminal makes `Ctrl+Q` hang forever.** Reproduced on a clean
instance: boot, `/terminals new t1` (or `aegis_term_spawn`), `Ctrl+Q` — the
screen clears and the process never exits. Confirmed with a faulthandler dump,
not inferred:

    Thread: ptyprocess.py:522 in read → concurrent/futures/thread.py:59 in run
    Main:   asyncio/runners.py:72 in close        ← blocked here

`action_quit` closes panes, the queue digest, the quota service, the queue
manager, the MCP plane and the file indexer — but never the `TerminalManager`.
Each terminal's `_reader_loop` awaits `state.pty.read()`, which is a blocking
`ptyprocess` read hoisted onto asyncio's **default executor**, so the reader
thread survives the loop: cancelling `reader_task` cancels the *await*, not the
thread parked in `os.read` on the master fd. `Runner.close()` then sits in
`shutdown_default_executor` (300 s in 3.13), and the atexit join behind it never
returns at all.

`TerminalManager.close()` has the same hole even when called explicitly —
`reader_task.cancel()` + `pty.close(force=True)` leaves the executor thread
blocked; whether the fd close wakes it is unverified.

**Decision: do not patch this.** Terminals get redesigned from scratch in a
future session — the fix is the design (own the reader thread, or read the pty
fd on the loop via `add_reader` / a dedicated daemon thread, so shutdown is a
thing the manager can actually perform), not a `close()` call bolted into
`action_quit`. Anyone reaching for the one-line fix first: it does not exist,
because nothing on the current seam can interrupt that read.

Files: `src/aegis/terminal/manager.py` (`_reader_loop` ~311, `close` ~211),
`src/aegis/tui/app.py` (`action_quit` ~1505).

### Mandatory file claims *(specced + planned 2026-08-07; no code yet)*

`src/aegis/locks/` is advisory: `claim()` returns `granted: false` and the
only consequence is that the claim is not recorded. Nothing stops the write.
So peers clobber each other's in-flight work — discovered at `git diff`, hours
later — and the board is only as accurate as the agents are disciplined,
which under-reports exactly the sessions most likely to collide.

**Threat model is the careless agent, not the adversarial one**, and that
choice is what makes it affordable. Making an agent genuinely *incapable* of
writing needs a kernel PEP, and claims are dynamic, which rules out the cheap
options: **Landlock is structurally disqualified** (a ruleset only ever
restricts further, never re-grants, so an agent that claims a path mid-session
could never write to it); uid+ACL needs root and collides with Alex's editor
and the autosync timer; per-agent FUSE is the clean total answer and costs a
mount per session. FUSE is the escalation path if the cheap gate proves
insufficient — the PDP boundary is exactly the seam it would consult.

**Shape: one PDP, three PEPs.** `ClaimRegistry` decides; enforcement goes
where aegis already sits on the write path — `AcpSession.request_permission` /
`write_text_file` (`drivers/acp.py:276,284`, where aegis performs the write
itself), a Claude `PreToolUse` hook injected via `--settings` in
`build_argv` (`drivers/claude.py:234`), and Bash. Remote hosts need no work:
`hosts/connection.py` already reverse-tunnels the MCP port.

Four decisions worth not re-litigating:

- **Auto-claim on write, exclusive-only denial.** Literal deny-by-default
  teaches every agent a ritual — hit wall, call `aegis_claim(["src/"])`,
  which *always* succeeds against a shared claim — so the board fills with
  giant meaningless claims and you have spent the signal without buying
  exclusion. Auto-claim instead makes the board accurate for free.
- **Only full overwrite of an existing file is denied among shared claims.**
  `Edit` carries an `old_string` and fails on its own if a peer moved the
  region; creating a new file clobbers nothing. Friction goes exactly on the
  clobber vector so the interstitial fires too rarely to ritualize.
- **The acknowledgment is `aegis_claim` itself.** A `PreToolUse` deny returns
  a reason and the model retries — there is no protocol slot on `Write` for a
  "yes I know" flag. So joining the shared claim *is* the ack, it needs no new
  primitive, it is unsatisfiable without reading the deny message, and it
  records the bookkeeping we wanted anyway.
- **Bash inverts the rule: deny only on positive match, pass on any parse
  failure.** Any static analysis of a shell command is a guess and a
  deny-by-default guess makes Bash unusable within one turn. Bash is porous
  here and the spec says so out loud.

**`live_handles` is the thing to fix first.** It is tab existence
(`core/manager.py:460`, `tui/app.py:428`) — fine for an advisory board,
wrong under enforcement, because agents in aegis characteristically finish and
sit there. A session done three hours ago is "live" and its exclusive claim is
a permanent wall whose deny message points at a handle that will never answer.
Hence gone / live / **dormant**, where dormant *degrades* exclusive to shared
rather than deleting it: the board still reads "bob was working here", only the
wall comes down. **The notification is the liveness probe** — if the holder is
really alive its inbox wakes it and it can re-claim exclusive. That is why
there is no `force` verb and no break-in tool, and why restart and dropped-SSH
self-heal with no extra code ("has a future" is deliberately the same
predicate `core/close_guard.py` uses to refuse a close).

Traps already found while planning, each of which would otherwise cost a cycle:

- **Auto-claim breaks `aegis_close`.** `mcp/server.py:1155` counts *all* of a
  handle's claims into `CloseFacts.claims` and refuses on any, so every agent
  that ever edited a file becomes unclosable. `explicit_count()` (auto-claims
  excluded) is in the plan as part of the same task that lands auto-claim.
- **`AegisColors` has no `warning` role**, and lives in `aegis.themes`, not
  `aegis.tui.themes`. Roles are ready/working/error/accent/muted/ok/err/user/
  user_bg, and it has no defaults. Contested rows use `err`.
- **`CommandResult` fields are `(ok, title, body)`** — `summary` belongs to
  `SlashCommand`, not to the result.
- **`fit.plain_width` measures with `len()`, not `cell_len`**, on the docstring's
  assertion that the bar's glyphs are single-width. Status-bar claim glyphs must
  stay single-width or that function moves to `cell_len` first. (`truncate_cells`
  already uses `cell_len`; the two are inconsistent today and this is where it
  surfaces.)
- **The sidebar section cannot list claims.** Under auto-claim a session that
  edited thirty files holds thirty claims. Rank by what demands action —
  contested, then explicit, then auto rolled up to a common prefix — and let a
  contested row lead with the **peer handle**, not the path, so the handle
  survives when the row narrows. `fit_rows` drops a segment whose narrowest
  tier does not fit, so a conflict vanishing at 80 cols is the worst failure
  this feature has; the render test pins it at the 26-cell floor.

Deliberately not planned: the spec's third notification (a TUI toast) stops at
the inbox, since the sidebar CLAIMS section already puts contested state
permanently on screen. One-task follow-up if it turns out to be wanted.

- Spec: `docs/superpowers/specs/2026-08-07-aegis-mandatory-file-claims-design.md`
- Plan: `docs/superpowers/plans/2026-08-07-aegis-mandatory-file-claims.md` —
  11 TDD tasks as vertical slices. **Task 5 (the ACP PEP) is the first point
  where an agent actually gets blocked end to end**; tasks 1–4 exist to make
  that slice safe to land (policy, then liveness *before* any enforcement can
  deadlock on a ghost, then registry + the close fix, then config kill-switch
  and the root-subtree domain).
- Config: `locks: {enforce: true, dormant_after: 20m}`. `enforce: false`
  disables the PEPs but **keeps auto-claim**, so the board stays accurate with
  the walls down.

### The conversational corpus *(VS1 tasks 1–2 of 7 shipped 2026-08-06; paused)*

**Where this stopped.** The two pure/read layers are on `main` and green;
nothing is half-finished, and the next task is a clean start.

- **Done:** Task 1, the pure extractor (`src/aegis/corpus/extract.py`,
  `a2a3c90`) and Task 2, the sidecar merge + provenance
  (`src/aegis/corpus/source.py`, `2d3604d`). 14 tests.
- **Next:** Task 3, the incremental beaver index. **It adds
  `beaver-db>=2.3` and re-locks `uv.lock`** — the reason this paused, since
  a re-lock lands on every peer sharing the checkout. Do it when the tree
  is quiet, or coordinate first.
- **Then:** Task 4 recall, 5 the `aegis history index` CLI, 6 the two MCP
  tools, 7 end-to-end against the real corpus.

**Two corrections to the plan, found by running the extractor over the real
corpus rather than its synthetic fixtures** — the fixtures invented the
field names, so they agreed with the bug. Both fixed in `1c50299`, both
written into the plan header so they are not reintroduced:

- **The tool field is `raw_input`, not `input`.** No persisted `ToolUse`
  event has an `input` key (19,008 real events checked), so `files_touched`
  was empty on every log in the corpus — the sleeper facet, dead on
  arrival. `locations` is harvested too. Over 40 real logs: 0% of exchanges
  carried a file before, 44% after, 1,213 distinct paths.
- **Half the corpus has no `SessionMeta` record** (28 of 60 logs), so
  `handle`/`cwd` were `None` and the key degraded to `?@<ts>`. The handle
  now falls back to the log filename via `parse_log_id`; a real
  `SessionMeta` still wins.

**The real corpus lives in the Workspace state dir**
(`/home/apiad/Workspace/.aegis/state` — 322 sessions, 212 sidecars, 1,492
imports), *not* the aegis repo's, which has none. Task 7 must point there.

---

aegis has kept a durable per-session ledger since May and nothing can ask it
anything. Two consumers want it and they are the same object underneath:
**recall** (`aegis_recall("did we discuss X")`) and **mining** (shard the
corpus for offline fan-out). Both need one middle step — turning raw event
logs into *exchanges* — so this is one feature, not two.

An exchange is one operator turn plus the agent's response arc up to the
next. Tool *results* are excluded: ~60% of corpus bytes, no retrieval signal.
`files_touched` is the sleeper facet — *"when were we working on the
geocoder"* is a path lookup, not a semantic query.

- Spec: `docs/superpowers/specs/2026-08-04-aegis-conversational-corpus-design.md`
- Plan: `docs/superpowers/plans/2026-08-05-conversational-corpus-vs1.md` —
  VS1 only (extractor → beaver index → `aegis_recall` / `aegis_recall_expand`
  → `aegis history index`), 7 TDD tasks. VS2 = provenance at source +
  `Interrupted`/`TurnAborted` + `SessionMeta.host`; VS3 = `history export`;
  VS4 = embeddings + RRF.

**Groundwork already landed (2026-08-05), outside the repo:** 1,492 Claude
Code transcripts archived to `state/claude-import/` (930 MB → 385 MB, 0
corrupt) and 212 provenance sidecars written to `state/backfill/`. Operator
turns in the ledger went 346 → 3,232; sessions with zero operator turns 93% →
29%. May and June are unrecoverable — Claude Code had already swept them.
So VS1 has a real corpus to test against on day one.

- **Why the ledger was blind:** `UserMessage` is claude's
  `--replay-user-messages` echo (`drivers/claude.py:241`) and that flag landed
  only weeks ago — 0/54 sessions in May, 0/73 in June, 15/16 in August.
- Three beaver traps, each probed against the real library and each a silent
  bug if missed: **`Document` has only `id` and `body`** (no `metadata=` —
  structured fields go in a pydantic model via `db.docs(name, model=…)`); the
  **sync `.search()` scores every hit `-0.0`** (use the async
  `query().fts(…).execute()`, which gives real BM25 — and it is *negative*,
  more negative is better); and **`.where(Model.field == x)` raises
  `AttributeError`** on pydantic v2, so all filtering happens in Python.
- FTS5 rejects punctuation — `registry.syalia.dev?` is a syntax error, and
  that is exactly the query shape a user types. Sanitize to OR-joined terms.
- **Never write into `state/sessions/`.** `repair.py:61`,
  `session_log.py:160` and `history.py:203` each glob `sessions/*.jsonl`;
  a sidecar placed there is read back as a session log. And never rewrite an
  existing log — it is the only copy of that conversation.
- Task 3 adds `beaver-db>=2.3` and re-locks `uv.lock`; coordinate if a peer
  is live in the tree.
- Open question deferred to VS4, not VS1: the index makes 730 MB searchable
  that nobody previously grepped, including every secret ever pasted into a
  session. Local-only, never network-exposed, no redaction pass in v1.

### Claude driver on the Agent SDK *(specced 2026-07-30, no plan yet)*

A **second** Claude driver (`claude-sdk`) beside `claude-code`, built on
`claude-agent-sdk` (PyPI 0.2.128) instead of a hand-built argv plus the
stream-json parser in `events.py`. The SDK spawns the *same* local `claude`
binary over the *same* protocol — this is not a billing or transport change.

The case is that three specced-and-unbuilt items are options on
`ClaudeAgentOptions`: `can_use_tool` is the `PermissionRouter` from the
fs-tool-surface spec (with `updated_input`, it also rewrites calls, which that
spec doesn't); `ClaudeSDKClient.set_model()` retires the `/model` half of 2B.1
with no resume-restart; `session_id` + `fork_session` collapse claude's id space
into the aegis log id (`9da13d0`) and hand us conversation fork for free. Plus
`hooks`, `max_budget_usd`, `agents`, `skills`, `setting_sources`.

Prior art: `pingdotgg/t3code` does exactly this in TypeScript
(`apps/server/src/provider/Layers/ClaudeAdapter.ts`).

- Spec: `docs/superpowers/specs/2026-07-30-aegis-claude-agent-sdk-driver-design.md`
- Plan: *not yet drafted — start with slice 1 (walking skeleton) and let its
  `parent_tool_use_id` probe decide the shape of slice 2*
- Known costs, all called out in the spec: the canonical-event mapping is the
  real work (`parent_tool_use_id` and `ParserState.tool_diffs` are the two easy
  things to drop); `HarnessDriver.build_argv` is abstract and argv-shaped, so
  demote it to a non-abstract `return []`; **`pre_spawn` hooks stop applying** —
  the main reason this ships alongside rather than replacing; `system_prompt`
  has one `append` slot where `build_argv` passes two (primer, then persona, in
  that order — pin it with a test).
- Registration trap: four places, and the fourth is
  **`_VALID_DRIVERS` at `config/yaml_loader.py:73`** — missing it is the exact
  lovelaice bug fixed in `449ebbb`. Gate on a YAML-declared agent that spawns,
  not a unit test of the driver class.

### `@peer` — ask an idle agent *(VS1–VS4 landed 2026-07-31)*

`@lucid-knuth is this right?` in any pane. Fills the hole between
`aegis_handoff` (free, no context) and `/fork` (~$1, whole conversation):
a bounded slice of where you stand, to an agent that already exists.

**Idle-only is the domain.** The guard reads the target and never the
source, so `@peer` is legal while your *own* pane is mid-turn — spending a
long turn's dead time on someone who is free is the whole use case.

- Spec: `docs/superpowers/specs/2026-07-31-aegis-at-mention-peer-ask-design.md`
- Plan: `docs/superpowers/plans/2026-07-31-aegis-at-mention-peer-ask.md`
- ~~Outstanding: three TUI wiring changes~~ **all landed** (verified
  2026-08-06). The `("/", "@")` gate is `tui/pane.py:1700` and carries a
  comment explaining why it must travel with `classify_input`;
  `AegisApp.peer_ask` takes the full kwarg set (`tui/app.py:1652`, fixed in
  `e97f759` — "the @peer bridge was forwarding three parameters short");
  `AegisApp.read_peer` is at `tui/app.py:1689`.
- ~~Also outstanding: `deferred=True` on `/peer`~~ **landed** —
  `commands/builtins/core.py:425,434`, with the `_DeferredTrack` machinery
  in `tui/pane.py`. `@peer` no longer blocks the Textual message pump.
- Deferred to v2, not built: multicast `@a @b` (more attractive under
  idle-only — "poll every free peer, one block, N answers" — but it changes
  the block layout and the timeout story); clickable `@handle` in *agent
  output* (jump-to-tab, no overlap with this); reading **closed** sessions
  (needs the `SessionMeta` scan to resolve current-handle → log id); the web
  treatment of the answer block.
- Open questions, deliberately unanswered: the teaser budget (2k is a
  starting number — if peers pull constantly it is too small, if they answer
  blind it is too big) and whether peers actually pull at all. If the pull
  rate is low the fix is not prompt wording but a `needs_more`-shaped
  structured field, the way `/btw` turned the same guess into a signal.

### Conversation fork *(VS1 landed 2026-07-31 — `a91e501`, `f6e6a97`)*

**`/fork [prompt]` works; `aegis_fork(target_handle, …)` forks an idle peer.**
Self-fork is the slash command's job, not MCP's — an agent calling a tool is
mid-turn by construction, and a mid-turn fork is torn (probe A: 42.7s, $1.38,
no answer). Remaining: VS2 (persist `forked_from`, fork-from-history) and
group fan-out, now gated on a cost number that exists.

**The cost question is answered: ~$1 per fork.** A fork does not inherit the
parent's warm cache, it builds its own — `cache_creation: 86,881` on top of
`cache_read: 215,775` on a mid-size conversation, scaling with context. So
"fork 8 ways" is ~$8 before any work happens: fan-out needs a cost gate, not
just a runtime. The ~15s floor is `claude` startup, not the fork.


A forked agent inherits the parent's **entire conversation** and continues
under a new handle. This retires the workaround baked into the MCP briefing —
*"the worker is a fresh agent with no context — write the payload as a
self-contained prompt with everything it needs"* — which is the largest cost
of delegating anything today.

**Not gated on `claude-sdk`** — `claude --fork-session` is a real flag on the
CLI aegis already drives (probed 2026-07-30), so `ClaudeDriver.fork()` is the
same three-line argv insertion as `.resume()`. `claude-sdk` inherits it via
`fork_session=True`. `opencode run --fork` exists too, but aegis drives
`opencode acp` and ACP v1 has no fork verb.

`fork()` is a sibling of
`resume()` on `HarnessDriver` with a `supports_fork` flag; MCP verb
`aegis_fork` is shaped like `aegis_spawn` (fire-and-forget, provenance via a
new `forked_from`). Per-fork `model`/`effort` overrides come free from the
existing `_overlay_agent`. Forking a *closed* session works too — it needs a
`session_id`, not a live process — so `Ctrl+R` gains fork-beside-reopen.

- Spec: `docs/superpowers/specs/2026-07-30-aegis-conversation-fork-design.md`
- **Not yet driven through a live pane** — the running `aegis serve` predates
  the code. The argv is verified to be the shape probed against real `claude`,
  and the wiring is unit-tested; the first restart is the real proof.
- Deferred to their own specs: group fan-out (`GroupRuntime.wait_all` needs an
  open broadcast, and `broadcast()` sends one objective to all members — no
  per-member angle, so fan-out needs a design decision); worktree isolation
  (worktree the *repo*, never the project root — `repos/` is gitignored and
  `vault/` must not be branched under autosync).

### Session titles *(shipped in v0.32.0 — only slice 4 remains)*

**Done and released.** A title beside the handle, generated automatically
when the first turn ends, settable by hand (`/title`, `/title --clear`),
regenerable from the transcript tail (bare `/title`), settable by the agent
(`aegis_title`, `aegis_rename(title=…)`), persisted, surviving a restart,
with `human > agent > auto` enforced. 2914 tests. Plan (slice 1):
`docs/superpowers/plans/2026-08-07-aegis-session-titles-slice1.md`;
commits `f2b73be..9ea030b`.

**Only slice 4 is left, and it is optional**: `generate_detailed` on the
gemini / opencode / lovelaice drivers. Today only `claude-code` implements
it, so a session on another harness simply stays untitled unless
`text_generation:` points at a claude profile — a correct degradation, not
a bug. Pick it up when someone actually runs a non-claude session as their
daily driver. The spec's open question (do those CLIs emit parseable JSON
without a schema flag?) is what `drivers/oneshot.py:parse_structured`
exists to absorb, and `supports_oneshot = False` is an acceptable answer.

**Still unverified by hand:** nobody has typed `/title` into a running
aegis and looked at it, and no auto-title has been generated against a real
model — the generation path is covered by fakes, and its live behaviour
(does the prompt actually yield 3-8 usable words on real openers?) is the
thing to watch on first use. The **web client renders no title** at all;
`SessionInfo.title` reaches the browser for free but nothing displays it.
Deliberate — this release was scoped to the TUI.

- **The tab bar was the wrong home, and the spec was stale about why.** It
  assumed `worker_label` owned the suffix; since the live task list shipped,
  `_tab_suffix` packs plan roll-up + worker label + `@host` in there. Measured:
  four tabs are **127 cells** untitled — already past a 120-col terminal — and
  190–210 with a title. The title went to the **status bar** (`P_TITLE = 25`,
  active session only, degrades on narrow terminals) and **`Ctrl+R`**.
- **Two bugs no unit test could see**, both found by driving it end to end:
  a `/rename` blanked the title (`_record_rename` re-derives every field —
  `7af708a`), and a resumed session forgot its title *and its
  `title_source`*, so an agent could overwrite Alex's title after any restart
  — the precedence rule was as strong as one uptime (`945190c`).

#### What slices 2 and 3 turned out to be

**Slice 2 was already built** — not by this feature: `/btw` needed the same
one-shot seam and shipped it. Verified on `main` 2026-08-07 before writing
a line of it:

| the spec asks for | already on `main` |
|---|---|
| `supports_oneshot` | `drivers/base.py:77`; `claude.py:232` sets it `True` |
| `generate(schema, *instructions)` | `drivers/base.py:127`, over `generate_detailed` at `:113` |
| the tolerant parser | `drivers/oneshot.py:parse_structured` (+ `Generation`) |
| `text_generation:` config | `config/yaml_loader.py:75,231`; resolved by `btw.generation_agent` |
| a claude implementation | `drivers/claude.py:288 generate_detailed` |

**Slice 3 — auto-titling — shipped**, and it was small precisely because
of the above: `titlegen.py` (one schema, two functions), an
`on_first_result` hook on the pane, and `AegisApp._autotitle` running it
off the loop. It reuses `btw.generation_agent` for the billing profile
(measured $0.0044/call on haiku) and `state/titles.py:sanitize_title` for
the output. Bare `/title` became *regenerate* in the same pass, with
`--clear` keeping the old affordance.

One thing worth knowing if you touch the fire-once logic: its first test
was **vacuous** and passed against a mutant. A successful first title sets
`title_source="auto"`, and *that* guard blocks the second call regardless —
so the test could not see whether the fire-once flag worked. It now has the
generator return `""`, leaving the flag as the only thing that can hold the
count at one. Mutation-check it if you change it.

`MissingSpawn` in `tests/test_mcp_bridge.py` is now non-conforming for two
reasons rather than one (no `spawn`, no `set_title`); it still asserts what
it was written to assert, but the name is half a lie.

Original design notes follow.

Ten tabs read `lucid-knuth` / `deep-dijkstra`. 0.28 made many tabs fast; it
didn't make them legible. CLAUDE.md's *"rename yourself once the purpose has
settled"* is the workaround — and it overloads a rename, which is **identity**
(`from_handle`, inbox routing, half the log id), with a job labels should do.

So: a `title` beside the handle, never instead of it. Handle semantics are
untouched. `title_source ∈ {auto, agent, human}` with strict `human > agent >
auto` precedence — which is also the whole concurrency story, since a late
auto-generation simply can't overwrite a human title. Stored by appending a
`SessionMeta` (already the mutation record — a rename appends one), so
`Ctrl+R` rows get readable for free.

Surfaces: `/title <text>` (human), `aegis_title` (agent), and an optional
`title=` on the existing `aegis_rename` so an agent can self-name in one call.

Second half is a **one-shot structured-generation seam on `HarnessDriver`**
(`supports_oneshot` + `generate(schema, *instructions)`) — no session, no MCP,
no tools. All four drivers can do it (verified on zion): claude
`-p --output-format json --json-schema`, gemini `-p -o json`, opencode
`run --format json`, lovelaice `lingo.Engine.create` (already in the venv via
`lovelaice>=2.11`). `text_generation: <profile>` in `.aegis.yaml` keeps a title
from costing Opus tokens. The seam generalises to generated commit subjects and
branch names later, which is why it's `generate(schema, …)` not
`generate_title()`.

- Spec: `docs/superpowers/specs/2026-07-30-aegis-session-titles-design.md`
- Plan: *not yet drafted — slice 1 (storage + manual set) ships value with
  zero LLM calls; generation is slices 2–4*
- Traps: gemini/opencode have no `--json-schema`, so a shared tolerant parser
  is needed; `lingo.Engine.create` returns `parsed=None` on reasoning models
  that emit JSON into the reasoning channel (observed on LM Studio) — fall back
  to `LLM.chat` + the same parse.
- Open: whether the tab bar has room at all — the cell already renders dot,
  index, handle, slug, and a muted suffix (which `QueueManager.worker_label`
  owns on worker tabs). Might belong in `Ctrl+R` + status bar instead. A
  screenshot answers this, not a spec.

### ✅ Dynamic workflows — Track 2 JSON DSL *(all 6 slices shipped — v0.19.0)*

Shipped end-to-end: `src/aegis/dsl/` (`models`/`interpreter`/`refs`/`validate`/
`plan`/`gate`), `aegis_run_dynamic_workflow` MCP tool, per-node checkpoint/
resume durability, bounded `map`/`parallel`/`loop`/`if` + `shell`/`judge`
predicates, `human` node via `ask_human`, and the cost gate
(`dynamic_workflow_autoapprove_agents`) — the first landing of the Track-1
gating rule too. 59 DSL tests green. **Deferred follow-on:** wire the same
cost-gate into the existing `aegis_run_workflow` (Track-1 Python) path.

Agent-authorable dynamic workflows as a validated JSON DSL — the safe/data
counterpart to Track-1 durable `@workflow` Python. Premise: harnesses now own
intra-harness fan-out (Claude Dynamic Workflows, Codex/Gemini subagents), so
aegis workflows reposition to what a single harness structurally can't do —
cross-harness, cross-restart/host durability, and mid-run human-in-the-loop.

Key decisions (see spec): the interpreter is itself a `@workflow` (inherits
durability/resume/gating); control flow = static shapes + hard-bounded
`loop`/`if` with typed `shell`|`judge` predicates; data flow =
select-never-compute selectors; leaf `target` = `spawn`|`session`|`queue`;
`human` node (TUI-only); gating = operator-implicit / agent-prompt-above-a-cost-
threshold. Also lands the missing **Track-1 gating** (operator implicit / agent
prompts, showing the script).

- Spec: `docs/superpowers/specs/2026-07-17-aegis-json-dsl-dynamic-workflows-design.md`
- Open questions (explicit decision points, not resolved in v1): `equals`
  predicate (avoid a `judge` agent-call when branching on a known value — felt
  at the design-thinking gate; `AnyPredicate` union left extensible); plan-preview
  cost estimate is a labelled static upper bound, not a prediction.
- Plan: `docs/superpowers/plans/2026-07-17-aegis-json-dsl-dynamic-workflows.md`
  — 6 vertical slices, thinnest-first, TDD per slice:
  1. walking skeleton (`sequence` + `agent`/`spawn`, `dynamic` @workflow);
  2. data-flow (`refs` selectors/templates, agent `inputs`/`schema`, semantic
     `validate`) + per-node checkpoint/resume durability;
  3. fan-out (`map` bounded concurrency + `parallel` barrier);
  4. bounded control flow (`loop`/`if` with `shell`+`judge` predicates,
     decision replay);
  5. `human` node (TUI via `ask_human`);
  6. `aegis_run_dynamic_workflow` MCP tool + `plan.py` preview + cost gate +
     the missing Track-1 gating rule + config threshold key.
- Grounding caveats flagged in the plan: no gating machinery exists to inherit
  (built from scratch in slice 6); durability rides `engine.checkpoint`/
  `resume_state` (not a bespoke ledger); `engine.parallel` has no concurrency
  cap (interpreter adds a `Semaphore`); shell predicate uses `engine.bash`
  (not `bash_predicate`); structured output is prompt-engineered + parsed;
  `jsonschema` promoted from transitive to direct dependency.

### Slash commands — Phase 2 *(decomposed into 2A–2D)*

Phase 1 shipped (v0.17.0): control commands `/help /sessions /agents /spawn
/queue /enqueue` + `!` shell escape, harness-agnostic registry + pure
`dispatch()`, magenta/blue input accents. Spec:
`docs/superpowers/specs/2026-07-16-aegis-slash-commands-design.md`.

Phase 2 (the powerful system) is decomposed into sub-specs, each its own
spec → plan → implement cycle; web parity threaded through each:

- [x] **2A — parser + resolution core** *(shipped)* — declarative typed args
  (`ArgSpec`/`Args`, flags-anywhere, greedy-verbatim), protected-builtin
  resolution with `source` tags + `CommandCollision`, `//` literal-slash
  escape (`classify_input`), `/queue new` persistence (`--ephemeral` opts
  out), and web-input parity (`deliver` routes slash → `command_result`).
  Spec: `docs/superpowers/specs/2026-07-17-aegis-slash-commands-2a-parser-resolution-design.md`;
  plan: `docs/superpowers/plans/2026-07-17-aegis-slash-commands-2a.md`.
- [x] **2B — full builtin coverage** *(shipped)* — operator-useful subset
  over the `AppBridge`: `/groups`, `/schedules`, `/terminals`, `/rename`,
  `/close`, `/themes`, `/clear`, plus agent management folded into `/agents`
  and queue listing on `/queues` (renamed from `/queue`). Collection nouns are
  plural and a bare noun-command lists. Adds the `CommandResult.effect`
  channel (frontend-applied theme/clear) and a `list_groups` bridge method.
  `/handoff` dropped (redundant with tab-switch for the operator; agent→agent
  stays MCP); `/config` dropped (agent verbs live on `/agents`). Spec:
  `docs/superpowers/specs/2026-07-17-aegis-slash-commands-2b-builtin-coverage-design.md`;
  plan: `docs/superpowers/plans/2026-07-17-aegis-slash-commands-2b.md`.
- [ ] **2B.1 — session-mutation slice** — `/model`, `/effort` via
  resume-restart (mutate the live `Agent`, tear down + `resume()` with the new
  argv so the conversation survives). Deferred from 2B: driver-capability-
  dependent session surgery that warrants its own spec + TDD pass.
  **Splits once `claude-sdk` lands** (see *Claude driver on the Agent SDK*):
  `ClaudeSDKClient.set_model()` does `/model` live on that driver with nothing
  to tear down. `/effort` does not split — there is no `set_effort()`, so it
  still needs the resume-restart, on every driver.
- [x] **2C — prompt commands + plugin `@command`** *(shipped)* — user-authored
  `.aegis/commands/<name>.md` (frontmatter `description`/`argument-hint` +
  `$1..$9`/`$ARGUMENTS` template, `@file` includes, embedded `` !`shell` ``,
  args-first expansion) expand and ride the `CommandResult.effect`
  `{"kind":"deliver"}` channel so both seams send them to the agent as a
  message (Claude-Code parity); plus a `@command` decorator beside
  `@workflow`/`@hook`/`@tool`, auto-registered on the plugin import sweep. Both
  plug into 2A's `source`-tagged registry, now with a full precedence rule
  (builtin > user > plugin) in `register()`. Palette (2D) color-codes the three
  sources. Boot-load at TUI `on_mount` + `serve`; no live watch. Spec:
  `docs/superpowers/specs/2026-07-17-aegis-slash-commands-2c-prompt-and-plugin-commands-design.md`;
  plan: `docs/superpowers/plans/2026-07-17-aegis-slash-commands-2c.md`.
- [x] **2D — discovery UX** *(shipped)* — inline **drop-up** command palette:
  type `/` and a panel rises above the input with fuzzy-matched commands,
  subverbs, and **live argument values** (agent/session/queue/group/schedule/
  terminal/theme names). Built on one pure `complete(text, bridge)` + an
  `Arg.completer` seam (static tuple or `(bridge) -> choices`) + a `fuzzy`
  scorer; TUI (`CommandPalette` widget + `GrowingInput.key_interceptor`) and
  web (`complete` RPC + drop-up `<div>`) render the same data. Spec:
  `docs/superpowers/specs/2026-07-17-aegis-slash-commands-2d-command-palette-design.md`;
  plan: `docs/superpowers/plans/2026-07-17-aegis-slash-commands-2d.md`.

### Native lovelaice agent (harness-free) *(VS1–VS5 shipped — on main + PyPI)*

aegis ships `lovelaice` as a dependency and drives `lovelaice-acp` over official
ACP v1 — a native agent that runs local or direct-API models with no external
harness. Shipped across lovelaice 2.7.0→2.11.0: v1 ACP server (legacy `AcpServer`
frozen for warden), per-session MCP attach (calls the aegis plane), full toolset
(read/bash/write/edit/glob/list_dir), token usage, streaming, `load_session`
resume, and cancel. aegis side: `Lovelaice` provider + `LovelaiceDriver` +
`extra_env`/`session_id`/`interrupt` on the generic ACP driver.

- Docs: `know-how/native-lovelaice-agent.md`; spec
  `docs/superpowers/specs/2026-07-10-lovelaice-native-acp-agent-design.md`;
  plans `docs/superpowers/plans/2026-07-{10,13}-lovelaice-native-agent-*`.
- **Deferred (own slice):** `workflow/run` + `conversation/archive` as ACP
  ext-methods — no consumer until warden migrates off the legacy dialect.
- Open: human eyeball of the native-agent render in a real TUI tab.

### Web client S1–S8 *(shipped — browser-verified, on main)*

Full web frontend of `aegis serve`: single-tab → multi-tab (picker, switching,
unseen markers, title pulse, cross-window coherence), Alt-based keyboard,
markdown/native-HTML/source file viewer (Alt+P), queue dashboard (Alt+Q),
group dashboard (Alt+G), config panel with editing (F2), theme switcher with
localStorage (Alt+Y). All slices specced + TDD'd + live-smoked in Chrome.

- WS protocol: `docs/superpowers/specs/2026-06-30-aegis-web-ws-protocol-design.md`
- Design + slice plans: `docs/superpowers/specs/2026-06-19-aegis-web-client-design.md`
  and `docs/superpowers/plans/2026-06-30-aegis-web-client-s*.md`
- Omnigent comparison that seeded the priority:
  `docs/superpowers/specs/2026-06-30-omnigent-vs-aegis-adoption-report.md`

### ✅ Web client S9.0–S9.2 — TUI becomes a WS client *(shipped 2026-07-16 as `aegis --remote`)*

S9.0–S9.2 shipped 2026-07-16 as `aegis --remote` (conversation loop).
`RemoteSessionManager` implements the conversation-loop `AppBridge` subset over
`WsClient`; `SSHTunnel` handles `ssh://` forwarding; `--remote` dispatches
scheme (ws/ssh) and auto-launches a local `aegis serve` for bare localhost invocations.

Deferred: **S9.3 (aux-surface RPCs)** — queue / canvas / terminal / group
dashboards raise `RemoteUnsupportedError` in remote mode; follow-up slice needed
to expose them over the WS protocol. **S10 (default flip)** — flip `--remote`
to the default and add `--classic` fallback; needs ≥1 week of daily remote use
before committing. See `know-how/remote-tui.md` for operational details and
known limitations.

- Spec: `docs/superpowers/specs/2026-07-01-aegis-tui-ws-client-design.md`
- Live smoke (loopback + zion→vps): **not yet done** — Steps 1–2 of Task 12
  require Alex's manual verification.

### Plugin substrate v1 *(complete — all 5 slices shipped in 0.15.0)*

Five-slice plan landed end-to-end on 2026-05-28: hooks (`@hook` — `pre_turn`
mutator + `post_turn` / `session_start` / `session_end` observers), tools
(`@tool` decorator + FastMCP registration with reserved-name guard), plugin
lifecycle (`plugin.toml` manifest, `InstallContext`, local-path install with
rollback, lockfile, `_install.py` / `_uninstall.py` hooks), registry
resolution (`gh:owner/repo#path` + `file://`, `git archive` HTTPS fetch,
`aegis plugin install / uninstall / list / show / update / search`), and the
canonical `plugins/skill-system/` plugin (pre_turn skill injection +
`load_skill` MCP tool, live-tested against a real `claude` subprocess).

- Spec: `docs/superpowers/specs/2026-05-28-aegis-plugin-substrate-design.md`
- Plan: `docs/superpowers/plans/2026-05-28-aegis-plugin-substrate-v1.md`
- Release notes: `CHANGELOG.md` § 0.15.0

Deferred to follow-ups (per spec § "Deferred — call-outs for future work"):
Tier B hook events (`pre_tool_use`, `post_tool_use`, `on_error`, `on_interrupt`,
`on_handoff`, `on_enqueue`); per-agent-profile tool scoping
(`agents.<name>.tools: [...]`); plugin-version constraints + inter-plugin
deps; Tier B substrate-events bus. Revisit when a concrete plugin demands
one.

### memory-system plugin *(shipped — v0.1.0)*

Second canonical plugin: Hermes-inspired persistent memory with
periodic dreaming. Exercises every v1 substrate primitive (`@hook`,
`@tool`, `@workflow`) end-to-end.

- Spec: `docs/superpowers/specs/2026-05-30-aegis-memory-plugin-design.md`
- Plan: `docs/superpowers/plans/2026-05-30-aegis-memory-plugin-v1.md`
- Release notes: `CHANGELOG.md` § memory-system plugin (v0.1.0)

### Driver visibility parity *(complete — all 7 slices shipped)*

Make every tool call legible across drivers: semantic kind icon, path hint,
structured input retained, success/failure styling. Slice 1 shipped
(`3f6772b` → `763e1b6`) — `ToolUse` / `ToolResult` carry `kind`, `tool_call_id`,
`raw_input`, `locations`, `status`; `_AegisAcpClient` and the claude parser
populate them; `render_event` shows a glyph per kind (📖 ✏️ ⌬ 🔎 ✻ 🌐 ➡️ 🗑 🔄 ⏺)
and a path-tail hint; codec round-trips through `state/event_codec.py` with
legacy-record decode. Two ride-along bug fixes: ACP `is_error` now derives from
`status=="failed"`, Gemini usage falls back to `field_meta.quota.token_count`.

Slice 2 shipped (`f141b51` → `de1fd68`): `AssistantText` / `AssistantThinking`
carry `message_id` from both drivers; new pure helper
`aegis.render.coalesce_chunks` merges adjacent same-`(type, message_id)`
chunks; `replay_blocks` pipes through it before rendering. Smoke against
real `opencode acp`: 80 raw events → 9 coalesced; opencode's per-token ✻
cliff is gone. Live pane streaming was already kind-coalesced via
`_stream_append`; this slice closes the same gap on the replay path.

Slice 3 shipped (`2648551` → `81b4956`): canonical `AgentPlan` event +
`PlanEntry` dataclass; claude parser promotes `TodoWrite` tool_use to
`AgentPlan`; ACP `AgentPlanUpdate` notification maps to the same event;
renderer shows a `📋 Plan — N/M done` block with status glyphs
(● completed, ◐ in_progress, ○ pending) and priority emphasis. Real-CLI
smoke against an opencode planning turn surfaced 4 distinct plan
revisions live (0/3 → 1/3 → 2/3 → 3/3). Polish item deferred: replace
prior `AgentPlan` from same turn instead of appending — ship if it
becomes noisy in real use.

Slice 4 shipped (`b1cd895` → `28e25a4`): `ToolResult.diff` field
carries `(path, old_text, new_text)`. ACP driver extracts it from
`FileEditToolCallContent` in `ToolCallProgress.content`. Claude parser
synthesizes from the matching `Edit`/`Write` tool_use input via the
new `ParserState.tool_diffs` cache. Renderer shows a small unified
preview — capped at 6 visible rows with truncation footer — with `-`
red and `+` green gutters. Real opencode write of a 5-line file
surfaces the full added content live in the transcript.

Slice 5 shipped (`8f9965c` → `dae8963`): `Result` carries stop_reason,
ttft_ms, num_turns, cost_usd, model_usage, permission_denials. Both
drivers populate (ACP cost comes from the last mid-turn UsageUpdate;
Gemini's per-model attribution from field_meta.quota.model_usage).
Renderer's terminator line surfaces cost + non-default stop_reason
when fired. Codec backward-compatible.

Slice 6 shipped (`72d7fc5` → `247b154`): canonical `ContextUpdate` +
`CostUsage`; ACP `session_update` maps UsageUpdate / CurrentModeUpdate /
SessionInfoUpdate to the canonical event. Renderer returns None
(transcript stays clean); status-bar / metrics consumption is a polish
follow-on.

Slice 7 shipped (`7840def`): `SystemInit` carries model, permission_mode,
version, available_commands. Claude reads from `system.init`; ACP emits
at boot from `InitializeResponse.agent_info` and follows with a second
`SystemInit` carrying available_commands when
`AvailableCommandsUpdate` fires.

The 7-slice arc is complete. The canonical event surface now exposes
every signal both substrates publish. Polish follow-ons (status-bar
consumption of `ContextUpdate`, plan-block replacement-within-turn,
TTFT for ACP) remain candidate work but aren't on the critical path.

- Spec: `docs/superpowers/specs/2026-05-28-aegis-driver-visibility-parity-design.md`
- Slice-1 plan: `docs/superpowers/plans/2026-05-28-aegis-driver-visibility-slice1.md` *(status: shipped)*

### Session history (`Ctrl+R`)

Modal listing every user-initiated agent session (open or closed, current
process or previous); reopens via jump-to-tab, `drv.resume()`, or fresh spawn
with recorded profile + cwd. Three slices: backend reads → resume path with
`session_id` latch → close marker + preview.

- Spec: `docs/superpowers/specs/2026-05-28-aegis-session-history-design.md`
- Plan: `docs/superpowers/plans/2026-05-28-aegis-session-history.md`

### Aegis filesystem tool surface

Six aegis-owned tools (`aegis_bash`, `aegis_read`, `aegis_write`, `aegis_edit`,
`aegis_grep`, `aegis_listdir`) routing every agent's filesystem + shell access
through the substrate. `PermissionRouter` (`allow` / `deny` / `ask`) with TUI
inline approval. Hard Claude built-in suppression via
`--tools ""`. Universal "prefer aegis tools" system-prompt addendum.

- Spec: `docs/superpowers/specs/2026-05-27-aegis-fs-tool-surface-design.md`
- Plan: `docs/superpowers/plans/2026-05-27-aegis-fs-tool-surface-v1.md`

### Agent sandbox *(designed, no plan yet)*

Per-profile opt-in isolation primitives: worktree isolation, declarative
read-only / hidden filesystem partitioning, outbound network block. Backend:
`bubblewrap` for filesystem + network (Linux-only); native `git worktree add`
for worktrees.

- Spec: `docs/superpowers/specs/2026-05-27-agent-sandbox-design.md`
- Plan: *not yet drafted*

### Queue v1 polish *(shipped 2026-07-13)*

Small follow-ups on top of the shipped substrate:

- **Worker tab handle suffix** *(shipped)* — in-flight worker tabs now
  show `<queue>#<task>` in muted after the slug, via
  `QueueManager.worker_label` threaded through `_refresh_tabbar` +
  `_TabCell.render_tab`. Clears on finalize/cancel.
- **`aegis_cancel(task_id)` MCP tool** *(shipped)* — `QueueManager.cancel`
  drops pending / interrupts+closes in-flight, marks `cancelled`, delivers
  one error callback so awaiting producers unblock. Idempotent.
- **`aegis_delegate` sync wrapper** *(shipped)* — `QueueManager.run`
  enqueues (callback off) + awaits a one-shot completion subscription,
  returning the worker's result directly; optional `timeout_s`.

### Sequential handoff — re-scope

Original framing (vision Phase 4): agent A summarises its current task state
and retires; agent B (potentially a different harness) is instantiated and
continues from where A left off.

Adjacent work has since shipped (workflow `send/drain/caller_handle`, inbox
arrivals with a visible block, canvas substrate, agent groups, remote plane).
Worth re-scoping before picking up — figure out what's left vs what's already
in the substrate.

### OpenAI Codex JSON-RPC driver

Codex CLI exposes a bidirectional JSON-RPC app server (`codex exec --json`).
Different from ACP but documented and stable. Needs a custom `CodexDriver`
implementing `HarnessSession` over JSON-RPC. Auth: `OPENAI_API_KEY` env var.
No deadline pressure.

### Web client + TUI WS-client migration *(designed, no plan yet)*

First-class web frontend (desktop), feature parity with the TUI. Hybrid
visual idiom (TUI-faithful transcript via `render_event_html`, native-web
chrome via HTMX + Jinja). One multiplexed WS per browser window; subscribe
sends full session history then live events; reconnect via `(session_id,
last_seq)` resume against the existing JSONL persistence. Themes move to
shared YAML (`src/aegis/data/themes/*.yaml`) so TUI and web stay visually
identical. End-state: TUI also becomes a WS client of `aegis serve` so
sessions are shared across TUI ↔ web.

Ten slices, S1–S10, vertical, foundation-first. Earliest "usable single-tab
web client" is end of S2; full TUI feature parity is end of S6; full
architectural unification (TUI flipped to `--remote` default with `--classic`
fallback) is S10. Each slice is an honest stop point.

- Spec: `docs/superpowers/specs/2026-06-19-aegis-web-client-design.md`
- Plan: *not yet drafted — start with S1 (theme YAML + shared render refactor)*

## Ideas — things the `generate()` seam unlocks

Unspecced, roughly ordered by how distinctively aegis-shaped they are. All
ride the one-shot structured-generation seam from
`2026-07-30-aegis-session-titles-design.md` — cheap, no session, no MCP, no
tools, `text_generation:` profile. **Nothing here belongs on the hot path of
a turn**: a one-shot call is 1–3s, so every trigger below is idle-, boundary-,
or operator-driven.

### Group reducer — `synthesize` and `dissent`

`groups/reducers.py` already has `register_reducer(name, fn)` and a
`_REGISTRY` — an open extension point. Today `wait_all` gives you `concat`
(a wall of N agents' text you re-read yourself) or `majority_vote` (only
fires when answers are string-identical, i.e. never for prose).

Two reducers, one call each. `synthesize` reconciles the members into one
answer. **`dissent` is the more interesting one** — report where they
*disagreed* and why, which is exactly what a majority vote destroys and what
you can't get by skimming four replies.

Distinctive because `aegis_group_spawn_mixed(preset=…)` can convene claude +
gemini + opencode + a local lovelaice model. Omnigent and t3code have no
groups at all; nobody else can panel four vendors and reconcile them.

### DSL structured output — collect on a logged debt

`dsl/interpreter.py` does `json.loads(_extract_json(reply))` at **three**
sites (133, 165, 229), and the DSL plan shipped with *"structured output is
prompt-engineered + parsed"* written down as a known caveat. `_run_judge`
(line 214) is a `generate(YesNo, …)` call wearing a costume.

Not a feature — makes bounded `loop`/`if` control flow trustworthy rather
than hopeful. Smallest item here.

### Session recap — "where are we right now"

After a session sits idle ~5 min, generate a short *current state* recap:
what's been established, what's in flight, what's next. For coming back to
one of ten tabs without re-reading the transcript.

Hook exists: `AgentSession._arm_idle_watcher` (`core/session.py:582`,
armed at 220/538, cancelled at 225/273) already detects idle — currently
gated on `supports_idle_events`, which a recap trigger should *not* be.

Distinct from a summary: recap is a live snapshot, regenerated as state
moves; a summary is retrospective and written once.

### Session summary — the retrospective

Written at close (or on demand), appended to the log. Turns `Ctrl+R` from a
list of handles into a searchable archive. Composes with session titles —
same record, same surface, same modal.

Regeneration should truncate the transcript from the **front**, not the back
(t3code's `preserveMessageEnd`): the end is what the summary is about.

### `/btw` — a side question that doesn't steal the turn

Ask a one-off question about what's happening *while the agent keeps
working*. Takes the last few turns as context, answers via `generate()`, and
**never touches the session** — no interrupt, no inbox delivery, no turn
consumed. "Why did it pick that file?", "is this the third retry?"

The point is the non-interruption. Today the only ways to ask are `Enter`
(queues a chip, waits for the turn to end) or `Alt+Enter` (cuts the live
turn). `/btw` is the missing read-only third option. Renders in the pane as
an operator-side aside, not as a conversation turn.

Open: how many turns of context, and does the answer persist in the
transcript or vanish on scroll?

### Loop auto-evaluate — an independent judge, not the same agent

`aegis_loop_stop(from_handle, reason)` (`mcp/server.py:1248`) has the agent
reap **its own** loop, and `core/loop.py` only otherwise stops at
`max_iterations` (`exhausted`, line 35). So the entity deciding "is the goal
met?" is the entity that wants to stop working — and the failure mode is
documented in this workspace's CLAUDE.md: a 20-iteration loop stopped at
iteration 1 with the user-visible half unbuilt, because a narrow reading of
the instruction was defensible *to the agent that wrote it*.

Idea: on each would-be-idle boundary, a **separate** one-shot call gets the
loop instruction plus a digest of what's happened, and answers "goal met?".
The agent can still call `aegis_loop_stop`, but a disagreeing judge re-arms
the loop with the gap named.

The failure mode to design against is the inverse — a judge that never
lets go. Needs a cap, and the judge's verdict surfaced in the loop strip so
it's arguable rather than silent.

## Backlog

### Plugin-first core — multi-quarter direction

Full vision at
`docs/superpowers/specs/2026-08-23-aegis-plugin-first-core-vision.md`.
Captured from a voice note on 2026-08-23 after comparing aegis to the
freshly-released DeepSeek Harness (DSH), which ships an
"everything-is-a-plugin" architecture on top of Cordis.

The destination: aegis shrinks to a minimal core (harness driver
abstraction, MCP wiring, UI abstraction, session lifecycle, plugin
runtime), and most of what today are native subsystems — workflows,
queues, scheduler jobs, canvases, terminals, groups, even the concrete
harness drivers — become bundled-but-swappable plugins. Two builds
become trivially possible: the coding-harness default, and a clean base
someone else can build a non-coding harness on top of.

Five ideas, in dependency order:

1. **Vertical plugin extension** — plugins can define subsystems, UI
   panels, MCP entries, event types, persistence backends, not just
   hooks/tools/workflows.
2. **Runtime lifecycle** — install / uninstall / enable / disable a
   plugin in a live `aegis serve`, without restart.
3. **Agent-authored plugins in-session** — the agent writes a plugin
   during a turn, attaches it, the next turn has the new capability. A
   meta-skill + MCP introspection surface tell the agent what's loaded.
4. **Plugin registry (aegis-hub)** — a canonical store + registrable
   additional stores + publish flow.
5. **UI abstraction** — atomic composable widgets in ~5 lines of Python
   that automatically bind to both TUI and web.

Non-goals: Cordis port, web-first UX, multi-provider LLM at aegis level,
cross-machine sync. Each idea is weeks-to-months; this is a
multi-quarter direction, not a sprint.

### Subscription-backed models (Antigravity / gateway) — DEFERRED INDEFINITELY

Gemini CLI subscription access died 2026-06-18; the `gemini --acp` driver is
now dead weight for subscription users. Replacement `agy` (Antigravity CLI,
v1.1.2, installed on zion) **dropped ACP** — only `--print` one-shot +
`--continue` resume, no stream-json, no per-session MCP injection. `agy models`
brokers a multi-provider pool under one Google quota (Gemini 3.x, **Claude
Opus/Sonnet 4.6 Thinking**, GPT-OSS), but the free/Pro quota is now ~20 req/day
— marginal value. Deferred indefinitely.

**Path C, zero driver code — WIRED + SMOKE-TESTED 2026-07-24.** Front a local
OpenAI-compatible gateway (OmniRoute, `localhost:20128/v1`) that harvests the
`agy` OAuth token, and point the existing **Lovelaice provider** at it via its
`base_url` + `api_key_file` fields (`config/__init__.py:67-71`,
`drivers/lovelaice.py:26-39`). Keeps full aegis-MCP injection / streaming /
idle events.

Declaring such an agent in `.aegis.yaml` was impossible until `449ebbb`:
`yaml_loader._PROVIDERS` never registered `lovelaice`, so `provider: lovelaice`
died with "unknown provider" — the provider model, driver, and driver registry
all existed, only the YAML loader's dict was missing it. That two-line fix is
the entire aegis→gateway wiring.

Proven end-to-end over OpenRouter (qwen3-32b): a lovelaice worker ran bash,
wrote files, called `aegis_list_sessions`, and delivered a message to a live
Claude peer via `aegis_handoff`. Findings:

- **`api_key_file` is mandatory even for a keyless gateway** — without it
  lovelaice's OpenAI client dies at ACP session start as an opaque
  `RequestError: Internal error`.
- **`aegis_handoff` does not validate `from_handle`** — an unregistered handle
  was accepted and routed. Possible spoofing surface; see Watching.
- qwen3-32b leaks `<tool_call>` fragments into the text channel (tools still
  execute) — the shim-parsing risk the handoff warned about is real, and
  OmniRoute's headline models are exactly that reasoning shape.

Remaining: OmniRoute itself has **zero connected providers**, so every model
returns an empty response until a provider is signed in at `localhost:20128`.
Local profile staged inert at `.aegis/agents/omni.yaml.disabled`.

Full capture, recipe, and risks:
`vault/+/agent_drafts/handoffs/handoff-2026-07-22-0830-aegis-subscription-models.md`

### Shrink the injected surface: MCP plane behind a discoverable index

The plane registers **65 tools** (`src/aegis/mcp/server.py`), and every session
also gets `PRIMING` appended to its system prompt (`server.py:370`) while
`aegis_meta` returns a `BRIEFING` well over 12k chars (`server.py:125`). A
worker that inlines all of that pays for it before doing any work.

Measured 2026-07-24: a single lovelaice turn (write a file, run bash, call two
aegis tools) cost **87.7k input tokens** — dominated by tool schemas, uncached
on that path.

**The cost is harness-dependent, which is the useful part.** Claude Code
already defers MCP tool schemas — inside aegis a Claude session sees only tool
*names* and must call `ToolSearch` to load a schema before use. lovelaice has
no such mechanism, so it inlines all 65. Same plane, very different bill. So
the fix lands in one of two places:

- **aegis-side (helps every harness):** expose a small always-on core
  (`aegis_meta` + a search/fetch pair) and serve the rest on demand — the
  deferred-tool pattern Claude Code uses. Also worth trimming `PRIMING` and
  `BRIEFING`, which are prose-heavy and re-sent per session.
- **lovelaice-side:** teach `lovelaice.mcp` lazy schema loading, so it gets
  the Claude-Code behaviour for free against any MCP server.

Prompted by Alex after the OmniRoute smoke — matters most for cheap/quota-bound
workers, where 88k/turn undercuts the whole premise of a cheap worker (the
`agy` pool is ~20 req/day). No plan yet; measure before and after.

### New `agy` driver (Path A) — noted for the future

A dedicated Antigravity driver that drives the `agy` binary directly
(`--print` + `--continue`/`--conversation`), notably unlocking **Claude
Opus/Sonnet 4.6 and Gemini via the Google subscription** without a gateway.
Downside vs Path C: no ACP ⇒ likely no aegis-MCP tools for those workers (probe
`agy plugin import claude` as a possible MCP route). Not now — noted for when
subscription access is worth a real driver-build.

### Move the Claude driver off `claude -p` — noted for the future

Today `ClaudeDriver` shells out to `claude -p --input-format stream-json
--output-format stream-json --verbose` (subprocess kept alive per session,
resume via `--resume <id>`, interrupt via a stream-json control_request —
`src/aegis/drivers/claude.py:199-232`). A future rework would drive Claude a
different way — most likely the **Claude Agent SDK** (aegis already runs
*inside* it) or an **ACP-native** path that unifies Claude with the
gemini/lovelaice drivers on one protocol (`AcpDriver`), instead of parsing the
CLI's stream-json. Motivation: robustness + protocol unification, less
dependence on the `claude -p` CLI surface. Not now — noted for the future.

## Watching

- **`aegis_handoff` accepts an unregistered `from_handle`.** During the
  2026-07-24 OmniRoute smoke a lovelaice worker passed
  `from_handle="lovelaice-probe"` — not a live session — and the message was
  accepted and routed to the target's inbox, attributed to that name. Handy
  for out-of-band probes, but it means any agent on the plane can attribute a
  message to any handle. Filing, not acting on it yet; decide whether the
  sender should be validated against the live session registry (or stamped by
  the substrate rather than passed by the caller).

- **VPS job-crawler dispatched the plan job (2026-05-20-aegis-task-queue-plan)
  but never picked up its follow-up implement job** (file existed on VPS
  with `status: armed` and `fire_at` in the past, crawler was healthy
  and firing every 60s). One-off so far; needs a closer look at the
  crawler's eligibility logic if it happens again. Filing here, not
  acting on it yet.
