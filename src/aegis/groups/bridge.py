"""Concrete GroupsBridge implementation reused by AegisApp + SessionManager."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from aegis.groups.persistence import PersistedGroupLog
from aegis.groups.registry import GroupRegistry
from aegis.groups.runtime import GroupRuntime
from aegis.groups.wiring import GroupWiring


@dataclass
class _GroupsBridge:
    runtime: GroupRuntime
    wiring: GroupWiring

    async def spawn(self, *, profile: str, group: str,
                    handle: str | None = None) -> str:
        return await self.wiring.spawn(profile=profile, group=group,
                                        handle=handle)

    async def broadcast(self, group: str, *, sender: str, objective: str,
                        output_format: str, tool_guidance: str,
                        boundaries: str):
        return await self.runtime.broadcast(
            group, sender=sender, objective=objective,
            output_format=output_format, tool_guidance=tool_guidance,
            boundaries=boundaries,
        )

    async def wait_all(self, group: str, *, timeout: float = 600.0,
                       reducer: str = "concat"):
        return await self.runtime.wait_all(group, timeout=timeout,
                                            reducer=reducer)

    async def wait_any(self, group: str, *, timeout: float = 600.0,
                       cancel_losers: bool = True):
        return await self.runtime.wait_any(
            group, timeout=timeout, cancel_losers=cancel_losers)

    async def spawn_mixed(self, *, group: str,
                          profiles: list[str]) -> list[str]:
        return await self.wiring.spawn_group(group, profiles)

    async def status(self, group: str) -> dict:
        g = self.runtime.registry.get(group)
        rec = self.runtime.tracker.current(group)
        return {
            "name": g.name,
            "members": [
                {"handle": h, "profile": m.profile}
                for h, m in g.members.items()
            ],
            "current_broadcast": (
                {"id": rec.id, "objective": rec.objective,
                 "started_at": rec.started_at,
                 "members": list(rec.members)}
                if rec else None
            ),
        }

    async def dissolve(self, group: str) -> dict:
        self.runtime.registry.dissolve(group)
        return {"dissolved": group}

    async def rename(self, old: str, new: str) -> dict:
        self.runtime.registry.rename(old, new)
        return {"old": old, "new": new}

    async def move_member(self, handle: str, *, from_group: str,
                          to_group: str) -> dict:
        self.runtime.registry.move_member(
            handle, from_group=from_group, to_group=to_group)
        return {"handle": handle, "from": from_group, "to": to_group}


def make_groups_bridge(*, session_manager, inbox_router,
                       state_dir: Path | None = None) -> _GroupsBridge:
    log = PersistedGroupLog(state_dir) if state_dir is not None else None
    registry = GroupRegistry(log=log)
    bus: asyncio.Queue = asyncio.Queue()
    runtime = GroupRuntime(registry=registry, inbox=inbox_router,
                           member_bus=bus, log=log)
    wiring = GroupWiring(session_manager=session_manager, registry=registry,
                         inbox=inbox_router, member_bus=bus)
    if log is not None:
        live = (set(session_manager.live_handles())
                if hasattr(session_manager, "live_handles") else set())
        registry.start(live_handles=live)
    return _GroupsBridge(runtime=runtime, wiring=wiring)
