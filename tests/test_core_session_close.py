from __future__ import annotations

import pytest

from aegis.core.session import AgentSession
from aegis.events import AssistantText


class FakeSession:
    def __init__(self):
        self.started = False
        self.closed = False

    async def start(self):
        self.started = True

    async def send(self, t):
        pass

    async def close(self):
        self.closed = True

    async def events(self):
        yield AssistantText(text="hi")


def _make() -> AgentSession:
    return AgentSession(FakeSession(), agent=None, agent_slug="default",
                        handle="h1")


def test_on_close_fires_on_primary_and_extras():
    s = _make()
    calls: list[tuple] = []
    s.on_close = lambda se, reason: calls.append(("primary", reason))
    s.add_close_observer(lambda se, reason: calls.append(("extra1", reason)))
    s.add_close_observer(lambda se, reason: calls.append(("extra2", reason)))
    s._emit_close("explicit")
    assert calls == [("primary", "explicit"),
                     ("extra1", "explicit"),
                     ("extra2", "explicit")]


def test_one_extra_raising_does_not_break_others():
    s = _make()
    calls: list[str] = []

    def boom(se, reason):
        raise RuntimeError("boom")

    s.add_close_observer(boom)
    s.add_close_observer(lambda se, reason: calls.append("survived"))
    s._emit_close("crash")
    assert calls == ["survived"]


@pytest.mark.asyncio
async def test_close_method_fires_close_observers():
    s = _make()
    reasons: list[str] = []
    s.add_close_observer(lambda se, reason: reasons.append(reason))
    await s.close()
    assert reasons == ["explicit"]
