"""The workflow operations (workflows.py), for people and, as MCP tools, for agents.

Approving and rejecting are a person's: neither is marked for agents, so no
MCP tool reaches them (ops.Registry.call refuses ``not_for_agents``). That is
the whole of "an agent never starts a workflow without a person's click".
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from .ops import Caller, OpError

if TYPE_CHECKING:
    from .app import App


class _Strict(BaseModel):
    model_config = {"extra": "forbid"}


class Definition(_Strict):
    definition: str = Field(min_length=1, description="The workflow as YAML.")


class Arm(Definition):
    args: dict[str, Any] = Field(
        default_factory=dict, description="Values for the workflow's args schema."
    )


class RunName(_Strict):
    run: str


class Result(_Strict):
    value: Any = Field(description="Your result, matching the node's result schema.")
    summary: str = Field(
        min_length=1, description="One plain sentence for the person: what you did."
    )


def register_workflow_ops(app: App) -> None:
    r, wf, reg = app.registry, app.workflows, app.sessions

    def me(caller: Caller):
        s = reg.sessions.get(caller.log_id or "") if caller.is_agent else None
        if s is None:
            raise OpError("agents_only", "only an agent's session can do this")
        return s

    def owned(run: str, caller: Caller) -> None:
        st = wf.runs.get(run)
        if st is None:
            raise OpError("no_run", f"no workflow run {run!r}")
        who = app.who(caller)
        if not who.is_admin and app.owner_of(st.owner) != who.name:
            raise OpError("not_owner", f"{run} belongs to {app.owner_of(st.owner)}")
        if caller.is_agent and not wf.owns(caller.log_id, st):
            raise OpError(
                "not_yours", "only the session that armed a workflow can stop it"
            )

    @r.op("workflow.lint", Definition, agent=True, read=True)
    async def workflow_lint(p: Definition, caller):
        """Check a workflow without arming it: every error with its YAML path.
        Arming runs the same check."""
        from .workflow_lang import parse

        _, errors = parse(p.definition, wf._agents())
        return {"errors": [f"{e.path}: {e.message}" for e in errors]}

    @r.op("workflow.arm", Arm, agent=True)
    async def workflow_arm(p: Arm, caller):
        """Arm a workflow: a YAML tree in which every node says, in one sentence
        for the person, what it does. It is linted, drawn as a card under this
        call, and runs only when a person approves it there. Use it for work
        that needs coordination (many agents, checks, gates); a linear
        plan_update is for work you do yourself."""
        st = wf.arm(me(caller), p.definition, p.args)
        return {"run": st.run, "status": st.status}

    @r.op("workflow.approve", RunName)
    async def workflow_approve(p: RunName, caller):
        owned(p.run, caller)
        wf.approve(p.run)
        return wf.wire(p.run)

    @r.op("workflow.reject", RunName)
    async def workflow_reject(p: RunName, caller):
        owned(p.run, caller)
        wf.reject(p.run)
        return wf.wire(p.run)

    @r.op("workflow.stop", RunName, agent=True)
    async def workflow_stop(p: RunName, caller):
        """Stop a workflow you armed. Its running nodes are interrupted and
        their sessions unlocked, so they can be read and talked to."""
        owned(p.run, caller)
        await wf.stop_run(p.run)
        return wf.wire(p.run)

    @r.op("workflow.status", RunName, agent=True, read=True)
    async def workflow_status(p: RunName, caller):
        """A run's tree: each node's state, session, times and summary, and the story so far."""
        if p.run not in wf.runs:
            raise OpError("no_run", f"no workflow run {p.run!r}")
        return wf.wire(p.run)

    # Open to read permission, like turn_end: a node reports what it did
    # whatever it may change, or a review node could never finish.
    @r.op("workflow.result", Result, agent=True, read=True)
    async def workflow_result(p: Result, caller):
        """Report your node's result, as a workflow node: a value matching the
        node's schema and a one-sentence summary for the person. Your turn
        should end right after."""
        return wf.result(me(caller), p.value, p.summary)
