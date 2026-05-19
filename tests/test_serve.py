from __future__ import annotations

import asyncio

import pytest

from aegis.cli import _serve
from aegis.mcp.bridge import AppBridge


class FakeMCP:
    url = "http://x"

    def __init__(self):
        self.started = False
        self.stopped = False
        self.bound = None

    def bind(self, b):
        self.bound = b

    async def start(self):
        self.started = True

    async def stop(self):
        self.stopped = True


@pytest.mark.asyncio
async def test_serve_headless_binds_and_stops():
    mcp = FakeMCP()
    stop = asyncio.Event()
    asyncio.get_event_loop().call_soon(stop.set)
    await _serve(agents={"default": 1}, default_agent="default",
                 make_session=lambda p, u, h: None, mcp=mcp,
                 tg=None, stop=stop)
    assert mcp.started and mcp.stopped
    assert isinstance(mcp.bound, AppBridge)
