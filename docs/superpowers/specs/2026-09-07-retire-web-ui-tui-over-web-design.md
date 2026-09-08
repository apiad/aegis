# One boot path: retiring the web UI, and aegis as a library

*Design — 2026-09-07. Status: approved, not yet planned.*

Two goals that turn out to be the same refactor: collapse aegis's front ends
to a single TUI (rendered locally, or over the web), and make aegis bootable
in-process as a library so sindri can drive it. Both reduce to **one boot
sequence with four attachments** — headless, local TUI, web-driver TUI, and
embedded.

## The problem

`AGENTS.md` claims aegis has "two co-equal first-class UIs over one
`aegis serve` backend … Both render the same transcripts with the same
fidelity." That claim is false, and it has been false for a while.

There are in fact **three** implementations of the aegis front end, not two —
and beneath them, **two divergent boot paths**:

1. `src/aegis/tui/` — the Textual TUI, driven by `core/manager.SessionManager`.
   Complete.
2. `src/aegis/web/static/` — a hand-written JS client speaking a bespoke WS
   protocol. Re-implements the agent picker, queue dashboard, group dashboard,
   file picker, file viewer, theme picker, config panel and command palette.
3. `src/aegis/tui/remote_manager.RemoteSessionManager` — a `SessionManager`
   look-alike backing `aegis --remote`, feeding the *same* TUI widgets from the
   WS protocol instead of from local state.

Feature coverage, measured by grep across `src/aegis/tui/` versus
`src/aegis/web/`:

| Subsystem | TUI | Web client |
|---|---|---|
| Shared terminals | ✅ | ❌ |
| Voice input | ✅ | ❌ |
| System meter | ✅ | ❌ |
| Plan strip | ✅ | ❌ |
| File browser tab | ✅ | ❌ |
| Monitors | ✅ | partial |

`RemoteSessionManager` is worse. Every one of these is present on the local
manager and absent from the remote one:

```
attach_terminal_manager  attach_canvas_manager   attach_monitor_manager
attach_queue_manager     attach_reminder_service attach_scheduler_context
attach_persistence       attach_locks_state      attach_remote_plane
attach_remotes           plan_state              _plan_roll_up
read_peer                live_handles            close_all
session_send_and_await   reconnect               _fork_capability
```

It is a conversation viewer wearing a `SessionManager` costume.

### The fourth divergence: two boot paths

`aegis` and `aegis serve` do not share a boot sequence. `_serve()`
(`cli.py:373`) has exactly one caller — `_run_serve` — and the TUI path
constructs `AegisApp(...)` directly (`cli.py:224`), passing `agents`,
`queues`, `hosts` and `voice` but **not** `schedules`, `remotes` or
`remote_plane`.

The consequence is live today, and stated in the code
(`src/aegis/tui/app.py:466`):

> *Scheduler-context stubs to satisfy AppBridge. The TUI does not run a
> scheduler; the `aegis_schedule_*` MCP tools will gracefully return errors
> when scheduler is None.*

So **schedules do not fire under `aegis`, only under `aegis serve`**, and the
peer remote plane (`_maybe_start_remote_plane`) likewise starts only in the
headless path. The two entry points are not one system with an optional UI;
they are two systems with overlapping subsystems.

This matters directly: naively spawning the TUI under a web supervisor would
silently drop schedules and the peer plane on the VPS, which is precisely
where both are used. Unifying the boot path is therefore **in scope**, not a
follow-up.

It is also the seam **sindri** needs. The forge wants aegis as a library —
booted in-process, at an arbitrary root, several instances at once — and the
same boot path that serves three UIs serves that as a fourth attachment with
no UI at all. One sequence, four entry points, is the whole design.

**Both non-TUI paths are also broken right now**, which is how this was found:

- Every `--remote` URL omits the `/ws` path. `WsClient` uses the URL verbatim
  (`ws_client.py:28`), and nothing appends it, so `cli.py:128` (the no-arg
  default `ws://localhost:8080`), `cli.py:141` (the `ssh://` tunnel) and the
  form documented in `know-how/remote-tui.md` all get HTTP 403. Verified
  against a live daemon: `"WebSocket /" 403` versus `"WebSocket /ws" [accepted]`.
- Once connected, the TUI crashes mounting the first pane:
  `AttributeError: 'types.SimpleNamespace' object has no attribute
  'render_tiers'`. `remote_manager.py:97` builds
  `SimpleNamespace(render=lambda _t: "")` under the comment *"Minimal metrics
  stub so refresh_metrics() doesn't crash"*; `pane.py:1289` has since moved to
  `metrics.render_tiers(now, palette)`. Reproduced in a plain terminal under a
  real pty, with no web layer involved.

The suite is green through both because `tests/tui/test_ws_client_reconnect.py`
and `tests/cli/test_remote_flag.py` assert the broken URL shape against bare
`websockets.serve` handlers that accept any path, with the manager mocked out.
They pin the client against a fake server that has no route table — tests that
cannot fail.

We are not going to fix two of these. We are going to delete two of them.

## The insight

Textual already ships a web driver. `textual.drivers.web_driver` is 354 lines
inside the `textual` package that is *already installed* in aegis's venv.

That means rendering the TUI in a browser is not a port. It is an environment
variable and a socket:

1. Spawn `aegis` as an ordinary subprocess with `stdin`/`stdout` as pipes.
2. Set `TEXTUAL_DRIVER=textual.drivers.web_driver:WebDriver` in its
   environment, plus `COLUMNS`, `ROWS`, `TEXTUAL_FPS`, `TEXTUAL_COLOR_SYSTEM`.
3. Textual then writes framed packets to stdout (`b"D"` data, `b"M"` meta)
   instead of escape codes to a terminal.
4. The browser paints the data packets with xterm.js and sends back a trivial
   JSON protocol: `["stdin", …]`, `["resize", …]`, `["ping", …]`.

No pty. No terminal emulation on our side. `textual-serve` (1,224 lines of
aiohttp glue) does exactly this and nothing more clever; a spike on
2026-09-07 confirmed it drives the real aegis TUI, that `Ctrl+T` round-trips
from the browser into the app, and that the TUI reflows legibly at a 393 px
phone viewport.

## The design

**One brain, supervised, reached through a socket.**

```
browser ──HTTPS──► Caddy ──basicauth──► supervisor (aegis web)
                                             │  spawns, owns, buffers
                                             ▼
                                    aegis  (the ordinary TUI)
                                    TEXTUAL_DRIVER=web_driver
                                             │
                                    SessionManager, MCP plane,
                                    queues, schedules, hosts,
                                    terminals, canvas, monitors
```

The subprocess is **the same `aegis` you get by typing `aegis` on that
machine**. It owns a real `SessionManager` and a real MCP plane. It is not a
client of anything. There is no second code path to keep at parity because
there is no second code path.

The supervisor owns no aegis state at all — only the subprocess, an output
buffer, the WebSocket(s), and auth. Its coupling to aegis is close to zero.

### Detached lifecycle — the one genuinely new thing

`textual-serve` ties the app's life to the socket: drop the connection and you
get `Session ended. [Restart]`. The spike hit this. For a phone on a flaky
link that is exactly backwards.

The supervisor must therefore:

- **spawn the subprocess independently of any connection**, and keep it alive
  across disconnects;
- **maintain a bounded ring buffer** of recent output packets;
- **on (re)connect, replay the buffer** so the client repaints, then stream
  live.

This is the tmux-shaped part, and it is the real reason to write our own ~300
line bridge rather than depend on `textual-serve`: process lifecycle is the
piece we most need to control, and the piece that library gets wrong for this
use case.

### Shared screen

Multiple browsers attach to **one** subprocess and see the same screen —
`tmux attach` semantics. All connected clients receive the same packet stream;
input from any client goes to the same stdin.

Consequence, accepted deliberately: two devices share a cursor and a resize.
The terminal size is negotiated as the **minimum** of connected viewports, so
the smallest attached client determines the geometry. A single client
reconnecting at a new size resizes normally.

### Auth

Unchanged, and this is a reason to build on the existing starlette server
rather than adopt `textual-serve` (which ships no auth at all):

- the existing `web.token` from `.aegis.yaml` / `AEGIS_WEB_TOKEN`, presented on
  the WS handshake exactly as today;
- the existing Caddy `basicauth` block in front of it, untouched;
- the existing `aegis token` command, untouched.

No new secret, no new surface, no change to `know-how/deploying-web.md`'s
topology beyond the unit's `ExecStart`.

### One boot path, with the UI as an attachment

Because `aegis` and `aegis serve` boot differently (above), the front-end
collapse is not sufficient on its own. `_serve()` becomes **the single boot
path for every entry point**, gaining an optional UI attachment:

```python
async def _serve(*, agents, default_agent, make_session, mcp, stop,
                 queues=None, schedules=None, remotes=None, remote_plane=None,
                 hosts=None, host_registry=None, local_root=None,
                 inline_schedule_names=None,
                 ui: UIAttachment | None = None) -> None:
```

Three attachments, one sequence:

| Entry point | `ui=` |
|---|---|
| `aegis serve` | `None` — headless, as today |
| `aegis` | local Textual UI |
| the supervisor's subprocess | Textual UI under `TEXTUAL_DRIVER=…web_driver` |

Every subsystem — scheduler, remote plane, queues, hosts, MCP, persistence —
is wired **once**, in `_serve`, regardless of attachment. `AegisApp` stops
being a boot path and becomes a view.

This is the part that makes the whole change worth doing: it is what "no
parallel code" actually requires, and it fixes a real live defect (`aegis`
not firing schedules) as a side effect rather than as separate work.

**Note the ordering constraint:** the boot unification must land *before* the
supervisor, or the supervisor ships a VPS that silently stops running
schedules.

### The fourth attachment: aegis as a library

**sindri** — the agentic forge, `repos/sindri` — needs aegis
**programmatically, not as a process**. From its vision transcript
(`vault/+/Inbox/2026-09-01-sindri-vision.md`): *"tenemos que actualizar la
arquitectura de aegis para que sea usable de manera programática cien por
ciento… es posible que todavía haya cosas ahí que estén encadenadas al hecho
de que hay un proceso corriendo."*

That guess is correct, and the coupling is specific. Three things bind aegis
to a process today:

**1. State is rooted at the process cwd, not at a parameter.** `_serve` accepts
`local_root` and uses it in exactly one place (`cli.py:386`), while calling
`Path.cwd()` **eight times** for everything that matters:

```
cli.py:401  attach_persistence(_state_dir(Path.cwd()))
cli.py:404  attach_locks_state(_state_dir(Path.cwd()))
cli.py:405  CanvasManager(state_dir=_state_dir(Path.cwd()), …)
cli.py:410  TerminalManager(state_dir=_state_dir(Path.cwd()) / "terminals")
cli.py:450  Scheduler(state_dir=_state_dir(Path.cwd()), …)
cli.py:455  attach_scheduler_context(state_root=Path.cwd(), …)
cli.py:463  root = Path.cwd()
cli.py:481  WebFrontend(…, state_dir=_state_dir(Path.cwd()), …)
```

This is the blocker, and it is fatal rather than merely untidy: sindri runs
**one aegis per repo worktree, many in one process**. `Path.cwd()` is a
process global — you cannot have two. The root must become an explicit
parameter threaded through every state constructor.

**2. The boot path exits the process on bad input.** `cli.py` carries 20
`typer.Exit` calls, several on the config-loading path a library would use.
A library raises `ConfigError`; only the CLI turns that into an exit code and
a red console line.

**3. The boot path owns the event loop and the signal handlers.**
`_run_serve` calls `asyncio.run(main_async())` and installs SIGINT/SIGTERM
handlers (`cli.py:760-772`). An embedded aegis runs inside sindri's loop and
must never touch its signals.

The fix falls out of the same refactor, as a fourth attachment:

| Entry point | Owns loop? | Owns signals? | `ui=` |
|---|---|---|---|
| `aegis serve` | yes | yes | `None` |
| `aegis` | yes | yes | local Textual |
| supervisor subprocess | yes | yes | Textual + web driver |
| **`aegis.embed()`** | **no — caller's** | **no** | `None` |

```python
async with aegis.embed(root=Path("/srv/repos/foo")) as ae:
    handle = await ae.manager.spawn("implementer", opening_prompt=plan_text)
    await ae.queues.enqueue("verify", payload, from_handle=handle)
```

`_serve` becomes loop-agnostic — it does the wiring and returns a handle;
`asyncio.run` and signal installation move up into the CLI wrappers where they
belong. `embed()` is then the same wiring with no UI and no process ownership.

**Already done, contrary to the vision doc.** The transcript says per-agent
system prompts *"no lo tenemos en aegis hoy, vamos a tener que añadirlo"*.
They landed since: `Agent.prompt` (`config/__init__.py:99`) points at a
Markdown persona file, resolved by `config/persona.py::read_persona`, and
composes with rather than replaces the primer. Sindri's "un agente es una
tupla que define un harness, un modelo, un effort level y un prompt" is
expressible in `.aegis.yaml` today.

**Deliberately not in this spec:** sindri's own primitives — file-pattern
triggers, worktree/branch determinism, bash preconditions, final checks with
retry loops, auto-merge. Those are forge concerns built *on* this API, and
they need their own design. What lands here is only the seam that makes them
possible: boot aegis in-process, at an arbitrary root, N times, with every
subsystem wired.

### Command surface afterwards

| Command | Before | After |
|---|---|---|
| `aegis` | TUI, own boot path, **no scheduler, no peer plane** | TUI via `_serve(ui=local)` — gains both |
| `aegis serve` | headless: MCP + queues + schedules + remote plane + **web frontend** | `_serve(ui=None)`, minus the web frontend |
| `aegis web` | ensure token, open browser, `_run_serve` | **the supervisor**: HTTP + WS, spawns `aegis` under the web driver |
| `aegis --remote ws://…` | broken | **removed** |
| `aegis --remote ssh://…` | broken | **removed** |
| `aegis token` | prints web token | unchanged |

On the VPS, `aegis-web.service` changes `ExecStart` from `aegis serve` to
`aegis web --no-browser`. Exactly one aegis brain runs there, as today.

### Explicitly out of scope — unchanged by this work

- **`hosts:` / SSH execution hosts** (`src/aegis/hosts/`, 684 lines). Verified
  to import nothing from `web/`, `ws_client` or `remote_manager`. `host=` is a
  parameter of `core/manager.py`'s spawn — the surviving manager. This is the
  reverse of `--remote`: it keeps the UI local and moves the *harness* away
  over an SSH ControlMaster.
- **`remotes:` / the peer remote plane** (`src/aegis/remote/plane.py`) — an
  independent HTTP plane that never touched the web WS plane.
- **The MCP plane** — owned by the subprocess, as it is owned by `aegis` today.

Worth stating plainly, because it inverts the expected trade: **`--remote`
refuses SSH-host tabs today.** From `remote_manager.py:226`, in the code being
deleted — *"the WS protocol has no field to carry a placement request — so
refuse clearly"* — and the web client's `spawn_session` RPC carries
`agent_profile` and nothing else. So retiring both paths removes **zero**
SSH-host capability, and afterwards `/spawn main@vps` works through the browser
for the first time, along with terminals, canvas, monitors, voice and plans.

## What we lose

Named here so it is a decision, not a discovery.

- **The PWA.** Installability, the offline shell and the service worker are
  properties of the DOM app. A browser tab pointed at a canvas is not
  installable in the same way. Accepted knowingly.
- **DOM semantics.** The served TUI is four stacked canvases; its entire ARIA
  tree is one `textbox "Terminal input"`. No text selection, no `Ctrl+F`, no
  share sheet, no screen-reader support. Copying a code block out of a
  transcript on a phone gets worse.
- **Flaky-link behaviour is unproven.** The buffer-and-replay design is aimed
  at it, but the current PWA's offline story was a real feature and the
  replacement has not been measured on a real mobile link.
- **Not verified on a physical phone.** The spike used headless Chrome at
  393×852, which has no soft keyboard eating half the viewport and no missing
  `Ctrl` key. Reflow looked good; ergonomics are untested.

## Deletion inventory

| Path | LOC | Fate |
|---|---|---|
| `src/aegis/web/static/` (app.js 1051, base.css 350, renderEvent.js 227, ws.js 144, coalesce.js 85, markdown.js 74, service-worker.js 51, index.html 48, tabs.js 34, queues.js 7) | 2,071 | delete |
| `src/aegis/web/wssession.py` | 421 | delete |
| `src/aegis/web/subscriptions.py` | 381 | delete |
| `src/aegis/web/compact.py` | 55 | delete |
| `src/aegis/web/history.py` | 37 | delete |
| `src/aegis/tui/remote_manager.py` | 395 | delete |
| `src/aegis/tui/ws_client.py` | 209 | delete |
| `src/aegis/web/server.py` | 151 | rewrite as the supervisor |
| `src/aegis/web/frontend.py` | 53 | keep port resolution, drop the rest |
| ~20 `tests/test_web_*.py`, `tests/cli/test_remote_flag.py`, `tests/tui/test_ws_client_reconnect.py` | — | delete |

**≈ 3,570 lines deleted**, against roughly 300 added for the supervisor plus a
vendored xterm.js. The WS protocol version (`PROTOCOL_MAJOR = 2`) and its
handshake go with it.

The boot unification is mostly *movement* rather than new code — hoisting the
`AegisApp(...)` construction at `cli.py:224` behind `_serve`'s `ui=`
attachment, and deleting the duplicated config loading in the TUI branch
(`cli.py:163-214`). Call it net-neutral on line count and the highest-risk
part of the change, since it touches the path every existing user runs.

Docs to follow: `AGENTS.md` (the "two co-equal first-class UIs" paragraph and
the `know-how/remote-tui.md` index entry), `know-how/remote-tui.md` (delete),
`know-how/deploying-web.md` (new `ExecStart`, PWA section removed),
`docs/remote.md`, `README.md`.

## Testing

The failure that produced this document was a green suite over a broken path,
so the bar is: **exercise the real artifact, not an adjacent one.**

- The supervisor's tests stand up the **actual starlette app** and connect a
  real WebSocket client to it — never a bare `websockets.serve` stand-in.
- The subprocess bridge is tested against a **real Textual app** spawned under
  `TEXTUAL_DRIVER=…web_driver`, asserting that data packets arrive and that
  stdin round-trips — not against a mocked process.
- Reattach is tested by killing the socket and reconnecting, asserting the
  subprocess **pid is unchanged** and the replayed buffer repaints. Asserting
  on the pid, not on a log line.
- Auth is tested by connecting with a wrong token and a missing token, each
  asserting rejection before any packet flows.
- At least one mutation check: break the replay path on purpose and confirm the
  reattach test goes red. A gate that cannot fail is worth less than none.
- **The boot unification needs its own regression**: assert that a `_serve`
  booted with `ui=local` starts a scheduler when `schedules:` is configured.
  That test fails against `main` today, which is the point of writing it.
- **Multi-instance is the test that proves the library seam.** Boot two
  `aegis.embed()` instances at two different roots in one process, spawn in
  each, and assert their state dirs, locks and canvases are disjoint. This
  cannot pass while `Path.cwd()` roots the state, so it is the honest gate on
  stage 1 rather than a proxy for it.

## Sequencing

The order is forced, and stage 1 is the risky one:

1. **Root the state explicitly.** Replace the eight `Path.cwd()` calls with a
   threaded `root`. Pure refactor, no behaviour change, immediately testable
   by booting two aegis instances at different roots in one process and
   asserting their state dirs don't collide. This is the prerequisite for both
   stage 2 and sindri.
2. **Unify the boot path.** `_serve(ui=…)` becomes the single entry sequence;
   `aegis` gains the scheduler and peer plane it lacks today. Loop and signal
   ownership move up into the CLI wrappers. Ships with the scheduler
   regression test above. Nothing user-visible changes except that schedules
   start working under `aegis`.
3. **Expose `aegis.embed()`.** The fourth attachment — no UI, caller's loop.
   Falls out of 1 and 2; mostly a public surface and its docs. Unblocks
   sindri without waiting on the rest.
4. **Build the supervisor.** `aegis web` spawns `_serve(ui=web_driver)`,
   buffers, reattaches, authenticates. Both UIs still exist at this point, so
   it can be exercised against the VPS before anything is deleted.
5. **Delete.** The web client, the WS plane, `RemoteSessionManager`,
   `ws_client`, `--remote`, and their tests and docs. Flip
   `aegis-web.service`'s `ExecStart`.

Stages 1–3 are the ones sindri is waiting on and carry no deletion; they can
ship while the web UI is still standing.

Deleting last means every step is independently revertible, and the
irreversible one happens only after its replacement has been used in anger.

## Open questions

None blocking. Two to settle during planning:

1. **Buffer size.** How many packets/bytes to retain for replay. Wants a
   measurement against a real transcript, not a guess.
2. **Idle subprocess policy.** Whether the supervisor ever reaps a subprocess
   with no clients attached, or keeps it forever (as a VPS daemon would want).
   Default: keep forever; revisit if it bites.
