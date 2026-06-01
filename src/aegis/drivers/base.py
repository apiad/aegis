from __future__ import annotations

import abc
from collections.abc import AsyncIterator

from aegis.config import Agent
from aegis.events import Event


class HarnessSession(abc.ABC):
    """One live conversation with a harness subprocess."""

    # Whether this harness can emit events *between* turns — i.e. after
    # a Result has been observed and aegis considers the session idle.
    # Claude Code can (background Monitor / sub-task wake-ups); ACP
    # harnesses cannot (events only flow during an active prompt()).
    # When True, the surrounding AgentSession arms an idle watcher to
    # promote those spontaneous events into an unsolicited turn instead
    # of letting them accumulate in the queue until the next user send.
    supports_idle_events: bool = False

    @abc.abstractmethod
    async def start(self) -> None: ...

    @abc.abstractmethod
    async def send(self, text: str) -> None: ...

    @abc.abstractmethod
    def events(self) -> AsyncIterator[Event]: ...

    @abc.abstractmethod
    async def close(self) -> None: ...

    def has_pending_event(self) -> bool:
        """Whether the harness has events buffered and waiting to be
        consumed. Used by AgentSession at turn-end to detect spontaneous
        emissions (e.g. Claude's Monitor firing while a turn was
        wrapping up). Default False — overrides return True only when
        ``supports_idle_events`` is True."""
        return False

    @property
    def session_id(self) -> str | None:
        """The driver-assigned session id, if known. Latched lazily as
        the upstream protocol reveals it. Returns None for drivers that
        don't expose one or before the first event arrives."""
        return None


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
