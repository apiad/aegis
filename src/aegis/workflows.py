"""The workflow engine (docs/superpowers/specs/2026-10-15-dynamic-workflows-design.html §05).

A run is armed by its main agent, approved by a person, and then driven here:
each node becomes something aegis already has, and everything that happens is
appended to the run's log (workflow_log.py), which boot folds back. Like the
schedules' clock, the engine is level-triggered: each pass looks at every
live run, so a missed hook only changes how soon a node moves.

This version runs ``seq``, ``par``, ``map`` and ``agent``. Every node
instance has a path (``images.base.impl`` inside item ``base`` of map
``images``); only leaves and a map's items are logged, and every other state
is derived from them on each pass (``_state``), so a restart rebuilds them by
folding. An agent node is a quiet session the
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
import math
import secrets
import time
from pathlib import Path
from typing import TYPE_CHECKING

from . import restarts
from .agents import PERMISSION_ORDER, resolve
from .ops import OpError
from .workflow_lang import (
    Node,
    Workflow,
    check_value,
    item_keys,
    parse,
    render,
    rows,
    value_of,
    walk,
)
from .workflow_log import LIVE, NodeState, RunLog, RunState, apply, fold

if TYPE_CHECKING:
    from .app import App
    from .session import Session

log = logging.getLogger(__name__)
PASS_S = 30.0  # a pass at least this often, as the schedules' clock does


def iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _fold(kids: list[str]) -> str:
    """A composite node's state from its children's."""
    for s in ("failed", "stopped"):
        if s in kids:
            return s
    if all(k == "done" for k in kids):
        return "done"
    if "running" in kids or "done" in kids:
        return "running"
    return "queued" if "queued" in kids else "pending"


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
        # The agent instances each run's last pass found no room for, and the
        # ones this pass finds: the card shows them queued.
        self._queued: dict[str, set[str]] = {}
        self._queuing: set[str] = set()

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

        def items(path: str):
            return st.nodes[path].items if path in st.nodes else None

        tree = [self._row(st, wf, r) for r in rows(wf, items)] if wf else []
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

    def _row(self, st: RunState, wf: Workflow, row: dict) -> dict:
        node, inst = self._template(wf, row["node"]), row["id"]
        prefix = inst[: -len(node.id)]
        each = row.get("each", False)
        row["state"] = "pending" if each else self._state(st, node, inst)
        n = st.nodes.get(inst)
        if n is not None and n.say:
            row["say"] = n.say
        elif not each:
            with contextlib.suppress(KeyError, TypeError, IndexError):
                row["say"] = render(row["say"], self._scope(st, wf, prefix))
        if n is not None:
            h = self._reg.sessions.get(n.log_id or "")
            row |= {
                "log_id": n.log_id,
                "handle": h.handle if h else None,
                "started": n.started,
                "ended": n.ended,
                "summary": n.summary,
                "error": n.error,
                "outputs": sorted(n.outputs),
            }
        if node.after:
            row["after"] = [
                {"name": a, "met": not each and self._met(st, wf, prefix, a) is True}
                for a in node.after
            ]
        if node.kind == "map" and n is not None and n.items is not None:
            do = node.children[0]
            count: dict[str, int] = {}
            for k, _ in n.items:
                s = self._state(st, do, f"{inst}.{k}.{do.id}")
                count[s] = count.get(s, 0) + 1
            row["count"] = count
        return row

    def _template(self, wf: Workflow, inst: str) -> Node:
        """An instance's node: ids have no dots, so the last segment is its id."""
        nid = inst.rsplit(".", 1)[-1]
        return next(n for n in walk(wf.root) if n.id == nid)

    def _state(self, st: RunState, node: Node, inst: str) -> str:
        """Where an instance stands: pending, queued, running, done, failed
        or stopped. An agent's comes from the log (queued from the last
        pass's room check); a seq's, par's and map's from their children's."""
        s = st.nodes.get(inst)
        if node.kind == "agent":
            if s is None or s.status == "pending":
                # An ended run queues nothing.
                q = self._queued.get(st.run, ()) if st.status == "running" else ()
                return "queued" if inst in q else "pending"
            return s.status
        if s is not None and s.status in ("failed", "stopped"):
            return s.status  # a map whose items could not be read
        if node.kind == "map":
            if s is None or s.items is None:
                return "pending"
            do = node.children[0]
            kids = [self._state(st, do, f"{inst}.{k}.{do.id}") for k, _ in s.items]
        else:
            prefix = inst[: -len(node.id)]
            kids = [self._state(st, c, prefix + c.id) for c in node.children]
        return _fold(kids)

    def _scope(self, st: RunState, wf: Workflow, prefix: str) -> dict:
        """What ``{{...}}`` reads at an instance under ``prefix``: args, each
        enclosing map's item under its ``as``, and every agent and map seen
        from here by its id. The lint has already refused a name that may
        not have a value yet."""
        scope: dict = {"args": st.args}
        parts = prefix.split(".")[:-1]  # "m.k.j.l." -> m, k, j, l
        for i in range(0, len(parts), 2):
            m = self._template(wf, parts[i])
            items = st.nodes[".".join(parts[: i + 1])].items or []
            scope[m.body["as"]] = dict((k, v) for k, v in items)[parts[i + 1]]
        depth = len(parts) // 2
        early: dict = {}
        for n in walk(wf.root):
            if (
                n.kind in ("agent", "map")
                and len(n.maps) <= depth
                and n.maps == tuple(parts[0 : 2 * len(n.maps) : 2])
            ):
                inst = self._resolve(n, prefix)
                v = self._value(st, n, inst)
                if v is not None:
                    scope[n.id] = v
                if n.kind == "agent" and (s := st.nodes.get(inst)) and s.outputs:
                    early[n.id] = s.outputs
        scope[""] = early  # read first by lookup, so {{a.o}} is the output
        return scope

    def _say(self, st: RunState, wf: Workflow, node: Node, prefix: str) -> str:
        """A node's say with its references read, as the card draws it; the
        template itself while a reference has no value."""
        with contextlib.suppress(KeyError, TypeError, IndexError):
            return render(node.say, self._scope(st, wf, prefix))
        return node.say

    def _resolve(self, n: Node, prefix: str) -> str:
        """The instance of ``n`` seen from a node under ``prefix``: the same
        item of every map the two share."""
        parts = prefix.split(".")[:-1]
        return "".join(f"{p}." for p in parts[: 2 * len(n.maps)]) + n.id

    def _value(self, st: RunState, n: Node, inst: str):
        """An agent's early outputs over its object result (an output wins
        a key both hold), or its other result as it is; a map's
        ``{key: {node id: value}}`` over the agents and maps in each item."""
        s = st.nodes.get(inst)
        if s is None:
            return None
        if n.kind == "agent":
            if s.result is None or isinstance(s.result, dict):
                return {**(s.result or {}), **s.outputs} or None
            return s.result
        if s.items is None:
            return None
        inside = [
            c
            for c in walk(n.children[0])
            if c.maps[-1:] == (n.id,) and c.kind in ("agent", "map")
        ]
        out = {}
        for key, _ in s.items:
            item = {c.id: self._value(st, c, f"{inst}.{key}.{c.id}") for c in inside}
            out[key] = {k: v for k, v in item.items() if v is not None}
        return out

    def wire_all(self) -> list[dict]:
        return [self.wire(r) for r in sorted(self.runs)]

    # -- what people and agents do ------------------------------------------
    def arm(self, owner: Session, text: str, args: dict) -> RunState:
        if self._in_a_node(owner):
            raise OpError(
                "nested",
                "a workflow's node, or a session it spawned, cannot arm a workflow",
            )
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

    def _reporting(self, s: Session, tool: str) -> tuple[RunState, str]:
        """The run and instance a node session reports for; refused to any
        other session, and once its run has ended."""
        wf_ref = s.workflow or {}
        st = self.runs.get(wf_ref.get("run", ""))
        if st is None or st.status != "running":
            raise OpError("not_a_node", f"only a running workflow node calls {tool}")
        return st, wf_ref["node"]

    def output(self, s: Session, name: str, value, summary: str) -> dict:
        st, inst = self._reporting(s, "workflow_output")
        n = st.nodes.get(inst)
        if n is not None and name in n.outputs:
            raise OpError(
                "output_given",
                f"output {name} was given already; a step may have started on it",
            )
        if not summary.strip():
            raise OpError("bad_output", "summary is one plain sentence for the person")
        rec = {"ev": "output", "node": inst, "name": name, "value": value}
        self._log(st.run, rec | {"summary": summary.strip()})
        self.changed()
        return {"accepted": True}

    def result(self, s: Session, value, summary: str) -> dict:
        st, inst = self._reporting(s, "workflow_result")
        node = self._template(self.defs[st.run], inst)
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
                "node": inst,
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
        self._queuing = set()
        state = await self._advance(st, wf, wf.root, "")
        if self._queuing != self._queued.get(run, set()):
            self._queued[run] = self._queuing
            self._publish(run)
        if state == "done":
            await self._end(st, "done", self._summary(st))
        elif state == "failed":
            bad = next((n for n in st.nodes.values() if n.status == "failed"), None)
            await self._end(
                st,
                "failed",
                f"{bad.say} failed: {bad.error}" if bad else "A node failed.",
            )

    async def _advance(
        self, st: RunState, wf: Workflow, node: Node, prefix: str
    ) -> str:
        """Move one instance on and say where it stands. A node not begun
        checks its after on every pass; once begun it is not checked again."""
        inst = prefix + node.id
        if node.after and self._state(st, node, inst) in ("pending", "queued"):
            for a in node.after:
                met = self._met(st, wf, prefix, a)
                if isinstance(met, str):
                    why = f"it waits on {a}, which can never come: {met}"
                    self._fail(st, inst, why, self._say(st, wf, node, prefix))
                    return "failed"
                if not met:
                    return "pending"
        if node.kind == "seq":
            for i, c in enumerate(node.children):
                s = await self._advance(st, wf, c, prefix)
                if s != "done":
                    # Begun once a child is done, as _state folds it: a seq
                    # waiting between two nodes still holds a cap's slot.
                    return "running" if i and s == "pending" else s
            return "done"
        if node.kind == "par":
            kids = [(c, prefix) for c in node.children]
            return await self._many(st, wf, kids, node.body.get("cap"))
        if node.kind == "map":
            m = st.nodes.get(inst)
            if m is not None and m.status == "failed":
                return "failed"
            if m is None or m.items is None:
                if not self._start_map(st, wf, node, prefix):
                    return "failed"
                m = st.nodes[inst]
            do = node.children[0]
            kids = [(do, f"{inst}.{k}.") for k, _ in m.items or []]
            return await self._many(st, wf, kids, node.body.get("cap"))
        if node.kind == "agent":
            return await self._agent(st, wf, node, prefix)
        raise OpError("kind", f"{node.kind} is not in this version yet")

    def _met(self, st: RunState, wf: Workflow, prefix: str, a: str) -> bool | str:
        """Whether one ``after`` entry holds for a node under ``prefix``:
        a node done, a map's item done, an agent's early output given. A
        string says why it never will."""
        target, _, sub = a.partition(".")
        t = self._template(wf, target)
        inst = self._resolve(t, prefix)
        if not sub:
            return self._state(st, t, inst) == "done"
        s = st.nodes.get(inst)
        if t.kind == "map":
            if s is None or s.items is None:
                return False
            if sub not in {k for k, _ in s.items}:
                return f"map {target} has no item {sub}"
            do = t.children[0]
            return self._state(st, do, f"{inst}.{sub}.{do.id}") == "done"
        if s is not None and sub in s.outputs:
            return True
        if s is not None and isinstance(s.result, dict) and sub in s.result:
            return True  # given in the result rather than as an early output
        if s is not None and s.status == "done":
            return f"{target} finished without its output {sub}"
        return False

    async def _many(
        self, st: RunState, wf: Workflow, kids: list[tuple[Node, str]], cap: int | None
    ) -> str:
        """Branches side by side, at most ``cap`` begun and not finished. A
        branch is begun when its derived state is running or queued, so a
        restart counts the same branches again from the fold."""
        now = [self._state(st, n, p + n.id) for n, p in kids]
        flight = sum(s in ("running", "queued") for s in now)
        out = []
        for (n, p), s in zip(kids, now):
            if s == "pending" and cap is not None and flight >= cap:
                out.append(s)
                continue
            s2 = s if s == "done" else await self._advance(st, wf, n, p)
            if s2 == "failed":
                return "failed"
            flight += (s2 in ("running", "queued")) - (s in ("running", "queued"))
            out.append(s2)
        return _fold(out) if out else "done"

    def _start_map(self, st: RunState, wf: Workflow, node: Node, prefix: str) -> bool:
        """Read the items once and record them; a restart reads the record."""
        inst = prefix + node.id
        try:
            items = value_of(node.body["over"], self._scope(st, wf, prefix))
        except (KeyError, TypeError, IndexError) as e:
            self._fail(
                st, inst, f"over has no value: {e}", self._say(st, wf, node, prefix)
            )
            return False
        if not isinstance(items, list):
            why = f"over is a {type(items).__name__}, not a list"
            self._fail(st, inst, why, self._say(st, wf, node, prefix))
            return False
        keys = item_keys(items)
        if isinstance(keys, str):
            self._fail(st, inst, keys, self._say(st, wf, node, prefix))
            return False
        self._log(
            st.run,
            {
                "ev": "map_items",
                "node": inst,
                "items": [[k, v] for k, v in zip(keys, items)],
            },
        )
        return True

    async def _agent(self, st: RunState, wf: Workflow, node: Node, prefix: str) -> str:
        inst = prefix + node.id
        n = st.nodes.get(inst)
        if n is None or n.status == "pending":
            try:
                started = await self._start_agent(st, wf, node, prefix)
            except Exception as e:
                log.exception("aegis: node %s of %s did not start", inst, st.run)
                self._fail(st, inst, f"it did not start: {getattr(e, 'message', e)}")
                return "failed"
            if (st.nodes.get(inst) or NodeState()).status == "failed":
                return "failed"
            if not started:
                self._queuing.add(inst)
                return "queued"
            return "running"
        if n.status in ("done", "failed"):
            return n.status
        s = self._reg.sessions.get(n.log_id or "")
        if s is None:
            self._fail(st, inst, "its session is gone")
            return "failed"
        if not self._reg.finished(s):
            return "running"
        if n.result is not None or n.summary:
            self._log(st.run, {"ev": "node_done", "node": inst})
            with contextlib.suppress(Exception):
                await self._reg.close(s.log_id)
            return "done"
        if not n.nudged:
            self._log(st.run, {"ev": "nudged", "node": inst})
            await s.deliver(
                f"> from workflow:{self._wid(st)} · nudge · {iso_now()}",
                "Your turn ended without workflow_result. Call it now with your result and a one-sentence summary, or the workflow fails.",
                through_lock=True,
            )
            return "running"
        self._fail(st, inst, "its turn ended twice with no workflow_result")
        return "failed"

    def _room(self, st: RunState, wf: Workflow, alias: str) -> bool:
        """Whether one more session of agent ``alias`` fits in every counter
        that applies: the server's max_agents total and its number for the
        alias, counting every open session that is a live run's node or was
        spawned under one (a session whose node is done but not yet closed
        still holds memory), then the run's limits total and its number for
        the alias, counting the same sessions of this run. An alias without
        a number of its own is bound only by the totals. The quota gate is
        asked after this."""
        live = [
            (s, run)
            for s in self._reg.sessions.values()
            if (run := self._run_of(s)) is not None and self.live(run)
        ]
        mine = [s for s, run in live if run == st.run]
        pool = self._app.config.current().workflows["max_agents"]
        for bound, among in ((pool, [s for s, _ in live]), (wf.limits, mine)):
            if len(among) >= bound.get("total", math.inf):
                return False
            if (
                alias in bound
                and sum(s.spec.agent == alias for s in among) >= bound[alias]
            ):
                return False
        return True

    def admit(self, parent: Session, alias: str) -> None:
        """Refuse a session that a node, or a session under one, spawns past
        the pool or its run's limits: it holds memory as a node does. The
        spawn is refused rather than held, so the node's turn goes on."""
        run = self._run_of(parent)
        wf = self.defs.get(run or "")
        if run is None or wf is None or not self.live(run):
            return
        if not self._room(self.runs[run], wf, alias):
            raise OpError(
                "no_room",
                f"you work inside workflow run {run}, and a session you spawn "
                f"counts in the server's workflows max_agents and the run's "
                f"limits, which have no room for another {alias}: do the work "
                "in this session",
            )

    async def _start_agent(
        self, st: RunState, wf: Workflow, node: Node, prefix: str
    ) -> bool:
        """Spawn and prompt one agent instance; False when it did not start
        (no room, a failure already logged, or the run ended meanwhile)."""
        inst = prefix + node.id
        owner = self.owner(st)
        if owner is None:
            self._fail(st, inst, "the main agent's session is closed")
            return False
        scope = self._scope(st, wf, prefix)
        try:
            prompt, say = render(node.body["prompt"], scope), render(node.say, scope)
            cwd = render(node.body["cwd"], scope) if node.body.get("cwd") else None
        except (KeyError, TypeError, IndexError) as e:
            self._fail(st, inst, f"a reference has no value: {e}")
            return False
        agents, default = self._app._agents()
        spec = resolve(
            agents,
            default,
            node.body["agent"],
            {},
            self._app._resolve_cwd(cwd, owner.spec.cwd),
            spawned_by=owner.log_id,
            owner=owner.spec.owner,
        )
        tag = {"run": st.run, "node": inst}
        # A crash between a spawn and its record leaves the session behind:
        # it is this node's, so it is adopted rather than doubled.
        s = next((x for x in self._reg.sessions.values() if x.workflow == tag), None)
        if s is None:
            if not self._room(st, wf, node.body["agent"]):
                return False  # a session closing wakes a pass (Registry.archive -> changed)
            g = self._app.gate(spec.owner, spec.harness, spec.model)
            if not g.may_start:
                return False  # the gate's next reading wakes a pass (app.on_quota -> changed)
            s = await self._reg.spawn(spec, title=say, quiet=True, workflow=tag)
        if st.status != "running":  # stopped while it spawned: never prompted
            with contextlib.suppress(Exception):
                await self._reg.close(s.log_id)
            return False
        self._log(
            st.run,
            {"ev": "node_started", "node": inst, "log_id": s.log_id, "say": say},
        )
        schema = json.dumps(node.body.get("result") or {"type": "object"})
        handle = owner.handle
        text = (
            f"> from workflow:{self._wid(st)} · node {inst} · {iso_now()}\n{prompt}\n\n"
            + PREAMBLE.format(
                node=inst,
                wid=self._wid(st),
                title=wf.title,
                owner=handle,
                schema=schema,
            )
        )
        wanted = wf.waited.get(node.id)
        if wanted:
            text += (
                f"\nOther steps wait on your early output{'s' if len(wanted) > 1 else ''} "
                + ", ".join(f"`{w}`" for w in wanted)
                + ": as soon as one is ready, call workflow_output with its name, "
                "its value and a one-sentence summary, then go on."
            )
        await s.send(text, through_lock=True)
        return True

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
        # Read before the record, which folds them to stopped: a failure
        # stops the nodes running beside it, as a stop does.
        running = [n for n in st.nodes.values() if n.status == "running"]
        if outcome == "stopped":
            self._log(st.run, {"ev": "stopped"})
        else:
            self._finish(st, outcome)
        for n in running:
            s = self._reg.sessions.get(n.log_id or "")
            if s is not None and s.status == "working":
                with contextlib.suppress(Exception):
                    await s.interrupt(through_lock=True)
        self._release(st)
        # Nothing of an ended run waits for room, and the room it held is
        # free now: a queued node of another run starts on this wake.
        self._queued.pop(st.run, None)
        self._publish(st.run)
        self.changed()
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

    def _fail(self, st: RunState, node: str, why: str, say: str = "") -> None:
        rec = {"ev": "failed", "node": node, "error": why}
        self._log(st.run, rec | ({"say": say} if say else {}))
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

    def _in_a_node(self, s: Session) -> bool:
        return self._run_of(s) is not None

    def _run_of(self, s: Session) -> str | None:
        """The run whose node ``s`` is, or the first session up its
        spawned_by line is, archived ones read from their meta."""
        lid: str | None = s.log_id
        seen: set[str] = set()
        while lid and lid not in seen:
            seen.add(lid)
            o = self._reg.sessions.get(lid)
            meta: dict = (
                {"workflow": o.workflow, "spawned_by": o.spec.spawned_by}
                if o is not None
                else self._reg.archived.get(lid) or {}
            )
            if meta.get("workflow"):
                return meta["workflow"].get("run", "")
            lid = meta.get("spawned_by")
        return None

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
