"""A scriptable fake harness + AgentSession builder.

Follows the FakeHarnessSession pattern already in
``tests/test_session_hook_wiring.py`` rather than inventing a second one.
Shared by the digest, recap and loop-judge tests, which all need to script
an event stream and then assert on what the harness was *sent* — the
substrate — rather than on a rendered string.
"""
from __future__ import annotations

import asyncio

from aegis.events import AssistantText, Result


class FakeHarnessSession:
    """Minimal stand-in for HarnessSession. Captures sends.

    ``script`` is the event list emitted per turn. Default is one
    assistant message plus a Result. A script with no trailing ``Result``
    gets one appended — ``events()`` returns on Result, so a script
    without one hangs the turn forever.
    """

    def __init__(self, script: list | None = None) -> None:
        self.sent: list[str] = []
        self.started = False
        self._script = script
        self._events_q: asyncio.Queue = asyncio.Queue()

    def _events_for_turn(self) -> list:
        evs = list(self._script) if self._script is not None else [
            AssistantText(text="response text")]
        if not any(isinstance(e, Result) for e in evs):
            evs.append(Result(duration_ms=10, is_error=False))
        return evs

    async def start(self) -> None:
        self.started = True

    async def send(self, text: str) -> None:
        self.sent.append(text)
        for ev in self._events_for_turn():
            await self._events_q.put(ev)

    async def events(self):
        while True:
            ev = await self._events_q.get()
            yield ev
            if isinstance(ev, Result):
                return

    async def close(self, reason: str = "") -> None:
        pass


class FakeAgent:
    def __init__(self, profile: str = "p", harness: str = "claude") -> None:
        self.profile = profile
        self.harness = harness
        self.model = "sonnet"


def build_session(tmp_path, *, script: list | None = None, **kwargs):
    """(session, harness) — an AgentSession over a scriptable fake."""
    from aegis.core.session import AgentSession

    harness = FakeHarnessSession(script)
    session = AgentSession(
        harness, FakeAgent(), "p", "t",
        project_root=tmp_path, **kwargs)
    return session, harness
