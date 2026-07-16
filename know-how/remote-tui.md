# Running the TUI against a remote aegis serve (`--remote`)

*When to reach for it: connecting the Textual TUI to a remote (or
auto-launched local) `aegis serve` daemon via `--remote ws://…` or
`--remote ssh://…`, or debugging the WS client / SSH tunnel path.*

`--remote` makes the TUI a WebSocket client of an `aegis serve` daemon.
Sessions live in the daemon; the TUI is a stateless viewport. You can
open multiple TUI windows against the same serve, and sessions survive
the TUI being closed and reopened.

## The three `--remote` modes

### 1. No argument — autolaunch localhost

```bash
aegis --remote
```

Equivalent to `aegis --remote ws://localhost:8080`. Before connecting,
aegis probes `localhost:8080`. If nothing is listening it spawns
`aegis serve` as a background subprocess (detached, stdout/stderr
suppressed) and waits up to 5 seconds for the port to open. Requires
a valid `.aegis.yaml` in the working tree (serve bootstraps from it).

Use this for local "daemon mode" — one persistent serve, multiple TUI
sessions across terminals.

### 2. `ws://` — direct WebSocket

```bash
aegis --remote ws://host:8080 --token <token>
```

Connects directly over an unencrypted WS connection. `--token` is
required; obtain it from the remote host with `aegis token` (prints
the token stored in `.aegis.yaml`). The `ws://` path is also used
internally by the `ssh://` path after the tunnel is up.

`wss://` (TLS) is **not yet supported** — see Known limitations.

### 3. `ssh://` — SSH port-forward (the usual remote path)

```bash
aegis --remote ssh://vps:8080
```

No `--token` needed. aegis:

1. Calls `ssh <host> aegis token` to fetch the token from the remote
   host (uses your existing `~/.ssh/config`; works through jump-hosts
   and ProxyCommand).
2. Starts `ssh -L <local-port>:localhost:<remote-port> -N <host>` as a
   background subprocess.
3. Probes `127.0.0.1:<local-port>` every 100 ms (10 s timeout).
4. Once reachable, connects the WS client to `ws://localhost:<local-port>`.

The tunnel subprocess lives for the TUI's lifetime; it is terminated
(SIGTERM then SIGKILL if needed) when the subprocess exits. See Known
limitations for a note on clean teardown.

## Auth

All `ws://` connections send a JSON auth frame (`{"type":"auth","token":"…"}`)
as the first message. The server responds with a `hello` frame carrying
`protocol_version` (checked against the client's `PROTOCOL_MAJOR = 2`).
A version mismatch raises `ProtocolMismatch` immediately.

For `ssh://` the token is fetched automatically — nothing to manage.
For `ws://` directly you need the token from `aegis token` on the remote.

## Reconnect + tail replay

`WsClient` reconnects automatically after a drop (exponential backoff,
1 s → 30 s cap). On reconnect it sends a `resume` frame listing every
active subscription with its last-seen `seq` and a `tail` count. The
server replays the last `tail` coalesced blocks for each session so the
TUI fills in what was missed during the drop. The `--tail N` flag (default
10) controls how many blocks to replay.

A "reconnecting" banner appears when the connection drops; it clears when
the server's replay fills the transcript.

## What works in v1

The conversation loop is fully supported remotely:

- Session list (populated via `list_sessions` RPC + `session_list` stream)
- Spawn session (`Ctrl+N`)
- Send message (text input → `deliver` RPC)
- Interrupt turn (`Escape`)
- Close session
- Handoff between sessions
- Rename session handle
- Transcript streaming (event/state/inbox stream frames)
- Tail-replay on subscribe and reconnect
- Cross-window coherence — multiple TUI windows see the same sessions

## What is disabled in v1 (aux-surface planes)

These planes are not yet exposed over the WS protocol. Accessing them
raises `RemoteUnsupportedError` and the TUI shows a "not available in
--remote v1" banner:

| Plane | Manager attribute | TUI feature |
|---|---|---|
| Queue | `queue_manager` | Queue dashboard (Alt+Q) |
| Canvas | `canvas_manager` | Canvas tab |
| Terminal | `terminal_manager` | Terminal tab |
| Groups | `groups` | Group dashboard (Alt+G) |
| Locks | `locks` | Inter-agent file-claims |
| Workflow / Scheduler | `workflow_registry` | Workflow dispatch |

S9.3 (aux-surface RPCs) is the follow-up slice for these.

## Known limitations

1. **No `wss://` support.** TLS WebSocket connections are rejected by
   `_build_remote_manager` (`unsupported scheme 'wss'`). For secure
   access over the open internet, use `ssh://` (tunnel is TLS-equivalent).

2. **Tunnel not torn down on `RemoteSessionManager.close()`.** The SSH
   subprocess is stashed as `mgr._tunnel` but `RemoteSessionManager`
   has no `close()` / `__aexit__` hook that calls `tunnel.__aexit__()`.
   The subprocess is killed at process exit. Follow-up flagged in the
   Task 10 report; wire teardown into `RemoteSessionManager.close()`.

3. **No `protocol_version` validation on reconnect.** The reconnect
   loop in `WsClient._reconnect_loop` re-reads the `hello` frame but
   does not re-check `protocol_version` (Task 6 follow-up). A server
   upgrade during a live TUI session could silently mismatch.

4. **No aux-surface support (S9.3 deferred).** Queue / canvas /
   terminal / group dashboards raise `RemoteUnsupportedError` in remote
   mode. Use the web client (`aegis web`) for those surfaces remotely.

5. **`user@host` in `ssh://` URL partially supported.** `urlparse`
   extracts `hostname` correctly but `_ssh_fetch_token` passes only
   the hostname to `ssh`, ignoring any user@ prefix. If your SSH config
   does not handle the user mapping, the fetch may fail. Workaround:
   set `User` in `~/.ssh/config` for the host.

## Debug checklist

**Connection fails immediately:**
- `ssh <host> aegis token` — verifies SSH reachability and that aegis
  is installed on the remote.
- `ssh <host> -L 8888:localhost:8080 -N` in a separate terminal — verifies
  the port-forward works independently.

**Auth rejected (code 4001):**
- Token mismatch. Re-run `aegis token` on the remote and pass it with
  `--token` (or re-try `ssh://` which fetches it fresh).

**`ProtocolMismatch` on connect:**
- Client and server are on different aegis versions. Update whichever
  is older (`uv pip install -U aegis-harness` on the remote host).

**WsClient never reconnects:**
- Check that `aegis serve` is still running on the remote
  (`systemctl status aegis-web` or equivalent).
- Tunnel subprocess may have exited if the SSH session dropped.
  Close the TUI and reopen with `aegis --remote ssh://…`.

**Sessions appear but transcripts are empty:**
- Increase tail: `aegis --remote ssh://host:8080 --tail 50`.
