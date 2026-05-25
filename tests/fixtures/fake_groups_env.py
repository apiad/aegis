"""Shared FakeManager + FakeSession for groups unit tests."""
from __future__ import annotations


class FakeSession:
    def __init__(self, handle: str):
        self.handle = handle
        self.delivered = []
        self._observers = []

    async def deliver(self, msg) -> None:
        self.delivered.append(msg)

    def add_event_observer(self, cb) -> None:
        self._observers.append(cb)

    def emit(self, ev) -> None:
        for cb in self._observers:
            cb(self, ev)


class FakeManager:
    def __init__(self):
        self.sessions: dict[str, FakeSession] = {}
        self._counter = 0

    async def spawn(self, profile: str, *, handle: str | None = None,
                    **_):
        if handle is None:
            self._counter += 1
            handle = f"{profile}-{self._counter}"
        s = FakeSession(handle)
        self.sessions[handle] = s
        return handle

    def get(self, handle: str):
        return self.sessions.get(handle)
