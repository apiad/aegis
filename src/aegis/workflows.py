"""The workflow engine (docs/superpowers/specs/2026-10-15-dynamic-workflows-design.html §05).

A run is armed by its main agent, approved by a person, and then driven here:
each node becomes something aegis already has, and everything that happens is
appended to the run's log (workflow_log.py), which boot folds back. Like the
schedules' clock, the engine is level-triggered: each pass looks at every
live run, so a missed hook only changes how soon a node moves.

This version runs ``seq`` and ``agent``. An agent node is a quiet session the
engine spawns with the main agent as its parent, so the main card waits on it
through the registry's own rule. The node is done when the worker has called
workflow_result and Registry.finished says it is done; a turn that ends
without a result is nudged once and then fails the run. The main agent hears
only the end, as one inbox turn: a pass and a stop share one lock, so a stop
never lands between a node's spawn and its record, and ``_end`` writes a run's
ending once. One run's exception fails that node and never stalls the others. Later slices add branches to ``_advance``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import secrets
import time
from pathlib import Path
from typing import TYPE_CHECKING

from . import restarts
from .agents import PERMISSION_ORDER, resolve
from .ops import OpError
from .workflow_lang import Node, Workflow, check_value, parse, render, rows, walk
from .workflow_log import LIVE, RunLog, RunState, apply, fold

if TYPE_CHECKING:
    from .app import App
    from .session import Session

log = logging.getLogger(__name__)
PASS_S = 30.0  # a pass at least this often, as the schedules' clock does


def iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


PREAMBLE = """---
You are node `{node}` of the workflow `{wid}` ("{title}"), which {owner} armed and a person approved.
When your task is done, call workflow_result with `value` matching this JSON Schema and `summary`, one plain sentence telling the person what you did:
{schema}
Nobody answers questions here and no person reads this session while the workflow runs: do the task with what you have, and say in the summary what you could not do."""


class Workflows:
    def __init__(self, app: App) -> None:
        self._app = app
        self._reg = app.sessions
        self.dir: Path = app.roots.state_root / "workflow-runs"
        self.runs: dict[str, RunState] = {}
        self.defs: dict[str, Workflow] = {}
        self._wake = asyncio.Event()
        self._task: asyncio.Task | None = None
        # One pass or one stop at a time, so a stop never lands between a
        # node's spawn and its record, and a run ends once.
        self._lock = asyncio.Lock()
        self._boot_failed: list[tuple[RunState, str]] = []

    # -- life -----------------------------------------------------------------
    def boot(self) -> None:
        if not self.dir.exists():
            return
        for p in sorted(self.dir.glob("*.jsonl")):
            st = fold(RunLog(p).read())
            if st is None:
                continue
            self.runs[st.run] = st
            if st.status in LIVE:
                wf, errors = parse(st.text, self._agents())
                if wf is None:
                    why = f"its definition no longer lints: {errors[0].message}"
                    self._finish(st, "failed", why)
                    self._release(st)
                    self._boot_failed.append((st, why))  # told once the loop runs
                else:
                    self.defs[st.run] = wf

    def start(self) -> None:
        self._task = asyncio.get_running_loop().create_task(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def _loop(self) -> None:
        for st, why in self._boot_failed:
            await self._tell_owner(st, "failed", f"The workflow failed: {why}.")
        self._boot_failed.clear()
        while True:
            # Cleared before the pass: a wake that arrives during it is
            # another pass, not lost.
            self._wake.clear()
            try:
                await self.step()
            except Exception:
                log.exception("aegis: a workflow pass failed")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), PASS_S)

    def changed(self) -> None:
        self._wake.set()

    # -- reading --------------------------------------------------------------
    def live(self, run: str) -> bool:
        st = self.runs.get(run)
        return st is not None and st.status in LIVE

    def of_owner(self, log_id: str) -> RunState | None:
        return next(
            (
                st
                for st in self.runs.values()
                if st.status in LIVE and self.owns(log_id, st)
            ),
            None,
        )

    def owner(self, st: RunState) -> Session | None:
        """The run's main agent now: the session that armed it, or the open
        session a handoff continued it in (registry.successor), followed
        through archived predecessors by their meta."""
        lid, last, seen = st.owner, None, set()
        while lid and lid not in seen:
            seen.add(lid)
            s = self._reg.sessions.get(lid)
            if s is not None:
                last, nxt = s, s.continued_by
            else:
                nxt = (self._reg.archived.get(lid) or {}).get("continued_by")
            lid = nxt if nxt != "starting" else None
        return last

    def owns(self, log_id: str | None, st: RunState) -> bool:
        o = self.owner(st)
        return log_id == (o.log_id if o is not None else st.owner)

    def wire(self, run: str) -> dict:
        st = self.runs[run]
        wf = self.defs.get(run) or parse(st.text, self._agents())[0]
        tree = rows(wf) if wf else []
        states = self._states(st, wf.root) if wf else {}
        for row in tree:
            n = st.nodes.get(row["id"])
            if n is not None:
                h = self._reg.sessions.get(n.log_id or "")
                row |= {
                    "state": n.status,
                    "log_id": n.log_id,
                    "handle": h.handle if h else None,
                    "started": n.started,
                    "ended": n.ended,
                    "say": n.say or row["say"],
                    "summary": n.summary,
                    "error": n.error,
                }
            else:
                row["state"] = states.get(row["id"], "pending")
        return {
            "run": run,
            "id": wf.id if wf else "",
            "title": wf.title if wf else "",
            "say": wf.say if wf else "",
            "owner": st.owner,
            "status": st.status,
            "created": st.created,
            "started": st.started,
            "ended": st.ended,
            "tree": tree,
            "story": st.story,
        }

    def _states(self, st: RunState, node: Node) -> dict[str, str]:
        """Each node's state for the card: an agent's from the log, a seq's
        from its children (failed if one failed, done when all are, running
        once one has started)."""
        out: dict[str, str] = {}

        def go(n: Node) -> str:
            if not n.children:
                s = st.nodes.get(n.id)
                out[n.id] = s.status if s is not None else "pending"
            else:
                kids = [go(c) for c in n.children]
                out[n.id] = (
                    "failed"
                    if "failed" in kids
                    else "done"
                    if all(k == "done" for k in kids)
                    else "pending"
                    if all(k == "pending" for k in kids)
                    else "running"
                )
            return out[n.id]

        go(node)
        return out

    def wire_all(self) -> list[dict]:
        return [self.wire(r) for r in sorted(self.runs)]

    # -- what people and agents do ------------------------------------------
    def arm(self, owner: Session, text: str, args: dict) -> RunState:
        if owner.workflow:
            raise OpError("nested", "a workflow's node cannot arm a workflow")
        if (cur := self.of_owner(owner.log_id)) is not None:
            raise OpError(
                "one_run",
                f"this session already has workflow run {cur.run}; stop it first",
            )
        wf, errors = parse(text, self._agents())
        if wf is None:
            raise OpError("lint", "\n".join(f"{e.path}: {e.message}" for e in errors))
        for n in walk(wf.root):
            if n.kind == "agent":
                self._check_permission(owner, n)
                cwd = n.body.get("cwd")
                if isinstance(cwd, str) and "{{" not in cwd:
                    # Refused before a person approves, not when the node starts.
                    self._app._resolve_cwd(cwd, Path(owner.spec.cwd))
        problems = check_value(args, wf.args, "$.args") if wf.args else []
        if problems:
            raise OpError("bad_args", "\n".join(problems))
        run = f"{wf.id}-{secrets.token_hex(3)}"
        st = RunState(run, owner.log_id, text, args, created=time.time())
        self.runs[run], self.defs[run] = st, wf
        self._log(
            run,
            {
                "ev": "armed",
                "run": run,
                "owner": owner.log_id,
                "text": text,
                "args": args,
            },
            fold_in=False,
        )
        owner.record_card({"kind": "workflow", "run": run})
        self._publish(run)
        return st

    def approve(self, run: str) -> None:
        st = self._get(run)
        if st.status != "armed":
            raise OpError("not_armed", f"{run} is {st.status}")
        self._log(run, {"ev": "approved"})
        self.changed()

    def reject(self, run: str) -> None:
        st = self._get(run)
        if st.status != "armed":
            raise OpError("not_armed", f"{run} is {st.status}")
        self._log(run, {"ev": "rejected"})

    async def stop_run(self, run: str) -> None:
        async with self._lock:
            st = self._get(run)
            if st.status not in LIVE:
                raise OpError("not_live", f"{run} is {st.status}")
            await self._end(
                st,
                "stopped",
                "The workflow was stopped. Its node sessions are open and unlocked.",
            )

    def result(self, s: Session, value, summary: str) -> dict:
        wf_ref = s.workflow or {}
        st = self.runs.get(wf_ref.get("run", ""))
        if st is None or st.status != "running":
            raise OpError(
                "not_a_node", "only a running workflow node calls workflow_result"
            )
        node = self._node(st.run, wf_ref["node"])
        problems = check_value(value, node.body.get("result") or {})
        if problems:
            raise OpError(
                "bad_result",
                "the value does not match the node's schema: " + "; ".join(problems),
            )
        if not summary.strip():
            raise OpError("bad_result", "summary is one plain sentence for the person")
        self._log(
            st.run,
            {
                "ev": "result",
                "node": node.id,
                "value": value,
                "summary": summary.strip(),
            },
        )
        self.changed()
        return {"accepted": True}

    def exited(self, s: Session, code: int) -> None:
        wf_ref = s.workflow or {}
        st = self.runs.get(wf_ref.get("run", ""))
        if st is None or st.status != "running" or code in restarts.SIGTERM_CODES:
            return
        n = st.nodes.get(wf_ref["node"])
        if n is not None and n.status == "running" and n.log_id == s.log_id:
            self._fail(st, wf_ref["node"], f"its session exited with code {code}")

    # -- the pass ---------------------------------------------------------------
    async def step(self) -> None:
        async with self._lock:
            for run in [r for r, st in self.runs.items() if st.status == "running"]:
                # One run's fault is that run's: the others still move.
                try:
                    await self._advance_run(run)
                except Exception:
                    log.exception("aegis: workflow run %s could not move", run)

    async def _advance_run(self, run: str) -> None:
        st, wf = self.runs[run], self.defs.get(run)
        if wf is None:
            return
        state = await self._advance(st, wf.root)
        if state == "done":
            await self._end(st, "done", self._summary(st))
        elif state == "failed":
            bad = next((n for n in st.nodes.values() if n.status == "failed"), None)
            await self._end(
                st,
                "failed",
                f"{bad.say} failed: {bad.error}" if bad else "A node failed.",
            )

    async def _advance(self, st: RunState, node: Node) -> str:
        """Move ``node`` on and say where it stands: pending, running, done or failed."""
        if node.kind == "seq":
            for c in node.children:
                s = await self._advance(st, c)
                if s != "done":
                    return s
            return "done"
        if node.kind == "agent":
            return await self._agent(st, node)
        raise OpError("kind", f"{node.kind} is not in this version yet")

    async def _agent(self, st: RunState, node: Node) -> str:
        n = st.nodes.get(node.id)
        if n is None or n.status == "pending":
            try:
                await self._start_agent(st, node)
            except Exception as e:
                log.exception("aegis: node %s of %s did not start", node.id, st.run)
                self._fail(st, node.id, f"it did not start: {getattr(e, 'message', e)}")
                return "failed"
            return "running"
        if n.status in ("done", "failed"):
            return n.status
        s = self._reg.sessions.get(n.log_id or "")
        if s is None:
            self._fail(st, node.id, "its session is gone")
            return "failed"
        if not self._reg.finished(s):
            return "running"
        if n.result is not None or n.summary:
            self._log(st.run, {"ev": "node_done", "node": node.id})
            with contextlib.suppress(Exception):
                await self._reg.close(s.log_id)
            return "done"
        if not n.nudged:
            self._log(st.run, {"ev": "nudged", "node": node.id})
            await s.deliver(
                f"> from workflow:{self._wid(st)} · nudge · {iso_now()}",
                "Your turn ended without workflow_result. Call it now with your result and a one-sentence summary, or the workflow fails.",
                through_lock=True,
            )
            return "running"
        self._fail(st, node.id, "its turn ended twice with no workflow_result")
        return "failed"

    async def _start_agent(self, st: RunState, node: Node) -> None:
        owner = self.owner(st)
        if owner is None:
            self._fail(st, node.id, "the main agent's session is closed")
            return
        scope = {"args": st.args} | {
            k: v.result for k, v in st.nodes.items() if v.result is not None
        }
        try:
            prompt, say = render(node.body["prompt"], scope), render(node.say, scope)
        except (KeyError, TypeError) as e:
            self._fail(st, node.id, f"a reference has no value: {e}")
            return
        agents, default = self._app._agents()
        spec = resolve(
            agents,
            default,
            node.body["agent"],
            {},
            self._app._resolve_cwd(node.body.get("cwd"), owner.spec.cwd),
            spawned_by=owner.log_id,
            owner=owner.spec.owner,
        )
        tag = {"run": st.run, "node": node.id}
        # A crash between a spawn and its record leaves the session behind:
        # it is this node's, so it is adopted rather than doubled.
        s = next((x for x in self._reg.sessions.values() if x.workflow == tag), None)
        if s is None:
            g = self._app.gate(spec.owner, spec.harness, spec.model)
            if not g.may_start:
                return  # the gate's next reading wakes a pass (app.on_quota -> changed)
            s = await self._reg.spawn(spec, title=say, quiet=True, workflow=tag)
        if st.status != "running":  # stopped while it spawned: never prompted
            with contextlib.suppress(Exception):
                await self._reg.close(s.log_id)
            return
        self._log(
            st.run,
            {"ev": "node_started", "node": node.id, "log_id": s.log_id, "say": say},
        )
        schema = json.dumps(node.body.get("result") or {"type": "object"})
        handle = owner.handle
        text = (
            f"> from workflow:{self._wid(st)} · node {node.id} · {iso_now()}\n{prompt}\n\n"
            + PREAMBLE.format(
                node=node.id,
                wid=self._wid(st),
                title=self.defs[st.run].title,
                owner=handle,
                schema=schema,
            )
        )
        await s.send(text, through_lock=True)

    # -- helpers ----------------------------------------------------------------
    def _summary(self, st: RunState) -> str:
        lines = [
            f"- {n.say} {n.summary}".rstrip()
            for n in st.nodes.values()
            if n.status == "done"
        ]
        results = {k: n.result for k, n in st.nodes.items() if n.result is not None}
        return (
            "The workflow is done.\n"
            + "\n".join(lines)
            + "\n\nResults:\n"
            + json.dumps(results, indent=2)
        )

    async def _end(self, st: RunState, outcome: str, body: str) -> None:
        """End a live run once: its record, its node sessions' tabs, and the
        one inbox turn its main agent gets."""
        if st.status not in LIVE:
            return
        if outcome == "stopped":
            self._log(st.run, {"ev": "stopped"})
            for n in st.nodes.values():
                s = self._reg.sessions.get(n.log_id or "")
                if n.status == "running" and s is not None and s.status == "working":
                    with contextlib.suppress(Exception):
                        await s.interrupt()
        else:
            self._finish(st, outcome)
        self._release(st)
        await self._tell_owner(st, outcome, body)

    def _release(self, st: RunState) -> None:
        """The node sessions still open get their tabs: the run no longer
        holds them, and the person reads them now."""
        for n in st.nodes.values():
            if n.log_id in self._reg.sessions:
                self._reg.set_quiet(n.log_id, False)

    async def _tell_owner(self, st: RunState, outcome: str, body: str) -> None:
        owner = self.owner(st)
        if owner is None:
            return
        try:
            await owner.deliver(
                f"> from workflow:{self._wid(st)} · {outcome} · {iso_now()}", body
            )
        except Exception:
            log.exception("aegis: could not tell %s how run %s ended", st.owner, st.run)

    def _fail(self, st: RunState, node: str, why: str) -> None:
        self._log(st.run, {"ev": "failed", "node": node, "error": why})
        # A failed node's session keeps its card and gets a tab, as a failed
        # queue worker does (spec §05).
        n = st.nodes.get(node)
        if n is not None and n.log_id in self._reg.sessions:
            self._reg.set_quiet(n.log_id, False)
        self.changed()

    def _finish(self, st: RunState, status: str, why: str = "") -> None:
        self._log(
            st.run,
            {"ev": "finished", "status": status, **({"why": why} if why else {})},
        )

    def _check_permission(self, owner: Session, n: Node) -> None:
        agents, default = self._app._agents()
        spec = resolve(
            agents,
            default,
            n.body["agent"],
            {},
            Path(owner.spec.cwd),
            owner=owner.spec.owner,
        )
        if PERMISSION_ORDER.index(spec.permission) > PERMISSION_ORDER.index(
            owner.spec.permission
        ):
            raise OpError(
                "not_allowed",
                f"{n.path}: agent {n.body['agent']} runs with {spec.permission}, above your {owner.spec.permission}",
            )

    def _node(self, run: str, node_id: str) -> Node:
        return next(n for n in walk(self.defs[run].root) if n.id == node_id)

    def _wid(self, st: RunState) -> str:
        wf = self.defs.get(st.run)
        return wf.id if wf else st.run

    def _agents(self) -> set[str]:
        agents, _ = self._app._agents()
        return {a.name if hasattr(a, "name") else a for a in agents}

    def _get(self, run: str) -> RunState:
        st = self.runs.get(run)
        if st is None:
            raise OpError("no_run", f"no workflow run {run!r}")
        return st

    def _log(self, run: str, rec: dict, fold_in: bool = True) -> None:
        rec = {"t": time.time(), **rec}
        RunLog(self.dir / f"{run}.jsonl").append(rec)
        if fold_in:
            apply(self.runs[run], rec)
        self._publish(run)

    def _publish(self, run: str) -> None:
        self._app.publish("workflows", [{"upsert": self.wire(run)}])
