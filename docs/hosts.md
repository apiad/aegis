# Execution hosts

An **execution host** runs an agent's harness process on another
machine. The session, the tab, the transcript and the MCP peer identity
all stay local; only the subprocess at the bottom lives elsewhere.

The point is not remote *access*. It is that the agent's `Bash`, `Read`,
`Edit` and `Grep` act on **that** machine's filesystem, natively —
instead of one `ssh` invocation per command, paying connection setup
every time and still reading the wrong tree.

```
   ┌─────────────────────────── zion (your laptop) ──────────────────┐
   │  aegis                                                          │
   │   ├─ tab 1  lucid-knuth      ──▶ claude (local)                 │
   │   └─ tab 2  bold-blum @vps   ──▶ ssh ──┐                        │
   │  MCP plane (loopback) ◀────────────────┼── reverse tunnel       │
   └────────────────────────────────────────┼────────────────────────┘
                                            │
   ┌────────────────────────── vps ─────────┼────────────────────────┐
   │                                        └──▶ claude              │
   │       Bash / Read / Edit / Grep act HERE                        │
   └─────────────────────────────────────────────────────────────────┘
```

Both tabs are peers. `lucid-knuth` can `aegis_handoff` to `bold-blum`,
share a canvas with it, and see it in `aegis_list_sessions` — the only
difference is which filesystem `bold-blum` is standing on.

## This is not `--remote`, and not the remote plane

Three things in aegis have "remote" in the name. They solve different
problems and compose fine, but confusing them will waste an afternoon.

| | who owns the session | what moves |
|---|---|---|
| `aegis --remote ws://…` | a **remote** `aegis serve` | the **UI** attaches remotely |
| [Remote plane](remote.md) (`remotes:`) | two peer serves | **callbacks** federate between aegises |
| **Execution hosts** (`hosts:`) | the **local** aegis | the **harness process** runs elsewhere |

Here there is exactly one aegis, running locally, owning every session.

**Not durable, by design.** When the link drops the remote harness dies
with it; [`/reconnect`](#when-the-link-drops) gets the tab back. If you
want work to continue while your laptop is closed, that is the
`--remote` case: run `aegis serve` on the box and attach to it.

## Configuring a host

```yaml
hosts:
  vps:
    ssh: vps.apiad.net          # handed to ssh verbatim — ~/.ssh/config applies
    cwd: /home/apiad/Workspace  # default working tree over there
  smaug:
    ssh: smaug.local
    cwd: /home/apiad/work
    ssh_opts: ["-o", "ServerAliveInterval=15"]
```

Or scriptably:

```bash
aegis config host add vps --ssh vps.apiad.net --cwd /home/apiad/Workspace
aegis config host list
aegis config host remove vps      # restart to drop the live host
```

Drop-in overlays work like every other section: `.aegis/hosts/vps.yaml`
holding the entry body. Declaring the same host inline *and* as an
overlay is a fail-loud collision.

`local` is implicit, always exists, and cannot be declared.

| field | required | meaning |
|---|---|---|
| `ssh` | yes | ssh destination. Aliases, `ProxyCommand`, jump hosts and non-standard ports all work — aegis does not reimplement any of it. |
| `cwd` | yes | default working directory on that machine |
| `ssh_opts` | no | extra flags appended to every ssh invocation for this host |
| `login_shell` | no (default `true`) | run the harness under `bash -lc`. See below. |
| `remote_mcp_port` | no | pin the remote end of the MCP tunnel instead of letting sshd allocate one |

### `login_shell` — leave it on

A non-interactive `ssh host cmd` does **not** source your profile. The
Claude Code installer puts `claude` in `~/.local/bin`, which `~/.profile`
adds to `PATH` — so without a login shell the harness is simply not on
`PATH`, and every spawn dies at preflight for a reason that has nothing
to do with you.

It also picks the *right* binary when there are several. On a host with
both a system and a user install, the login shell resolves the user one:

```console
$ ssh vps 'command -v claude'                 # non-interactive
$                                             # (nothing)
$ ssh vps 'bash -lc "claude --version"'
2.1.220 (Claude Code)                         # vs 2.1.114 in /usr/bin
```

Turn it off only for a host whose profile writes to stdout, which would
corrupt the protocol stream.

## What the remote machine needs

The harness CLI on `PATH` (under a login shell), an SSH key, and the
working directory. **Nothing else** — no aegis install, no `.aegis.yaml`.
All config, state, transcripts and coordination stay on the local side.

## Spawning there

Host is a **third orthogonal axis** beside agent profile and harness:
any harness runs on any host, resolved per spawn and never persisted —
exactly like the `model` / `effort` overrides.

=== "TUI"

    `Ctrl+N` offers a host tier ahead of the usual agent tiers. It only
    appears when you have hosts configured.

=== "Slash command"

    ```
    /spawn main@vps
    /spawn main@vps:/srv/app        # override the host's default cwd
    ```

=== "From an agent"

    ```python
    aegis_spawn(agent="main", prompt="…", from_handle="me", host="vps")
    ```

    The new agent is an ordinary peer: it can hand off to you, join a
    canvas, and appears in your `aegis_list_sessions` — carrying
    `host: "vps"` so you can see where it stands.

An agent profile may carry a `host:` default, which the picker still
overrides:

```yaml
agents:
  deploy:
    harness: claude-code
    model: opus
    host: vps          # this profile usually runs there
```

## Paths are host-scoped

`/home/apiad/Workspace/src/foo.py` exists on both machines and is a
**different file**. aegis treats that as a first-class fact rather than
letting it become a silent wrong answer:

- **Ctrl+click** on a remote pane does not open the local file of the
  same name. It offers `vps:/home/apiad/Workspace/src/foo.py` to copy.
- **File claims** carry their host. A remote claim and a local claim on
  the same path neither collide nor pretend to protect each other.
- **Backtick-token resolution** and the `@`-file picker are disabled on
  a remote pane — both walk the local tree.
- The tab is marked **`@vps`**.

!!! warning "Never hand a remote peer a path you resolved locally"
    It will read a different file and give you a confident wrong answer.
    Qualify it, or let the remote agent find the path itself.

## When the link drops

The pane reports `link to vps lost — <ssh stderr>` and enters its error
state, rather than sitting there looking idle on a session that no
longer exists.

```
/reconnect            # rebuild this pane's harness, in place
/reconnect bold-blum  # or a named one
```

`/reconnect` re-runs the harness on the same host and resumes the same
conversation id **in the same tab** — handle, transcript and scrollback
all survive, because only the process underneath is replaced. The remote
harness kept its own conversation store, so you lose just the in-flight
turn.

Harnesses without resume support get the error state alone and must be
respawned.

## How it works

- **One SSH ControlMaster per host**, opened lazily on that host's first
  spawn and shared by every session on it — no new TCP connection, no
  re-authentication, no per-command setup. That is the "persistent" part.
- **A reverse tunnel** carries the local MCP plane to the remote side, so
  the remote agent is an ordinary peer. sshd picks the remote port and
  announces it; aegis reads that and tells the harness.
- **Preflight**, once per host: the harness binary and the working
  directory are checked before anything spawns, so a missing harness is a
  sentence rather than a mysterious EOF.
- **A tunnel warm-up** opens one connection through the fresh forward
  before any harness needs it — the first connection through a new
  forward is the slow one, and a cold harness would otherwise race its
  own MCP handshake against that setup cost.

## Security

Worth reading before you point this at a machine other people use.

- **The harness command line is visible in the remote `ps`**, including
  the MCP primer and any persona. Fine on a single-user box; an
  information leak on a shared one.
- **The reverse-tunneled MCP port is unauthenticated.** Any user on that
  machine can reach it and drive your local aegis — spawn agents, read
  transcripts, hand off. Loopback binding limits this to users *on that
  host*. Acceptable on a personal VPS; a real blocker on a shared one.
- **Remote auth and quota are the remote machine's.** The harness there
  uses its own credentials and subscription, and local `aegis usage`
  will not see that spend.

## Troubleshooting

Run these in order; each isolates one layer.

```bash
ssh vps true                                    # reachable at all?
ssh -T vps 'bash -lc "command -v claude"'       # preflight, by hand
ssh -O check -o ControlPath=.aegis/state/ssh/vps.sock vps
ssh -v -N -R 0:127.0.0.1:1234 vps               # forward allocated?
```

That last one should print:

```
Allocated port 41573 for remote forward to 127.0.0.1:1234
```

**Different phrasing on your sshd?** Set `remote_mcp_port:` on the host
to skip auto-detection.

**`LogLevel=QUIET` in your `~/.ssh/config`** suppresses that
announcement, and the first spawn then hangs ~20s before failing with a
timeout. aegis passes `-o LogLevel=INFO` explicitly to defend against
this, but a `Match` block can still override it.

**A remote agent says the aegis tools aren't there.** The MCP handshake
crosses the tunnel, so an exact-name tool lookup can miss on the very
first call and resolve seconds later. Retry rather than concluding the
substrate is unreachable — the warm-up above exists to shrink this
window.

See also: `know-how/ssh-execution-hosts.md` in the repo for the
maintainer-facing procedure.
