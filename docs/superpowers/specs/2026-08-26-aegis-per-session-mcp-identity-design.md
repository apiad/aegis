# Per-session MCP identity — design

**Date:** 2026-08-26
**Status:** approved

## Summary

Make `from_handle` a transport fact instead of a convention. Mint a token per
harness spawn, inject it as an HTTP header alongside the MCP config aegis
already writes, and resolve the calling handle server-side from that header.
The `from_handle` parameter stays for one release as a fallback, then becomes
advisory.

---

## The problem

`AegisMCP` is **one co-resident FastMCP server** (`mcp/runtime.py:34`) on a
single loopback port. Every session on this aegis — every tab, every queue
worker, every SSH-hosted peer through its reverse tunnel — connects to that
same `http://127.0.0.1:<port>/mcp/`. There is no per-connection identity to
read a handle from.

So the handle is a *parameter*. It is baked into each agent's primer
(`PRIMING.format(handle=handle)`, `mcp/server.py:436`) and passed back by
convention. Three consequences, all live today:

- **An agent can pass a handle that is not its own**, by mistake or otherwise.
  Nothing checks it.
- **Tools that take no `from_handle`** — `aegis_list_sessions`, `aegis_claims`,
  `aegis_canvas_list`, `aegis_meta`, every `config_*` — cannot be attributed at
  all.
- **The comms ledger therefore records `from: ""`** for those, and
  `aegis comms list` prints `(unattributed)`. `CommsMiddleware._record` reads
  `args.get("from_handle")` (`comms/middleware.py:73`) and deliberately does not
  guess — a fabricated attribution in an audit record is worse than an honest
  gap — but a ledger whose whole point is *who talked to whom* has a hole in the
  *who*.

## Why now

This is not a regression and it has been tolerable, because every consumer so
far has been **observational**. That changes with mandatory file claims
(`2026-08-07-aegis-mandatory-file-claims-design.md`): `aegis_claim`,
`aegis_release`, `aegis_close` and `aegis_loop_stop` all gate on `from_handle`
matching, and under enforcement a wrong handle stops being a bad audit row and
starts being a wall in the wrong place — or the absence of one. Advisory claims
can live with a spoofable caller. Enforced claims cannot.

Identity is therefore slice 0 of that feature, not a neighbour of it.

---

## The carrier: an HTTP header, on both harness families

Probed 2026-08-26 rather than assumed:

| surface | evidence |
|---|---|
| Claude Code | `claude mcp add --transport http … --header "X-Api-Key: abc123"` is a documented option (CLI 2.1.226). The `--mcp-config` JSON that `mcp_config_json` writes takes the same `headers` key. |
| ACP | `mcp_servers` entries already carry `"headers": []` (`drivers/acp.py:490-494`) — the field exists and is empty. `acp.schema.McpServerHttp.headers` is `List[HttpHeader]`, and `HttpHeader` has `name` and `value`. |
| FastMCP | 3.2.0 exposes `fastmcp.server.dependencies.get_http_headers()`, which "never raises… even if there is no active HTTP request". `CommsMiddleware.on_call_tool` is already mounted at the single choke point every tool passes through (`mcp/server.py:601`). |

Both injection points are places aegis already writes per-session data, so
nothing new has to be plumbed to reach them.

**The header is `X-Aegis-Session`, and it must not be `Authorization`.**
`get_http_headers()` strips `content-length` and `authorization` by default.
Using the obvious bearer-token spelling would yield an empty dict and silently
attribute nothing — the failure mode being a feature that appears to work
because unattributed is exactly what it looked like before.

---

### The rejected alternative: a token in the URL

The token could ride the MCP **URL** as a query parameter
(`http://127.0.0.1:<port>/mcp/?s=<token>`) instead of a header. That is
genuinely cheaper: `mcp_url` is already threaded end to end — through
`SessionFactory`, `HarnessDriver.session/resume/fork`, `build_argv`, the ACP
`mcp_servers` entry and the SSH launcher's rewrite — so a per-session URL is
roughly two lines in `SessionManager` and nothing anywhere else, against seven
defaulted parameters for the header.

Rejected anyway, for two reasons. Both protocols model headers **explicitly and
only** for this purpose — `claude mcp add --header`, `acp.schema.HttpHeader` —
so the header is where a reader expects to find credentials and where a future
`Authorization`-style upgrade would live. And a URL is the part of a request
that gets logged, echoed into error messages, and pasted into bug reports; a
token there leaks by default, while a header leaks only deliberately. The cost
is seven parameters that all default to `""`, which breaks no existing caller.

Worth revisiting only if the parameter threading turns out to fight something
concrete — not on grounds of line count alone.

## Token lifecycle

A token is minted **per harness spawn**, not per conversation. A resumed
session is a new subprocess and gets a new token; a token therefore never needs
to survive an aegis restart, which is why the registry is in memory and has no
persistence story.

`SessionTokens` (new, `src/aegis/mcp/identity.py`), owned by `AegisMCP`:

- `mint(handle) -> str` — a fresh opaque token, replacing any prior token for
  that handle.
- `resolve(token) -> str | None` — the handle, or `None` for an unknown token.
- `rename(old, new)` — a rename keeps the token and re-points it. Handles are
  identity and `aegis_rename` migrates inbox routing atomically; the token has
  to move with it or every post-rename call goes unattributed.
- `revoke(handle)` — on session close, so a dead session's token stops
  resolving.

Tokens are opaque and compared by equality only. `secrets.token_urlsafe(24)`.

## Resolution

One helper, `resolve_caller() -> str | None`, reads `get_http_headers()`, looks
up `X-Aegis-Session`, and returns the handle. It is called from exactly two
places:

1. **`CommsMiddleware._record`** — the ledger's `from` becomes the resolved
   handle when there is one, falling back to `args.get("from_handle")`, then to
   `""`. This alone closes the attribution hole for the no-parameter tools.
2. **A `verified_handle(claimed)` helper** used by the gating tools. When the
   token resolves, the token wins and a disagreement is logged as a mismatch.
   When it does not, the claimed handle is used unchanged.

## Policy for v1: attribution, not authentication

**An unauthenticated call is recorded, not rejected.** The token would also
solve the open MCP plane — the reverse-tunnelled port is currently reachable by
any user on an SSH host, which the hosts spec calls "fine on a personal VPS, a
blocker on a shared one" — and the same token is the material for that. But
reject-on-missing turns a debugging convenience into a lockout, and it would
break every out-of-band caller (a `curl` against the plane, a test harness, a
plugin) on the release that introduces it.

So v1 resolves and records. Rejection is a config flag in a later slice, once
there is evidence that every real caller carries a token. The mismatch log is
what produces that evidence.

## Non-goals

- **Not authentication.** See above. No call is refused in v1.
- **Not persistence.** Tokens die with the process that minted them, because
  the subprocess they identify does too.
- **Not a transport change.** Still one co-resident FastMCP server on one port.
  This adds a header, not a socket per session.
- **Not removing `from_handle`.** It stays a parameter for one release. Its
  removal is a separate, breaking change with its own deprecation note.

---

## Traps

Four, each of which produces a silent wrong answer rather than an error:

1. **`get_http_headers()` strips `authorization`.** Hence `X-Aegis-Session`.
   A test that asserts the header round-trips is the only thing that catches a
   later rename back to a stripped name.

2. **`hosts/launcher.py:_substitute_mcp_url` matches on exact equality.** It
   rewrites the one argv element equal to `mcp_config_json("")`, because a
   remote session's URL is not known until sshd allocates the tunnel port. If
   `mcp_config_json` gains a token parameter and the launcher keeps calling it
   without one, the placeholder no longer matches, no substitution happens, and
   **every SSH-hosted session gets an empty MCP URL** — no aegis plane at all,
   silently. The launcher must build its placeholder with the same token.

3. **ACP headers are a list of `{name, value}`, not a mapping.** A dict there is
   accepted by aegis's own dict-shaped code and rejected or ignored downstream.

4. **`resolve_caller()` runs outside an HTTP request in tests and in-process
   callers.** `get_http_headers()` returns `{}` rather than raising, so the
   helper must treat "no request" and "no token" identically and never assume a
   request context exists.

## Files

| file | change |
|---|---|
| `src/aegis/mcp/identity.py` | **new** — `SessionTokens`, `HEADER`, `resolve_caller`, `verified_handle` |
| `src/aegis/mcp/runtime.py` | `AegisMCP` owns a `SessionTokens`; expose it to `build_server` |
| `src/aegis/mcp/server.py` | `mcp_config_json(url, token)`; `verified_handle` on the gating tools |
| `src/aegis/drivers/claude.py` | pass the token through `build_argv` |
| `src/aegis/drivers/acp.py` | fill the `headers` list in `mcp_servers` |
| `src/aegis/hosts/launcher.py` | build the placeholder with the same token |
| `src/aegis/comms/middleware.py` | resolve `from` from the token, fall back to the arg |
| `src/aegis/core/manager.py` | mint on spawn, rename on rename, revoke on close |

## Testing

The decisive test is a **live round-trip**: a real `claude` subprocess, spawned
with a real `--mcp-config`, calling a real tool on a real FastMCP server, with
the server asserting the header arrived. Everything else can pass while the
header is silently dropped somewhere in the chain, which is the one failure this
whole design rests on not happening. It goes behind the `live` marker with the
other `claude` tests.
