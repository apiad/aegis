"""The workflow operations (workflows.py), for people and, as MCP tools, for agents.

Approving and rejecting are a person's: neither is marked for agents, so no
MCP tool reaches them (ops.Registry.call refuses ``not_for_agents``). That is
the whole of "an agent never starts a workflow without a person's click".
Answering a person's gate is the same: ``workflow.gate`` is not for agents,
nor are ``workflow.edit.apply`` and ``workflow.edit.reject``: an agent
proposes a change, and only a person lets it run. ``workflow.see`` opens a
step's see for a person: it takes a run and a step, never a path.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field, model_validator

from . import files
from . import layout as canvas
from .ops import Caller, NoParams, OpError
from .transcript.entries import _sent_file
from .workflow_lang import parse, rows, walk
from .workflow_store import _named, check_named

if TYPE_CHECKING:
    from .app import App


class _Strict(BaseModel):
    model_config = {"extra": "forbid"}


class Definition(_Strict):
    definition: str = Field(min_length=1, description="The workflow as YAML.")


class Arm(_Strict):
    definition: str | None = Field(
        default=None, min_length=1, description="The workflow as YAML."
    )
    id: str | None = Field(
        default=None,
        description="A saved workflow's id (workflow_list), instead of definition.",
    )
    token: str | None = Field(
        default=None,
        description="The launch token from a person's /workflow line, with id: the run starts at once.",
    )
    args: dict[str, Any] = Field(
        default_factory=dict, description="Values for the workflow's args schema."
    )
    run: str | None = Field(
        default=None,
        description="A stopped or failed run of yours to arm again; it keeps its args.",
    )
    dock: str | None = Field(default=None, description=canvas.DOCK_DOC)

    @model_validator(mode="after")
    def _one(self):
        # A run armed again may carry a definition: the edit it makes.
        given = [k for k in ("definition", "id", "run") if getattr(self, k) is not None]
        if not given or (self.id is not None and len(given) > 1):
            raise ValueError(
                "give one of definition, id (a saved workflow) or run (to arm a run again)"
            )
        if self.token is not None and self.id is None:
            raise ValueError(
                "a launch token goes with id, the workflow the person launched"
            )
        return self


class SaveDef(_Strict):
    definition: str = Field(
        min_length=1, description="The workflow as YAML; its id names the file."
    )


class WorkflowId(_Strict):
    id: str


class RunName(_Strict):
    run: str


class NodeRef(_Strict):
    run: str
    node: str


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
    node: str | None = Field(
        default=None,
        description="As the main agent: the step this reports, as its header names it. Needed when several of your steps are open.",
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
    node: str | None = Field(
        default=None,
        description="As the main agent: the step this reports, as its header names it. Needed when several of your steps are open.",
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


class EditDef(_Strict):
    run: str
    definition: str = Field(
        min_length=1, description="The whole workflow as YAML, with the same ids."
    )


class EditName(_Strict):
    run: str
    v: int


class GateAnswer(_Strict):
    run: str
    node: str
    go: bool
    answer: str = ""


class Ask(_Strict):
    question: str = Field(
        min_length=1, description="What you cannot go on without, in one message."
    )


class Note(_Strict):
    run: str
    text: str = Field(
        min_length=1, description="One plain sentence for the run's story."
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

        got, errors = parse(p.definition, wf._agents())
        out: dict = {"errors": [f"{e.path}: {e.message}" for e in errors]}
        return out | ({"id": got.id} if got is not None and not errors else {})

    @r.op("workflow.arm", Arm, agent=True)
    async def workflow_arm(p: Arm, caller):
        """Arm a workflow: a YAML tree in which every node says, in one sentence
        for the person, what it does. It is linted, drawn as a card under this
        call, and runs only when a person approves it there. Use it for work
        that needs coordination (many agents, checks, gates); a linear
        plan_update is for work you do yourself. With run, arms a stopped
        or failed run of yours again: what finished keeps its result, the
        rest runs after a new approval; a definition given with it may
        change only what an edit may. With dock, the card also opens beside
        the transcript, as artifact_send's page does."""
        if p.run is not None:
            owned(p.run, caller)
        s = me(caller)
        # place() lets the pane it places through, so the run's id is not
        # needed yet: a bad dock is refused before anything is armed.
        if p.dock is not None:
            try:
                canvas.place(s.layout, "wf-new", p.dock, s.dockable)
            except canvas.LayoutError as e:
                raise OpError("bad_params", str(e)) from e
        if p.id is not None and p.token is not None:
            st = wf.arm_launched(s, p.id, p.args, p.token)
        elif p.id is not None:
            saved = app.saved.get(p.id)
            check_named(saved, wf._agents())
            st = wf.arm(s, saved.text, p.args, saved=saved)
        else:
            st = wf.arm(s, p.definition, p.args, run=p.run)
        out = {"run": st.run, "status": st.status}
        if p.dock is not None:
            # The run exists now; a placement that no longer fits is
            # reported, not raised, as artifact_send does.
            try:
                s.set_layout(
                    canvas.place(s.layout, f"wf-{st.run}", p.dock, s.dockable), "agent"
                )
            except canvas.LayoutError as e:
                out |= {"docked": False, "reason": str(e)}
        return out

    def by(caller: Caller) -> dict:
        s = reg.sessions.get(caller.log_id or "") if caller.is_agent else None
        if s is not None:
            return {"kind": "agent", "log_id": s.log_id, "handle": s.handle}
        return {"kind": "user", "principal": app.who(caller).name}

    @r.op("workflow.save", SaveDef, agent=True)
    async def workflow_save(p: SaveDef, caller):
        """Save a workflow into the project, as .aegis/workflows/<id>.yaml, so
        a person can run it with /workflow <id> <text> and a schedule can fire
        it. Its generated ids are written into the file, so later edits keep
        them. Saving needs no approval and replaces a saved one with the same
        id; running it still does, unless a person launched it."""

        def generated(text: str) -> int:
            got, _ = parse(text, wf._agents())
            return sum(n.generated for n in walk(got.root)) if got else 0

        s = app.saved.save(p.definition, wf._agents(), by(caller))
        app.schedules.workflow_changed(s.id)
        return {
            "id": s.id,
            "sha": s.sha,
            "path": str(s.path),
            "ids_written": generated(p.definition) - generated(s.text),
        }

    @r.op("workflow.list", NoParams, agent=True, read=True)
    async def workflow_list(_, caller):
        """The saved workflows a person can run with /workflow <id> <text>:
        built in and the project's, each with its title, args schema,
        version, who saved it, and what is wrong with it if it does not lint."""
        return app.saved.listing(wf._agents())

    @r.op("workflow.read", WorkflowId, admin=True)
    async def workflow_read(p: WorkflowId, caller):
        """A saved workflow for Settings → Workflows: its file's text, to
        export, and its steps as the card draws them. A person's: an agent
        reads the file itself."""
        s = app.saved.get(p.id)
        got, error = _named(s, wf._agents())
        out = {"id": s.id, "source": s.source, "sha": s.sha, "text": s.text}
        if got is None:
            return out | {"error": error}
        steps = [{"say": r["say"], "depth": r["depth"]} for r in rows(got)]
        return out | {"steps": steps}

    @r.op("workflow.delete", WorkflowId, agent=True)
    async def workflow_delete(p: WorkflowId, caller):
        """Delete a saved workflow from the project. A built-in one cannot be
        deleted. A schedule that fires it pauses."""
        app.saved.delete(p.id, by(caller))
        app.schedules.workflow_changed(p.id)
        return {"deleted": p.id}

    @r.op("workflow.approve", RunName)
    async def workflow_approve(p: RunName, caller):
        owned(p.run, caller)
        wf.approve(p.run)
        return wf.wire(p.run)

    @r.op("workflow.reject", RunName)
    async def workflow_reject(p: RunName, caller):
        owned(p.run, caller)
        await wf.reject(p.run)
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

    # Open to read permission, as workflow_result is: any node may ask.
    @r.op("workflow.ask", Ask, agent=True, read=True)
    async def workflow_ask(p: Ask, caller):
        """As a workflow node, ask the main agent a question you cannot go on
        without, then end your turn: the answer arrives as your next
        message. It never reaches a person."""
        return wf.ask(me(caller), p.question)

    @r.op("workflow.note", Note, agent=True)
    async def workflow_note(p: Note, caller):
        """Write one line into your workflow's story, the card the person
        reads: why you answered or decided as you did."""
        owned(p.run, caller)
        wf.note(p.run, p.text)
        return {"noted": True}

    # Not marked for agents: only a person answers a by: person gate.
    @r.op("workflow.gate", GateAnswer)
    async def workflow_gate(p: GateAnswer, caller):
        owned(p.run, caller)
        await wf.answer_gate(p.run, p.node, p.go, p.answer, by="person")
        return wf.wire(p.run)

    # Not marked for agents: a person opens a step's see (spec §07). The
    # browser names a run and a step, never a path.
    @r.op("workflow.see", NodeRef)
    async def workflow_see(p: NodeRef, caller):
        owned(p.run, caller)
        path, fragment, line = wf.see_file(p.run, p.node)
        try:
            (rec,) = await asyncio.to_thread(
                files.store_all, app.roots.state_root, [path]
            )
        except files.FileError as e:
            raise OpError(e.code, e.message) from e
        return {"files": [_sent_file(rec)], "fragment": fragment, "line": line}

    @r.op("workflow.edit", EditDef, agent=True)
    async def workflow_edit(p: EditDef, caller):
        """Change your running workflow: send the whole definition with the
        same ids. Steps that started stay as they are, except a failed or
        stopped one, which the change retries. The person approves a diff of
        the steps' sentences on a card under this call; until then the run
        goes on, and steps the change touches wait for the answer."""
        owned(p.run, caller)
        st = wf.propose_edit(me(caller), p.run, p.definition)
        assert st.proposal is not None
        return {"run": p.run, "proposed": st.proposal["v"], "diff": st.proposal["diff"]}

    # Not marked for agents: only a person applies or rejects an edit.
    @r.op("workflow.edit.apply", EditName)
    async def workflow_apply_edit(p: EditName, caller):
        owned(p.run, caller)
        await wf.apply_edit(p.run, p.v)
        return wf.wire(p.run)

    @r.op("workflow.edit.reject", EditName)
    async def workflow_reject_edit(p: EditName, caller):
        owned(p.run, caller)
        wf.reject_edit(p.run, p.v)
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
        return wf.result(me(caller), p.value, p.summary, p.node)

    # Open to read permission, as workflow_result is.
    @r.op("workflow.output", Output, agent=True, read=True)
    async def workflow_output(p: Output, caller):
        """Give an early output while your node keeps working, as a workflow
        node: a step waiting on it starts now. Each name once. Your first
        message names the outputs other steps wait on."""
        return wf.output(me(caller), p.name, p.value, p.summary, p.node)
