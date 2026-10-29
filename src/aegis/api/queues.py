"""Queue tasks as objects: ``srv.queues`` and the ``Task`` handle.

A Task is a live handle by task id, as a Session is by log id. A task
enqueued from a program has no enqueuer session, so its result reaches no
inbox: read it with ``wait()`` or ``info()``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._wait import until
from .models import TaskInfo
from .sessions import Session

if TYPE_CHECKING:
    from ..server import Server

FINISHED = frozenset({"completed", "failed", "cancelled"})


class TaskNotFound(LookupError):
    """No task has that id."""


class Queues:
    """The queues configured in .aegis.yaml, and their tasks."""

    def __init__(self, server: Server) -> None:
        self._srv = server

    async def enqueue(self, queue: str, payload: str) -> Task:
        """Hand ``payload`` to a fresh worker on ``queue``."""
        r = await self._srv._call(
            "queue.enqueue", {"queue": queue, "payload": payload, "callback": False}
        )
        return Task(self._srv, r["task_id"])

    async def get(self, task_id: str) -> Task:
        if task_id not in self._srv.app.queues.tasks:
            raise TaskNotFound(f"no task {task_id!r}")
        return Task(self._srv, task_id)


class Task:
    """A live handle on one queue task."""

    def __init__(self, server: Server, task_id: str) -> None:
        self._srv = server
        self.id = task_id

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Task) and other.id == self.id

    def __hash__(self) -> int:
        return hash(self.id)

    def __repr__(self) -> str:
        return f"Task({self.id!r})"

    async def info(self) -> TaskInfo:
        return TaskInfo.model_validate(
            await self._srv._call("task.status", {"task_id": self.id})
        )

    async def wait(self, *, timeout: float | None = None) -> TaskInfo:
        """Wait until the task completed, failed or was cancelled."""
        tasks = self._srv.app.queues.tasks
        await until(
            self._srv,
            lambda: tasks[self.id].status in FINISHED,
            ("sessions",),
            timeout,
        )
        return await self.info()

    async def cancel(self) -> TaskInfo:
        """Drop the task if it is pending; stop and archive its worker if running."""
        return TaskInfo.model_validate(
            await self._srv._call("task.cancel", {"task_id": self.id})
        )

    async def resume(self) -> TaskInfo:
        """Put a failed or cancelled task's worker back to work."""
        return TaskInfo.model_validate(
            await self._srv._call("task.resume", {"task_id": self.id})
        )

    async def worker(self) -> Session | None:
        """The worker's session while it is open, else None."""
        info = await self.info()
        s = self._srv.app.sessions.sessions.get(info.worker or "")
        return (
            Session(self._srv, s.log_id) if s is not None and not s.archived else None
        )
