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

# The canonical spelling for the wire, where a human may read it.
HEADER_NAME = "X-Aegis-Session"


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
