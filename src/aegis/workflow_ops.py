"""The workflow operations (workflows.py), for people and, as MCP tools, for agents.

Approving and rejecting are a person's: neither is marked for agents, so no
MCP tool reaches them (ops.Registry.call refuses ``not_for_agents``). That is
the whole of "an agent never starts a workflow without a person's click".
Answering a person's gate is the same: ``workflow.gate`` is not for agents.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

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


class Stop(RunName):
    node: str | None = Field(
        default=None,
        description="One running step to stop, by its id; the run goes on and waits for your answer about it.",
    )


class Result(_Strict):
    value: Any = Field(description="Your result, matching the node's result schema.")
    summary: str = Field(
        min_length=1, description="One plain sentence for the person: what you did."
    )


class Output(_Strict):
    name: str = Field(
        pattern=r"^[A-Za-z0-9_-]{1,40}$",
        description="The output's name, as later steps wait on it (your-node.name).",
    )
    value: Any = Field(description="The output's value.")
    summary: str = Field(
        min_length=1,
        description="One plain sentence for the person: what this output is.",
    )


class Answer(_Strict):
    run: str
    to: str = Field(
        min_length=1,
        description="What you answer: the id in the message's header (a node path, or q<n> for a question).",
    )
    choice: Literal["continue", "abort", "retry", "skip", "fail"] | None = Field(
        default=None,
        description="A gate: continue or abort. A failure: retry, skip or fail. A question takes text instead.",
    )
    value: Any = Field(
        default=None,
        description="A gate's value for later steps, or a skipped step's result.",
    )
    text: str = Field(
        default="",
        description="The answer to a question, or one sentence for the story saying why.",
    )


class GateAnswer(_Strict):
    run: str
    node: str
    go: bool
    answer: str = ""


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

    @r.op("workflow.stop", Stop, agent=True)
    async def workflow_stop(p: Stop, caller):
        """Stop a workflow you armed. Its running nodes are interrupted and
        their sessions unlocked, so they can be read and talked to. With
        node, stop only that running step: it fails, the run goes on, and
        you retry, skip or change it with workflow_answer or workflow_edit."""
        owned(p.run, caller)
        if p.node:
            await wf.stop_node(p.run, p.node)
        else:
            await wf.stop_run(p.run)
        return wf.wire(p.run)

    @r.op("workflow.answer", Answer, agent=True)
    async def workflow_answer(p: Answer, caller):
        """Answer what your workflow woke you for: a gate by: main
        (continue or abort, with an optional value), a worker's question
        (text), or a failed step (retry, skip or fail). The message's header
        names what to answer."""
        owned(p.run, caller)
        await wf.answer(me(caller), p.run, p.to, p.choice, p.value, p.text)
        return wf.wire(p.run)

    # Not marked for agents: only a person answers a by: person gate.
    @r.op("workflow.gate", GateAnswer)
    async def workflow_gate(p: GateAnswer, caller):
        owned(p.run, caller)
        await wf.answer_gate(p.run, p.node, p.go, p.answer, by="person")
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
        should end right after. As a judge, value is {pass, why}."""
        return wf.result(me(caller), p.value, p.summary)

    # Open to read permission, as workflow_result is.
    @r.op("workflow.output", Output, agent=True, read=True)
    async def workflow_output(p: Output, caller):
        """Give an early output while your node keeps working, as a workflow
        node: a step waiting on it starts now. Each name once. Your first
        message names the outputs other steps wait on."""
        return wf.output(me(caller), p.name, p.value, p.summary)
