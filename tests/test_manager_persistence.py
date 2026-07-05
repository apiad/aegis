from __future__ import annotations

import asyncio

import pytest

from aegis.core.manager import SessionManager
from aegis.events import AssistantText, Result
from aegis.state.session_log import replay_events


class FakeSession:
    def __init__(self, events):
        self._events = list(events)

    async def start(self): ...
    async def send(self, t): ...
    async def close(self): ...

    async def events(self):
        for e in self._events:
            await asyncio.sleep(0)
            yield e


@pytest.mark.asyncio
async def test_serve_spawn_persists_events(tmp_path):
    evs = [AssistantText(text="from-serve", usage=None),
           Result(duration_ms=1, is_error=False, usage=None)]
    mgr = SessionManager(
        agents={"default": object()}, default_agent="default",
        make_session=lambda profile, url, handle: FakeSession(evs),
        mcp=None)
    mgr.attach_persistence(tmp_path)
    handle = await mgr.spawn("default")
    await mgr.get(handle).send("go")
    await mgr.get(handle)._task
    r = replay_events(tmp_path, handle)
    assert [type(e).__name__ for e in r.events] == ["AssistantText", "Result"]


@pytest.mark.asyncio
async def test_no_persistence_when_not_attached(tmp_path):
    mgr = SessionManager(
        agents={"default": object()}, default_agent="default",
        make_session=lambda profile, url, handle: FakeSession(
            [AssistantText(text="x", usage=None)]),
        mcp=None)
    handle = await mgr.spawn("default")
    await mgr.get(handle).send("go")
    await mgr.get(handle)._task
    assert replay_events(tmp_path, handle).events == []
