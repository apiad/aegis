# Per-session MCP Identity Implementation Plan

**Status:** implemented 2026-08-26 (commits `9ff6efe`…`b72d064`). All 8 tasks
landed; full hermetic suite green (3415 passed), live round-trip green against
the real `claude` CLI.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

## Where the implementation departed from this plan

Three places, all found by running the thing rather than reading it:

1. **`_substitute_mcp_url` matches the config's *shape*, not an equality with a
   rebuilt placeholder — and takes no `token`** (Task 4). The plan threaded a
   token down to it so the two reconstructed strings would still match. But the
   exact-equality comparison is *what creates* trap #2; keeping it means the
   next field added to the blob re-breaks every SSH session silently. Reading
   the `mcpServers.aegis` entry and filling its empty `url` cannot drift that
   way, needs no token at the launcher, and preserves whatever else the blob
   carries. Mutation-verified: restoring equality matching fails exactly the
   tokenful test — while the 36 pre-existing launcher tests stay green, i.e.
   the old suite would not have caught this.

2. **FastMCP 3.2.0's `Client` takes no `headers` parameter at all** (Task 2).
   Not the in-memory transport being header-blind, as the plan's fallback
   anticipated — the kwarg does not exist on `Client.__init__` in either
   transport. Headers belong to `StreamableHttpTransport(url, headers=...)`,
   so the test runs a real HTTP server on a free port and passes them there.

3. **`CommsLedger.read(day)` is day-scoped**; the plan's `_rows` helper called
   it with no argument. Tests use `read_all()`.

Additionally, the live test binds a **free port** rather than a fixed `8765`:
a collision on a dev box fails as a timeout, which reads exactly like the
header being dropped — the one failure this test exists to distinguish.

## Two tests added beyond the plan

Both close a seam the plan left covered only by a mock:

- `tests/test_identity_ledger_http.py` — the ledger's attribution with
  **nothing mocked**: a real header on a real request reaching the middleware
  `build_server` mounted, landing in the ledger as the `from` a human reads in
  `aegis comms list`. The plan's ledger tests monkeypatch `caller_token`, so
  none of them exercise that path.
- `tests/test_identity_acp_live.py` — the **ACP half of Task 8**. The plan's
  live test covers `claude` only, and ACP carries the token in a different
  field of a different shape, which is precisely trap #3. Verified against a
  real `lovelaice-acp` agent on a real model. Note `lovelaice-acp` ships in
  aegis's own venv but is not necessarily on the outer PATH — run with
  `PATH="$PWD/.venv/bin:$PATH"` or it silently skips.

**Goal:** Resolve the calling agent's handle from a per-spawn token carried in an HTTP header, so `from_handle` stops being a parameter any agent can get wrong.

**Architecture:** `AegisMCP` owns a `SessionTokens` registry. The token is minted at spawn, injected into the MCP config aegis already writes (a `headers` map for Claude, a `List[HttpHeader]` for ACP), and read back server-side via FastMCP's `get_http_headers()`. Two consumers: the comms ledger's `from` field, and a `verified_handle()` helper on the tools that gate on identity. `from_handle` remains a fallback for one release.

**Tech Stack:** Python 3.13+, `uv`, pytest, FastMCP 3.2.0, `acp` (agent-client-protocol), Claude Code CLI 2.1.226.

**Spec:** `docs/superpowers/specs/2026-08-26-aegis-per-session-mcp-identity-design.md`

## Global Constraints

- Python 3.13+. Use `uv run python -m pytest`, never bare `pytest`.
- Run the fast suite as `uv run python -m pytest -q -m "not live"`. Never use `-k "not live"` — it matches `live` as a substring and silently eats unrelated names.
- TDD: failing test first, minimal implementation, commit per logical unit.
- English for all code, comments, identifiers, error strings, and commit messages.
- Conventional commits, scope `identity` unless the change is clearly elsewhere.
- New tests follow the existing convention: `tests/test_identity_*.py`.
- **The header is `X-Aegis-Session`. It must never be `Authorization`** — `get_http_headers()` strips that one by default and the result is silent non-attribution.
- **v1 never refuses a call.** An unresolvable token is recorded as unattributed, exactly as today. Rejection is a later slice.

## File Structure

| file | responsibility |
|---|---|
| `src/aegis/mcp/identity.py` | **new.** The whole identity vocabulary: `HEADER`, `SessionTokens`, `caller_token`, `resolve_caller`, `verified_handle`. Pure except for the one FastMCP dependency call, which is isolated in `caller_token`. |
| `src/aegis/mcp/runtime.py` | `AegisMCP` owns the registry and hands it to `build_server`. |
| `src/aegis/mcp/server.py` | `mcp_config_json` grows a token argument; gating tools resolve through `verified_handle`. |
| `src/aegis/comms/middleware.py` | `from` resolves from the token first, the argument second. |
| `src/aegis/drivers/claude.py` | token into `--mcp-config`. |
| `src/aegis/drivers/acp.py` | token into `mcp_servers[].headers`. |
| `src/aegis/hosts/launcher.py` | placeholder built with the same token, or SSH sessions lose the plane. |
| `src/aegis/core/manager.py` | mint on spawn, re-point on rename, revoke on close. |
| `src/aegis/cli.py` | `_session_factory.make_session` takes and forwards the token. |
| `src/aegis/drivers/base.py` | `session` / `resume` / `fork` take `token: str = ""`. |

**Every new parameter is keyword-with-default.** `SessionFactory` is typed
`Callable[[object, str, str], object]` and `manager.py:192-201` documents that
"plain `(profile, url, handle)` callables must keep working" — the token
therefore rides the conditional-`extra` pattern that `fork_from` and `place`
already use, never a positional argument.

---

### Task 1: The identity vocabulary

Everything downstream calls this. No FastMCP server, no subprocess, no registry wiring — just the token store and the header lookup.

**Files:**
- Create: `src/aegis/mcp/identity.py`
- Test: `tests/test_identity_tokens.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `HEADER = "x-aegis-session"` in `aegis.mcp.identity`
  - `SessionTokens` with `mint(handle: str) -> str`, `resolve(token: str | None) -> str | None`, `token_for(handle: str) -> str | None`, `rename(old: str, new: str) -> None`, `revoke(handle: str) -> None`
  - `caller_token() -> str | None`
  - `resolve_caller(tokens: SessionTokens | None) -> str | None`
  - `verified_handle(tokens: SessionTokens | None, claimed: str | None) -> tuple[str, bool]`

- [x] **Step 1: Write the failing test**

Create `tests/test_identity_tokens.py`:

```python
from __future__ import annotations

from aegis.mcp.identity import (
    HEADER, SessionTokens, resolve_caller, verified_handle,
)


def test_header_is_not_authorization():
    """get_http_headers() strips `authorization` by default, so spelling
    the header that way would silently attribute nothing."""
    assert HEADER == "x-aegis-session"
    assert HEADER.lower() != "authorization"


def test_mint_then_resolve_round_trips():
    t = SessionTokens()
    tok = t.mint("alice")
    assert t.resolve(tok) == "alice"


def test_tokens_are_distinct_per_handle():
    t = SessionTokens()
    assert t.mint("alice") != t.mint("bob")


def test_minting_again_replaces_the_previous_token():
    """A respawn supersedes the old subprocess; its token must stop
    resolving or a dead session keeps an identity."""
    t = SessionTokens()
    old = t.mint("alice")
    new = t.mint("alice")
    assert t.resolve(old) is None
    assert t.resolve(new) == "alice"


def test_unknown_and_empty_tokens_resolve_to_none():
    t = SessionTokens()
    t.mint("alice")
    assert t.resolve("nope") is None
    assert t.resolve("") is None
    assert t.resolve(None) is None


def test_token_for_reports_the_live_token():
    t = SessionTokens()
    tok = t.mint("alice")
    assert t.token_for("alice") == tok
    assert t.token_for("bob") is None


def test_rename_keeps_the_token_and_repoints_it():
    """aegis_rename migrates identity atomically. If the token did not
    follow, every call after a rename would go unattributed."""
    t = SessionTokens()
    tok = t.mint("alice")
    t.rename("alice", "carol")
    assert t.resolve(tok) == "carol"
    assert t.token_for("carol") == tok
    assert t.token_for("alice") is None


def test_rename_of_an_unknown_handle_is_a_noop():
    t = SessionTokens()
    t.rename("ghost", "carol")
    assert t.token_for("carol") is None


def test_revoke_stops_resolution():
    t = SessionTokens()
    tok = t.mint("alice")
    t.revoke("alice")
    assert t.resolve(tok) is None
    assert t.token_for("alice") is None


def test_revoke_of_an_unknown_handle_is_a_noop():
    SessionTokens().revoke("ghost")


def test_resolve_caller_outside_a_request_is_none():
    """get_http_headers() returns {} rather than raising when there is no
    active request — tests and in-process callers must not blow up."""
    assert resolve_caller(SessionTokens()) is None
    assert resolve_caller(None) is None


def test_verified_handle_falls_back_to_the_claim():
    handle, verified = verified_handle(SessionTokens(), "alice")
    assert handle == "alice"
    assert verified is False


def test_verified_handle_tolerates_a_missing_claim():
    handle, verified = verified_handle(None, None)
    assert handle == ""
    assert verified is False
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_identity_tokens.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'aegis.mcp.identity'`

- [x] **Step 3: Write `identity.py`**

Create `src/aegis/mcp/identity.py`:

```python
"""Who is calling the aegis MCP plane.

``AegisMCP`` is one co-resident FastMCP server on one loopback port, so
every session — every tab, every queue worker, every SSH-hosted peer
through its reverse tunnel — arrives on the same connection surface.
There is no transport identity to read, which is why ``from_handle`` was
a parameter in the first place.

So we manufacture one: a token minted per harness spawn, injected into
the MCP config aegis already writes for that spawn, and read back off the
request header. The token identifies the *subprocess*, not the
conversation — a resumed session is a new process and gets a new token,
which is why this store is in memory and has no persistence story.

The header is ``X-Aegis-Session`` and deliberately not ``Authorization``:
FastMCP's ``get_http_headers()`` strips ``authorization`` (and
``content-length``) by default, so the obvious bearer spelling would
resolve to nothing at all — and "nothing at all" is exactly what this
looked like before the feature, which is what makes it dangerous.
"""
from __future__ import annotations

import secrets

# Lowercase because HTTP header names are case-insensitive and
# get_http_headers() hands back a plain dict; we normalise both sides
# rather than depend on which case it chose.
HEADER = "x-aegis-session"


class SessionTokens:
    """Live spawn tokens, both directions. In memory by design."""

    def __init__(self) -> None:
        self._by_token: dict[str, str] = {}
        self._by_handle: dict[str, str] = {}

    def mint(self, handle: str) -> str:
        """A fresh token for ``handle``, superseding any previous one."""
        self.revoke(handle)
        token = secrets.token_urlsafe(24)
        self._by_token[token] = handle
        self._by_handle[handle] = token
        return token

    def resolve(self, token: str | None) -> str | None:
        if not token:
            return None
        return self._by_token.get(token)

    def token_for(self, handle: str) -> str | None:
        return self._by_handle.get(handle)

    def rename(self, old: str, new: str) -> None:
        """Follow a handle rename. The token is unchanged — it identifies
        the process, and the process did not restart."""
        token = self._by_handle.pop(old, None)
        if token is None:
            return
        self._by_handle[new] = token
        self._by_token[token] = new

    def revoke(self, handle: str) -> None:
        token = self._by_handle.pop(handle, None)
        if token is not None:
            self._by_token.pop(token, None)


def caller_token() -> str | None:
    """The token on the in-flight HTTP request, if there is one.

    Never raises: ``get_http_headers()`` returns ``{}`` when no request is
    active, and an import failure must not take the plane down.
    """
    try:
        from fastmcp.server.dependencies import get_http_headers
        headers = get_http_headers() or {}
    except Exception:      # noqa: BLE001 — identity is best-effort in v1
        return None
    for name, value in headers.items():
        if name.lower() == HEADER:
            return value or None
    return None


def resolve_caller(tokens: SessionTokens | None) -> str | None:
    """The handle behind the in-flight call, or None when unverifiable."""
    if tokens is None:
        return None
    return tokens.resolve(caller_token())


def verified_handle(tokens: SessionTokens | None,
                    claimed: str | None) -> tuple[str, bool]:
    """``(handle, verified)`` for a call that also passed ``from_handle``.

    The token wins when it resolves — that is the whole point. Otherwise
    the claim stands unverified, which is v1's contract: we resolve and
    record, we do not refuse.
    """
    resolved = resolve_caller(tokens)
    if resolved is not None:
        return resolved, True
    return (claimed or ""), False
```

- [x] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_identity_tokens.py -q`
Expected: PASS (13 tests)

- [x] **Step 5: Commit**

```bash
git add src/aegis/mcp/identity.py tests/test_identity_tokens.py
git commit -m "feat(identity): per-spawn session tokens and header resolution"
```

---

### Task 2: Header resolution under a real FastMCP request

Task 1 tested the store with no request context. This one proves the header actually reaches `caller_token()` through a live FastMCP HTTP server — everything downstream is worthless if it does not.

**Files:**
- Test: `tests/test_identity_http.py`

**Interfaces:**
- Consumes: `HEADER`, `SessionTokens`, `resolve_caller` (Task 1).
- Produces: nothing — this task is a proof, not an API.

- [x] **Step 1: Write the failing test**

Create `tests/test_identity_http.py`:

```python
from __future__ import annotations

import pytest
from fastmcp import Client, FastMCP

from aegis.mcp.identity import HEADER, SessionTokens, resolve_caller


def _server(tokens: SessionTokens) -> FastMCP:
    server = FastMCP("identity-probe")

    @server.tool
    async def whoami() -> str:
        """Report the resolved caller, or the empty string."""
        return resolve_caller(tokens) or ""

    return server


@pytest.mark.asyncio
async def test_the_header_reaches_the_server_and_resolves():
    tokens = SessionTokens()
    token = tokens.mint("alice")
    server = _server(tokens)
    async with Client(server, headers={HEADER: token}) as client:
        out = await client.call_tool("whoami", {})
    assert out.data == "alice"


@pytest.mark.asyncio
async def test_a_capitalised_header_still_resolves():
    """HTTP header names are case-insensitive; the lookup must be too."""
    tokens = SessionTokens()
    token = tokens.mint("alice")
    server = _server(tokens)
    async with Client(server, headers={"X-Aegis-Session": token}) as client:
        out = await client.call_tool("whoami", {})
    assert out.data == "alice"


@pytest.mark.asyncio
async def test_no_header_resolves_to_nothing_rather_than_failing():
    tokens = SessionTokens()
    tokens.mint("alice")
    server = _server(tokens)
    async with Client(server) as client:
        out = await client.call_tool("whoami", {})
    assert out.data == ""


@pytest.mark.asyncio
async def test_a_stale_token_resolves_to_nothing():
    tokens = SessionTokens()
    stale = tokens.mint("alice")
    tokens.revoke("alice")
    server = _server(tokens)
    async with Client(server, headers={HEADER: stale}) as client:
        out = await client.call_tool("whoami", {})
    assert out.data == ""
```

- [x] **Step 2: Run tests to verify the header path is real**

Run: `uv run python -m pytest tests/test_identity_http.py -q`
Expected: PASS if FastMCP's in-memory client propagates headers; **FAIL on the first two if it does not**.

If the first two fail with an empty result, the in-memory transport does not carry headers. That is a transport limitation, not a design failure — switch `_server` to an HTTP transport by running the server on a free port and connecting with `Client(f"http://127.0.0.1:{port}/mcp/", headers=...)`, reusing the readiness-wait loop from `AegisMCP.start` (`mcp/runtime.py:60-68`). Do not delete the assertions; the point of this task is that the header survives a real request.

- [x] **Step 3: Commit**

```bash
git add tests/test_identity_http.py
git commit -m "test(identity): header round-trips through a live FastMCP request"
```

---

### Task 3: The registry lives on `AegisMCP`, and the ledger uses it

Wires the store to the one object that already owns the plane, and closes the attribution hole for every tool that takes no `from_handle`.

**Files:**
- Modify: `src/aegis/mcp/runtime.py`
- Modify: `src/aegis/mcp/server.py` — `build_server` signature and the `CommsMiddleware` construction (`:588-603`)
- Modify: `src/aegis/comms/middleware.py` — `__init__` and `_record` (`:41-73`)
- Test: `tests/test_identity_ledger.py`

**Interfaces:**
- Consumes: `SessionTokens`, `resolve_caller` (Task 1).
- Produces:
  - `AegisMCP.tokens: SessionTokens`
  - `build_server(bridge, tokens: SessionTokens | None = None) -> FastMCP`
  - `CommsMiddleware(ledger, tokens: SessionTokens | None = None)`

- [x] **Step 1: Write the failing test**

Create `tests/test_identity_ledger.py`:

```python
from __future__ import annotations

from aegis.comms.middleware import CommsMiddleware
from aegis.comms.persistence import CommsLedger
from aegis.mcp.identity import SessionTokens


def _mw(tmp_path, tokens=None):
    return CommsMiddleware(CommsLedger(tmp_path), tokens=tokens)


def _rows(tmp_path):
    return CommsLedger(tmp_path).read()


def test_a_resolved_token_attributes_a_tool_that_takes_no_from_handle(
        tmp_path, monkeypatch):
    tokens = SessionTokens()
    tokens.mint("alice")
    monkeypatch.setattr("aegis.mcp.identity.caller_token",
                        lambda: tokens.token_for("alice"))
    mw = _mw(tmp_path, tokens)
    mw._record("aegis_list_sessions", {}, "c1", "2026-08-26T00:00:00Z",
               0.0, "ok", None)
    assert [r["from"] for r in _rows(tmp_path)] == ["alice"]


def test_the_token_wins_over_a_wrong_from_handle(tmp_path, monkeypatch):
    tokens = SessionTokens()
    tokens.mint("alice")
    monkeypatch.setattr("aegis.mcp.identity.caller_token",
                        lambda: tokens.token_for("alice"))
    mw = _mw(tmp_path, tokens)
    mw._record("aegis_handoff", {"from_handle": "bob"}, "c1",
               "2026-08-26T00:00:00Z", 0.0, "ok", None)
    assert [r["from"] for r in _rows(tmp_path)] == ["alice"]


def test_without_a_token_the_argument_still_attributes(tmp_path):
    mw = _mw(tmp_path, SessionTokens())
    mw._record("aegis_handoff", {"from_handle": "bob"}, "c1",
               "2026-08-26T00:00:00Z", 0.0, "ok", None)
    assert [r["from"] for r in _rows(tmp_path)] == ["bob"]


def test_with_neither_the_row_stays_honestly_unattributed(tmp_path):
    mw = _mw(tmp_path, SessionTokens())
    mw._record("aegis_list_sessions", {}, "c1", "2026-08-26T00:00:00Z",
               0.0, "ok", None)
    assert [r["from"] for r in _rows(tmp_path)] == [""]


def test_the_runtime_owns_a_token_store():
    from aegis.mcp.runtime import AegisMCP
    assert AegisMCP().tokens.resolve("nope") is None
```

- [x] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_identity_ledger.py -q`
Expected: FAIL — `TypeError: CommsMiddleware.__init__() got an unexpected keyword argument 'tokens'`

- [x] **Step 3: Give `AegisMCP` the store**

In `src/aegis/mcp/runtime.py`, add to `AegisMCP.__init__` after `self._bridge`:

```python
        self.tokens = SessionTokens()
```

Import at the top of the module:

```python
from aegis.mcp.identity import SessionTokens
```

And pass it through in `start()`:

```python
        self._server = build_server(bridge, tokens=self.tokens)
```

- [x] **Step 4: Thread it into `build_server`**

In `src/aegis/mcp/server.py`, change the signature at `:588`:

```python
def build_server(bridge: AppBridge, tokens=None) -> FastMCP:
```

and the middleware construction at `:601`:

```python
    server.add_middleware(CommsMiddleware(CommsLedger(
        Path(_state_dir) if _state_dir
        else Path.cwd() / ".aegis" / "state"), tokens=tokens))
```

- [x] **Step 5: Resolve in the middleware**

In `src/aegis/comms/middleware.py`, replace `__init__`:

```python
    def __init__(self, ledger: CommsLedger, tokens=None) -> None:
        self._ledger = ledger
        self._tokens = tokens
```

and the `from_handle` line inside `_record`:

```python
                from_handle=self._from(args),
```

adding the helper beside `_record`:

```python
    def _from(self, args: dict) -> str:
        """Who called. The token wins when it resolves; the argument is
        the fallback for one release; unattributed stays honest.

        Resolution is inside the ledger's try/except by construction —
        `_record` already swallows-and-logs — so a broken identity path
        cannot fail a tool call.
        """
        from aegis.mcp.identity import resolve_caller
        return (resolve_caller(self._tokens)
                or str(args.get("from_handle") or ""))
```

- [x] **Step 6: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_identity_ledger.py -q`
Expected: PASS (5 tests)

- [x] **Step 7: Run the comms and MCP suites for regressions**

Run: `uv run python -m pytest tests/ -q -m "not live" -k "comms or mcp"`
Expected: PASS

- [x] **Step 8: Commit**

```bash
git add src/aegis/mcp/runtime.py src/aegis/mcp/server.py \
        src/aegis/comms/middleware.py tests/test_identity_ledger.py
git commit -m "feat(identity): resolve the comms ledger's from-handle from the token"
```

---

### Task 4: Injection into the Claude config, and the SSH placeholder

The token has to reach the subprocess. This task also carries the trap that would otherwise break every SSH-hosted session silently.

**Files:**
- Modify: `src/aegis/mcp/server.py` — `mcp_config_json` (`:2322`)
- Modify: `src/aegis/drivers/claude.py` — `build_argv` (`:285`)
- Modify: `src/aegis/hosts/launcher.py` — `_substitute_mcp_url` (`:129`)
- Test: `tests/test_identity_injection.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `mcp_config_json(url: str, token: str = "") -> str`
  - `ClaudeDriver.build_argv(agent, cwd, mcp_url, handle, launcher=LOCAL, token="")`
  - `_substitute_mcp_url(argv: list[str], url: str, token: str = "") -> list[str]`

- [x] **Step 1: Write the failing test**

Create `tests/test_identity_injection.py`:

```python
from __future__ import annotations

import json

from aegis.hosts.launcher import _substitute_mcp_url
from aegis.mcp import mcp_config_json
from aegis.mcp.identity import HEADER


def _headers(blob: str) -> dict:
    return json.loads(blob)["mcpServers"]["aegis"].get("headers", {})


def test_config_without_a_token_carries_no_headers():
    assert _headers(mcp_config_json("http://x/mcp/")) == {}


def test_config_with_a_token_carries_the_header():
    blob = mcp_config_json("http://x/mcp/", "tok123")
    assert _headers(blob) == {"X-Aegis-Session": "tok123"}


def test_the_header_name_matches_what_the_server_looks_for():
    blob = mcp_config_json("http://x/mcp/", "tok123")
    assert list(_headers(blob))[0].lower() == HEADER


def test_substitute_matches_a_tokenful_placeholder():
    """The launcher rewrites the one argv element equal to the
    placeholder. If it builds the placeholder without the token while
    build_argv baked one in, nothing matches, no substitution happens,
    and the remote session gets an empty MCP URL — no aegis plane at
    all, silently."""
    argv = ["claude", "--mcp-config", mcp_config_json("", "tok123"), "-p"]
    out = _substitute_mcp_url(argv, "http://real/mcp/", "tok123")
    assert json.loads(out[2])["mcpServers"]["aegis"]["url"] == "http://real/mcp/"
    assert _headers(out[2]) == {"X-Aegis-Session": "tok123"}


def test_substitute_still_works_with_no_token():
    argv = ["claude", "--mcp-config", mcp_config_json(""), "-p"]
    out = _substitute_mcp_url(argv, "http://real/mcp/")
    assert json.loads(out[2])["mcpServers"]["aegis"]["url"] == "http://real/mcp/"


def test_substitute_leaves_unrelated_arguments_alone():
    argv = ["claude", "-p", "--model", "opus", mcp_config_json("", "t")]
    out = _substitute_mcp_url(argv, "http://real/mcp/", "t")
    assert out[:4] == ["claude", "-p", "--model", "opus"]


def test_build_argv_bakes_the_token_into_the_config():
    from aegis.config import Agent
    from aegis.drivers.claude import ClaudeDriver
    agent = Agent(harness="claude-code", model="opus", effort="medium",
                  permission="auto")
    argv = ClaudeDriver().build_argv(agent, ".", "http://x/mcp/", "alice",
                                     token="tok123")
    blob = argv[argv.index("--mcp-config") + 1]
    assert _headers(blob) == {"X-Aegis-Session": "tok123"}
```

- [x] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_identity_injection.py -q`
Expected: FAIL — `TypeError: mcp_config_json() takes 1 positional argument but 2 were given`

- [x] **Step 3: Extend `mcp_config_json`**

In `src/aegis/mcp/server.py`, replace the function at `:2322`:

```python
def mcp_config_json(url: str, token: str = "") -> str:
    """The `--mcp-config` blob for one spawn.

    ``token`` is that spawn's identity. It rides as a header because that
    is the one per-session channel both harness families already carry —
    Claude Code documents `--header` for http transport, and ACP's
    `mcp_servers` entries have a headers list. The canonical spelling is
    capitalised here for readability; the server matches case-insensitively.
    """
    entry: dict = {"type": "http", "url": url}
    if token:
        entry["headers"] = {"X-Aegis-Session": token}
    return json.dumps({"mcpServers": {"aegis": entry}})
```

- [x] **Step 4: Thread the token through `build_argv`**

In `src/aegis/drivers/claude.py`, change the signature at `:285` and the one argv line:

```python
    def build_argv(self, agent: Agent, cwd: str,
                   mcp_url: str, handle: str,
                   launcher: Launcher = LOCAL,
                   token: str = "") -> list[str]:
```

```python
            "--mcp-config", mcp_config_json(mcp_url, token),
```

- [x] **Step 5: Fix the launcher placeholder**

In `src/aegis/hosts/launcher.py`, replace `_substitute_mcp_url`:

```python
def _substitute_mcp_url(argv: list[str], url: str,
                        token: str = "") -> list[str]:
    """Fill in the MCP URL that wasn't known when argv was built.

    ``build_argv`` bakes ``mcp_config_json(mcp_url, token)`` into the argv
    at session-construction time, but a remote session's URL depends on
    the port sshd allocates when the tunnel opens — which is later. The
    registry hands drivers an empty URL as a placeholder; this rewrites
    exactly that one element just before exec. Matching on anything
    looser (an empty string, a substring) would rewrite unrelated
    arguments.

    The token must be passed here too: the placeholder is compared by
    equality, so a placeholder built without the token no longer matches
    the argv element that has one, nothing is substituted, and the remote
    session comes up with an empty MCP URL — no aegis plane, no error.
    """
    from aegis.mcp import mcp_config_json
    placeholder = mcp_config_json("", token)
    real = mcp_config_json(url, token)
    return [real if a == placeholder else a for a in argv]
```

- [x] **Step 6: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_identity_injection.py -q`
Expected: PASS (7 tests)

- [x] **Step 7: Find every caller of the two changed signatures**

Run: `grep -rn "_substitute_mcp_url\|build_argv(" src/ tests/`
Update each call site to pass the token where one is available; both parameters default to `""`, so a caller that has no token keeps working unchanged. The `SshLauncher.spawn` path is the one that must pass it.

- [x] **Step 8: Run the hosts and driver suites for regressions**

Run: `uv run python -m pytest tests/ -q -m "not live" -k "hosts or launcher or driver or claude"`
Expected: PASS

- [x] **Step 9: Commit**

```bash
git add src/aegis/mcp/server.py src/aegis/drivers/claude.py \
        src/aegis/hosts/launcher.py tests/test_identity_injection.py
git commit -m "feat(identity): inject the session token into the Claude MCP config"
```

---

### Task 5: Injection into the ACP `mcp_servers` entry

The other harness family. Its headers field already exists and is empty.

**Files:**
- Modify: `src/aegis/drivers/acp.py` — `AcpSession.__init__` (`:314`) and the `mcp_servers` construction (`:490-494`)
- Test: `tests/test_identity_acp.py`

**Interfaces:**
- Consumes: `HEADER` (Task 1).
- Produces: `AcpSession(..., token: str = "")`, stored as `self._token`, and an `mcp_servers` entry whose `headers` is `[{"name": "X-Aegis-Session", "value": token}]`.

- [x] **Step 1: Write the failing test**

Create `tests/test_identity_acp.py`:

```python
from __future__ import annotations

from aegis.drivers.acp import AcpSession
from aegis.mcp.identity import HEADER


def _entry(token: str, url: str = "http://x/mcp/") -> dict:
    sess = AcpSession.__new__(AcpSession)
    sess._mcp_url = url
    sess._token = token
    return sess._mcp_servers()[0]


def test_headers_are_a_list_of_name_value_pairs():
    """acp.schema.McpServerHttp.headers is List[HttpHeader], and
    HttpHeader has `name` and `value`. A mapping is the wrong shape."""
    entry = _entry("tok123")
    assert isinstance(entry["headers"], list)
    assert entry["headers"] == [{"name": "X-Aegis-Session",
                                 "value": "tok123"}]


def test_the_header_name_matches_what_the_server_looks_for():
    assert _entry("tok123")["headers"][0]["name"].lower() == HEADER


def test_no_token_means_no_headers():
    assert _entry("")["headers"] == []


def test_no_url_means_no_servers():
    sess = AcpSession.__new__(AcpSession)
    sess._mcp_url = ""
    sess._token = "tok123"
    assert sess._mcp_servers() == []
```

- [x] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_identity_acp.py -q`
Expected: FAIL — `AttributeError: 'AcpSession' object has no attribute '_mcp_servers'`

- [x] **Step 3: Extract the entry builder and fill the headers**

In `src/aegis/drivers/acp.py`, add the method to `AcpSession`:

```python
    def _mcp_servers(self) -> list[dict]:
        """The `mcp_servers` argument for new_session / load_session.

        `headers` is a LIST of {name, value} — `acp.schema.HttpHeader` —
        not a mapping. A dict here is accepted by our own dict-shaped code
        and then ignored downstream, which reads as "identity silently
        stopped working" rather than as an error.
        """
        if not self._mcp_url:
            return []
        token = getattr(self, "_token", "")
        headers = ([{"name": "X-Aegis-Session", "value": token}]
                   if token else [])
        return [{"type": "http", "name": "aegis",
                 "url": self._mcp_url, "headers": headers}]
```

Replace the inline construction at `:490-494` with:

```python
            mcp_servers = self._mcp_servers()
```

- [x] **Step 4: Store the token at construction**

In `AcpSession.__init__` (`:314`), add `token: str = ""` as a keyword parameter and store it beside the handle at `:324`:

```python
        self._token = token
```

- [x] **Step 5: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_identity_acp.py -q`
Expected: PASS (4 tests)

- [x] **Step 6: Run the ACP and lovelaice suites for regressions**

Run: `uv run python -m pytest tests/ -q -m "not live" -k "acp or lovelaice or opencode or gemini"`
Expected: PASS

- [x] **Step 7: Commit**

```bash
git add src/aegis/drivers/acp.py tests/test_identity_acp.py
git commit -m "feat(identity): inject the session token into ACP mcp_servers"
```

---

### Task 6: Lifecycle — mint on spawn, follow a rename, revoke on close

Without this the store is always empty and every other task is inert.

**Files:**
- Modify: `src/aegis/drivers/base.py:84,88,98` — `session` / `resume` / `fork`
- Modify: `src/aegis/drivers/claude.py:368,376,389` — the same three
- Modify: `src/aegis/drivers/acp.py:696,709` — `session` / `resume`
- Modify: `src/aegis/cli.py:61` — `make_session`
- Modify: `src/aegis/core/manager.py` — the two spawn paths (`:191-202`, `:319-320`), `close` (`:404`), `rename_handle` (`:488`)
- Test: `tests/test_identity_lifecycle.py`

**Interfaces:**
- Consumes: `SessionTokens` (Task 1), `AegisMCP.tokens` (Task 3), `build_argv(..., token)` (Task 4), `AcpSession(..., token)` (Task 5).
- Produces:
  - `HarnessDriver.session(agent, cwd, mcp_url, handle, launcher=LOCAL, token="")` and the same trailing `token: str = ""` on `resume` and `fork`
  - `make_session(profile, mcp_url, handle, fork_from=None, place=None, resume_from=None, token="")`
  - `SessionManager` calls `mint` / `rename` / `revoke` on `self._mcp.tokens`

> **The token is keyword-with-default the whole way down.** `SessionFactory` is
> `Callable[[object, str, str], object]` and the manager explicitly supports
> plain 3-argument factories, so the token goes into the conditional `extra`
> dict beside `fork_from` and `place` — a positional argument would break every
> existing factory and every driver stub in the test suite.

- [x] **Step 1: Write the failing test**

Create `tests/test_identity_lifecycle.py`:

```python
from __future__ import annotations

from aegis.mcp.identity import SessionTokens


class _FakeMCP:
    def __init__(self) -> None:
        self.url = "http://x/mcp/"
        self.tokens = SessionTokens()


def test_a_spawned_handle_has_a_token():
    """The token must exist before the subprocess starts — it is baked
    into that subprocess's argv."""
    mcp = _FakeMCP()
    token = mcp.tokens.mint("alice")
    assert mcp.tokens.resolve(token) == "alice"


def test_renaming_a_session_keeps_its_token_resolving():
    mcp = _FakeMCP()
    token = mcp.tokens.mint("alice")
    mcp.tokens.rename("alice", "carol")
    assert mcp.tokens.resolve(token) == "carol"


def test_closing_a_session_revokes_its_token():
    mcp = _FakeMCP()
    token = mcp.tokens.mint("alice")
    mcp.tokens.revoke("alice")
    assert mcp.tokens.resolve(token) is None


@pytest.mark.asyncio
async def test_spawn_hands_the_factory_a_token_that_resolves_to_the_handle():
    """The end-to-end contract of this task: whatever handle the manager
    generates, the factory is handed a token that resolves back to it —
    and it is handed it at spawn, because the token is baked into the
    subprocess argv the factory is about to build."""
    from aegis.config import Agent
    from aegis.core.manager import SessionManager

    seen: dict = {}

    def make_session(profile, mcp_url, handle, token="", **kw):
        seen["handle"] = handle
        seen["token"] = token
        return _FakeSession()

    mcp = _FakeMCP()
    mgr = SessionManager(
        {"main": Agent(harness="claude-code", model="opus",
                       effort="medium", permission="auto")},
        "main", make_session, mcp)
    await mgr.spawn("main")

    assert seen["token"], "the factory was handed no token"
    assert mcp.tokens.resolve(seen["token"]) == seen["handle"]


def test_a_three_argument_factory_still_works():
    """SessionFactory is typed (object, str, str) and the manager says
    plain callables must keep working, so the token rides `extra` and is
    omitted when there is nothing to pass."""
    from aegis.config import Agent
    from aegis.core.manager import SessionManager

    def legacy(profile, mcp_url, handle):
        return _FakeSession()

    SessionManager(
        {"main": Agent(harness="claude-code", model="opus",
                       effort="medium", permission="auto")},
        "main", legacy, None)
```

Add `import pytest` at the top of the file, and the fake session beside `_FakeMCP`:

```python
class _FakeSession:
    handle = "fake"

    def add_event_observer(self, *a, **kw): return None
    def add_state_observer(self, *a, **kw): return None
    def add_inbox_observer(self, *a, **kw): return None
    def add_close_observer(self, *a, **kw): return None
    async def close(self): return None
```

- [x] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_identity_lifecycle.py -q`
Expected: the first three PASS (they exercise Task 1's store directly and are here to pin the lifecycle contract); `test_spawn_hands_the_factory_a_token_that_resolves_to_the_handle` FAILS on `assert seen["token"]`, because nothing mints yet.

If `_FakeSession` needs more of the session surface, add the missing no-op methods — `spawn` attaches observers and a session log to whatever the factory returns.

- [x] **Step 3: Thread `token` down the driver seam**

In `src/aegis/drivers/base.py`, add `token: str = ""` as the last keyword parameter of `session` (`:84`), `resume` (`:88`) and `fork` (`:98`). Mirror it on the concrete implementations — `claude.py:368,376,389` and `acp.py:696,709` — forwarding to `build_argv(..., token=token)` on the Claude side and `AcpSession(..., token=token)` on the ACP side. A driver that has no use for it accepts and drops it; the default keeps every existing call site working.

In `src/aegis/cli.py:61`, add `token=""` to `make_session` and forward it on all three branches:

```python
    def make_session(profile, mcp_url, handle, fork_from=None, place=None,
                     resume_from=None, token=""):
        place = place or Place("local", cwd)
        if hosts is not None:
            launcher, url = hosts.launcher_for(place, mcp_url)
        else:
            launcher, url = LocalLauncher(local_root=cwd), mcp_url
        drv = get_driver(profile.harness)
        if fork_from is not None:
            return drv.fork(profile, place.cwd, url, handle, fork_from,
                            launcher, token=token)
        if resume_from is not None:
            return drv.resume(profile, place.cwd, url, handle, resume_from,
                              launcher, token=token)
        return drv.session(profile, place.cwd, url, handle, launcher,
                           token=token)
```

- [x] **Step 4: Mint at both spawn sites**

In `src/aegis/core/manager.py`, at the cold-spawn site (`:191`), mint alongside the URL:

```python
        url = self._mcp.url if self._mcp is not None else ""
        token = (self._mcp.tokens.mint(h) if self._mcp is not None else "")
```

and add it to the same conditional `extra` dict that already carries `fork_from` and `place`:

```python
        if token:
            extra["token"] = token
```

Do the same at the reconnect site (`:319-320`), minting for `handle` and passing `token=token` into that `self._make_session(...)` call. Mint **before** the factory runs — a token minted afterwards is one the subprocess never receives.

- [x] **Step 5: Follow renames and revoke on close**

In `rename_handle` (`:488`), after the existing rename bookkeeping:

```python
        if self._mcp is not None:
            self._mcp.tokens.rename(old, new)
```

In `close` (`:404`), after the session is torn down:

```python
        if self._mcp is not None:
            self._mcp.tokens.revoke(handle)
```

- [x] **Step 6: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_identity_lifecycle.py -q`
Expected: PASS (4 tests)

- [x] **Step 7: Run the manager and session suites for regressions**

Run: `uv run python -m pytest tests/ -q -m "not live" -k "manager or session or spawn or rename"`
Expected: PASS

- [x] **Step 8: Commit**

```bash
git add src/aegis/core/manager.py tests/test_identity_lifecycle.py
git commit -m "feat(identity): mint on spawn, follow renames, revoke on close"
```

---

### Task 7: `verified_handle` on the gating tools

The payoff. Four tools currently trust the caller for the handle they gate on; this is what mandatory file claims will stand on.

**Files:**
- Modify: `src/aegis/mcp/server.py` — `aegis_claim`, `aegis_release`, `aegis_close`, `aegis_loop_stop`
- Test: `tests/test_identity_gating.py`

**Interfaces:**
- Consumes: `verified_handle` (Task 1), `tokens` in the `build_server` closure (Task 3).
- Produces: no new API — the four tools resolve `from_handle` through `verified_handle` before using it.

- [x] **Step 1: Write the failing test**

Create `tests/test_identity_gating.py`:

```python
from __future__ import annotations

from aegis.mcp.identity import SessionTokens, verified_handle


def test_a_resolving_token_overrides_a_wrong_claim(monkeypatch):
    tokens = SessionTokens()
    tokens.mint("alice")
    monkeypatch.setattr("aegis.mcp.identity.caller_token",
                        lambda: tokens.token_for("alice"))
    handle, verified = verified_handle(tokens, "bob")
    assert (handle, verified) == ("alice", True)


def test_an_absent_token_leaves_the_claim_standing(monkeypatch):
    monkeypatch.setattr("aegis.mcp.identity.caller_token", lambda: None)
    handle, verified = verified_handle(SessionTokens(), "bob")
    assert (handle, verified) == ("bob", False)


def test_an_agreeing_token_is_verified(monkeypatch):
    tokens = SessionTokens()
    tokens.mint("alice")
    monkeypatch.setattr("aegis.mcp.identity.caller_token",
                        lambda: tokens.token_for("alice"))
    handle, verified = verified_handle(tokens, "alice")
    assert (handle, verified) == ("alice", True)


def test_v1_never_refuses(monkeypatch):
    """The contract for this release: resolve and record, do not reject.
    A caller with no token still gets its claimed handle back."""
    monkeypatch.setattr("aegis.mcp.identity.caller_token", lambda: None)
    handle, _ = verified_handle(SessionTokens(), "bob")
    assert handle == "bob"
```

And the test that actually proves the four tools adopted it — the helper being
correct says nothing about whether anything calls it:

```python
import pytest
from fastmcp import Client

from aegis.mcp.identity import HEADER
from aegis.mcp.server import build_server


@pytest.mark.asyncio
async def test_aegis_claim_records_under_the_token_not_the_argument(
        locks_bridge):
    """The payoff, stated as a behaviour: an agent that passes someone
    else's handle claims under its OWN. Without this the whole feature is
    a helper nobody calls."""
    tokens = SessionTokens()
    token = tokens.mint("alice")
    server = build_server(locks_bridge, tokens=tokens)
    async with Client(server, headers={HEADER: token}) as client:
        await client.call_tool("aegis_claim", {
            "paths": ["src/x.py"], "from_handle": "bob"})
    holders = [c.handle for c in locks_bridge.registry.active()]
    assert holders == ["alice"], f"claimed as {holders}, not alice"
```

`locks_bridge` is a fixture returning `make_locks_bridge(...)` over a live
handle set containing `alice` and `bob`; build it the way
`tests/test_locks_bridge.py` already does rather than inventing a second shape.
If `build_server` needs more of the `AppBridge` surface than the locks bridge
provides, reuse whichever fake `tests/test_mcp_bridge.py` supplies and attach
the locks bridge to it.

- [x] **Step 2: Run tests to confirm the contract holds**

Run: `uv run python -m pytest tests/test_identity_gating.py -q`
Expected: PASS (4 tests). These exercise Task 1's helper directly, so they are green before the tools adopt it — they pin the contract the adoption depends on. **If any fails, Task 1 is wrong; fix it before touching the tools.** The failing-first signal for this task is the mutation check in Step 5, which is what actually proves the four tools call the helper.

- [x] **Step 3: Adopt it in the four tools**

In `src/aegis/mcp/server.py`, locate each of `aegis_claim`, `aegis_release`, `aegis_close`, `aegis_loop_stop` (`grep -n "def aegis_claim\|def aegis_release\|def aegis_close\|def aegis_loop_stop" src/aegis/mcp/server.py`). In each, immediately after the signature, replace the direct use of the parameter:

```python
        from aegis.mcp.identity import verified_handle
        from_handle, _verified = verified_handle(tokens, from_handle)
```

`tokens` is in scope from the `build_server` closure. Every later use of `from_handle` in that function is now the resolved value.

- [x] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_identity_gating.py -q`
Expected: PASS (4 tests)

- [x] **Step 5: Mutation-check the override, twice**

Two separate mutations, because they catch different failures:

**(a) The helper.** Change `verified_handle` so the resolved branch returns
`claimed` instead of `resolved`, then run:

Run: `uv run python -m pytest tests/test_identity_gating.py tests/test_identity_ledger.py -q`
Expected: FAIL on `test_a_resolving_token_overrides_a_wrong_claim`,
`test_the_token_wins_over_a_wrong_from_handle`, and
`test_aegis_claim_records_under_the_token_not_the_argument`. Revert, confirm PASS.

**(b) The adoption.** Revert (a), then remove the `verified_handle` line from
`aegis_claim` alone and run:

Run: `uv run python -m pytest tests/test_identity_gating.py -q`
Expected: FAIL on `test_aegis_claim_records_under_the_token_not_the_argument`
and **nothing else**. Revert, confirm PASS.

If (b) passes with the line removed, no test observes the adoption and the other
three tools are unverified too — write the equivalent assertion for at least one
more tool before proceeding. A helper that is correct and uncalled is the
failure mode this step exists to catch.

- [x] **Step 6: Run the full hermetic suite**

Run: `uv run python -m pytest -q -m "not live"`
Expected: PASS. A red run is a regression, not noise to re-roll.

- [x] **Step 7: Commit**

```bash
git add src/aegis/mcp/server.py tests/test_identity_gating.py
git commit -m "feat(identity): gate claim/release/close/loop_stop on the resolved handle"
```

---

### Task 8: The live round-trip

The one test that proves the chain end to end: a real `claude` subprocess, a real MCP config, a real header, a real server.

**Files:**
- Test: `tests/test_identity_live.py`

**Interfaces:**
- Consumes: everything above.
- Produces: nothing.

- [x] **Step 1: Write the test**

Create `tests/test_identity_live.py`:

```python
from __future__ import annotations

import shutil

import pytest

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(shutil.which("claude") is None,
                       reason="claude CLI not on PATH"),
]


@pytest.mark.asyncio
async def test_a_real_claude_carries_the_token_to_a_real_server(tmp_path):
    """Everything else in this feature can be green while the header is
    dropped somewhere between the config file and the request. This is
    the only test that would notice."""
    import asyncio

    from fastmcp import FastMCP

    from aegis.mcp.identity import SessionTokens, resolve_caller

    tokens = SessionTokens()
    token = tokens.mint("alice")
    seen: list[str | None] = []

    server = FastMCP("identity-live")

    @server.tool
    async def report_caller() -> str:
        """Record who aegis thinks is calling. Call this tool once."""
        who = resolve_caller(tokens)
        seen.append(who)
        return who or "(unattributed)"

    port = 8765
    task = asyncio.create_task(
        server.run_http_async(host="127.0.0.1", port=port,
                              show_banner=False))
    try:
        await asyncio.sleep(1.0)
        from aegis.mcp import mcp_config_json
        proc = await asyncio.create_subprocess_exec(
            "claude", "-p", "--output-format", "json",
            "--mcp-config", mcp_config_json(
                f"http://127.0.0.1:{port}/mcp/", token),
            "--strict-mcp-config",
            "--permission-mode", "bypassPermissions",
            "Call the report_caller tool exactly once, then stop.",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(tmp_path))
        await asyncio.wait_for(proc.communicate(), timeout=180)
    finally:
        task.cancel()

    assert seen, "claude never called the tool — the MCP attach failed"
    assert seen[0] == "alice", f"header did not survive: got {seen[0]!r}"
```

- [x] **Step 2: Run it against the real CLI**

Run: `uv run python -m pytest tests/test_identity_live.py -q -m live`
Expected: PASS. If `seen` is empty the MCP attach failed (check the config shape); if `seen[0]` is `None` the header was dropped in transit, which is the finding this task exists to produce.

- [x] **Step 3: Commit**

```bash
git add tests/test_identity_live.py
git commit -m "test(identity): live round-trip — real claude, real header, real server"
```

---

## Follow-ups, deliberately not in this plan

- **Rejecting unauthenticated calls.** v1 resolves and records. The config flag comes once the mismatch log shows every real caller carries a token.
- **Removing the `from_handle` parameter.** A breaking change with its own deprecation note, one release later.
- **A mismatch counter on the status bar.** Nothing consumes `_verified` yet beyond the override itself; wire a surface when there is something worth watching.
