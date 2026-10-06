"""Smoke test: SessionManager.__init__ produces a working .groups bridge."""
from __future__ import annotations

from pathlib import Path

from aegis.config.roots import AegisRoots
from aegis.core.manager import SessionManager


class _FakeHarness:
    async def start(self): ...
    async def send(self, t): ...
    async def close(self): ...

    async def events(self):
        if False:
            yield


def test_session_manager_exposes_groups_bridge():
    sm = SessionManager(
        agents={"default": object()}, default_agent="default",
        make_session=lambda profile, url, handle: _FakeHarness(),
        mcp=None,
        roots=AegisRoots.for_project(Path.cwd()),
    )
    assert hasattr(sm, "groups")
    assert hasattr(sm.groups, "spawn")
    assert hasattr(sm.groups, "broadcast")
    assert hasattr(sm.groups, "wait_all")
