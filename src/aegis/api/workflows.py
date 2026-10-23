"""Workflow runs as objects: ``srv.workflows`` and the ``WorkflowRun`` handle.

A run belongs to a session, its main agent: aegis wakes that session when a
gate, a worker's question or a failed step needs an answer. So ``arm`` and
``answer`` act as the owner session (an agent caller, as workflow_arm and
workflow_answer require), and the rest as the server's admin person, as the
card's buttons do.
"""

from __future__ import annotations

import builtins
from typing import TYPE_CHECKING, Any, Literal

from ..ops import Caller
from ._wait import until
from .models import RunInfo
from .sessions import Session

if TYPE_CHECKING:
    from ..server import Server

ENDED = frozenset({"done", "failed", "stopped", "rejected"})


class RunNotFound(LookupError):
    """No workflow run has that id."""


class Workflows:
    """The workflow runs on one server."""

    def __init__(self, server: Server) -> None:
        self._srv = server

    async def arm(
        self,
        definition: str,
        *,
        owner: Session,
        args: dict[str, Any] | None = None,
        approve: bool = False,
    ) -> WorkflowRun:
        """Arm ``definition`` (the YAML a workflow_arm takes) with ``owner`` as
        its main agent. It runs once approved: pass ``approve`` to approve it
        now, as the person would on its card."""
        r = await self._srv.app.registry.call(
            "workflow.arm",
            {"definition": definition, "args": args or {}},
            Caller("agent", log_id=owner.log_id),
        )
        run = WorkflowRun(self._srv, r["run"])
        if approve:
            await run.approve()
        return run

    async def get(self, run: str) -> WorkflowRun:
        if run not in self._srv.app.workflows.runs:
            raise RunNotFound(f"no workflow run {run!r}")
        return WorkflowRun(self._srv, run)

    async def list(self) -> builtins.list[WorkflowRun]:
        """Every run this server keeps, oldest id first."""
        return [WorkflowRun(self._srv, r) for r in sorted(self._srv.app.workflows.runs)]


class WorkflowRun:
    """A live handle on one workflow run."""

    def __init__(self, server: Server, run: str) -> None:
        self._srv = server
        self.run = run

    def __eq__(self, other: object) -> bool:
        return isinstance(other, WorkflowRun) and other.run == self.run

    def __hash__(self) -> int:
        return hash(self.run)

    def __repr__(self) -> str:
        return f"WorkflowRun({self.run!r})"

    def _state(self):
        st = self._srv.app.workflows.runs.get(self.run)
        if st is None:
            raise RunNotFound(f"no workflow run {self.run!r}")
        return st

    async def info(self) -> RunInfo:
        return RunInfo.model_validate(
            await self._srv._call("workflow.status", {"run": self.run})
        )

    async def owner(self) -> Session:
        """The run's main agent."""
        return Session(self._srv, self._state().owner)

    async def approve(self) -> RunInfo:
        return RunInfo.model_validate(
            await self._srv._call("workflow.approve", {"run": self.run})
        )

    async def reject(self) -> RunInfo:
        return RunInfo.model_validate(
            await self._srv._call("workflow.reject", {"run": self.run})
        )

    async def stop(self, node: str | None = None) -> RunInfo:
        """Stop the run, or with ``node`` only that running step."""
        params: dict[str, Any] = {"run": self.run}
        if node is not None:
            params["node"] = node
        await self._srv._call("workflow.stop", params)
        return await self.info()

    async def answer(
        self,
        to: str,
        *,
        choice: Literal["continue", "abort", "retry", "skip", "fail", "more"]
        | None = None,
        value: Any = None,
        text: str = "",
    ) -> RunInfo:
        """Answer what the run waits on, as its main agent: ``to`` is an open
        item's ``to`` (RunInfo.open)."""
        params: dict[str, Any] = {"run": self.run, "to": to, "text": text}
        if choice is not None:
            params["choice"] = choice
        if value is not None:
            params["value"] = value
        await self._srv.app.registry.call(
            "workflow.answer", params, Caller("agent", log_id=self._state().owner)
        )
        return await self.info()

    async def wait(self, *, timeout: float | None = None) -> RunInfo:
        """Wait until the run ends, or until it waits on an answer
        (``RunInfo.open`` not empty); a run not yet approved waits for that."""
        await until(
            self._srv,
            lambda: (
                (st := self._state()).status in ENDED
                or (st.status == "running" and bool(st.open))
            ),
            ("workflow-runs",),
            timeout,
        )
        return await self.info()
