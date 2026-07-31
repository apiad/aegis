"""--remote v1 has no fork verb on the WS protocol.

AppBridge grew ``fork``, so every implementation owes an answer. The
honest one here is the house's RemoteUnsupportedError, which the TUI
already catches to show its 'not available in --remote v1' banner —
rather than an AttributeError from a method that was never written.
"""
from __future__ import annotations

import pytest

from aegis.tui.remote_manager import RemoteSessionManager, RemoteUnsupportedError


@pytest.mark.asyncio
async def test_remote_fork_raises_the_house_unsupported_error():
    mgr = RemoteSessionManager.__new__(RemoteSessionManager)
    with pytest.raises(RemoteUnsupportedError, match="remote v1"):
        await mgr.fork("some-handle", prompt="go")
