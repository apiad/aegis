# aegis SSH execution hosts

**Status:** approved, not yet planned
**Date:** 2026-08-04

Run a harness process on another machine over a persistent SSH
connection, while the aegis session that owns it stays local. One tab in
the local tab bar is a `claude` running on `vps.apiad.net` at
`/home/apiad/Workspace`; the tab beside it is a `claude` running here.
They see each other through the ordinary aegis peer surfaces and can
hand off, share a canvas, and claim files against each other.

## Motivation

Today an agent that needs to touch a remote box does it one `ssh`
invocation per `Bash` call. Every command pays connection setup, nothing
persists between commands, and the agent's `Read`/`Edit`/`Grep` still
run against the *local* filesystem — so it cannot actually work over
there, only poke at it.

The fix is to move the harness, not the commands. If `claude` itself
runs on the VPS, all of its tools are native there: `Bash` is a local
shell on the VPS, `Read` reads the VPS's files, `Grep` walks the VPS's
tree. aegis keeps the transcript, the tab, the inbox, and the MCP peer
identity.

## What this is not

aegis already has two things with "remote" in the name. This is neither
of them, and the three should not be confused.

| | who owns the session | what moves |
|---|---|---|
| `aegis --remote ws://\|ssh://` | a remote `aegis serve` | the **UI** is remote-attached |
| `remotes:` / `remote_plane` | two peer aegis serves | **callbacks** federate between aegises |
| **this spec** | the **local** aegis | the **harness process** is remote |

Here there is exactly one aegis, running locally, owning every session.
Only the subprocess at the bottom of a session lives somewhere else.

**Durability is explicitly out of scope.** When the link drops, the
remote harness dies with it. That is the accepted contract: this feature
is for "let me run this one on the VPS", not for "keep thinking while my
laptop is closed". The durable path already exists and stays the
recommendation for that case — run `aegis serve` on the VPS and attach
with `aegis --remote ssh://vps:8080`, or just run `aegis` there over
SSH.

## Object model

### `hosts:` config section

A new top-level section in `.aegis.yaml`, with `.aegis/hosts/*.yaml`
drop-in overlays merging fail-loud the same way `agents:`, `queues:`,
`schedules:` and `groups:` already do (`config/yaml_loader.py`,
`_SECTIONS`).

```yaml
hosts:
  vps:
    ssh: vps.apiad.net
    cwd: /home/apiad/Workspace
  smaug:
    ssh: smaug.local
    cwd: /home/apiad/work
    ssh_opts: ["-o", "ServerAliveInterval=15"]
```

| field | required | meaning |
|---|---|---|
| `ssh` | yes | the destination handed to `ssh`. Resolved through the user's `~/.ssh/config`, so aliases, `ProxyCommand`, jump hosts and non-standard ports all work without aegis reimplementing any of it. |
| `cwd` | yes | default working directory on that host |
| `ssh_opts` | no | extra flags appended to every `ssh` invocation for this host |

`local` is an implicit host that always exists, cannot be declared, and
whose cwd is `find_project_root()`. Declaring a host named `local` is a
fail-loud config error.

The remote working tree needs **no** `.aegis.yaml` and no aegis install.
It is a working directory for the harness, nothing more. All config,
state, transcripts and coordination live on the local side. The remote
box needs only the harness CLI on `PATH`.

### `Place` — resolved per spawn, never persisted

```python
@dataclass(frozen=True)
class Place:
    host: str   # "local" | a hosts: key
    cwd: str
```

Host is a **third orthogonal axis** beside agent profile and harness.
Any harness runs on any host; an agent profile is not tied to a host.
This mirrors how `model` / `effort` / `prompt` already work — per-session
overrides layered at spawn by `core/manager._overlay_agent`, never
written back to config.

Resolution precedence at spawn time:

**host** — explicit spawn argument (`@vps`) → the agent profile's
optional `host:` default → `local`.

**cwd** — explicit spawn argument (`@vps:/other/tree`) → that host's
`cwd:` → for `local` only, `find_project_root()`.

An agent profile *may* carry `host:` as a convenience default
(`agents: {vps-worker: {harness: claude-code, host: vps}}`), so a preset
is one keystroke. It is never required, and the picker always overrides
it.

## Architecture

### The launcher seam

Both driver families already converge on one shape:

```
argv (from build_argv) + cwd + env
    → create_subprocess_exec(*argv, cwd=…, env=…, stdin/stdout/stderr=PIPE)
```

`ClaudeSession.start()` (`drivers/claude.py`) and `AcpSession.start()`
(`drivers/acp.py`) do the identical thing. So remoteness belongs in a
single abstraction *underneath* both drivers, not duplicated across
them:

```python
class Launcher(Protocol):
    host_key: str    # "local" | "vps"

    async def spawn(self, argv: list[str], *, cwd: str,
                    env: dict[str, str] | None) -> asyncio.subprocess.Process: ...
```

- **`LocalLauncher`** — today's `create_subprocess_exec` call, moved
  verbatim. `host_key = "local"`.
- **`SshLauncher(conn)`** — execs
  `ssh -T <ctl_opts> <ssh_dest> '<remote_cmd>'`, where

  ```
  remote_cmd = "cd <shlex.quote(cwd)> && exec env K=V … <shlex.quote(argv)…>"
  ```

  and returns the same three pipes. `-T` disables PTY allocation so
  stdin/stdout stay a clean byte stream for stream-json / ACP JSON-RPC.

`HarnessDriver.session` / `resume` / `fork` gain a trailing
`launcher: Launcher = LOCAL` parameter. Because it is defaulted, every
existing call site and every existing test keeps working untouched.
Inside the two session classes the change is one line each: the
`create_subprocess_exec(...)` call becomes
`await self._launcher.spawn(argv, cwd=self._cwd, env=env)`.

This is what makes "any harness on any host" real rather than a
claude-only special case. The ACP path works unmodified because ACP's
own `new_session(cwd=…, mcp_servers=…)` handshake simply carries the
*remote* cwd and the tunneled MCP URL.

**Pre-spawn hooks are untouched.** `_apply_pre_spawn_hooks` still runs
against the **inner** argv, before wrapping. A hook that rewrites
claude's flags has no business knowing about ssh, and this ordering
means it never has to.

`_STREAM_LIMIT` (16 MiB) still applies — it is a property of the
`StreamReader`, which is now reading ssh's stdout rather than claude's,
with the same payloads flowing through it.

### `HostConnection` — the persistent SSH

A `HostRegistry` held by `SessionManager` owns at most one
`HostConnection` per host, opened lazily on that host's first spawn.

**Control master.** One background subprocess per host:

```
ssh -M -N \
    -o ControlPath=<state>/ssh/<host>.sock \
    -o ControlPersist=60s \
    -o ExitOnForwardFailure=yes \
    -R 0:127.0.0.1:<mcp_port> \
    <ssh_opts…> <ssh_dest>
```

Every subsequent session on that host runs
`ssh -T -o ControlPath=<sock> …`, which multiplexes over the existing
master: no new TCP connection, no re-authentication, no per-command
setup cost. This is the "persistent SSH" the feature is named for.

**Reverse tunnel for the MCP plane.** `AegisMCP` binds `127.0.0.1` on a
port picked at startup (`mcp/runtime.py`, `_free_port`) and exposes
`http://127.0.0.1:<port>/mcp/`. The remote harness must reach that URL
for the remote agent to be a real peer — to call `aegis_handoff`,
`aegis_claim`, `aegis_list_sessions`, `aegis_canvas_*`.

`-R 0:127.0.0.1:<mcp_port>` asks sshd to allocate a free port on the
remote side, avoiding collisions when two aegis instances target the
same host. ssh reports the choice on stderr:

```
Allocated port 41573 for remote forward to 127.0.0.1:8931
```

The connection parses that line and derives the remote-side MCP URL
`http://127.0.0.1:41573/mcp/`, which is what
`mcp_config_json(mcp_url)` gets baked with for sessions on that host.

*This stderr parse is the one genuinely fragile mechanism in the
design.* It is guarded three ways: `-o ExitOnForwardFailure=yes` so a
failed forward kills the master instead of silently proceeding with a
broken tunnel; a bounded wait with a legible timeout error if the line
never appears; and a hermetic unit test over captured ssh stderr
samples. A configurable fixed `remote_mcp_port` on the host entry is the
escape hatch if a given sshd's phrasing differs.

Because `-R` binds the remote **loopback** by default
(`GatewayPorts=no`), only processes on that box can reach the tunnel.

**Preflight**, once per host, over the established master:
`command -v <harness_bin>` and `test -d <cwd>`. Failure surfaces as a
legible pane message — `vps: claude not found on PATH` — rather than as
a mysterious immediate EOF from a subprocess that never started.

**Health and teardown.** `ssh -O check` probes liveness;
`ssh -O exit` tears the master down on aegis quit. A `HostConnection`
with no live sessions is closed after `ControlPersist` expires.

### Threading the place through spawn

The single change on the spawn path: `_session_factory(cwd)` in
`cli.py` currently closes over one local cwd and hands
`SessionManager` a `make_session(profile, mcp_url, handle, fork_from)`.
It gains a `place` parameter and closes over the `HostRegistry` as well
as the local cwd, resolving the launcher + the effective mcp_url from
the place:

```python
def _session_factory(cwd: str, hosts: HostRegistry): ...

def make_session(profile, mcp_url, handle, fork_from=None, place=None):
    place = place or Place("local", cwd)
    # local → (LocalLauncher, mcp_url unchanged)
    # remote → (SshLauncher, http://127.0.0.1:<allocated>/mcp/)
    launcher, url = hosts.launcher_for(place, mcp_url)
    drv = get_driver(profile.harness)
    if fork_from is not None:
        return drv.fork(profile, place.cwd, url, handle, fork_from,
                        launcher=launcher)
    return drv.session(profile, place.cwd, url, handle, launcher=launcher)
```

`launcher_for` must stay **synchronous**, because `_sync_spawn` is — and
opening an SSH master is not. The registry therefore hands back an
`SshLauncher` immediately, bound to a not-yet-open `HostConnection`; the
connection is established (or reused) inside `SshLauncher.spawn`, which
is already `async` and is awaited from `ClaudeSession.start()` /
`AcpSession.start()`. Concurrent spawns on the same host await one
shared `asyncio.Lock`, so two tabs opened at once share a single master
rather than racing to create two. Preflight and the allocated-port parse
happen there too, which is also where their errors belong: a host that
cannot be reached fails the session that asked for it, visibly, instead
of failing a spawn call that has no pane to report into yet.

`SessionManager._sync_spawn` gains `host` / `cwd` keyword arguments
alongside the existing `model` / `effort` / `prompt`, resolves them into
a `Place`, passes it to `_make_session`, and stores it on the
`AgentSession`. `spawn()` and `fork()` forward them. A fork inherits its
parent's place unless overridden — branching a conversation should not
silently relocate it.

## Failure and reconnect

SSH dying mid-turn presents to `ClaudeSession._pump_stdout` as plain
stdout EOF — indistinguishable from the harness exiting normally. Today
that ends the turn and leaves the tab looking idle while the session is
actually dead. That is the failure this design must not ship.

`SshLauncher` keeps a ring buffer of the ssh process's stderr (the same
pattern `AcpSession` already uses for subprocess stderr). On EOF, if the
ssh process exited non-zero, the session emits a **`RemoteLinkLost`**
event carrying the stderr tail instead of a silent end-of-turn. The pane
enters an error state showing what actually happened
(`Connection closed by remote host`, `ssh: connect to host … timed out`).

**Reconnect.** When the driver has `supports_resume = True` and a
`session_id` was latched (`ClaudeSession._latch_session_id`), the error
state offers a reconnect — a `/reconnect` command plus a key binding —
which calls `drv.resume(agent, cwd, mcp_url, handle, session_id,
launcher=<fresh>)` on the same host, **in the same tab**. The remote
`~/.claude` still holds the conversation, so the recovery costs only the
in-flight turn.

Harnesses without resume get the error state alone; the tab is dead and
must be respawned. This is the A1 fallback under the A2 default, and it
needs no new machinery — `resume()`, `supports_resume`, and the latched
`session_id` all exist today.

## Host-scoped paths

Once a tab runs on vps, every path in its transcript names a file in the
*remote* tree. Three local surfaces currently assume otherwise, and the
worst of them fails silently.

**Ctrl+click on a Read/Write/Edit block.** `render_shared.file_target`
(`src/aegis/render_shared.py:118`) returns a `FileTarget` that the TUI
opens locally. `/home/apiad/Workspace/src/foo.py` exists on both zion
and vps and is a *different file* — so ctrl+click does not error, it
quietly opens the wrong one. For a non-local pane, `file_target` returns
`None`; the block instead offers the qualified path
`vps:/home/apiad/Workspace/src/foo.py` as copyable text. A silent wrong
answer becomes a correct, legible one.

**Claims.** `Claim` (`src/aegis/locks/models.py`) gains
`host: str = "local"`, and `claims_overlap(a, b)` returns `False`
immediately when `a.host != b.host`. Without this, a remote agent
claiming `src/foo.py` and a local agent claiming `src/foo.py` either
collide spuriously or — worse — appear to protect each other while
editing unrelated files. `aegis_claim` derives the host from the calling
session's place; `aegis_claims` renders it.

**File completion.** The `@`-picker and file indexer complete *local*
paths into a prompt that will be read on the remote. Disabled on remote
panes in v1; a remote index is part of the deferred filesystem-bridge
work.

**Marker.** The tab label and status bar carry `@vps`, so the host a
pane runs on is never something you have to remember.

## Surfaces

**`Ctrl+N` picker** gains a host tier *first* — Local / vps / smaug —
ahead of the existing two-tier preset and harness→model→effort paths
(`tui/picker.py`, `AgentPicker`). A preset that carries `host:`
pre-selects that host. Choosing a host does not restrict which agents
are offered; the axes are independent.

**Slash commands.** `/spawn claude-code@vps` and
`/spawn claude-code@vps:/some/other/tree`. The `@host[:cwd]` suffix
parses in `commands/args.py` as an `ArgSpec` and is offered by the
command palette's completer, populated from the `hosts:` keys.

**MCP.** `aegis_spawn(profile, …, host=…, cwd=…)` — so a *local agent*
can spawn a remote peer for itself without the operator touching the
picker. `SessionInfo` (`mcp/bridge.py`) gains `host: str = "local"`, so
`aegis_list_sessions` shows every peer's location and an agent deciding
where to hand something off can see which peers are already on the right
box.

**Config CLI.** `aegis config host add|remove|list`, routed through
`config/edit.py` like every other writing verb, preserving comments.
`add` is hot-registering (a new host is spawnable immediately);
`remove` needs a restart, consistent with the other `remove` verbs.

## Testing

**Hermetic** (`uv run pytest -q -m "not live"`):

- `SshLauncher` argv composition as a pure function: `shlex` quoting of a
  cwd with spaces, of an argv element containing single quotes (the
  `--append-system-prompt` primer is a realistic worst case), `-T`
  present, ControlPath threaded, `ssh_opts` appended.
- The allocated-port stderr parse over captured samples, including the
  no-line-ever timeout path.
- Launcher-agnosticism: a `FakeLauncher` returning a scripted process
  proves `ClaudeSession` and `AcpSession` behave identically regardless
  of launcher — and that pre-spawn hooks still see the inner argv.
- `Place` resolution precedence (explicit > profile default > local; cwd
  override > host cwd > project root), and the fail-loud on a host named
  `local`.
- `claims_overlap` returning `False` across hosts and unchanged within a
  host.
- `file_target` returning `None` for a non-local pane.
- `hosts:` YAML loading + overlay merge + fail-loud on an agent profile
  referencing an unknown host.

**Live** (`-m live`, auto-skipping when unavailable): **ssh to
`localhost` as the "remote" host.** A real ControlMaster, a real `-R`
tunnel, a real `claude` round trip, and — the assertion that matters —
the "remote" agent successfully calling `aegis_list_sessions` back
through the tunnel into the local MCP server. That exercises the whole
mechanism end to end on any dev box with sshd running, without needing
the VPS up.

Following the repo's discipline: a failing test is a real failure, and
the live test should be mutation-checked once (break the tunnel port on
purpose, confirm it goes red) so it cannot pass vacuously.

## Known limitations

Stated here rather than discovered later.

- **The wrapped argv is visible in the remote `ps`.** It carries the MCP
  primer, the persona, and the MCP config JSON. On single-user boxes this
  is acceptable; on a shared host it is an information leak. A future
  option is staging a launch script over the control connection so only
  a path appears in the process table.
- **The reverse-tunneled MCP port is unauthenticated.** `mcp_config_json`
  emits a bare `http://127.0.0.1:<port>/mcp/` with no token, and the
  aegis MCP server does not authenticate. Any user on the remote box can
  reach that port and drive the local aegis — spawn agents, read
  transcripts, hand off. Loopback-only binding limits this to users on
  that host. Acceptable on a personal VPS; a real blocker for a shared
  one, and the reason a token on the MCP plane is the natural follow-up.
- **Remote auth and quota are the remote box's.** The harness on vps uses
  the VPS's own `~/.claude` credentials and subscription. Local
  `aegis usage` will not see that spend.
- **No filesystem bridge.** Remote files cannot be opened, indexed, or
  completed locally. A later slice with its own spec.
- **Not durable.** By design; see *What this is not*.

## Deferred

- Filesystem bridge — fetch remote files over the control connection
  into a local cache so ctrl+click, the indexer, and the `@`-picker work
  against a remote pane.
- A token on the aegis MCP plane, which would close the unauthenticated
  tunnel port.
- Staging the launch command as a remote script to keep argv out of `ps`.
- Host-aware terminals (`aegis_term_spawn(host=…)`) — the terminal plane
  is a separate substrate and gets its own treatment.
