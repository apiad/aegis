# Running a harness on another machine (`hosts:`)

*When to reach for it: configuring or debugging an execution host — a
session whose harness process runs on another box over SSH
(`/spawn main@vps`), or the SSH ControlMaster / reverse MCP tunnel behind
it.*

The local aegis keeps the session, the transcript, the tab and the MCP
peer identity. Only the harness subprocess runs elsewhere — so that
agent's `Bash`, `Read`, `Edit` and `Grep` act on **that** machine's
filesystem, natively, rather than one `ssh` invocation per command.

## What this is not

aegis has three things with "remote" in the name. They are different.

| | who owns the session | what moves |
|---|---|---|
| `aegis --remote ws://\|ssh://` | a remote `aegis serve` | the **UI** attaches remotely |
| `remotes:` / `remote_plane` | two peer aegis serves | **callbacks** federate between aegises |
| **`hosts:` (this doc)** | the **local** aegis | the **harness process** runs elsewhere |

**Durability is out of scope by design.** When the link drops the remote
harness dies with it; `/reconnect` gets the tab back. If you want work to
continue while your laptop is closed, run `aegis serve` on the VPS and
attach with `aegis --remote ssh://vps:8080`, or just run `aegis` there.

## Configuring a host

```yaml
hosts:
  vps:
    ssh: vps.apiad.net          # handed to ssh verbatim → ~/.ssh/config applies
    cwd: /home/apiad/Workspace  # default working tree there
  smaug:
    ssh: smaug.local
    cwd: /home/apiad/work
    ssh_opts: ["-o", "ServerAliveInterval=15"]
```

or scriptably:

```bash
aegis config host add vps --ssh vps.apiad.net --cwd /home/apiad/Workspace
aegis config host list
aegis config host remove vps          # restart to drop the live host
```

Drop-in overlays work like every other section: `.aegis/hosts/vps.yaml`
holding the entry body. Declaring a host in both places is a fail-loud
collision.

`local` is implicit, always exists, and cannot be declared — allowing it
would make the meaning of `host: local` depend on config.

| field | required | meaning |
|---|---|---|
| `ssh` | yes | ssh destination. Aliases, `ProxyCommand`, jump hosts and non-standard ports all work, because aegis does not reimplement any of it. |
| `cwd` | yes | default working directory on that machine |
| `ssh_opts` | no | extra flags appended to every ssh invocation for this host |
| `login_shell` | no (default **true**) | wrap the remote command in `bash -lc`. See below — this is not cosmetic. |
| `remote_mcp_port` | no | pin the remote end of the reverse tunnel instead of letting sshd allocate one |

### `login_shell` is on by default for a real reason

A non-interactive `ssh host cmd` does **not** source the user's profile.
The claude installer puts `claude` in `~/.local/bin`, which is added to
`PATH` by `~/.profile` — so without a login shell the harness is simply
not on `PATH` and every remote spawn dies at preflight, for a reason that
has nothing to do with the user. Turn it off only for a host whose
profile writes to stdout, which would corrupt the protocol stream.

Check it by hand:

```bash
ssh -T vps 'command -v claude'            # likely empty
ssh -T vps 'bash -lc "command -v claude"' # should find it
```

## What the remote machine needs

The harness CLI on `PATH` (under a login shell), an SSH key, and the
working directory. **Nothing else** — no aegis install, no `.aegis.yaml`.
All config, state, transcripts and coordination stay local.

## Spawning there

```
Ctrl+N                       → host tier, then the usual agent tiers
/spawn main@vps
/spawn main@vps:/srv/app     → override the host's default cwd
```

From inside an agent:

```python
aegis_spawn(agent="main", prompt="…", from_handle="me", host="vps")
```

Host is a **third orthogonal axis** beside agent profile and harness: any
harness runs on any host, resolved per spawn and never persisted, exactly
like the `model` / `effort` overrides. An agent profile may carry
`host:` as a default, which the picker still overrides.

## How it works

- **One ControlMaster per host**, opened lazily on that host's first
  spawn and shared by every session on it: no new TCP connection, no
  re-authentication, no per-command setup. That is the "persistent" part.
- **A reverse tunnel** (`-R 0:127.0.0.1:<mcp_port>`) carries the local MCP
  plane to the remote side, so the remote agent is an ordinary peer —
  `aegis_handoff`, `aegis_claim`, `aegis_list_sessions` all work. sshd
  picks the remote port and announces it on stderr; aegis parses that
  line.
- **Preflight** runs once per host (`command -v <harness>` + `test -d
  <cwd>`) so a missing harness is a sentence rather than a mysterious EOF.
- **A tunnel warm-up** opens one TCP connection through the fresh forward
  before any harness needs it. A forward that was *allocated* is not
  necessarily one that *works*, and the first connection through a new
  forward is the slow one — without the warm-up a cold harness races its
  own MCP handshake against that setup cost.

### Symptom this prevents: "the aegis tools aren't there"

The first remote agent spawned through this feature reported its system
prompt saying the aegis MCP server was *still connecting*, and an
exact-name `ToolSearch("select:aegis_meta,…")` returning **no matching
tools**. A keyword search seconds later resolved the full surface.

An agent that takes that first miss at face value concludes the substrate
is unreachable and gives up. If you see it: retry the search rather than
believing it, and check the warm-up isn't silently failing (its
diagnostic lands in the connection's stderr ring).

## Paths are host-scoped

`/home/apiad/Workspace/src/foo.py` exists on both zion and vps and is a
**different file**. So:

- **Ctrl+click** on a remote pane does not open the local file of the
  same name — it offers `vps:/home/apiad/Workspace/src/foo.py` to copy.
- **File claims** carry their host; a remote claim and a local claim on
  the same path neither collide nor pretend to protect each other.
- **Backtick-token resolution** and the `@`-file picker are disabled on a
  remote pane, because both walk the local tree.
- The tab is marked **`@vps`**.

Never hand a remote peer a path you resolved locally.

## When the link drops

The pane reports `link to vps lost — <ssh stderr>` and goes to its error
state rather than looking idle. `/reconnect` rebuilds the harness on the
same host and resumes the same conversation id **in the same tab**, so
you lose only the in-flight turn (the remote `~/.claude` still holds the
conversation). Harnesses without resume support get the error state
alone and must be respawned.

## Debug checklist

Each of these is a command to run, in order:

```bash
ssh vps true                                   # reachable at all?
ssh -T vps 'bash -lc "command -v claude"'      # preflight, by hand
ssh -O check -o ControlPath=.aegis/state/ssh/vps.sock vps   # master alive?
ssh -v -N -R 0:127.0.0.1:1234 vps              # does the forward get allocated?
```

That last one should print:

```
Allocated port 41573 for remote forward to 127.0.0.1:1234
```

**If the phrasing differs on your sshd**, set `remote_mcp_port:` on the
host to skip auto-detection.

**If `LogLevel=QUIET` is set in your `~/.ssh/config`**, that announcement
is suppressed and the first spawn hangs for 20s before failing with a
timeout. aegis passes `-o LogLevel=INFO` explicitly to defend against
this, but a `Match` block can still override it.

**Two readers on one pipe.** Only the transport drains ssh's stderr, and
only for a session that does not read stderr itself (`ClaudeSession`).
`AcpSession` runs its own drain, so `SshLauncher.watch_stderr()` must not
be called for it — a pipe has exactly one legitimate reader.

## Security, stated not hidden

- **The wrapped argv is visible in the remote `ps`**, including the MCP
  primer, the persona, and the MCP config. Fine on a single-user box; an
  information leak on a shared one.
- **The reverse-tunneled MCP port is unauthenticated.** Any user on that
  machine can reach it and drive your local aegis — spawn agents, read
  transcripts, hand off. Loopback binding limits this to users on that
  host. Acceptable on a personal VPS; a real blocker on a shared one.
- **Remote auth and quota are the remote box's** (`~/.claude` there).
  Local `aegis usage` will not see that spend.

## Testing

`tests/test_ssh_hosts_live.py` uses **`localhost` as the remote host**, so
it runs anywhere sshd accepts a key-based connection — real master, real
tunnel, real `claude`. It auto-skips when `ssh -o BatchMode=yes localhost
true` fails.

The tunnel test is the one that matters, and it has been mutation-checked:
break the `-R` forward on purpose and confirm it goes red before trusting
it.
