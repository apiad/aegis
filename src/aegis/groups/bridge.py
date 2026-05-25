"""Concrete GroupsBridge implementation reused by AegisApp + SessionManager."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

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


def make_groups_bridge(*, session_manager, inbox_router) -> _GroupsBridge:
    registry = GroupRegistry()
    bus: asyncio.Queue = asyncio.Queue()
    runtime = GroupRuntime(registry=registry, inbox=inbox_router,
                           member_bus=bus)
    wiring = GroupWiring(session_manager=session_manager, registry=registry,
                         inbox=inbox_router, member_bus=bus)
    return _GroupsBridge(runtime=runtime, wiring=wiring)
