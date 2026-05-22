from __future__ import annotations

import abc
from collections.abc import AsyncIterator

from aegis.config import Agent
from aegis.events import Event


class HarnessSession(abc.ABC):
    """One live conversation with a harness subprocess."""

    @abc.abstractmethod
    async def start(self) -> None: ...

    @abc.abstractmethod
    async def send(self, text: str) -> None: ...

    @abc.abstractmethod
    def events(self) -> AsyncIterator[Event]: ...

    @abc.abstractmethod
    async def close(self) -> None: ...


class HarnessDriver(abc.ABC):
    """Translates a harness-agnostic Agent into a concrete session."""

    supports_resume: bool = False

    @abc.abstractmethod
    def build_argv(self, agent: Agent, cwd: str,
                   mcp_url: str, handle: str) -> list[str]: ...

    @abc.abstractmethod
    def session(self, agent: Agent, cwd: str,
                mcp_url: str, handle: str) -> HarnessSession: ...

    def resume(self, agent: Agent, cwd: str,
               mcp_url: str, handle: str, session_id: str) -> HarnessSession:
        """Build a session bound to an existing driver-side conversation.

        Default raises — only resume-capable drivers override.
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not support session resume")
