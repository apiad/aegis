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


@pytest.mark.asyncio
async def test_serve_wires_inbox_and_queue_manager():
    from aegis.queue import InboxRouter, QueueManager

    mcp = FakeMCP()
    stop = asyncio.Event()
    asyncio.get_event_loop().call_soon(stop.set)
    await _serve(agents={"default": 1}, default_agent="default",
                 make_session=lambda p, u, h: None, mcp=mcp,
                 tg=None, stop=stop)
    bridge = mcp.bound
    assert isinstance(bridge.inbox_router, InboxRouter)
    assert isinstance(bridge.queue_manager, QueueManager)


@pytest.mark.asyncio
async def test_serve_passes_queues_into_queue_manager():
    from aegis.queue import InboxRouter, Queue

    mcp = FakeMCP()
    stop = asyncio.Event()
    asyncio.get_event_loop().call_soon(stop.set)
    queues = {"impl": Queue(name="impl", agent_profile="claude-impl",
                            max_parallel=2)}
    await _serve(agents={"default": 1}, default_agent="default",
                 make_session=lambda p, u, h: None, mcp=mcp,
                 tg=None, stop=stop, queues=queues)
    bridge = mcp.bound
    assert isinstance(bridge.inbox_router, InboxRouter)
    assert bridge.queue_manager.list_queues() == ["impl"]


def test_serve_refuses_to_start_when_queues_invalid(tmp_path, monkeypatch):
    """The CLI surfaces a load_queues ConfigError at boot — clean exit."""
    from typer.testing import CliRunner

    from aegis.cli import app

    root = tmp_path
    (root / ".aegis.py").write_text("""
from aegis import Agent
agents = {"x": Agent(harness="claude-code", model="opus",
                     effort="high", permission="auto")}
default_agent = "x"
queues = {"impl": {"agent": "ghost", "max_parallel": 1}}
""")
    monkeypatch.chdir(root)
    res = CliRunner().invoke(app, ["serve"])
    assert res.exit_code != 0
    assert "ghost" in res.output and "impl" in res.output
