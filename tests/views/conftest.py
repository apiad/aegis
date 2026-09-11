"""Shared fakes for tests/views.

``mcp=None`` is not a usable default for a view: ``AegisApp`` calls
``self._mcp.bind(self)`` at ``app.py:499`` and ``await self._mcp.start()`` at
``:587`` unconditionally on the local plane, so a ``None`` raises
``AttributeError`` before any assertion in a test can run. Every test here that
builds a view imports ``FakeMCP`` from this module.

Import it explicitly — ``from tests.views.conftest import FakeMCP``. A conftest
is a normal module, and pytest does not inject its names into test modules.
"""
from __future__ import annotations


class _FakeTokens:
    """``SessionManager`` mints a per-session token (`core/manager.py:237`)."""

    def mint(self, handle: str) -> str:
        return "test-token"

    def revoke(self, handle: str) -> None:
        return None

    def rename(self, old: str, new: str) -> None:
        return None


class FakeMCP:
    """Enough MCP for the local plane.

    ``AegisApp`` binds it, starts it, reads ``.url`` and ``.port``, and stops it
    (`app.py:499`, `:587`, `:591`, `:672`, `:1711`).
    """

    def __init__(self) -> None:
        self.port = 0
        self.url = "http://127.0.0.1:0/mcp/"
        self.bound = None
        self.tokens = _FakeTokens()

    def bind(self, bridge) -> None:
        self.bound = bridge

    async def start(self) -> None:
        self.port = 12345

    async def stop(self) -> None:
        return None
