"""GroupWiring — adapter that bridges aegis core to the groups substrate.

Spawns ``AgentSession`` via ``SessionManager``, registers it in the
``GroupRegistry``, binds it to the ``InboxRouter`` so broadcasts land
in it, and attaches an event observer that pushes ``(handle, final_text)``
onto the runtime's ``member_bus`` on every ``Result`` event. The
"final assistant text of the turn" is the most recent ``AssistantText``
observed before the ``Result`` — matches the queue-substrate convention.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from aegis.events import AssistantText, Result
from aegis.groups.models import MemberRef
from aegis.groups.registry import GroupRegistry
from aegis.queue.inbox import InboxRouter


@dataclass
class GroupWiring:
    session_manager: Any
    registry: GroupRegistry
    inbox: InboxRouter
    member_bus: asyncio.Queue

    async def spawn(self, *, profile: str, group: str,
                    handle: str | None = None) -> str:
        h = await self.session_manager.spawn(profile=profile, handle=handle)
        session = self.session_manager.get(h)
        self.registry.add_member(group, MemberRef(handle=h, profile=profile))
        if session is not None:
            self.inbox.bind_session(h, session)
            last_text = {"text": ""}
            loop = asyncio.get_event_loop()

            def _observe(sess, ev) -> None:
                if isinstance(ev, AssistantText):
                    last_text["text"] = ev.text
                elif isinstance(ev, Result):
                    loop.call_soon_threadsafe(
                        self.member_bus.put_nowait, (h, last_text["text"]))

            session.add_event_observer(_observe)
        return h

    async def spawn_many(self, *, profile: str, n: int,
                         group: str) -> list[str]:
        if n < 1:
            raise ValueError("n must be >= 1")
        return [await self.spawn(profile=profile, group=group)
                for _ in range(n)]

    async def spawn_group(self, name: str, profiles: list[str]) -> list[str]:
        if not profiles:
            raise ValueError("profiles must not be empty")
        return [await self.spawn(profile=p, group=name) for p in profiles]
