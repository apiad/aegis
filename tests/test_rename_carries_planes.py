"""Regression: a rename must carry every handle-keyed plane with it.

A monitor and a reminder are keyed by the handle that armed them. That key
is what scopes the UI (`snapshot(for_handle=...)`) and, more importantly,
what the wake is delivered to. Leave it behind on a rename and the monitor
keeps watching a process nobody is listening for: the strip on the renamed
tab shows nothing, and when the condition finally trips, the message goes
to a handle no session answers to.

``SessionManager.rename_handle`` migrated the inbox and the lock claims but
not the monitor or reminder planes — even though it owns both
(``attach_monitor_manager`` / ``attach_reminder_service``). ``AegisApp``'s
own ``rename_handle`` migrates all of them, so the two paths disagreed and
only one of them stranded you.

Observed live on 2026-08-12: a session renamed mid-run kept its monitor
under the old handle, and ``aegis_monitors(from_handle=<new>)`` came back
empty while the monitor was still watching under ``<old>``.
"""
from __future__ import annotations

import pytest

from aegis.core.manager import SessionManager
from aegis.queue.inbox import InboxRouter


class FakeHarness:
    async def start(self): ...
    async def send(self, t): ...
    async def close(self): ...

    async def events(self):
        if False:
            yield


def _mgr() -> SessionManager:
    return SessionManager(
        {"default": object()}, "default",
        make_session=lambda profile, url, handle: FakeHarness(),
        mcp=None, inbox=InboxRouter(),
    )


class _Plane:
    """Stands in for MonitorManager / ReminderService: both key their
    records by ``from_handle`` and both expose ``rename(old, new)``."""

    def __init__(self) -> None:
        self.owner = "old-name"

    def rename(self, old: str, new: str) -> None:
        if self.owner == old:
            self.owner = new


@pytest.mark.asyncio
async def test_rename_carries_the_monitor_plane():
    m = _mgr()
    m._sync_spawn("default", handle="old-name")
    m.attach_monitor_manager(plane := _Plane())

    await m.rename_handle("old-name", "new-name")

    assert plane.owner == "new-name", (
        "the monitor stayed under the old handle — its wake would be "
        "delivered to a handle nobody answers to")


@pytest.mark.asyncio
async def test_rename_carries_the_reminder_plane():
    m = _mgr()
    m._sync_spawn("default", handle="old-name")
    m.attach_reminder_service(plane := _Plane())

    await m.rename_handle("old-name", "new-name")

    assert plane.owner == "new-name"


@pytest.mark.asyncio
async def test_rename_survives_planes_that_were_never_attached():
    """`serve` attaches both planes; a bare SessionManager (and much of the
    test suite) attaches neither, and a rename must not fault on that."""
    m = _mgr()
    m._sync_spawn("default", handle="old-name")
    assert m.monitor_manager is None and m.reminder_service is None

    res = await m.rename_handle("old-name", "new-name")
    assert res == {"ok": True, "old": "old-name", "new": "new-name"}
