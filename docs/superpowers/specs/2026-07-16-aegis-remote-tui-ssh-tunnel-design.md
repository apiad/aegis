# Aegis Remote TUI via SSH Tunnel — Design

**Status:** draft
**Date:** 2026-07-16
**Scope:** extend the `--remote` TUI mode (S9, `2026-07-01-aegis-tui-ws-client-design.md`) with
automatic SSH tunnel setup and cross-machine session resilience. S9 assumed a co-resident
`aegis serve`; this spec adds the cross-machine path where the daemon runs on a remote host
(e.g. a VPS) and the TUI runs on the developer's laptop.

## Motivation

Two concrete use cases:

1. **Low-bandwidth development.** Run the TUI on the laptop but keep all agent sessions on the
   VPS. Only the WS wire (small JSON frames) passes through the SSH tunnel; heavy model API
   traffic stays on the server side.

2. **Session resilience.** When the TUI is killed or the laptop sleeps, sessions keep running
   on the VPS daemon. Reopening the TUI reconnects and replays recent history.

## Prerequisites

This spec builds on S9. Before implementing this spec, S9.0–S9.2 must be complete:
- `src/aegis/tui/ws_client.py` exists and is tested.
- `RemoteSessionManager` implements `AppBridge` and `aegis --remote ws://…` works.

## New pieces (delta from S9)

### 1. `SSHTunnel` (`src/aegis/remote/ssh_tunnel.py`)

Manages an `ssh -L` subprocess for the duration of a TUI session.

```
SSHTunnel(host, remote_port) → context manager
  .local_port: int   # ephemeral, chosen by OS
  .__aenter__: spawns ssh, probes TCP until ready
  .__aexit__:  terminates ssh subprocess
```

Implementation:
- Bind a random local port by opening a socket to port 0, reading the assigned port, then closing.
- Spawn: `ssh -L <local>:localhost:<remote_port> -N <host>` (relies on `~/.ssh/config` for host alias, key, etc.)
- Probe: TCP connect to `127.0.0.1:<local>` every 100 ms, up to 10 s; raise `TunnelError` on timeout.
- Teardown: `process.terminate()` + `await process.wait()`.

No retry on spawn failure (SSH key missing, host unreachable) — surface the error immediately.

### 2. Token fetch via SSH (`aegis token` subcommand)

The `aegis serve` WS endpoint requires bearer-token auth. When using `ssh://` scheme, the CLI
fetches the token automatically by running a single SSH command before opening the tunnel:

```
ssh <host> aegis token
```

`aegis token` is a new top-level subcommand (one line of Typer + one line of output) that prints
the active web token to stdout. The value comes from whatever `aegis serve` reads today
(YAML config or state dir).

The fetched token is passed to `RemoteWsClient` as the bearer credential; the user never sees
or copies it.

If the user prefers an explicit token (e.g. `ws://` scheme): `--token <tok>` flag on `aegis`.

### 3. `ssh://` URL scheme in `--remote`

Extend the URL parsing in `cli.py`:

| Flag value | Behavior |
|---|---|
| `ws://host:port` | Direct WS connect, no tunnel. Requires `--token`. |
| `ssh://host:port` | Fetch token via SSH → open `SSHTunnel(host, port)` → connect WS to `ws://localhost:<local_port>/ws?t=<token>`. |

`host` is an SSH alias or hostname; `port` is the remote `aegis serve` WS port (default 8080).

CLI:
```
aegis --remote ssh://vps:8080
aegis --remote ssh://vps:8080 --tail 20
aegis --remote ws://localhost:8080 --token abc123
```

### 4. `tail` parameter on `subscribe` (WS protocol extension)

When a TUI reconnects to a daemon it was previously watching, it should replay recent history
without needing to remember a specific `last_seq`.

Add an optional `tail` field to the `subscribe` frame:

```json
{"type": "subscribe", "target": {"kind": "session", "handle": "lucid-knuth"}, "tail": 10}
```

Server behavior in `_open_session()` (`wssession.py`):
- If `tail` is present: read the JSONL for the session, compute
  `from_seq = max(0, last_seq - tail)`, replay from there, then attach the live observer.
- If absent: existing behavior (no replay).

`RemoteSessionManager` uses `tail=k` (default 10, overridable via `--tail`) on every subscribe
call at startup.

### 5. Reconnection with `tail` replay

If the WS connection drops mid-session (tunnel hiccup, daemon restart):
- TUI shows a "Disconnected — reconnecting…" banner in the status bar.
- `RemoteWsClient` retries with exponential backoff: 1 s, 2 s, 4 s … cap 30 s.
- On reconnect: re-authenticate, re-subscribe all open handles with `tail=k`.
- Each pane clears its transcript and re-renders from the replayed events to avoid duplicates.

Sessions on the VPS continue running while the TUI is disconnected; no session state is lost.

## What does NOT change

- All TUI rendering, widgets, keyboard shortcuts — identical to S9 (and today's local TUI).
- The `aegis serve` daemon — it already runs as `aegis-web.service` on the VPS; no daemon-side
  changes except adding the `tail` field to `_open_session()` and the `aegis token` subcommand.
- The `ws://` path from S9 — this spec only adds the `ssh://` variant on top.
- The in-process `aegis` (no `--remote`) — unchanged; remains the default per S9's migration plan.

## Slice breakdown

| # | Deliverable | Notes |
|---|---|---|
| **R1** | `aegis token` subcommand | 5 lines; prerequisite for R3 |
| **R2** | `tail` in `_open_session()` | Server-side; small change in `wssession.py` |
| **R3** | `SSHTunnel` + `ssh://` URL parsing | New file + CLI wiring; requires `asyncio.subprocess` |
| **R4** | Reconnect + `tail` replay in `RemoteWsClient` | Extend S9's reconnect loop |
| **R5** | Status bar "Disconnected" banner | Small TUI widget change |

R1–R3 can be implemented in order in a single session. R4–R5 follow naturally.

## Testing

- **Unit:** `SSHTunnel` with a mock subprocess; assert port probe and teardown.
- **Integration:** `aegis --remote ssh://localhost:<port>` (loopback SSH) against a real `aegis serve`; spawn → stream → disconnect tunnel → reconnect → verify replay.
- **Manual:** from zion, `aegis --remote ssh://vps:8080`; confirm sessions survive TUI restart.

## References

- S9 base spec: `docs/superpowers/specs/2026-07-01-aegis-tui-ws-client-design.md`
- WS protocol: `docs/superpowers/specs/2026-06-30-aegis-web-ws-protocol-design.md`
- Server WS handler: `src/aegis/web/wssession.py`
- AppBridge protocol: `src/aegis/mcp/bridge.py`
- Existing remote plane: `src/aegis/remote/`
