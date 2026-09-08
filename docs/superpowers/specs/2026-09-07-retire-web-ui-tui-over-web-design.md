# The aegis daemon: one brain, many views

*Design — 2026-09-07. Revised 2026-09-08 after peer review
(`docs/superpowers/reviews/2026-09-08-one-boot-path-spec-review.md`) and a
second round of measurement that replaced the architecture.*

*Status: ready to plan.*

Three goals that turn out to be one refactor:

1. collapse aegis's front ends to a single TUI, rendered locally or over the web;
2. make aegis bootable in-process as a library, so sindri can drive it;
3. let a long-lived aegis on the VPS be reached from a browser *and* a terminal.

All three reduce to the same shape: **one brain, N views**. The brain is a
daemon holding every subsystem. A view is a Textual app bound to that brain by
a direct Python reference, rendering at its own geometry into whatever
transport its client arrived on.

## The problem

`AGENTS.md` claims aegis has "two co-equal first-class UIs over one
`aegis serve` backend … Both render the same transcripts with the same
fidelity." That is false, and has been for a while.

There are **three** implementations of the aegis front end, and beneath them
**two divergent boot paths**:

1. `src/aegis/tui/` — the Textual TUI over `core/manager.SessionManager`.
   Complete.
2. `src/aegis/web/static/` — a hand-written JS client speaking a bespoke WS
   protocol. Re-implements the agent picker, queue dashboard, group dashboard,
   file picker, file viewer, theme picker, config panel and command palette.
3. `tui/remote_manager.RemoteSessionManager` — a `SessionManager` look-alike
   backing `aegis --remote`, feeding the same TUI widgets from that protocol.

Coverage, by grep across `src/aegis/tui/` versus `src/aegis/web/`:

| Subsystem | TUI | Web client |
|---|---|---|
| Shared terminals | ✅ | ❌ |
| Voice input | ✅ | ❌ |
| System meter | ✅ | ❌ |
| Plan strip | ✅ | ❌ |
| File browser tab | ✅ | ❌ |
| Monitors | ✅ | partial |

`RemoteSessionManager` is worse — every one of these exists on the local
manager and not on the remote one:

```
attach_terminal_manager  attach_canvas_manager   attach_monitor_manager
attach_queue_manager     attach_reminder_service attach_scheduler_context
attach_persistence       attach_locks_state      attach_remote_plane
attach_remotes           plan_state              _plan_roll_up
read_peer                live_handles            close_all
session_send_and_await   reconnect               _fork_capability
```

It is a conversation viewer wearing a `SessionManager` costume.

**Why it rotted, and the lesson that shapes this design:** `--remote` carried a
*semantic* protocol — `spawn_session`, `list_sessions`, `subscribe`. Every new
aegis subsystem needed a new field, and eighteen of them never got one. Any
design that puts a semantic protocol between the UI and the brain will rot the
same way. This one puts **bytes** there instead.

### The fourth divergence: two boot paths

`aegis` and `aegis serve` do not share a boot sequence. `_serve()`
(`cli.py:373`) has exactly one caller — `_run_serve` — while the TUI path
constructs `AegisApp(...)` directly (`cli.py:224`), passing `agents`, `queues`,
`hosts` and `voice` but **not** `schedules`, `remotes` or `remote_plane`.

The consequence is live, and stated in the code (`tui/app.py:466`):

> *Scheduler-context stubs to satisfy AppBridge. The TUI does not run a
> scheduler; the `aegis_schedule_*` MCP tools will gracefully return errors
> when scheduler is None.*

So **schedules do not fire under `aegis`, only under `aegis serve`**, and the
peer remote plane starts only in the headless path.

### Both non-TUI paths are broken today

Which is how this was found:

- Every `--remote` URL omits `/ws`. `WsClient` uses the URL verbatim
  (`ws_client.py:28`); nothing appends the path. So `cli.py:128` (no-arg
  default), `cli.py:141` (the `ssh://` tunnel) and the form documented in
  `know-how/remote-tui.md` all get HTTP 403. Verified live:
  `"WebSocket /" 403` versus `"WebSocket /ws" [accepted]`.
- Once connected it crashes mounting the first pane: `AttributeError:
  'types.SimpleNamespace' object has no attribute 'render_tiers'`.
  `remote_manager.py:97` builds `SimpleNamespace(render=lambda _t: "")` under
  the comment *"Minimal metrics stub so refresh_metrics() doesn't crash"*;
  `pane.py:1289` has since moved to `metrics.render_tiers(now, palette)`.
  Reproduced in a plain terminal under a real pty, no web layer involved.

The suite is green through both because `tests/tui/test_ws_client_reconnect.py`
and `tests/cli/test_remote_flag.py` assert the broken URL shape against bare
`websockets.serve` handlers that accept any path, with the manager mocked.
Tests that cannot fail.

## The insight

Three facts, each measured on 2026-09-07/08 against textual 8.2.6–8.2.8.

**1. Textual has no process-global app state.** `active_app`,
`active_message_pump`, `visible_screen_stack` and the rest are all `ContextVar`
(`textual/_context.py:17-27`) — task-local. Two `App` instances were run
concurrently in one process at 40×20 and 140×50, sharing one plain object;
both rendered at their own size and both saw the shared mutation.

**2. A driver's output sink is redirectable per instance.**
`WebDriver.__init__` sets `self._write = partial(os.write, self.fileno)` — an
*instance attribute* — and `App(driver_class=…)` is a supported constructor
argument (`app.py:574`). Subclassing `WebDriver` and overriding `_write`
captured the framed packets in-process with nothing reaching stdout.

**3. The frames carry raw ANSI.** A captured packet:

```
header:  b'D\x00\x00\x00\x1e'
payload: b'\x1b[1;1H\x1b[38;2;255;0;0mHELLO\x1b[0m'
```

So `b"D" + length + ANSI`. A browser unframes and feeds xterm.js; a terminal
unframes and writes straight to stdout. **One protocol, two renderers.**

Together: many views, each at its own size, each sinking into its own socket,
all sharing one brain, in one process — with no subprocess, no pty, no
terminal emulation, and no semantic protocol.

## The design

```
aegis daemon — one per project root
├── brain      — SessionManager, MCP, queues, schedules, hosts, canvas,
│                terminals, monitors                        ← exactly one
├── views      — N × AegisApp, each its own geometry/focus/scroll
└── transports — unix socket  .aegis/state/daemon.sock   (terminals)
                 WebSocket    per the `web:` config block (browsers)
```

Both transports carry identical framed ANSI. The `web:` block stops meaning
"run a web server" and starts meaning "also listen on WS", so **one daemon
serves your terminal and dev.apiad.net at once**.

A view holds the manager **by direct Python reference**. There is one
`AegisApp` class, instantiated N times. No protocol between UI and brain, and
therefore nothing that can fall behind.

### Brain state versus view state

The rule: **opening a tab opens it for everyone; what you're looking at and
what you've half-typed is yours.**

| Brain — one copy, all views | View — per attachment |
|---|---|
| the session set (handle, profile, provider, `session_id`, `log_id`, `created_at`) | focused tab |
| tab **order** | geometry |
| transcripts, agent state, metrics, plans, titles | scroll position per tab |
| queues, monitors, canvas, terminals, hosts | input draft text per tab |
| **pending messages** (submitted, awaiting turn boundary) | unseen markers |
| | open modals (config panel, file picker, palette) |

Three things the current code already gets right or nearly right:

- **`unseen` is already view-side** — it lives on the pane (`app.py:1098,1449`);
  `manager.py:490` only sets it when building an RPC report. This is also the
  correct semantics for the rule above: if you are reading `foo` and I am not,
  it is read for you and unread for me.
- **Input drafts are already per-tab** — `GrowingInput` is yielded inside
  `ConversationPane` (`pane.py:1053`), so once panes are per-view, drafts are
  per-(view, tab) for free.
- **`state/workspace.py::Workspace` conflates the two and must split.** It
  stores `active_handle` — pure view state — beside tab identity and `order`,
  which are brain state. Becomes `workspace.json` (brain: what exists) plus
  `.aegis/state/views/<view-id>.json` (view: focus, scroll, drafts, geometry).

**One line that is easy to get wrong.** A *draft* is per-view; a *pending
message* is not. `PendingStrip` holds messages already submitted while the
agent is mid-turn, queued for the turn boundary, cancellable by clicking a
chip. Those are brain state — per-view they would show different queues for
one agent, and cancelling in one view would not cancel in the other. Text
still in the box is yours; text you have sent is everyone's.

### Persistent views

A view outlives its socket. On disconnect the daemon keeps it, keyed by client
id — `localStorage` for browsers, per-tty for terminals, `--view NAME` to
override. Reconnect restores your focus, scroll and drafts.

This needs a forced full repaint on reattach, because Textual emits
*incremental* frames: `render_update` returns `render_partial_update()` unless
the whole screen region is dirty (`_compositor.py:1099-1121`), there is no
repaint meta message (`web_driver.py:238-250`), and `_on_resize` early-returns
on unchanged geometry (`app.py:4352`). Measured mechanisms:

| Mechanism | Result |
|---|---|
| `app.refresh(repaint=True, layout=True)` | nothing emitted |
| `app.refresh(…, recompose=True)` | nothing emitted |
| `screen.refresh(repaint=True, layout=True)` | nothing emitted |
| `compositor.render_update(full=True)` | **full `LayoutUpdate`** |
| `compositor._dirty_regions.add(size.region)` | **full `LayoutUpdate`** |

So: dirty the screen region, then let the next render cycle emit a full frame.
Roughly five lines, reaching one private attribute — acceptable because we own
`AegisApp`, which is exactly what `textual-serve` cannot assume.

**This is no longer a blocker.** A fresh view always paints from scratch, so
if the repaint mechanism disappoints, the fallback is dropping the view on
disconnect and building a new one — losing focus and scroll, but never
correctness.

### `aegis` alone

Start-or-attach:

```
$ aegis
  → daemon for this project root?
      no  → fork one detached, wait for its socket
      yes → nothing to do
  → attach a terminal view: raw tty, pipe the socket, SIGWINCH → resize
```

`aegis` is always a *client*. The brain is always a daemon. There is no mode
where the app and the brain are the same object — that sameness produced the
four boot paths above.

`aegis --foreground` keeps the old shape (brain + one view, dying together)
for CI, `uvx aegis`, tests and debugging the daemon itself.

**Autostart** is on, with an idle timeout: the daemon exits after N minutes
with zero views **and** zero live agents, so laptops self-clean while the VPS
daemon — which always has agents — never dies.

**`Ctrl+Q` detaches.** It leaves the brain running and shows a footer naming
what survives (`brain running · 3 agents · aegis kill to stop`). `aegis kill`
stops the brain; `aegis ls` lists daemons across roots.

### `aegis attach` is a dumb pipe, not `--remote` returning

The attach client holds no aegis state. It connects, sets raw mode, pipes both
directions, and sends `resize` on `SIGWINCH`. Perhaps 80 lines, and it never
grows.

| | `--remote` (dying) | `aegis attach` (new) |
|---|---|---|
| Where the TUI runs | the **client** | the **daemon** |
| What crosses the wire | sessions, spawns, queues, subscriptions | **bytes and a window size** |
| Client needs a `SessionManager` | yes | no |
| Protocol grows with each feature | **yes — the rot** | **no** |

**Required: attach must work across machines.** Both transports carry the
identical `b"D" + length + ANSI` frames, so the client takes a target rather
than assuming a local socket:

```bash
aegis attach                              # this root's daemon, unix socket
aegis attach --view review                # a named view on it
aegis attach wss://dev.apiad.net --token …   # a daemon on another machine
```

The remote form is a first-class requirement, not a nicety: it is how a
terminal on zion drives the VPS daemon while keeping **local** geometry,
tmux and scrollback — none of which survive an `ssh vps -t aegis attach`
session, where the far side owns the terminal.

The cost is a URL scheme and a token. Raw mode, the pipe loop and `SIGWINCH`
are unchanged, because the frames are the same on both transports. What must
*not* creep in is any awareness of sessions, agents or queues — the moment the
client parses aegis concepts it has become `RemoteSessionManager` again, and
this table stops being true.

### Command surface

| Command | Before | After |
|---|---|---|
| `aegis` | TUI, own boot path, no scheduler/peer plane | ensure daemon, attach a terminal view |
| `aegis attach [--view N]` | — | explicit attach to this root's daemon |
| `aegis attach wss://host --token …` | — | **attach a local terminal to a remote daemon** |
| `aegis serve` | headless MCP + queues + schedules + plane + web frontend | **run the daemon** in the foreground |
| `aegis web` | ensure token, open browser, `_run_serve` | **open a browser at the daemon's URL** — no longer a server |
| `aegis kill` / `aegis ls` | — | stop / list daemons |
| `aegis --remote ws://…`, `ssh://…` | broken | **removed** |
| `aegis token` | prints web token | unchanged |

On the VPS, `aegis-web.service` runs `aegis serve`; Caddy proxies to the WS
transport as it does today.

### Auth

**One secret, presented in the handshake. Caddy's `basicauth` is dropped.**

The WS path already works this way and is the model for everything else:
`ws.js:50` sends `{"type":"auth","token":…}` as its **first frame**, and
`wssession.py:137` validates it there. The query string was never the
credential for the socket — `?t=` exists only so the page can *learn* the token
to put in that frame, and `server.py:103` uses it to guard `/download`.

| Client | How it presents `web.token` |
|---|---|
| Browser | `?t=` **once**, exchanged immediately for an `HttpOnly; Secure; SameSite` cookie; the page reads nothing thereafter and the WS handshake authenticates from the cookie |
| `aegis attach wss://…` | the same auth frame, token from `~/.aegis/tokens/<host>` (so it is not in shell history either); `--token` overrides |
| `aegis attach` (local) | nothing — the unix socket is guarded by filesystem permissions |

**Why not `?token=` everywhere.** It reads simpler, but it moves the
credential from a handshake into URLs, and URLs land in Caddy's access log,
browser history and any `Referer`. This token *is* a shell — the daemon runs
`permission: full` on the VPS Workspace — so it should appear in exactly one
place, once.

**What dropping `basicauth` buys**, beyond one secret instead of two: the
terminal client no longer needs to synthesise a basic-auth header from
`--user`/`~/.netrc` (a terminal has no login prompt), and two documented traps
in `know-how/deploying-web.md` disappear — the service-worker install-time 401,
and URL-embedded credentials polluting the SW scope.

**What it costs, stated plainly.** Today Caddy rejects unauthenticated traffic
*before* it reaches aegis, so scanners never touch our code. Afterwards,
aegis's own handshake is the only thing between the open internet and full code
execution on the VPS. `secrets.token_urlsafe(32)` is strong enough that this is
an acceptable trade, but it makes the handshake path security-critical: it gets
adversarial tests (see Testing), not happy-path ones.

**`wss://` only for remote targets.** The `ws://` path in the retired
`--remote` was never TLS-capable (`know-how/remote-tui.md` lists it as a known
limitation). A client that carries a full-access token across a network must
not repeat that: plain `ws://` is accepted for `localhost` and refused
otherwise.

### Explicitly out of scope

- **`hosts:` / SSH execution hosts** (`src/aegis/hosts/`, 684 lines) — imports
  nothing from `web/`, `ws_client` or `remote_manager`; `host=` is a parameter
  of the surviving manager. This is the *reverse* of `--remote`: UI stays put,
  the harness moves.
- **`remotes:` / the peer remote plane** (`remote/plane.py`) — independent
  HTTP plane, never touched the web WS plane.
- **The MCP plane** — owned by the brain, as it is by `aegis` today.

Worth stating because it inverts the expected trade: **`--remote` refuses
SSH-host tabs today** (`remote_manager.py:226`: *"the WS protocol has no field
to carry a placement request — so refuse clearly"*), and the web client's
`spawn_session` carries `agent_profile` and nothing else. Retiring both removes
**zero** SSH-host capability, and afterwards `/spawn main@vps` works from the
browser for the first time, along with terminals, canvas, monitors, voice and
plans.

## aegis as a library

Sindri — the agentic forge, `repos/sindri` — needs aegis **programmatically,
not as a process**. From its vision transcript
(`vault/+/Inbox/2026-09-01-sindri-vision.md`): *"tenemos que actualizar la
arquitectura de aegis para que sea usable de manera programática cien por
ciento… es posible que todavía haya cosas ahí que estén encadenadas al hecho de
que hay un proceso corriendo."*

Correct, and the coupling is specific.

**1. State is rooted at the process cwd.** **66 `Path.cwd()`/`os.getcwd()`
sites across 21 files**, plus **61 call sites of `find_project_root`** — itself
cwd-rooted (`config/__init__.py:141`). Inside `_serve` alone it is eight
(`cli.py:401,404,405,410,450,455,463,481`), while `_serve` *accepts* a
`local_root` it uses once (`cli.py:386`). Worse, most of the rest is
late-bound:

- **`mcp/server.py` calls `find_project_root` 26 times** — the config tools
  (`aegis_config_add_agent`, `add_queue`, `config_show`, `schedule_*`),
  resolving the root **at call time from the process cwd**. Under sindri, an
  agent in worktree B editing config hits whichever `.aegis.yaml` the process
  walks up to. Silent cross-instance writes: the sharpest failure here.
- **`core/manager.py:97,117,167`** — `root_fn=lambda: self.state_root or
  Path.cwd()`, lazy closures resolved per call.
- **`core/session.py:85,91`** — `project_root or Path.cwd()`, the harness
  subprocess's actual cwd.
- Also `terminal/manager.py:156`, `workflow/engine.py:199`, `usage/env.py:14`,
  nine sites in `tui/app.py`.

**This is not a pure refactor.** Replacing `x or Path.cwd()` and lazy closures
with a threaded root converts late-bound process-global resolution into
early-bound per-instance resolution — a semantic change, and the
highest-risk work in this document.

**Name the roots first.** There is no single "root" today: `_serve` has
`local_root`; `SessionManager` has `state_root` *and* `local_root`;
`HostRegistry` has `state_dir` + `local_root`; `find_project_root` is a fourth;
and `cli.py:196` already distinguishes `root` from `effective_cwd`. Planning
must name three and say which is threaded where:

| Role | Anchors |
|---|---|
| **config root** | `.aegis.yaml`, overlays, plugin dirs, persona files |
| **state root** | `.aegis/state/` — persistence, locks, canvas, terminals, views |
| **harness cwd** | the directory the agent subprocess runs in |

They coincide in the CLI case, which is why the conflation survived. Under
sindri they do not.

**2. The boot path exits the process on bad input.** The library-relevant
`typer.Exit` calls are the config guards at `cli.py:166-175`, `cli.py:184-187`
and `cli.py:713-740`. Those must raise `ConfigError`; only the CLI turns it
into an exit code.

**3. The boot path owns the loop and the signals.** `_run_serve` calls
`asyncio.run` and installs SIGINT/SIGTERM (`cli.py:760-772`). Embedded aegis
runs in sindri's loop and must not touch its signals.

The fix is the same seam, with zero views:

```python
async with aegis.embed(root=Path("/srv/repos/foo")) as ae:
    handle = await ae.manager.spawn("implementer", opening_prompt=plan_text)
    await ae.queues.enqueue("verify", payload, from_handle=handle)
```

| Entry point | Owns loop | Views |
|---|---|---|
| `aegis serve` | yes | 0…N, as clients arrive |
| `aegis --foreground` | yes | 1, local tty |
| `aegis.embed()` | **no — caller's** | 0 |

**Already done, contrary to the vision doc.** Per-agent system prompts, listed
there as missing, have landed: `Agent.prompt` (`config/__init__.py:99`) points
at a Markdown persona resolved by `config/persona.py::read_persona`, composing
with rather than replacing the primer.

**Not in this spec:** sindri's own primitives — file-pattern triggers,
worktree/branch determinism, bash preconditions, checks with retry loops,
auto-merge. Those build *on* this API and need their own design.

## What we lose

- **The PWA.** Installability and the offline shell are properties of the DOM
  app; a tab pointed at a canvas is not installable the same way. (The service
  worker carries no push code, so notifications are not among the losses.)
- **DOM semantics.** The rendered view is canvas; its whole ARIA tree is one
  `textbox`. No text selection, no `Ctrl+F`, no share sheet, no screen reader.
  Copying a code block out of a transcript on a phone gets worse. This is the
  real cost of the whole design.
- **Deep links and browser navigation.** No per-session URLs, no back/forward.
- **Unproven on a physical phone.** The spike used headless Chrome at 393×852,
  which has no soft keyboard eating the viewport and no missing `Ctrl` key.
  Reflow looked good; ergonomics are untested, and a soft-keyboard shim is
  budgeted below on the assumption it will be needed.
- **Crash isolation.** Views share the brain's process, so a view exception can
  take the daemon down. No regression versus `aegis` today (same process
  already), but it is why `--foreground`, supervision and honest persistence
  matter.

Two items that were losses in the previous draft and are now **recovered**:
independent per-client views (each view has its own focus, scroll and drafts),
and terminal access to the remote brain (`aegis attach`).

## Deletion inventory

| Path | LOC | Fate |
|---|---|---|
| `web/static/` (app.js 1051, base.css 350, renderEvent.js 227, ws.js 144, coalesce.js 85, markdown.js 74, service-worker.js 51, index.html 48, tabs.js 34, manifest 17, icon.svg 6, queues.js 7) | 2,094 | delete |
| `web/wssession.py` | 421 | delete |
| `web/subscriptions.py` | 381 | delete |
| `web/compact.py` | 55 | delete |
| `web/history.py` | 37 | delete |
| `tui/remote_manager.py` | 395 | delete |
| `tui/ws_client.py` | 209 | delete |
| `web/server.py` | 151 | rewrite as the daemon's transports |
| `web/frontend.py` | 53 | delete; `_resolve_port` (used at `cli.py:624`) moves |
| ~20 `tests/test_web_*.py`, `tests/cli/test_remote_flag.py`, `tests/tui/test_ws_client_reconnect.py` | — | delete |

**≈ 3,592 lines deleted.** The WS protocol version (`PROTOCOL_MAJOR = 2`) and
its handshake go with it.

Added, realistically **500–700 lines plus a vendored xterm.js (~250 KB)**: the
driver subclass, the two transports, view lifecycle and persistence, the
`aegis attach` client, the daemon supervisor bits (autostart, idle timeout,
`ls`/`kill`), a soft-keyboard shim, and the `deliver_chunk_request` download
path (`web_driver.py:250`, today a real route with a real test). This design
needs no subprocess management, no packet codec and no process supervisor,
which is where the previous draft's larger estimate went.

Docs to follow: `AGENTS.md` (the "two co-equal first-class UIs" paragraph and
the `know-how/remote-tui.md` index entry), `know-how/remote-tui.md` (delete),
`docs/remote.md`, `README.md`.

`know-how/deploying-web.md` needs the most work, and it is operational rather
than cosmetic — the Caddy site block loses its `basicauth` directive, the
"Secrets" section drops `~/.aegis-web-basicpw` and keeps only the aegis token,
the login URL stops being `?t=` -forever and becomes a one-time exchange, the
whole "SW + basic auth" and PWA-installability discussion goes, and the
topology diagram loses a layer. Removing `basicauth` from a live public
hostname is the single most dangerous edit in this plan: **it must land in the
same change as the cookie exchange, never before it**, or `dev.apiad.net`
stands briefly open with `permission: full`.

## Testing

The failure that produced this document was a green suite over a broken path.
The bar is: **exercise the real artifact, not an adjacent one.**

- Transport tests stand up the **actual** app and connect a real client — never
  a bare `websockets.serve` stand-in.
- **Multi-view**: two views at different geometries over one brain; assert both
  sizes hold, that a tab opened in one appears in the other, and that focus,
  scroll and drafts do **not** cross.
- **Persistence**: disconnect and reconnect; assert the view id is reused, the
  focused tab and draft survive, and a full frame arrives.
- **The library seam must assert on a write, not a path.** Boot two
  `aegis.embed()` instances at two roots in one process, spawn in each, have
  agent A call `aegis_config_add_agent`, and assert **instance B's
  `.aegis.yaml` is byte-identical**. Comparing state-dir paths for disjointness
  is a proxy — it passes while the 26 late-bound `find_project_root` calls
  still resolve through the process cwd. This fails today for that reason.
- **Boot unification**: assert a brain booted for a local view starts a
  scheduler when `schedules:` is configured. Fails against `main` today.
- **Pending versus draft**: submit a message from view A mid-turn, assert it
  appears in view B's pending strip, and that cancelling it in B cancels it
  in A.
- **Transport equivalence**: attach one view over the unix socket and one over
  WS, and assert the emitted frames are byte-identical for the same view state.
  This is the assertion that keeps `aegis attach` a dumb pipe — the moment the
  remote client needs its own encoding, the two transports have diverged and
  the second implementation is back.
- **Remote attach refuses plaintext**: `aegis attach ws://` to a non-loopback
  host is rejected before the token is sent. Assert on the token never leaving
  the process, not merely on the error message.
- **The handshake is now the only gate, so it gets adversarial tests**, not
  happy-path ones: no token, empty token, wrong token, well-formed token for a
  *different* daemon, auth frame sent second instead of first, and a
  non-auth frame sent before authenticating. Each asserts the connection is
  refused **before any brain state is touched** — not merely that an error
  frame comes back.
- **The token never appears in a URL or a log.** After a full browser session
  (load, cookie exchange, WS connect, reconnect), assert `web.token` appears in
  no access-log line and in no request path. This is the assertion that keeps
  the cookie exchange honest; without it, a future `?token=` shortcut passes
  every other test here.
- At least one mutation check: break the repaint path deliberately and confirm
  the reconnect test goes red.

## Sequencing

1. **Root the state explicitly.** Replace the 66 cwd sites and thread the three
   named roots. Gated by the multi-instance config-write test. Highest risk;
   lands alone.
2. **Unify the boot path.** One boot sequence; the brain gains the scheduler and
   peer plane the TUI lacks today. Loop and signal ownership move to the CLI
   wrappers.
3. **Expose `aegis.embed()`.** Falls out of 1 and 2. **Unblocks sindri**
   without waiting on anything below.
4. **The view seam.** Driver subclass, N views over one brain, brain/view state
   split, persistence. Exercised with `--foreground` and the unix socket only.
5. **Transports and clients.** WS + `aegis attach` (both the local unix-socket
   form and the remote `wss://` form, which is a requirement of this stage, not
   a follow-up), autostart, idle timeout,
   `ls`/`kill`. Both UIs still exist here — **`web=` stays wired through this
   stage** so dev.apiad.net keeps serving while the daemon is exercised beside
   it on a second port.
6. **Delete.** The web client, WS plane, `RemoteSessionManager`, `ws_client`,
   `--remote`, their tests and docs. Flip `aegis-web.service`, and only now
   remove `web=`. The command table describes the world after **this** stage.

Stages 1–3 carry no deletion and can ship while the web UI stands. Stages 5–6
are cleanly revertible; **stage 1 is not**, once the rest builds on it — the
mitigation is the test above and landing it alone, not a promise of rollback.

## Open questions

Non-blocking; settle during planning.

1. **Idle-timeout duration** for daemon self-reaping, and whether zero-agents is
   the right second condition.
2. **Geometry with zero views attached.** A brain with no views has no screen;
   the first view to attach defines its own. Confirm nothing in the TUI assumes
   a size before the first attach.
3. **Voice with N views.** One microphone, many views — `VoiceStrip` needs an
   explicit owner, or push-to-talk becomes ambiguous.
4. **Tab order on reorder.** Order is brain state, so reordering in one view
   reorders for everyone. Confirm that is wanted, or move it view-side.
