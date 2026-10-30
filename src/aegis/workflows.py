"""The workflow engine (docs/superpowers/specs/2026-10-15-dynamic-workflows-design.html §05).

A run is armed by its main agent, approved by a person, and then driven here:
each node becomes something aegis already has, and everything that happens is
appended to the run's log (workflow_log.py), which boot folds back. Like the
schedules' clock, the engine is level-triggered: each pass looks at every
live run, so a missed hook only changes how soon a node moves.

This version runs every kind. An sh and an sh probe are
asyncio tasks the engine holds (``_tasks``), never awaited by a pass: each
logs its outcome, or keeps it in memory for the card, and wakes one. An sh
running in the log with no task after a boot was cut by the restart and
fails, never run again; a wait resumes on its logged end. A judge is a
quiet session in the pool, as an agent node is. An if's branch, a loop's
rounds and each until are logged once, so a restart never asks a probe
again. Every deadline on the path from the root is checked on each pass
against wall time, on every instance not yet done (a finished or skipped one,
leaf or group, never expires); a leaf with retries left goes back to pending. Every node
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
ending once. One run's exception fails that node and never stalls the others.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import math
import os
import secrets
import signal
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from . import layout as canvas
from . import launch, refs, restarts
from .agents import PERMISSION_ORDER, resolve
from .monitors import CONDITION_LIMIT_S, _percent, iso_now
from .ops import OpError
from .workflow_lang import (
    MAX_ROUNDS,
    RENDER_ERRORS,
    ExprError,
    LintError,
    Node,
    Workflow,
    check_value,
    compile_expr,
    next_stop,
    duration,
    edit_diff,
    edit_errors,
    evaluate,
    find,
    fmt_duration,
    item_keys,
    parse,
    render,
    rows,
    shell_command,
    template_key,
    value_of,
    walk,
    with_defaults,
)
from .workflow_log import LIVE, NodeState, RunLog, RunState, apply, fold
from .workflow_store import Saved, check_named

if TYPE_CHECKING:
    from .app import App
    from .session import Session

log = logging.getLogger(__name__)
PUBLISH_S = 0.25  # a run's wire goes out at most this often
PASS_S = 30.0  # a pass at least this often, as the schedules' clock does
CUT = "cut by restart: it may have run in part; rerun or skip?"
NUL = "the command holds a NUL byte, which bash cannot take"
SH_LIMIT_S = 1800.0  # an sh with no deadline over it
CHECK_LIMIT_S = 3600.0  # a check with no deadline over it, a monitor's default
SH_PROGRESS_EVERY_S = 15.0  # an sh's progress command runs this often (#682)
TELL_RETRY_S = 1.0  # a failed wake is tried again after this, doubling to 30 s


def tell_backoff(fails: int) -> float:
    """The wait before the next try at a wake that failed ``fails`` times
    in a row. The exponent is capped: past 2 ** 1023 a float overflows,
    and a main agent down for hours fails that often."""
    return min(TELL_RETRY_S * 2 ** min(fails - 1, 16), 30.0)


TAIL = 4000  # the end of an sh's stdout and of its stderr kept as its value
TAIL_LINES = 50  # what a failed or timed-out sh keeps of its output, to read why
LEAVES = frozenset({"agent", "sh", "wait", "check", "gate"})  # logged; the rest derived
STARTS = frozenset(
    {
        "node_started",
        "judge_started",
        "map_items",
        "loop_started",
        "branch",
        "gate_opened",
        "probe_started",
    }
)


async def _tail(stream, seen: dict | None = None, name: str = "") -> str:
    """A stream's last TAIL characters, read in chunks so a chatty command
    never holds more than a few of them. ``seen[name]`` holds them as they
    come, so a command cut by its deadline still says where it was."""
    buf = b""
    while chunk := await stream.read(65536):
        buf = (buf + chunk)[-TAIL * 4 :]
        if seen is not None:
            seen[name] = buf
    return buf.decode("utf-8", "replace")[-TAIL:]


def last_lines(out: str, err: str) -> str:
    """The end of a command's output, stdout then stderr, TAIL_LINES at
    most: what a failure shows of why."""
    lines = (out.rstrip() + "\n" + err.rstrip()).strip().splitlines()
    return "\n".join(lines[-TAIL_LINES:])


async def run_sh(
    cmd: str,
    cwd: str,
    on_pid,
    env: dict[str, str] | None = None,
    seen: dict | None = None,
    *,
    user: str | None,
) -> tuple[int, str, str]:
    """An sh node's command under bash, with ``env`` (its values, see
    shell_command) added to the server's, in its own process group, so a
    deadline, a stop or a shutdown ends everything it started: cancelling
    the task ends the group, SIGTERM first and SIGKILL 2 s later, or as
    soon as the group is empty. The SIGKILL goes even when bash has
    exited, since a member may ignore SIGTERM and outlive it."""
    proc = await launch.child(
        ["bash", "-c", cmd],
        cwd=cwd,
        env={**os.environ, **(env or {})},
        stdout=launch.PIPE,
        stderr=launch.PIPE,
        new_session=True,
        user=user,
        via=launch.resolve_via(),
    )
    on_pid(proc.pid)
    try:
        out, err = await asyncio.gather(
            _tail(proc.stdout, seen, "out"), _tail(proc.stderr, seen, "err")
        )
        await proc.wait()
    except asyncio.CancelledError:
        await _signal_group(proc.pid, signal.SIGTERM, user)
        end = time.monotonic() + 2
        while time.monotonic() < end and restarts.group_alive(proc.pid):
            await asyncio.sleep(0.05)
        await _signal_group(proc.pid, signal.SIGKILL, user)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(asyncio.shield(proc.wait()), 2)
        raise
    return proc.returncode or 0, out, err


async def _signal_group(pgid: int, sig: signal.Signals, user: str | None) -> None:
    try:
        await launch.signal_process_async(pgid, sig, user=user, group=True)
    except OSError as e:
        log.warning(
            "workflow: could not send %s to group %s as %s: %s",
            sig.name,
            pgid,
            user,
            e,
        )


def _news(o: dict) -> bool:
    """Whether an open item only tells the main agent something: a person's
    gate opened or passed. Nothing waits on its answer."""
    return o["kind"] == "passed" or (o["kind"] == "gate" and o.get("by") == "person")


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
If you cannot go on without an answer, call workflow_ask with your question and end your turn: it reaches the main agent, never a person, and the answer arrives as your next message. No person reads this session while the workflow runs."""

MAIN_PREAMBLE = """---
This is step `{node}` of your workflow, and it is yours to do. When it is done, call workflow_result with `node` "{node}", `value` matching {schema} and a one-sentence `summary`."""

REUSED = """---
This is step `{node}` of the same workflow, in the session you already have. When it is done, call workflow_result with `value` matching this JSON Schema and `summary`, one plain sentence telling the person what you did:
{schema}"""

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {"pass": {"type": "boolean"}, "why": {"type": "string"}},
    "required": ["pass", "why"],
}
JUDGE_PREAMBLE = """---
You judge step `{node}` of the workflow `{wid}` ("{title}"), which {owner} armed and a person approved. Decide from what you are given, change nothing, and call workflow_result with `value` {{"pass": true or false, "why": "one sentence"}} and `summary`, the same sentence for the person. Nobody answers questions here."""


def _lids(n: NodeState) -> list[str]:
    """Every session a node instance holds: its agent's and its judges'."""
    return [x for x in [n.log_id, *(j.get("log_id") for j in n.judges.values())] if x]


LAUNCH_TTL_S = 86400.0
LAUNCH_KEEP_S = 3600.0  # a spent or expired token is remembered this long

LAUNCH = """{who} typed: {line}

That launches the saved workflow {id}, "{title}". Make its args from the text and from what you know. They must match this JSON Schema, and a key it does not name is refused:
{schema}

Then call workflow_arm with id="{id}", token="{token}" and those args. The run starts at once: {why}. The token works once, only in this session and only for this workflow. If a required value cannot be filled in, ask the person instead{alone}."""


@dataclass
class Launch:
    log_id: str  # the session it was given to
    id: str  # the saved workflow
    sha: str
    text: str  # the definition as it was at the line
    typed: str  # what followed the id
    origin: dict  # {by: person, principal, line} | {by: schedule, name, run}
    expires: float
    used: bool = False


class Launches:
    """One-time launch tokens (spec §06). Only the server mints one, on two
    paths a person's action reached: the composer's /workflow and /loop,
    which run inside session.send, an operation no agent can call; and the
    fire of a schedule that is not waiting for a person's approval. A token
    is bound to one session, one saved workflow and its text at that
    moment, and it is never returned by an operation: it reaches the agent
    only in the launch turn. ``check`` never spends it, so an arm refused
    for its args can be tried again, and another session's try leaves it
    alone; ``spend`` is called by the arm that creates the run. Tokens live
    in memory: a restart voids them, and the agent asks again."""

    def __init__(self) -> None:
        self.clock = time.monotonic
        self._grants: dict[str, Launch] = {}

    def issue(self, log_id: str, saved, typed: str, origin: dict) -> str:
        now = self.clock()
        self._grants = {
            k: g for k, g in self._grants.items() if g.expires + LAUNCH_KEEP_S > now
        }
        token = secrets.token_urlsafe(16)
        self._grants[token] = Launch(
            log_id, saved.id, saved.sha, saved.text, typed, origin, now + LAUNCH_TTL_S
        )
        return token

    def check(self, token: str, log_id: str, wid: str) -> Launch:
        g = self._grants.get(token)
        if g is None:
            why = "aegis did not issue it, or the server restarted since"
        elif g.log_id != log_id:
            why = "it was given to another session"
        elif g.used:
            why = "it was already used: one line starts one run"
        elif self.clock() > g.expires:
            why = "it expired: a launch token lasts 24 hours"
        elif g.id != wid:
            why = f"it is for workflow {g.id}, not {wid}"
        else:
            return g
        raise OpError(
            "bad_token",
            f"that launch token does not work: {why}. Ask the person to type the "
            "/workflow line again; arming without a token shows them a card to approve.",
        )

    def spend(self, g: Launch) -> None:
        g.used = True


class Workflows:
    def __init__(self, app: App) -> None:
        self.launches = Launches()
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
        # The commands and polling probes running now, by (run, instance):
        # no pass ever awaits one. A running sh with no task here was cut
        # by a restart.
        self._tasks: dict[tuple[str, str], asyncio.Task] = {}
        # A running sh's output so far, by (run, instance): a deadline that
        # cuts it keeps its last lines.
        self._seen_out: dict[tuple[str, str], dict[str, bytes]] = {}
        # A probe's last reading, for the card; never logged.
        self._readings: dict[tuple[str, str], str] = {}
        # A progress command's last number, 0 to 100, for the card's bar;
        # never logged, so a restart reads it again.
        self._percent: dict[tuple[str, str], int] = {}
        # An if's or an until's sh probe outcome, by (run, instance, round),
        # until a pass reads it; a restart loses it and the probe runs again.
        self._probed: dict[tuple[str, str, str], dict] = {}
        # The nearest wall time a pass must look again (a wait's end).
        self._next_due = math.inf
        # Failed deliveries to each run's main agent in a row, for the
        # backoff before the next try.
        self._tell_fails: dict[str, int] = {}
        # The (run, instance) of each node answered whose next turn has not
        # ended: its last turn ended before the answer, so it is not
        # settled yet. A restart forgets it, as it forgets a probe's task.
        self._answered: set[tuple[str, str]] = set()
        # The runs changed since their wire last went out, and the flush that
        # will send them (_publish).
        self._dirty: set[str] = set()
        self._flush_at: asyncio.TimerHandle | None = None
        # The runs whose wire raised and has not gone out since: logged once.
        self._unpublishable: set[str] = set()

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
                # A crash left this run's commands running: end them before
                # any pass, so a cut sh is never still running (spec §05).
                for n in st.nodes.values():
                    if n.status == "running" and n.pid:
                        restarts.end_group(n.pid, n.pstart, user=self.linux_user(st))
                try:
                    wf, errors = parse(st.text, self._agents())
                except Exception as e:  # one run's text never stops the boot
                    log.exception(
                        "workflow %s: its definition broke the parser", st.run
                    )
                    wf, errors = None, [LintError("$", f"the parser failed: {e}")]
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
        # No command outlives a clean stop: each task ends its process group.
        tasks = list(self._tasks.values())
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._publish_now()

    async def _loop(self) -> None:
        for st, why in self._boot_failed:
            await self._tell_owner(st, "failed", f"The workflow failed: {why}.")
        self._boot_failed.clear()
        for st in list(self.runs.values()):
            if st.status == "rejected" and st.reject_untold:
                await self._tell_rejected(st)
            if st.status == "running" and st.approve_untold:
                await self.tell_approved(st.run)
        while True:
            # Cleared before the pass: a wake that arrives during it is
            # another pass, not lost.
            self._wake.clear()
            try:
                await self.step()
            except Exception:
                log.exception("aegis: a workflow pass failed")
            # Woken by a record, or at the nearest end a pass asked for.
            wait = max(0.05, min(PASS_S, self._next_due - time.time()))
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), wait)

    def changed(self) -> None:
        self._wake.set()

    def turn_ended(self, s: Session) -> None:
        """A session's turn ended: a node answered has now had its turn."""
        w = s.workflow or {}
        if w.get("probe") != "judge":
            run = w.get("run", "")
            self._answered.discard((run, w.get("node", "")))
            # A reused session's turn is the step running in it now.
            st = self.runs.get(run)
            for k, n in st.nodes.items() if st else ():
                if n.log_id == s.log_id:
                    self._answered.discard((run, k))
        self.changed()

    def _due_at(self, t: float) -> None:
        """Ask for a pass at wall time ``t``, sooner if another asked."""
        self._next_due = min(self._next_due, t)

    # -- reading --------------------------------------------------------------
    def live(self, run: str) -> bool:
        st = self.runs.get(run)
        return st is not None and st.status in LIVE

    def lock(self, s: Session) -> str | None:
        """Why nobody but the engine may steer ``s``, a node's session, or
        None. It is the run's while the run is live. Once its step is done
        and a later step may still continue in it (``session:``), the reason
        names those steps and says the run closes it once none can (#678)."""
        ref = s.workflow
        if not ref or not self.live(ref["run"]):
            return None
        run, inst = ref["run"], ref["node"]
        st, wf = self.runs.get(run), self.defs.get(run)
        n = st.nodes.get(inst) if st is not None else None
        head = f"{s.handle} is node {inst} of workflow {run}"
        if (
            st is None
            or wf is None
            or n is None
            or ref.get("probe")
            or n.status not in ("done", "skipped")
        ):
            return f"{head}; stop the workflow to talk to it"
        later = self._may_continue(st, wf, inst, s.log_id)
        if not later:
            return f"{head}; stop the workflow to talk to it"
        return (
            f"{head}, which is done, but {', '.join(later)} may still continue "
            "in its session, so the run keeps it open and closes it once none "
            "can; stop the workflow to talk to it or close it sooner"
        )

    def _may_continue(
        self, st: RunState, wf: Workflow, inst: str, log_id: str
    ) -> list[str] | None:
        """The ids of the steps that may still run in the session ``log_id``
        that step ``inst`` ran in, as _close_spent decides them; None when
        its owner's instance is not found."""
        key = template_key(inst)
        owner = wf.by_key.get(wf.session_of.get(key, key))
        if owner is None:
            return None
        oinst = next(
            (
                i
                for i, x in st.nodes.items()
                if template_key(i) == owner.key and x.log_id == log_id
            ),
            None,
        )
        if oinst is None:
            return None
        prefix = oinst[: -len(owner.id)]
        return [
            u.id
            for u in (wf.by_key[k] for k in wf.users.get(owner.key, []))
            if self._may_run(st, wf, owner, u, prefix)
        ]

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

    def linux_user(self, st: RunState) -> str | None:
        """The Linux user whose process group a persisted run may still own."""
        lid, user, seen = st.owner, None, set()
        while lid and lid not in seen:
            seen.add(lid)
            s = self._reg.sessions.get(lid)
            if s is not None:
                user, nxt = s.spec.linux_user, s.continued_by
            else:
                meta = self._reg.archived.get(lid)
                if meta is None:
                    break
                user, nxt = meta.get("linux_user"), meta.get("continued_by")
            lid = nxt if nxt != "starting" else None
        return user

    def owns(self, log_id: str | None, st: RunState) -> bool:
        o = self.owner(st)
        return log_id == (o.log_id if o is not None else st.owner)

    def definition(self, run: str) -> Workflow | None:
        """A run's definition. An ended run read back at boot is parsed once,
        not on every subscribe; only a running run advances, so caching it
        moves nothing."""
        wf = self.defs.get(run)
        if wf is None and (st := self.runs.get(run)) is not None:
            wf = parse(st.text, self._agents())[0]
            if wf is not None:
                self.defs[run] = wf
        return wf

    def wire(self, run: str, detail: bool = True) -> dict:
        """A run as browsers see it. Without ``detail``, its summary: no
        ``tree`` and no ``story``, which were 96% of a run's bytes and only its
        card draws (#525); the rest of the client reads the summary."""
        st = self.runs[run]
        wf = self.definition(run)
        out = {
            "run": run,
            "id": wf.id if wf else "",
            "title": wf.title if wf else "",
            "say": wf.say if wf else "",
            "owner": st.owner,
            "status": st.status,
            "created": st.created,
            "started": st.started,
            "ended": st.ended,
            "args": st.args,
            "saved": st.saved,
            "launch": st.launch,
            # What the person approves to run without asking, and where.
            "cwd": st.cwd,
            "commands": self._commands(st.args, wf) if wf else {"sh": 0, "probes": 0},
            "version": st.version,
            "replaced_by": st.replaced_by,
            # The edit waiting for the person, without its text.
            "proposal": (
                {k: v for k, v in st.proposal.items() if k != "text"}
                if st.proposal
                else None
            ),
            "edits": st.edits,
            "open": [
                {
                    "to": to,
                    "kind": o["kind"],
                    "node": o.get("node"),
                    "by": o.get("by"),
                    "text": self._open_text(st, to, o),
                }
                for to, o in st.open.items()
            ],
        }
        if detail:
            out["tree"] = self._rows(st, wf) if wf else []
            out["story"] = st.story
        return out

    def taken(self, run: str, to: str | None) -> dict:
        """The short reply to an answer or a stop: the run's status, the
        state of what it named, and what the run still waits on. The whole
        tree is workflow_status's."""
        st, wf = self.runs[run], self.definition(run)
        out: dict = {"run": run, "status": st.status}
        if to and wf is not None and template_key(to) in wf.by_key:
            out |= {"node": to, "state": self._state(st, self._template(wf, to), to)}
        out["open"] = [
            {"to": k, "kind": o["kind"], "text": self._open_text(st, k, o)}
            for k, o in st.open.items()
        ]
        return out

    def _open_text(self, st: RunState, to: str, o: dict) -> str:
        n = st.nodes.get(o.get("node") or "") or NodeState()
        if o["kind"] == "ask":
            return o.get("question") or ""
        if o["kind"] == "gate":
            return (n.gate or {}).get("ask") or n.say
        if o["kind"] == "failed":
            return o.get("error") or n.error
        if o["kind"] == "passed":
            return "continued by the person"
        return o.get("outcome") or ""

    def _rows(self, st: RunState, wf: Workflow) -> list[dict]:
        """The card's rows, each with its state, as the wire carries them."""

        def items(path: str):
            n = st.nodes.get(path)
            if n is None or wf is None:
                return None
            if self._template(wf, path).kind == "loop":
                return (
                    None
                    if n.max is None
                    else [[str(k), None] for k in range(1, n.round + 1)]
                )
            return n.items

        tree = [self._row(st, wf, r) for r in rows(wf, items)]
        # Under a branch an if did not take, every row is skipped too.
        under: int | None = None
        for r in tree:
            if under is not None and r["depth"] > under:
                r["state"] = "skipped"
                continue
            under = r["depth"] if r["state"] == "skipped" else None
        return tree

    def _seen(self, st: RunState, wf: Workflow) -> dict[str, str]:
        """What has started, by template key, as edit_errors reads it: a
        leaf is running if an instance runs, parked if one failed or
        stopped, ran once one finished; a group is finished when every
        instance is done, begun once one has moved. Keys nothing started
        are left out."""
        by: dict[str, list[str]] = {}
        for r in self._rows(st, wf):
            if r.get("each"):
                continue
            if wf.by_key[r["node"]].kind in LEAVES:
                s = (st.nodes.get(r["id"]) or NodeState()).status
            else:
                s = r["state"]
            by.setdefault(r["node"], []).append(s)
        out: dict[str, str] = {}
        for key, states in by.items():
            if wf.by_key[key].kind in LEAVES:
                if "running" in states:
                    out[key] = "running"
                elif "failed" in states or "stopped" in states:
                    out[key] = "parked"
                elif "done" in states or "skipped" in states:
                    out[key] = "ran"
            elif all(s == "done" for s in states):
                out[key] = "finished"
            elif any(s not in ("pending", "queued", "skipped") for s in states):
                out[key] = "begun"
        return out

    def _commands(self, args: dict, wf: Workflow) -> dict:
        """The shell commands and sh probes a person approves, in all and
        grouped by how often each runs: once, for each of a map's items, or
        each round of a loop, counted from the definition and the args. A
        loop's own until probe runs each of its rounds."""
        groups: dict[str, dict] = {}
        for n in walk(wf.root):
            sh, probe = n.kind == "sh", "sh" in (n.body.get("probe") or {})
            if sh or probe:
                g = groups.setdefault(self._per(args, wf, n), {"sh": 0, "probes": 0})
                g["sh" if sh else "probes"] += 1
                g["probes"] += "progress" in n.body
        return {
            "sh": sum(g["sh"] for g in groups.values()),
            "probes": sum(g["probes"] for g in groups.values()),
            "groups": [{"per": per, **g} for per, g in groups.items()],
        }

    def _per(self, args: dict, wf: Workflow, node: Node) -> str:
        """How often a node runs, from the maps and loops around it: "for
        each of 3 items", "each round, up to 4 rounds", or "" for once."""
        bits, x = [], node if node.kind == "loop" else wf.parent.get(node.key)
        scope = {"args": args}
        while x is not None:
            if x.kind in ("map", "loop"):
                try:
                    v = value_of(x.body["over" if x.kind == "map" else "max"], scope)
                except RENDER_ERRORS:
                    v = None  # read from a step's value, known only when it runs
                if x.kind == "map":
                    k = len(v) if isinstance(v, list) else None
                    many = f"for each of {k} item{'s' if k != 1 else ''}"
                    bits.append(many if k else "for each item")
                else:
                    known = isinstance(v, int) and not isinstance(v, bool)
                    up = f"each round, up to {v} round{'s' if v != 1 else ''}"
                    bits.append(up if known else "each round")
            x = wf.parent.get(x.key)
        return ", ".join(bits)

    def _row(self, st: RunState, wf: Workflow, row: dict) -> dict:
        # By key: a row drawn once for a map not started has no item in its id.
        node, inst = wf.by_key[row["node"]], row["id"]
        prefix = inst[: -len(node.id)]
        each = row.get("each", False)
        row["state"] = "pending" if each else self._state(st, node, inst)
        if not each and self._held(st, inst, row["state"]):
            row["held"] = True
        if not each and (st.nodes.get(inst) or NodeState()).status == "skipped":
            row["state"] = "skipped"  # done for the engine, drawn as skipped
        took = (st.nodes.get(row.get("if", "")) or NodeState()).took
        if row.get("branch") and took is not None and took != row["branch"]:
            row["state"] = "skipped"
        n = st.nodes.get(inst)
        if n is not None and n.say:
            row["say"] = n.say
        elif not each:
            with contextlib.suppress(*RENDER_ERRORS):
                row["say"] = render(row["say"], self._scope(st, wf, prefix))
        if n is not None:
            h = self._reg.sessions.get(n.log_id or "")
            # What the step's session has cost so far, open or archived, for
            # the card's spent and estimated total (#712).
            cost = (
                h.cost_usd
                if h
                else (self._reg.archived.get(n.log_id or "") or {}).get("cost_usd")
            )
            row |= {
                "log_id": n.log_id,
                "handle": h.handle if h else None,
                "cost": cost,
                "started": n.started,
                "ended": n.ended,
                "summary": n.summary,
                "error": n.error,
                "outputs": sorted(n.outputs),
                "attempts": n.attempts,
            }
            if n.cmd and (node.kind == "sh" or "sh" in (node.body.get("probe") or {})):
                row["cmd"] = n.cmd  # with its values, to read; they ran as env
            if node.kind == "loop" and n.max is not None:
                row["rounds"] = {"round": n.round, "max": n.max}
            if n.gate is not None:
                row |= {"ask": n.gate.get("ask"), "shown": n.gate.get("shown") or {}}
                row["by"] = n.gate.get("by") or row.get("by")
            if n.tail:
                row["tail"] = n.tail
        if node.kind == "gate" and node.key in st.delegated:
            row["delegated"] = True
            if n is None or n.gate is None:
                row["by"] = "main"  # it opens as the main agent's
        if (reading := self._readings.get((st.run, inst))) is not None:
            row["reading"] = reading
        if (percent := self._percent.get((st.run, inst))) is not None:
            row["percent"] = percent
        if node.after:
            row["after"] = [
                {
                    "name": a,
                    "met": not each and self._met(st, wf, node, prefix, a) is True,
                }
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

    def _held(self, st: RunState, inst: str, state: str) -> bool:
        """Whether an instance not begun waits for the person's answer to
        the proposal that touches it."""
        return (
            st.proposal is not None
            and template_key(inst) in (st.proposal.get("touched") or ())
            and state in ("pending", "queued")
        )

    def _template(self, wf: Workflow, inst: str) -> Node:
        """An instance's node, by its template key."""
        return wf.by_key[template_key(inst)]

    def _state(self, st: RunState, node: Node, inst: str) -> str:
        """Where an instance stands: pending, queued, running, done, failed
        or stopped. A leaf's comes from the log (queued from the last
        pass's room check); a seq's, par's and map's from their children's."""
        s = st.nodes.get(inst)
        if s is not None and s.status == "skipped":
            return "done"  # the main agent went on without it
        if node.kind in LEAVES:
            if s is None or s.status == "pending":
                # An ended run queues nothing.
                q = self._queued.get(st.run, ()) if st.status == "running" else ()
                return "queued" if inst in q else "pending"
            return s.status
        if s is not None and s.status in ("failed", "stopped"):
            return s.status  # a logged failure wins for every composite
        if node.kind == "if":
            if s is None or s.took is None:
                if (s is not None and s.judges) or (st.run, inst) in self._tasks:
                    return "running"  # its probe is deciding
                q = self._queued.get(st.run, ()) if st.status == "running" else ()
                return "queued" if inst in q else "pending"
            if s.took == "none":
                return "done"
            prefix = inst[: -len(node.id)]
            c = next(c for c in node.children if c.branch == s.took)
            return self._state(st, c, prefix + c.id)
        if node.kind == "loop":
            if s is None or s.max is None:
                return "pending"
            do = node.children[0]
            last = self._state(st, do, f"{inst}.{s.round}.{do.id}")
            if last != "done":
                return _fold([last, "running"])
            if "probe" in node.body:
                u = s.untils.get(s.round)
                if u is None:
                    return "running"  # its until has not answered yet
                if u.get("pass"):
                    return "done"
                # Spent, as _repeat ends it: no round is left to run.
                spent = s.round >= s.max and node.body.get("at_max") == "done"
                return "done" if spent else "running"
            return "done" if s.round >= s.max else "running"
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
            m = self._template(wf, ".".join(parts[: i + 1]))
            if m.kind == "loop":
                continue  # a round has no item, only its own nodes
            items = st.nodes[".".join(parts[: i + 1])].items or []
            scope[m.body["as"]] = dict((k, v) for k, v in items)[parts[i + 1]]
        depth = len(parts) // 2
        early: dict = {}
        # Inner scopes last, so a name reads the innermost node that has it.
        for n in sorted(walk(wf.root), key=lambda n: len(n.maps)):
            if (
                n.kind in ("agent", "map", "sh", "check", "loop", "gate")
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
        with contextlib.suppress(*RENDER_ERRORS):
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
        if n.kind in ("agent", "sh", "check", "gate"):
            if s.result is None or isinstance(s.result, dict):
                return {**(s.result or {}), **s.outputs} or None
            return s.result
        if n.kind == "loop":
            if not s.round:
                return None
            keys = [str(s.round)]  # the last round's, as one map item's
        elif s.items is None:
            return None
        else:
            keys = [k for k, _ in s.items]
        inside = [
            c
            for c in walk(n.children[0])
            if c.maps == (*n.maps, n.id)
            and c.kind in ("agent", "map", "sh", "check", "loop", "gate")
        ]
        out = {}
        for key in keys:
            item = {c.id: self._value(st, c, f"{inst}.{key}.{c.id}") for c in inside}
            out[key] = {k: v for k, v in item.items() if v is not None}
        return out[keys[0]] if n.kind == "loop" else out

    def see_file(
        self, run: str, node: str, check: bool = True
    ) -> tuple[Path, str | None, int | None]:
        """The file a step's see names, as its row carries it: relative
        to the run's folder unless absolute, with its #fragment or :line.
        The caller gives a run and a step, never a path (as file.peek).
        ``check`` false leaves whether the file is there to the caller,
        which asks as the run's owner (workflow_ops.py)."""
        st = self._get(run)
        row = next((r for r in self.wire(run)["tree"] if r["id"] == node), None)
        see = (row or {}).get("see")
        if not see:
            raise OpError("no_see", f"step {node} has no see to open yet")
        raw, _, fragment = see.partition("#")
        line = None
        if m := refs.LINE.search(raw):
            line, raw = int(m.group(0)[1:]), raw[: m.start()]
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = Path(self._cwd(st)) / path
        if check and not path.is_file():
            raise OpError("no_file", f"no file {raw} in {self._cwd(st)}")
        return path, fragment or None, line

    def wire_all(self) -> list[dict]:
        return [self.wire(r) for r in sorted(self.runs)]

    def summaries(self) -> list[dict]:
        return [self.wire(r, detail=False) for r in sorted(self.runs)]

    # -- what people and agents do ------------------------------------------
    def arm(
        self,
        owner: Session,
        text: str | None,
        args: dict,
        run: str | None = None,
        saved: Saved | None = None,
        launch: Launch | None = None,
    ) -> RunState:
        if self._in_a_node(owner):
            raise OpError(
                "nested",
                "a workflow's node, or a session it spawned, cannot arm a workflow",
            )
        if run is not None:
            return self._arm_again(owner, run, text, args)
        if text is None:
            raise OpError("lint", "arming needs a definition, or a run to arm again")
        cur = self.of_owner(owner.log_id)
        if cur is not None and cur.status != "armed":
            raise OpError(
                "one_run",
                f"this session already has workflow run {cur.run}; stop it first",
            )
        wf, errors = parse(text, self._agents())
        if wf is None:
            raise OpError("lint", "\n".join(f"{e.path}: {e.message}" for e in errors))
        self._check_arm(owner, wf, list(walk(wf.root)))
        args = with_defaults(args, wf.args) if wf.args else args
        problems = check_value(args, wf.args, "$.args") if wf.args else []
        if problems:
            raise OpError("bad_args", "\n".join(problems))
        run = f"{wf.id}-{secrets.token_hex(3)}"
        cwd = str(owner.spec.cwd)
        # Armed again before approval: the new card replaces the old one.
        v = cur.version + 1 if cur is not None else 1
        kept = (
            {"id": saved.id, "sha": saved.sha, "source": saved.source}
            if saved
            else None
        )
        st = RunState(
            run,
            owner.log_id,
            text,
            args,
            created=time.time(),
            cwd=cwd,
            version=v,
            saved=kept,
            launch=(started := self._started_by(launch)),
        )
        self.runs[run], self.defs[run] = st, wf
        self._log(
            run,
            {
                "ev": "armed",
                "run": run,
                "owner": owner.log_id,
                "text": text,
                "args": args,
                "cwd": cwd,
                **({"v": v} if v > 1 else {}),
                **({"saved": kept} if kept else {}),
                **({"launch": started} if started else {}),
            },
            fold_in=False,
        )
        if cur is not None:
            self._log(cur.run, {"ev": "replaced", "by": run})
        if launch is not None:
            # Approved by the launch, before its card is first drawn: the
            # card never shows an Approve button for a run already running.
            self._log(run, {"ev": "approved", "by": launch.origin["by"]})
            self.changed()
        # A new run goes out at once: its card and its pane, drawn next, must
        # never find it missing.
        self._publish_now()
        owner.record_card({"kind": "workflow", "run": run})
        if cur is not None:
            # The new card takes the old one's pane. The standing layout, not
            # owner.layout: that already trims the replaced run.
            old = f"wf-{cur.run}"
            lay = owner.standing.get("layout") or canvas.DEFAULT
            if old in canvas.ids(lay):
                owner.set_layout(canvas.rename(lay, old, f"wf-{run}"), "agent")
        self._publish(run)
        return st

    @staticmethod
    def _started_by(g: Launch | None) -> dict | None:
        """What the card says of a launch: who, and their words. Never the
        token, which is not kept anywhere but Launches."""
        return {"typed": g.typed, **g.origin} if g is not None else None

    def arm_launched(
        self, owner: Session, wid: str, args: dict, token: str
    ) -> RunState:
        """Arm what a person launched: the token's text, the agent's args
        checked closed against its schema, and approved at once by the
        launch. Every check runs before the token is spent."""
        g = self.launches.check(token, owner.log_id, wid)
        wf, errors = parse(g.text, self._agents())
        if wf is None:
            raise OpError("lint", "\n".join(f"{e.path}: {e.message}" for e in errors))
        args = with_defaults(args, wf.args) if wf.args else args
        problems = check_value(
            args,
            wf.args or {"type": "object", "properties": {}},
            "$.args",
            closed=True,
        )
        if problems:
            raise OpError("bad_args", "\n".join(problems))
        now = self._app.saved.all().get(g.id)
        source = now.source if now is not None else "project"
        saved = Saved(g.id, g.text, g.sha, source, Path())
        st = self.arm(owner, g.text, args, saved=saved, launch=g)
        self.launches.spend(g)
        return st

    def launch(self, s: Session, saved: Saved, typed: str, origin: dict) -> str:
        """The launch turn for a person's /workflow line, or for the fire of
        a schedule a person approved: a fresh token for ``s``, bound to the
        version ``saved`` the caller checked, and what the agent needs to arm
        with it. A schedule's fire mints only for the version it pins, so a
        save between its check and here cannot change what runs. Refused
        before any token exists when the run could not start."""
        wid = saved.id
        if origin["by"] == "schedule":
            sch = self._app.schedules.items.get(origin["name"])
            if sch is None or not sch.pin or sch.pin["sha"] != saved.sha:
                raise OpError(
                    "changed",
                    f"workflow {wid} is not the version schedule "
                    f"{origin['name']} was approved for",
                )
        if self._in_a_node(s):
            raise OpError("nested", "a workflow's node cannot launch a workflow")
        cur = self.of_owner(s.log_id)
        if cur is not None and cur.status == "running":
            raise OpError(
                "one_run",
                f"this session already runs workflow {cur.run}; "
                "stop it from its card first",
            )
        check_named(saved, self._agents())  # it lints, and its id is its name
        wf = parse(saved.text, self._agents())[0]
        assert wf is not None
        token = self.launches.issue(s.log_id, saved, typed, origin)
        person = origin["by"] == "person"
        return LAUNCH.format(
            who="The person" if person else f"Schedule {origin['name']}",
            line=origin.get("line") or f"/workflow {wid} {typed}",
            id=wid,
            title=wf.title,
            schema=json.dumps(wf.args or {"type": "object", "properties": {}}),
            token=token,
            why="their line is the approval"
            if person
            else "the person approved this schedule",
            alone=""
            if person
            else ", or, since nobody may be there, end your turn with needs_you",
        )

    def _arm_again(
        self, owner: Session, run: str, text: str | None, args: dict
    ) -> RunState:
        """Arm a stopped or failed run again, for a new approval: what
        finished keeps its result, every leaf that did not runs again, and
        a definition given with it changes only what an edit may. A run
        whose arming again the person rejected is armed again the same way."""
        st = self._get(run)
        again = st.status == "rejected" and st.started is not None
        if st.status not in ("stopped", "failed") and not again:
            raise OpError("not_stopped", f"{run} is {st.status}")
        if not self.owns(owner.log_id, st):
            raise OpError(
                "not_yours", "only the session that armed a run arms it again"
            )
        if (cur := self.of_owner(owner.log_id)) is not None:
            raise OpError(
                "one_run",
                f"this session already has workflow run {cur.run}; stop it first",
            )
        if args and args != st.args:
            raise OpError("bad_args", "a run armed again keeps its args")
        text = text or st.text
        # What the person last approved, not a change they since rejected.
        ok = st.approved or {}
        base: str = ok.get("text") or st.text
        base_v = int(ok.get("v") or st.version)
        old = (self.defs.get(run) if st.text == base else None) or parse(
            base, self._agents()
        )[0]
        wf, errors = parse(text, self._agents())
        if wf is None or old is None:
            raise OpError("lint", "\n".join(f"{e.path}: {e.message}" for e in errors))
        if text != base:
            errors = edit_errors(old, wf, self._seen(st, old))
            if errors:
                raise OpError(
                    "edit", "\n".join(f"{e.path}: {e.message}" for e in errors)
                )
        self._check_arm(owner, wf, list(walk(wf.root)))  # the owner may have changed
        reset: set[str] = set()
        for k, n in st.nodes.items():
            node = self._template(old, k)
            if node.kind in LEAVES and n.status not in ("done", "skipped"):
                reset.add(k)  # a leaf that did not finish runs again
            elif node.kind not in LEAVES and n.status in ("failed", "stopped"):
                # A failed group, whole; or an if stopped while its probe ran.
                reset |= {x for x, _ in self._under(st, node, k)}
        v = base_v + (text != base)
        rec = {"ev": "rearmed", "v": v, "text": text, "reset": sorted(reset)}
        if text != base:
            # The again card shows the change, as an edit's card does.
            d = edit_diff(old, wf)
            rec |= {"diff": d["diff"], "commands": d["commands"]}
        self._log(run, rec)
        self.defs[run] = wf
        self._forget_probes(run)
        owner.record_card(
            {
                "kind": "workflow_decision",
                "run": run,
                "what": "again",
                # The index of its "Armed again" line, which the card reads.
                "key": str(len(st.story) - 1),
            }
        )
        return st

    def _check_arm(self, owner: Session, wf: Workflow, nodes: list[Node]) -> None:
        """What arming and an edit refuse before a person sees a card: a
        node's agent above the owner's permission, a shell command from a
        session without full permission, a cwd that does not resolve."""
        for n in nodes:
            probe = n.body.get("probe") or {}
            # The owner runs a main step, and an earlier step's session runs
            # a reused one, each as it is: neither spawns an agent.
            main = n.body.get("session") is not None
            if n.kind == "agent" and not main:
                self._check_permission(owner, n.body["agent"], n.path)
            if "judge" in probe:
                self._check_permission(owner, probe["judge"]["agent"], n.path)
            if (n.kind == "sh" or "sh" in probe) and owner.spec.permission != "full":
                raise OpError(
                    "not_allowed",
                    f"{n.path}: a workflow's shell commands run on the server without "
                    f"asking, so only a session with full permission arms one; yours "
                    f"has {owner.spec.permission}",
                )
            if n.kind == "agent" and not main:
                cwd = n.body.get("cwd")
                if isinstance(cwd, str) and "{{" not in cwd:
                    # Refused before a person approves, not when the node starts.
                    self._app._resolve_cwd(cwd, Path(owner.spec.cwd))

    def propose_edit(self, owner: Session, run: str, text: str) -> RunState:
        """The main agent's change to its running workflow, linted against
        what has run, as a diff the person applies or keeps on a card. The
        run goes on; what the change touches waits for the answer."""
        st = self._get(run)
        if st.status != "running":
            raise OpError(
                "not_running", f"{run} is {st.status}: arm it again to change it"
            )
        new, errors = parse(text, self._agents())
        if new is None:
            raise OpError("lint", "\n".join(f"{e.path}: {e.message}" for e in errors))
        old = self.defs[run]
        errors = edit_errors(old, new, self._seen(st, old))
        if errors:
            raise OpError("edit", "\n".join(f"{e.path}: {e.message}" for e in errors))
        d = edit_diff(old, new)
        if not d["diff"]:
            raise OpError("no_change", "the definition is the one running")
        changed = [
            new.by_key[x["key"]]
            for x in d["diff"]
            if x["op"] in ("add", "change") and x["key"] != "$"
        ]
        self._check_arm(owner, new, changed)
        # A step added or moved to a seq holds the step after it too: run
        # now, that step would go before it and the change would go stale.
        for x in d["diff"]:
            p = new.parent.get(x["key"]) if x["op"] in ("add", "move") else None
            if p is not None and p.kind == "seq":
                keys = [c.key for c in p.children]
                nxt = [k for k in keys[keys.index(x["key"]) + 1 :] if k in old.by_key]
                if nxt and nxt[0] not in d["touched"]:
                    d["touched"].append(nxt[0])
        v = max([st.version, *(e["v"] for e in st.edits)]) + 1
        self._log(run, {"ev": "edit_proposed", "v": v, "text": text, **d})
        owner.record_card(
            {"kind": "workflow_decision", "run": run, "what": "edit", "key": str(v)}
        )
        return st

    async def apply_edit(self, run: str, v: int) -> None:
        """A person applies the open proposal: linted again against what
        has run since, then the run follows it, and a failed step it
        touches runs again. Takes the lock, as an answer does."""
        async with self._lock:
            st = self._get(run)
            p = st.proposal
            if p is None or p["v"] != v or st.status != "running":
                raise OpError("not_proposed", f"change v{v} of {run} is not waiting")
            new, errors = parse(p["text"], self._agents())
            if new is None:
                raise OpError(
                    "stale", "\n".join(f"{e.path}: {e.message}" for e in errors)
                )
            old = self.defs[run]
            errors = edit_errors(old, new, self._seen(st, old))
            if errors:
                raise OpError(
                    "stale",
                    "the run has moved since the change was proposed: "
                    + "; ".join(f"{e.path}: {e.message}" for e in errors),
                )
            touched = set(p.get("touched") or [])
            self._log(run, {"ev": "edit_applied", "v": v})
            self.defs[run] = new
            for k, o in list(st.open.items()):
                if o["kind"] == "failed" and template_key(k) in touched:
                    reset = self._reset_list(st, new, k)
                    rec = {"ev": "retried", "node": k, "reset": reset}
                    self._log(run, rec | {"text": f"after change v{v}."})
            self.changed()

    def reject_edit(self, run: str, v: int) -> None:
        """A person keeps the plan: the proposal closes, and what it held
        goes on as it was."""
        st = self._get(run)
        if st.proposal is None or st.proposal["v"] != v or st.status != "running":
            raise OpError("not_proposed", f"change v{v} of {run} is not waiting")
        self._log(run, {"ev": "edit_rejected", "v": v})
        self.changed()

    def approve(self, run: str) -> None:
        st = self._get(run)
        if st.status != "armed":
            raise OpError("not_armed", f"{run} is {st.status}")
        # "tell" as for a rejection: a boot tells an approval whose
        # delivery a crash cut, and no other.
        self._log(run, {"ev": "approved", "tell": True})
        self._owner_card(st)  # the click answered its ask; it waits now
        self.changed()

    async def tell_approved(self, run: str) -> None:
        """Tell the main agent its run was approved and when it next stops,
        once: the record follows the delivery, as woke does."""
        st = self._get(run)
        body = (
            f"The person approved {run} and it is running. "
            + self.next_text(st)
            + ' End this turn with turn_end(attention="review") and a short '
            "message to them, in their language: the run is going; while it "
            "runs they can write to you to steer it or ask about it, which does "
            "not stop it; and when the run next stops, as above. Your tab shows "
            "waiting on the run until then."
        )
        if await self._tell_owner(st, "approved", body):
            self._log(run, {"ev": "approved_told"})

    def next_text(self, st: RunState) -> str:
        """Where a running run next stops, from its definition: the first
        gate or main step not finished, or else its end."""
        wf = self.defs.get(st.run)
        done = {template_key(k) for k, n in st.nodes.items() if n.status == "done"}
        n = next_stop(wf, done) if wf else None
        if n is None:
            when = "calls you when it ends"
        elif (
            n.kind == "gate"
            and n.body.get("by") == "person"
            and n.key not in st.delegated
        ):
            when = (
                f'stops at the gate "{n.body.get("ask")}", which the person '
                "answers on the card"
            )
        elif n.kind == "gate":
            when = f'calls you at the gate "{n.body.get("ask")}", which you answer'
        else:
            when = f'calls you at the step "{n.say}", which you run yourself'
        return (
            f"It next {when}. A failed step or a worker's question can call you sooner."
        )

    def _owner_card(self, st: RunState) -> None:
        """Redraw the main agent's card, which waits on a running run
        (registry._waits)."""
        if (o := self.owner(st)) is not None:
            self._reg.refresh_card(o.log_id)

    async def reject(self, run: str) -> None:
        st = self._get(run)
        if st.status != "armed":
            raise OpError("not_armed", f"{run} is {st.status}")
        # "tell" marks a record whose delivery is logged after it, so a boot
        # tells a rejection whose delivery a crash cut, and no other.
        self._log(run, {"ev": "rejected", "tell": True})
        await self._tell_rejected(st)

    async def _tell_rejected(self, st: RunState) -> None:
        """Tell the main agent its plan was rejected, once: the record
        follows the delivery, as woke does for an open item."""
        run = st.run
        # Otherwise the main agent waits on a run that will never start.
        if st.started is None:
            body = (
                f"The person rejected {run} on its card, so nothing ran. Ask them "
                "what to change, then arm a new version with workflow_arm."
            )
        else:
            body = (
                f"The person rejected arming {run} again, so nothing more ran; "
                "the steps it finished are kept. Ask them what to change, then "
                f'arm it again with workflow_arm(run="{run}") to keep those '
                "steps, or arm a new version from the start."
            )
        if await self._tell_owner(st, "rejected", body):
            self._log(run, {"ev": "rejected_told"})

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

    def _reporting(
        self, s: Session, tool: str, step: str | None = None
    ) -> tuple[RunState, str]:
        """The run and instance a node session reports for; refused to any
        other session, and once its run has ended. The main agent reports
        for one of its run's open session: main steps: the one ``step``
        names, which it must name when several are open."""
        wf_ref = s.workflow or {}
        if not wf_ref:
            mine = self.of_owner(s.log_id)
            if mine is not None and mine.status == "running":
                open_ = [
                    k for k, n in mine.nodes.items() if n.main and n.status == "running"
                ]
                if step is not None:
                    if step not in open_:
                        raise OpError(
                            "not_open",
                            f"step {step} is not one of yours that is open; "
                            f"open: {', '.join(open_) or 'none'}",
                        )
                    return mine, step
                if len(open_) == 1:
                    return mine, open_[0]
                if open_:
                    raise OpError(
                        "several_steps",
                        f"steps {', '.join(open_)} are open: pass node, the step "
                        "this reports, as its header names it",
                    )
        st = self.runs.get(wf_ref.get("run", ""))
        if st is None or st.status != "running":
            raise OpError("not_a_node", f"only a running workflow node calls {tool}")
        n = st.nodes.get(wf_ref["node"]) or NodeState()
        if wf_ref.get("probe") == "judge":
            cur = (n.judges.get(wf_ref.get("round", "")) or {}).get("log_id")
        else:
            # A reused session reports for the step running in it now, not
            # the step it was spawned for; the lint and _reuse keep that to
            # one step at a time.
            now = [
                k
                for k, x in st.nodes.items()
                if x.log_id == s.log_id and x.status == "running" and not x.main
            ]
            if now:
                return st, now[0]
            cur = n.log_id
        if cur != s.log_id:
            raise OpError(
                "stale_attempt",
                "this session is an earlier attempt at its node; the node has moved on",
            )
        return st, wf_ref["node"]

    def output(
        self, s: Session, name: str, value, summary: str, step: str | None = None
    ) -> dict:
        st, inst = self._reporting(s, "workflow_output", step)
        if (s.workflow or {}).get("probe") == "judge":
            raise OpError(
                "judge", "a judge gives only its verdict, with workflow_result"
            )
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

    def result(self, s: Session, value, summary: str, step: str | None = None) -> dict:
        st, inst = self._reporting(s, "workflow_result", step)
        wf_ref = s.workflow or {}
        if wf_ref.get("probe") == "judge":
            j = st.nodes[inst].judges.get(wf_ref.get("round", "")) or {}
            if "pass" in j:
                raise OpError("verdict_given", "this judge gave its verdict already")
            problems = check_value(value, JUDGE_SCHEMA)
            if problems:
                raise OpError(
                    "bad_result",
                    "a judge's value is {pass, why}: " + "; ".join(problems),
                )
            self._log(
                st.run,
                {
                    "ev": "verdict",
                    "node": inst,
                    "round": wf_ref.get("round", ""),
                    "pass": value["pass"],
                    "why": value["why"],
                    "summary": summary.strip(),
                },
            )
            self.changed()
            return {"accepted": True}
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

    def ask(self, s: Session, question: str) -> dict:
        """A node's question for the main agent. The node ends its turn; the
        answer is its next message, and until then its silence is not a
        turn without a result."""
        if not s.workflow:
            raise OpError(
                "main_agent", "you are the main agent: ask the person in your own words"
            )
        st, inst = self._reporting(s, "workflow_ask")
        if s.workflow.get("probe") == "judge":
            raise OpError(
                "judge", "a judge decides from what it is given and asks nothing"
            )
        q = f"q{st.asks + 1}"
        self._log(
            st.run, {"ev": "asked", "node": inst, "q": q, "question": question.strip()}
        )
        self.changed()  # the pass tells the main agent
        return {
            "asked": q,
            "note": "End your turn; the answer arrives as your next message.",
        }

    def note(self, run: str, text: str) -> None:
        """A line the main agent adds to the story: why it decided as it did."""
        st = self._get(run)
        if st.status not in LIVE:
            raise OpError("not_live", f"{run} is {st.status}")
        if not text.strip():
            raise OpError("bad_note", "a note is one plain sentence for the person")
        self._log(run, {"ev": "note", "text": text.strip()})

    def exited(self, s: Session, code: int) -> None:
        wf_ref = s.workflow or {}
        st = self.runs.get(wf_ref.get("run", ""))
        if st is None or st.status != "running" or code in restarts.SIGTERM_CODES:
            return
        n = st.nodes.get(wf_ref["node"])
        if wf_ref.get("probe") == "judge":
            j = n.judges.get(wf_ref.get("round", ""), {}) if n is not None else {}
            if j.get("log_id") == s.log_id and "pass" not in j:
                why = f"its judge's session exited with code {code}"
                self._fail(st, wf_ref["node"], why)
            return
        if n is not None and n.status == "running" and n.log_id == s.log_id:
            self._fail(st, wf_ref["node"], f"its session exited with code {code}")

    # -- the pass ---------------------------------------------------------------
    async def step(self) -> None:
        async with self._lock:
            self._next_due = math.inf
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
        await self._close_spent(st, wf)
        if self._queuing != self._queued.get(run, set()):
            self._queued[run] = self._queuing
            self._publish(run)
        if state == "done" and st.proposal is None:
            # A run with a change waiting ends once the person answers it.
            await self._end(st, "done", self._summary(st))
        # A failure waits on the main agent: it ends the run only by its
        # answer, a person's stop or a limit (spec §05, slice 4).
        await self._tell_open(st)

    async def _advance(
        self, st: RunState, wf: Workflow, node: Node, prefix: str
    ) -> str:
        """Move one instance on and say where it stands. A leaf that failed
        with retries left is back to pending and runs again on the next
        pass, so it reads as running, not failed."""
        got = await self._move(st, wf, node, prefix)
        n = st.nodes.get(prefix + node.id)
        if got == "failed" and n is not None and n.status == "pending":
            return "running"
        return got

    async def _move(self, st: RunState, wf: Workflow, node: Node, prefix: str) -> str:
        """One instance's move. A node not begun checks its after on every
        pass; once begun it is not checked again. A skipped one is done."""
        inst = prefix + node.id
        mine = st.nodes.get(inst)
        if mine is not None and mine.status == "skipped":
            return "done"
        if node.after and self._state(st, node, inst) in ("pending", "queued"):
            for a in node.after:
                met = self._met(st, wf, node, prefix, a)
                if isinstance(met, str):
                    why = f"it waits on {a}, which can never come: {met}"
                    say = self._say(st, wf, node, prefix)
                    self._fail(st, inst, why, say, retry=False)
                    return "failed"
                if not met:
                    return "pending"
        if st.proposal is not None and self._held(
            st, inst, self._state(st, node, inst)
        ):
            return "pending"  # the edit touches it: it waits for the answer
        mine = st.nodes.get(inst)
        if mine is not None and mine.status == "failed":
            return "failed"
        end = self._end_at(st, wf, node, inst)
        if end is not None and self._state(st, node, inst) == "done":
            end = None  # a finished instance, leaf or group, never expires
        if end is not None:
            if time.time() >= end[0]:
                if node is wf.root:
                    # The run's own limit: nobody is asked (spec §05, slice 4).
                    await self._end(
                        st,
                        "failed",
                        f"The workflow ran past its deadline of {fmt_duration(end[1])}.",
                    )
                    return "failed"
                await self._expire(st, node, inst, end[1])
                return "failed"
            self._due_at(end[0])
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
        if node.kind == "sh":
            return await self._sh(st, wf, node, prefix)
        if node.kind == "wait":
            return self._wait(st, wf, node, prefix)
        if node.kind == "check":
            return await self._check(st, wf, node, prefix)
        if node.kind == "if":
            return await self._if(st, wf, node, prefix)
        if node.kind == "loop":
            return await self._repeat(st, wf, node, prefix)
        if node.kind == "gate":
            return self._gate(st, wf, node, prefix)
        raise OpError("kind", f"{node.kind} is not in this version yet")

    async def _if(self, st: RunState, wf: Workflow, node: Node, prefix: str) -> str:
        """Ask the probe once, log the branch taken, then run that branch; a
        restart never asks again once the branch is logged."""
        inst = prefix + node.id
        n = st.nodes.get(inst)
        if n is not None and n.status == "failed":
            return "failed"
        if n is None or n.took is None:
            if "sh" in node.body["probe"] and (n is None or not n.started):
                # The probe's time is the if's own (spec §05: a deadline
                # bounds a step), so its clock starts before the probe does.
                say = self._say(st, wf, node, prefix)
                self._log(st.run, {"ev": "probe_started", "node": inst, "say": say})
                n = st.nodes[inst]
            v = await self._probe(st, wf, node, prefix, inst, "")
            if isinstance(v, str):
                return v
            took = (
                "then" if v["pass"] else ("else" if len(node.children) > 1 else "none")
            )
            self._log(
                st.run,
                {
                    "ev": "branch",
                    "node": inst,
                    "took": took,
                    "why": v.get("why", ""),
                    "say": self._say(st, wf, node, prefix),
                },
            )
            n = st.nodes[inst]
        if n.took == "none":
            return "done"
        child = next(c for c in node.children if c.branch == n.took)
        return await self._advance(st, wf, child, prefix)

    async def _repeat(self, st: RunState, wf: Workflow, node: Node, prefix: str) -> str:
        """Run do once per round, each round its own scope (``l.2.fix``);
        after each, ask until once and log it. Out of rounds with an until
        that never passed fails, unless at_max is done; without until, max
        rounds is done."""
        inst = prefix + node.id
        n = st.nodes.get(inst)
        if n is not None and n.status == "failed":
            return "failed"
        if n is None or n.max is None:
            say = self._say(st, wf, node, prefix)
            try:
                mx = value_of(node.body["max"], self._scope(st, wf, prefix))
            except RENDER_ERRORS as e:
                self._fail(st, inst, f"max has no value: {e}", say, retry=False)
                return "failed"
            if (
                isinstance(mx, bool)
                or not isinstance(mx, int)
                or not 1 <= mx <= MAX_ROUNDS
            ):
                why = f"max is {mx!r}, not a whole number from 1 to {MAX_ROUNDS}"
                self._fail(st, inst, why, say, retry=False)
                return "failed"
            rec = {"ev": "loop_started", "node": inst, "max": mx, "round": 1}
            self._log(st.run, rec | {"say": say})
            n = st.nodes[inst]
        k, do = n.round, node.children[0]
        mx = n.max or 0
        got = await self._advance(st, wf, do, f"{inst}.{k}.")
        if got != "done":
            return "running" if got in ("pending", "queued") else got
        if "probe" in node.body:
            u = n.untils.get(k)
            if u is None:
                v = await self._probe(st, wf, node, f"{inst}.{k}.", inst, str(k))
                if isinstance(v, str):
                    return "running" if v == "queued" else v
                self._log(
                    st.run,
                    {
                        "ev": "until",
                        "node": inst,
                        "n": k,
                        "pass": v["pass"],
                        "why": v.get("why", ""),
                    },
                )
                u = st.nodes[inst].untils[k]
            if u["pass"]:
                return "done"
            if k >= mx:
                if node.body.get("at_max") == "done":
                    return "done"  # the rounds the person allowed are spent
                self._fail(st, inst, f"until did not pass in {mx} rounds", retry=False)
                return "failed"
        elif k >= mx:
            return "done"
        self._log(st.run, {"ev": "round", "node": inst, "n": k + 1})
        self.changed()
        return "running"

    def _gate(self, st: RunState, wf: Workflow, node: Node, prefix: str) -> str:
        """Open once, log what the decider sees, then wait for the answer.
        A person's gate is a card under nothing in the main transcript; a
        main gate is told to the main agent by _tell_open."""
        inst = prefix + node.id
        n = st.nodes.get(inst)
        if n is not None and n.status in ("done", "failed"):
            return n.status
        if n is not None and n.status == "running":
            return "running"
        scope = self._scope(st, wf, prefix)
        say = self._say(st, wf, node, prefix)
        try:
            ask = render(node.body["ask"], scope)
            shown = {k: evaluate(compile_expr(k), scope) for k in node.body["show"]}
        except RENDER_ERRORS as e:
            self._fail(st, inst, f"a reference has no value: {e}", say, retry=False)
            return "failed"
        by = "main" if node.key in st.delegated else node.body["by"]
        self._log(
            st.run,
            {
                "ev": "gate_opened",
                "node": inst,
                "by": by,
                "ask": ask,
                "shown": shown,
                "say": say,
                "tell": True,
            },
        )
        if by == "person" and (owner := self.owner(st)) is not None:
            # Keyed by attempt: a gate asked again gets a new card at the
            # bottom, and the old one stays where it was.
            owner.record_card(
                {
                    "kind": "workflow_decision",
                    "run": st.run,
                    "what": "gate",
                    "key": f"{inst}@{st.nodes[inst].attempts}",
                }
            )
        return "running"

    def _under(
        self, st: RunState, node: Node, inst: str
    ) -> list[tuple[str, NodeState]]:
        """The logged instances inside this one, itself included: the
        nodes of its subtree under the same item of every enclosing map."""
        prefix, keys = inst[: -len(node.id)], {c.key for c in walk(node)}
        return [
            (k, n)
            for k, n in st.nodes.items()
            if k.startswith(prefix) and template_key(k) in keys
        ]

    def _end_at(
        self, st: RunState, wf: Workflow, node: Node, inst: str
    ) -> tuple[float, float] | None:
        """When this instance must have finished, and the span that says so:
        its own deadline from its start (its first logged instance's, or
        the run's latest approval when that is later, as after arming again); at
        the root, also the run's from approval; for an sh or a check with no
        deadline over it, their defaults. An ancestor's deadline is enforced
        at the ancestor, which every pass reaches first."""
        ends: list[tuple[float, float]] = []
        leaf = node.kind in ("sh", "check")
        if not (node.deadline or leaf or (node is wf.root and wf.deadline)):
            return None  # nothing to scan for: most nodes, on most passes
        times = [n.started for _, n in self._under(st, node, inst) if n.started]
        began = min(times) if times else None
        if began and st.started:
            # Arming again approves the run again: what ran before it does
            # not count against the deadline of a group that runs again.
            began = max(began, st.started)
        if node.deadline and began:
            ends.append((began + node.deadline, node.deadline))
        if node is wf.root and wf.deadline and st.started:
            ends.append((st.started + wf.deadline, wf.deadline))
        if leaf and began and not self._bounded(wf, node):
            span = SH_LIMIT_S if node.kind == "sh" else CHECK_LIMIT_S
            ends.append((began + span, span))
        return min(ends) if ends else None

    def _bounded(self, wf: Workflow, node: Node) -> bool:
        """Whether a deadline on this node or one above it bounds it."""
        x: Node | None = node
        while x is not None:
            if x.deadline or (x is wf.root and wf.deadline):
                return True
            x = wf.parent.get(x.key)
        return False

    async def _expire(self, st: RunState, node: Node, inst: str, span: float) -> None:
        """A deadline passed: end every running leaf inside, then fail the
        instance it bounds. An sh running with no task in this process
        was cut by a restart first: it fails as cut, never retried, since a
        retry would run it again (spec §05)."""
        self._forget_probes(st.run, inst)
        wf = self.defs[st.run]
        for k, n in self._under(st, node, inst):
            if (
                n.status == "running"
                and self._template(wf, k).kind == "sh"
                and (st.run, k) not in self._tasks
            ):
                self._fail(st, k, CUT, retry=False)
                if k == inst:
                    return
        tails = []
        for k, n in self._under(st, node, inst):
            if n.status not in ("done", "failed", "stopped"):
                if (seen := self._seen_out.get((st.run, k))) is not None:
                    said = last_lines(
                        *(
                            seen.get(s, b"").decode("utf-8", "replace")[-TAIL:]
                            for s in ("out", "err")
                        )
                    )
                    tails.append(said if k == inst else f"{k}:\n{said}")
                await self._halt(st, k, n)
        self._fail(
            st,
            inst,
            f"ran past its deadline of {fmt_duration(span)}",
            tail="\n".join(tails),
        )

    async def _halt(self, st: RunState, inst: str, n: NodeState) -> None:
        """End one running leaf: kill its task, interrupt its session and its
        judges' through the lock. The sessions stay for a person to read."""
        task = self._tasks.pop((st.run, inst), None)
        if task is not None:
            task.cancel()
        for lid in _lids(n):
            s = self._reg.sessions.get(lid)
            if s is not None and s.status == "working":
                with contextlib.suppress(Exception):
                    await s.interrupt(through_lock=True)

    def _met(
        self, st: RunState, wf: Workflow, node: Node, prefix: str, a: str
    ) -> bool | str:
        """Whether one ``after`` entry of ``node`` holds under ``prefix``:
        a node done, a map's item done, an agent's early output given. A
        string says why it never will. The lint found the target."""
        target, _, sub = a.partition(".")
        t = find(wf.by_key, target, node.maps)
        assert t is not None
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
        restart counts the same branches again from the fold. A failed
        branch keeps its slot until the main agent retries or skips it, and
        the others go on beside it."""
        now = [self._state(st, n, p + n.id) for n, p in kids]
        busy = ("running", "queued", "failed")
        flight = sum(s in busy for s in now)
        out = []
        for (n, p), s in zip(kids, now):
            if s == "pending" and cap is not None and flight >= cap:
                out.append(s)
                continue
            s2 = s if s in ("done", "failed") else await self._advance(st, wf, n, p)
            flight += (s2 in busy) - (s in busy)
            out.append(s2)
        return _fold(out) if out else "done"

    def _start_map(self, st: RunState, wf: Workflow, node: Node, prefix: str) -> bool:
        """Read the items once and record them; a restart reads the record."""
        inst = prefix + node.id
        try:
            items = value_of(node.body["over"], self._scope(st, wf, prefix))
        except RENDER_ERRORS as e:
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
        if node.body.get("session") == "main":
            return await self._main_step(st, wf, node, prefix)
        inst = prefix + node.id
        n = st.nodes.get(inst)
        if (n is None or n.status == "pending") and node.key in wf.session_of:
            return await self._reuse(st, wf, node, prefix)
        if n is None or n.status == "pending":
            try:
                started = await self._start_agent(st, wf, node, prefix)
            except Exception as e:
                log.exception("aegis: node %s of %s did not start", inst, st.run)
                why = f"it did not start: {getattr(e, 'message', e)}"
                say = self._say(st, wf, node, prefix)
                self._fail(st, inst, why, say, retry=False)
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
        reported = n.result is not None or bool(n.summary)
        asking = any(o["kind"] == "ask" and o["node"] == inst for o in st.open.values())
        if asking and not reported:
            return "running"  # it waits for the main agent's answer, not nudged
        if (st.run, inst) in self._answered and not reported:
            return "running"  # the answer's turn has not ended yet
        got = await self._settle(
            st,
            inst,
            s,
            reported=reported,
            nudged=n.nudged,
            nudge_rec={"ev": "nudged", "node": inst},
            keep=self._keep(st, wf, node, prefix),
        )
        if got == "done":
            self._log(st.run, {"ev": "node_done", "node": inst})
        elif got == "failed":
            self._fail(st, inst, "its turn ended twice with no workflow_result")
        return got

    def _keep(self, st: RunState, wf: Workflow, node: Node, prefix: str) -> bool:
        """Whether this step's session stays open after it: a step may still
        run in it. _close_spent closes it once none may."""
        owner = wf.by_key[wf.session_of.get(node.key, node.key)]
        return any(
            self._may_run(st, wf, owner, wf.by_key[k], prefix, node)
            for k in wf.users.get(owner.key, [])
        )

    def _may_run(
        self,
        st: RunState,
        wf: Workflow,
        owner: Node,
        user: Node,
        prefix: str,
        now: Node | None = None,
    ) -> bool:
        """Whether ``user`` may still run in ``owner``'s session, seen from
        an instance under ``prefix``. Inside a loop the owner is not in,
        every round runs it again, so the outermost such loop must finish;
        otherwise the user itself. Done or skipped, under a skipped group
        or in a branch not taken, it will not. ``now`` is the step settling,
        which is not waited on. The lint keeps a user out of a map its
        owner is not in, so every scope above that unit is the owner's."""
        unit = x = user
        while (p := wf.parent.get(x.key)) is not None:
            if p.kind == "loop" and owner.maps[: len(p.maps) + 1] != (*p.maps, p.id):
                unit = p
            x = p
        if unit is now:
            return False
        if self._state(st, unit, self._resolve(unit, prefix)) == "done":
            return False
        x = unit
        while (p := wf.parent.get(x.key)) is not None:
            s = st.nodes.get(self._resolve(p, prefix))
            if s is not None and s.status == "skipped":
                return False
            if p.kind == "if" and s is not None and s.took not in (None, x.branch):
                return False  # in a branch not taken
            x = p
        return True

    async def _close_spent(self, st: RunState, wf: Workflow) -> None:
        """Close each kept session nothing more will run in: its owner is
        done or skipped, its turn is over, and no user may still run."""
        for key, users in wf.users.items():
            owner = wf.by_key[key]
            for inst, n in list(st.nodes.items()):
                if template_key(inst) != key or n.status not in ("done", "skipped"):
                    continue
                s = self._reg.sessions.get(n.log_id or "")
                if s is None or not self._reg.finished(s):
                    continue
                prefix = inst[: -len(owner.id)]
                if not any(
                    self._may_run(st, wf, owner, wf.by_key[k], prefix) for k in users
                ):
                    with contextlib.suppress(Exception):
                        await self._reg.close(s.log_id)

    async def _reuse(self, st: RunState, wf: Workflow, node: Node, prefix: str) -> str:
        """Start a step in an earlier step's session: no spawn and no slot,
        since the session holds its slot while it is open. It waits while
        that session is still in a turn."""
        inst = prefix + node.id
        say = self._say(st, wf, node, prefix)
        root = wf.by_key[wf.session_of[node.key]]
        src = st.nodes.get(self._resolve(root, prefix)) or NodeState()
        s = self._reg.sessions.get(src.log_id or "")
        if s is None:
            self._fail(st, inst, "the session it continues is gone", say, retry=False)
            return "failed"
        if not self._reg.finished(s):
            self._queuing.add(inst)
            return "queued"
        try:
            prompt = render(node.body["prompt"], self._scope(st, wf, prefix))
        except RENDER_ERRORS as e:
            self._fail(st, inst, f"a reference has no value: {e}", say, retry=False)
            return "failed"
        self._log(
            st.run, {"ev": "node_started", "node": inst, "log_id": s.log_id, "say": say}
        )
        # The Fleet's card, the placeholder and the lock name the step
        # running in it now, not the one it was spawned for.
        s._set(workflow={"run": st.run, "node": inst})
        self._reg.metas.write(s.meta())
        schema = json.dumps(node.body.get("result") or {"type": "object"})
        await s.send(
            f"> from workflow:{self._wid(st)} · node {inst} · {iso_now()}\n{prompt}\n\n"
            + REUSED.format(node=inst, schema=schema),
            through_lock=True,
        )
        return "running"

    async def _main_step(
        self, st: RunState, wf: Workflow, node: Node, prefix: str
    ) -> str:
        """A step the main agent runs in its own session, outside the pool.
        It never waits for room: the main agent holds no slot, and several of
        its steps may be open at once, each reported by name (#467). Its
        prompt is an inbox turn; it ends when the main agent's
        workflow_result is accepted. The main agent's turns are
        also the person's, so no turn end nudges or fails it: only its
        deadline bounds it. It logs no log_id, so nothing that ends a run
        interrupts or unquiets the main agent."""
        inst = prefix + node.id
        n = st.nodes.get(inst)
        if n is not None and n.status in ("done", "failed"):
            return n.status
        if n is not None and n.status == "running":
            if n.result is not None or n.summary:
                self._log(st.run, {"ev": "node_done", "node": inst})
                return "done"
            return "running"
        owner = self.owner(st)
        say = self._say(st, wf, node, prefix)
        if owner is None:
            self._fail(st, inst, "the main agent's session is closed", say, retry=False)
            return "failed"
        try:
            prompt = render(node.body["prompt"], self._scope(st, wf, prefix))
        except RENDER_ERRORS as e:
            self._fail(st, inst, f"a reference has no value: {e}", say, retry=False)
            return "failed"
        self._log(
            st.run, {"ev": "node_started", "node": inst, "say": say, "main": True}
        )
        schema = json.dumps(node.body.get("result") or {"type": "object"})
        await owner.deliver(
            f"> from workflow:{self._wid(st)} · step {inst} · {iso_now()}",
            f"{prompt}\n\n" + MAIN_PREAMBLE.format(node=inst, schema=schema),
        )
        return "running"

    def _cwd(self, st: RunState) -> str:
        if st.cwd:
            return st.cwd
        o = self.owner(st)
        return str(o.spec.cwd) if o is not None else str(self._app.roots.harness_cwd)

    def _begin(
        self, st: RunState, wf: Workflow, node: Node, prefix: str, **extra
    ) -> NodeState:
        inst = prefix + node.id
        say = self._say(st, wf, node, prefix)
        self._log(st.run, {"ev": "node_started", "node": inst, "say": say, **extra})
        return st.nodes[inst]

    def _done(self, st: RunState, inst: str, value, summary: str) -> str:
        self._log(
            st.run, {"ev": "result", "node": inst, "value": value, "summary": summary}
        )
        self._log(st.run, {"ev": "node_done", "node": inst})
        return "done"

    async def _sh(self, st: RunState, wf: Workflow, node: Node, prefix: str) -> str:
        inst, key = prefix + node.id, (st.run, prefix + node.id)
        n = st.nodes.get(inst)
        if n is not None and n.status in ("done", "failed"):
            return n.status
        if n is None or n.status == "pending":
            scope = self._scope(st, wf, prefix)
            try:
                cmd, env = shell_command(node.body["sh"], scope)
                shown = render(node.body["sh"], scope)
                progress = self._progress_command(node, scope)
            except RENDER_ERRORS as e:
                self._fail(
                    st,
                    inst,
                    f"a reference has no value: {e}",
                    self._say(st, wf, node, prefix),
                    retry=False,
                )
                return "failed"
            self._begin(st, wf, node, prefix, cmd=shown)
            self._tasks[key] = asyncio.create_task(
                self._run_sh(st, inst, cmd, env, progress)
            )
            return "running"
        if key not in self._tasks:
            # Running in the log with no task in this process: the server
            # stopped under it. Never run again (spec §05).
            self._fail(st, inst, CUT, retry=False)
            return "failed"
        return "running"

    async def _run_sh(
        self,
        st: RunState,
        inst: str,
        cmd: str,
        env: dict[str, str],
        progress: tuple[str, dict[str, str]] | None = None,
    ) -> None:
        def pid(p: int) -> None:
            self._log(
                st.run,
                {"ev": "pid", "node": inst, "pid": p, "start": restarts.started_at(p)},
            )

        values = [*env.values(), *(progress[1].values() if progress else ())]
        watch = None
        try:
            if any("\0" in v for v in values):
                raise ValueError(NUL)
            if progress:
                watch = asyncio.create_task(self._watch_progress(st, inst, *progress))
            seen = self._seen_out[(st.run, inst)] = {}
            code, out, err = await run_sh(
                cmd, self._cwd(st), pid, env, seen, user=self._user(st)
            )
        except ValueError:  # a NUL byte, from a value: every attempt would
            # fail the same way, so it fails once, never retried (below)
            code, out, err = None, "", NUL
        except OSError as e:
            code, out, err = None, "", str(e)
        finally:
            if watch:
                watch.cancel()
            self._drop((st.run, inst))
            self._seen_out.pop((st.run, inst), None)
        n = st.nodes.get(inst)
        if st.status != "running" or n is None or n.status != "running":
            return  # stopped, or ended by a deadline, while it ran
        if err == NUL and code is None:
            self._fail(st, inst, NUL, retry=False)
        elif code == 0:
            last = out.strip().splitlines()[-1:]
            self._done(
                st,
                inst,
                {"code": 0, "out": out, "err": err},
                "exit 0" + (f": {last[0][:160]}" if last else ""),
            )
        else:
            why = (err.strip().splitlines() or out.strip().splitlines() or [""])[-1][
                :200
            ]
            head = f"exit {code}" if code is not None else "bash did not start"
            self._fail(
                st, inst, head + (f": {why}" if why else ""), tail=last_lines(out, err)
            )
        self.changed()

    def _forget_probes(self, run: str, inst: str = "") -> None:
        """Drop the probe answers kept for an instance and those inside it,
        or for the whole run: an attempt that failed, passed its deadline
        or was armed again asks its probes anew. A seq's or a par's steps
        carry no prefix of theirs, so the instances inside are matched as
        ``_under`` matches them: by template key, under the same map items."""
        wf = self.defs.get(run)
        node = wf.by_key.get(template_key(inst)) if wf and inst else None
        prefix = inst[: -len(node.id)] if node else inst
        keys = {c.key for c in walk(node)} if node else set()
        for k in list(self._probed):
            if k[0] == run and (
                not inst
                or k[1] == inst
                or k[1].startswith(inst + ".")
                or (k[1].startswith(prefix) and template_key(k[1]) in keys)
            ):
                del self._probed[k]

    def _drop(self, key: tuple[str, str]) -> None:
        """Forget an ending task, unless a retry has already put the next
        attempt's task in its place."""
        if self._tasks.get(key) is asyncio.current_task():
            del self._tasks[key]

    def _wait(self, st: RunState, wf: Workflow, node: Node, prefix: str) -> str:
        inst = prefix + node.id
        n = st.nodes.get(inst)
        if n is not None and n.status in ("done", "failed"):
            return n.status
        if n is None or n.status == "pending":
            try:
                raw = value_of(node.body["wait"], self._scope(st, wf, prefix))
            except RENDER_ERRORS as e:
                self._fail(
                    st,
                    inst,
                    f"a reference has no value: {e}",
                    self._say(st, wf, node, prefix),
                    retry=False,
                )
                return "failed"
            secs = duration(raw)
            if secs is None:
                self._fail(
                    st,
                    inst,
                    f"{raw!r} is not a duration",
                    self._say(st, wf, node, prefix),
                    retry=False,
                )
                return "failed"
            cmd = str(raw)
            if bounds := node.body.get("between"):
                # A value an agent gave never waits past what was approved.
                clamped = min(max(secs, bounds[0]), bounds[1])
                if clamped != secs:
                    secs, cmd = clamped, f"{fmt_duration(clamped)} ({raw} asked)"
            n = self._begin(st, wf, node, prefix, until=time.time() + secs, cmd=cmd)
        if time.time() >= (n.until or 0):
            return self._done(st, inst, None, f"Waited {n.cmd}.")
        self._due_at(n.until or 0)
        return "running"

    async def _check(self, st: RunState, wf: Workflow, node: Node, prefix: str) -> str:
        inst, key = prefix + node.id, (st.run, prefix + node.id)
        n = st.nodes.get(inst)
        if n is not None and n.status in ("done", "failed"):
            return n.status
        probe = node.body["probe"]
        scope = self._scope(st, wf, prefix)
        if n is None or n.status == "pending":
            cmd = ""
            if "sh" in probe:
                try:
                    cmd = render(probe["sh"], scope)  # shown, never run
                    # Its progress reads the same values: one missing fails
                    # the check here, never inside the polling task.
                    shown_progress = render(node.body.get("progress", ""), scope)
                except RENDER_ERRORS as e:
                    self._fail(
                        st,
                        inst,
                        f"a reference has no value: {e}",
                        self._say(st, wf, node, prefix),
                        retry=False,
                    )
                    return "failed"
                if "\0" in cmd + shown_progress:
                    say = self._say(st, wf, node, prefix)
                    self._fail(st, inst, NUL, say, retry=False)
                    return "failed"
            n = self._begin(st, wf, node, prefix, cmd=cmd)
        if "expr" in probe:
            try:
                passed = evaluate(compile_expr(probe["expr"]), scope)
            except ExprError as e:
                self._fail(st, inst, str(e), retry=False)
                return "failed"
            if passed:
                return self._done(st, inst, None, "The check passed.")
            # The values it reads never change once given: asking again
            # would only spin until the check's limit.
            self._fail(st, inst, f"{probe['expr']} is false", retry=False)
            return "failed"
        if "sh" in probe:
            # A restart lost the task: the probe simply runs again (spec §05).
            if key not in self._tasks:
                cmd, env = shell_command(probe["sh"], scope)
                progress = self._progress_command(node, scope)
                self._tasks[key] = asyncio.create_task(
                    self._poll(st, inst, cmd, env, probe["every"], progress)
                )
            return "running"
        return await self._check_judge(st, wf, node, prefix)  # task 7

    def _user(self, st: RunState) -> str | None:
        """The Linux user the run's commands run as: its owner's (users.py).
        Raises PermissionError, which fails the step, rather than run as the
        server when the owner is gone or has no user in multi-user mode."""
        owner = self.owner(st)
        if owner is None:
            if self._reg.multi_user:
                raise PermissionError("the workflow's owner is gone")
            return None
        return self._reg.user_for(owner.spec)

    async def _probe_sh(
        self, cmd: str, cwd: str, env: dict[str, str], st: RunState
    ) -> tuple[int | None, str, str]:
        """An sh probe's exit code, stdout and stderr, run as an sh node is
        (its own process group, ended when its task is cancelled); the code
        is None when bash did not start or the probe ran too long."""
        try:
            return await asyncio.wait_for(
                run_sh(cmd, cwd, lambda _: None, env, user=self._user(st)),
                CONDITION_LIMIT_S,
            )
        except TimeoutError:
            return None, "", f"took over {CONDITION_LIMIT_S:g}s"
        except OSError as e:
            return None, "", str(e)

    async def _poll(
        self,
        st: RunState,
        inst: str,
        cmd: str,
        env: dict[str, str],
        every: float,
        progress: tuple[str, dict[str, str]] | None = None,
    ) -> None:
        try:
            while True:
                rc, out, err = await self._probe_sh(cmd, self._cwd(st), env, st)
                self._readings[(st.run, inst)] = (
                    f"exit {rc if rc is not None else '?'} at {time.strftime('%H:%M')}"
                )
                self._publish(st.run)
                n = st.nodes.get(inst)
                if st.status != "running" or n is None or n.status != "running":
                    return
                if rc == 0:
                    if progress:
                        self._percent[(st.run, inst)] = 100
                    self._done(
                        st, inst, {"code": 0, "out": out[-TAIL:]}, "The check passed."
                    )
                    self.changed()
                    return
                if progress:
                    await self._read_progress(st, inst, *progress)
                await asyncio.sleep(every)
        finally:
            self._drop((st.run, inst))

    def _progress_command(
        self, node: Node, scope: dict
    ) -> tuple[str, dict[str, str]] | None:
        """A step's progress command with its values as env, or None."""
        if "progress" not in node.body:
            return None
        return shell_command(node.body["progress"], scope)

    async def _read_progress(
        self, st: RunState, inst: str, cmd: str, env: dict[str, str]
    ) -> None:
        """Run a progress command once and keep its number for the card. A
        reading that is no number keeps the last one: progress is for the
        person, and never fails the step (spec 2026-10-23, #682)."""
        rc, out, _ = await self._probe_sh(cmd, self._cwd(st), env, st)
        value = _percent(out) if rc == 0 else None
        if value is not None and value != self._percent.get((st.run, inst)):
            self._percent[(st.run, inst)] = value
            self._publish(st.run)

    async def _watch_progress(
        self, st: RunState, inst: str, cmd: str, env: dict[str, str]
    ) -> None:
        """Read an sh's progress until its command ends and cancels this."""
        while True:
            await self._read_progress(st, inst, cmd, env)
            await asyncio.sleep(SH_PROGRESS_EVERY_S)

    async def _check_judge(
        self, st: RunState, wf: Workflow, node: Node, prefix: str
    ) -> str:
        """A check whose probe is a judge, asked once: a yes is done, a no
        fails the check (and its retries ask again)."""
        inst = prefix + node.id
        v = await self._judge(
            st, wf, node, prefix, inst, "", node.body["probe"]["judge"]
        )
        if isinstance(v, str):
            return v
        if v["pass"]:
            return self._done(st, inst, {"pass": True, "why": v["why"]}, v["why"])
        self._fail(st, inst, "the judge said no: " + v["why"])
        return "failed"

    async def _judge(
        self,
        st: RunState,
        wf: Workflow,
        node: Node,
        prefix: str,
        inst: str,
        rnd: str,
        j: dict,
    ) -> dict | str:
        """A judge's verdict for one evaluation, or running, queued or failed
        while there is none. ``inst`` is the check, if or loop it judges for;
        ``prefix`` is the scope its prompt reads (a loop's round); ``rnd``
        names the evaluation: "" for a check or an if, the round for until.
        A judge is a quiet session in the pool, as an agent node is."""
        n = st.nodes.get(inst) or NodeState()
        rec = n.judges.get(rnd) or {}
        if not rec.get("log_id"):
            owner = self.owner(st)
            if owner is None:
                self._fail(st, inst, "the main agent's session is closed")
                return "failed"
            tag: dict = {"run": st.run, "node": inst, "probe": "judge", "round": rnd}
            if n.attempts:
                tag["attempt"] = n.attempts
            scope = self._scope(st, wf, prefix)
            try:
                prompt = render(j["prompt"], scope)
                inputs = {k: evaluate(compile_expr(k), scope) for k in j["inputs"]}
            except RENDER_ERRORS as e:
                self._fail(st, inst, f"a reference has no value: {e}", retry=False)
                return "failed"
            say = self._say(st, wf, node, prefix)
            s = await self._spawn_node(st, wf, j["agent"], tag, f"Judge: {say}")
            if s is None:
                self._queuing.add(inst)
                return "queued"
            if st.status != "running":  # stopped while it spawned
                with contextlib.suppress(Exception):
                    await self._reg.close(s.log_id)
                return "running"
            self._log(
                st.run,
                {"ev": "judge_started", "node": inst, "round": rnd, "log_id": s.log_id},
            )
            text = (
                f"> from workflow:{self._wid(st)} · judge {inst} · {iso_now()}\n{prompt}\n\n"
                + "".join(
                    f"{k}:\n```json\n{json.dumps(v, indent=2)}\n```\n"
                    for k, v in inputs.items()
                )
                + JUDGE_PREAMBLE.format(
                    node=inst, wid=self._wid(st), title=wf.title, owner=owner.handle
                )
            )
            await s.send(text, through_lock=True)
            return "running"
        s = self._reg.sessions.get(rec["log_id"])
        if s is None:
            if "pass" in rec:
                return rec  # judged, and its session closed
            self._fail(st, inst, "its judge's session is gone")
            return "failed"
        got = await self._settle(
            st,
            inst,
            s,
            reported="pass" in rec,
            nudged=bool(rec.get("nudged")),
            nudge_rec={"ev": "judge_nudged", "node": inst, "round": rnd},
        )
        if got == "done":
            return rec
        if got == "failed":
            self._fail(st, inst, "its judge's turn ended twice with no verdict")
        return got

    async def _probe(
        self, st: RunState, wf: Workflow, node: Node, prefix: str, inst: str, rnd: str
    ) -> dict | str:
        """An if's or an until's probe, evaluated once: ``{pass, why}``, or
        running, queued or failed while there is no answer. An expr answers
        at once; an sh runs once as a task; a judge goes through _judge."""
        probe = node.body["probe"]
        if "judge" in probe:
            return await self._judge(st, wf, node, prefix, inst, rnd, probe["judge"])
        scope = self._scope(st, wf, prefix)
        if "expr" in probe:
            try:
                return {
                    "pass": bool(evaluate(compile_expr(probe["expr"]), scope)),
                    "why": "",
                }
            except RENDER_ERRORS as e:
                self._fail(
                    st, inst, str(e), self._say(st, wf, node, prefix), retry=False
                )
                return "failed"
        got = self._probed.pop((st.run, inst, rnd), None)
        if got is not None:
            return got
        key = (st.run, inst)
        if key not in self._tasks:
            try:
                cmd, env = shell_command(probe["sh"], scope)
            except RENDER_ERRORS as e:
                why = f"a reference has no value: {e}"
                self._fail(st, inst, why, self._say(st, wf, node, prefix), retry=False)
                return "failed"
            if any("\0" in v for v in env.values()):
                self._fail(st, inst, NUL, self._say(st, wf, node, prefix), retry=False)
                return "failed"
            self._tasks[key] = asyncio.create_task(
                self._probe_once(st, inst, rnd, cmd, env)
            )
        return "running"

    async def _probe_once(
        self, st: RunState, inst: str, rnd: str, cmd: str, env: dict[str, str]
    ) -> None:
        try:
            rc, out, err = await self._probe_sh(cmd, self._cwd(st), env, st)
            self._readings[(st.run, inst)] = (
                f"exit {rc if rc is not None else '?'} at {time.strftime('%H:%M')}"
            )
            last = (err.strip().splitlines() or out.strip().splitlines() or [""])[-1]
            why = f"exit {rc if rc is not None else '?'}" + (
                f": {last[:160]}" if last else ""
            )
            self._probed[(st.run, inst, rnd)] = {"pass": rc == 0, "why": why}
        finally:
            self._drop((st.run, inst))
        self.changed()

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

    async def _spawn_node(
        self,
        st: RunState,
        wf: Workflow,
        alias: str,
        tag: dict,
        title: str,
        cwd: str | None = None,
    ) -> Session | None:
        """The quiet session an agent node or a judge runs in, spawned with
        the main agent as its parent; None while the pool, the run's limits
        or the quota gate have no room for it (the caller queues)."""
        owner = self.owner(st)
        assert owner is not None  # the caller failed the node already
        # An earlier attempt's session is archived when the next one spawns,
        # so a retried node holds one slot; its transcript stays readable.
        same = {k: v for k, v in tag.items() if k != "attempt"}
        for x in list(self._reg.sessions.values()):
            w = x.workflow or {}
            if w != tag and {k: v for k, v in w.items() if k != "attempt"} == same:
                with contextlib.suppress(Exception):
                    await self._reg.close(x.log_id)
        # A crash between a spawn and its record leaves the session behind:
        # it is this node's, so it is adopted rather than doubled.
        s = next((x for x in self._reg.sessions.values() if x.workflow == tag), None)
        if s is not None:
            return s
        if not self._room(st, wf, alias):
            return None  # a session closing wakes a pass (Registry.archive -> changed)
        agents, default = self._app._agents()
        spec = resolve(
            agents,
            default,
            alias,
            {},
            self._app._resolve_cwd(cwd, owner.spec.cwd),
            spawned_by=owner.log_id,
            owner=owner.spec.owner,
        )
        spec, g = self._app.start_spec(spec)
        if not g.may_start:
            return (
                None  # the gate's next reading wakes a pass (app.on_quota -> changed)
            )
        return await self._reg.spawn(spec, title=title, quiet=True, workflow=tag)

    async def _settle(
        self,
        st: RunState,
        inst: str,
        s: Session,
        reported: bool,
        nudged: bool,
        nudge_rec: dict,
        keep: bool = False,
    ) -> str:
        """Where a node's or a judge's session stands once it has been
        prompted: running while it works; done, and closed unless ``keep``
        (a later step runs in it), once it has reported and finished; nudged
        once when a turn ends with no report; failed when the second does."""
        if not self._reg.finished(s):
            return "running"
        if reported:
            if not keep:
                with contextlib.suppress(Exception):
                    await self._reg.close(s.log_id)
            return "done"
        if not nudged:
            self._log(st.run, nudge_rec)
            await s.deliver(
                f"> from workflow:{self._wid(st)} · nudge · {iso_now()}",
                "Your turn ended without workflow_result. Call it now with your result and a one-sentence summary, or the workflow fails.",
                through_lock=True,
            )
            return "running"
        return "failed"

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
        except RENDER_ERRORS as e:
            why = f"a reference has no value: {e}"
            self._fail(st, inst, why, self._say(st, wf, node, prefix), retry=False)
            return False
        tag: dict = {"run": st.run, "node": inst}
        if (tried := (st.nodes.get(inst) or NodeState()).attempts) > 0:
            tag["attempt"] = tried  # adoption after a crash finds this attempt
        s = await self._spawn_node(st, wf, node.body["agent"], tag, say, cwd)
        if s is None:
            return False
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
        wanted = wf.waited.get(node.key)
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
        running = [
            n
            for n in st.nodes.values()
            if n.status == "running" or any("pass" not in j for j in n.judges.values())
        ]
        if outcome == "stopped":
            self._log(st.run, {"ev": "stopped"})
        else:
            # A failed ending's reason is the story's last line.
            self._finish(st, outcome, body if outcome != "done" else "")
        for n in running:
            for lid in _lids(n):
                s = self._reg.sessions.get(lid)
                if s is not None and s.status == "working":
                    with contextlib.suppress(Exception):
                        await s.interrupt(through_lock=True)
        # Its commands and probes end too; each task ends its own process
        # group, so nothing here waits on one. Each leaves the table now, so
        # a run armed again at once starts its own, not waits on this one.
        for key in [k for k in self._tasks if k[0] == st.run]:
            self._tasks.pop(key).cancel()
        if (wf := self.defs.get(st.run)) is not None:
            # A session kept for a step that will not run is archived, not
            # given a tab. One kept for a step not run yet keeps its tab:
            # arming the run again continues it.
            await self._close_spent(st, wf)
        self._release(st)
        # Nothing of an ended run waits for room, and the room it held is
        # free now: a queued node of another run starts on this wake.
        self._queued.pop(st.run, None)
        self._answered = {k for k in self._answered if k[0] != st.run}
        self._publish(st.run)
        self._owner_card(st)
        self.changed()
        self._app.schedules.changed()  # a schedule's run may have waited on it
        await self._tell_owner(st, outcome, body)

    def _release(self, st: RunState) -> None:
        """The node sessions still open get their tabs: the run no longer
        holds them, and the person reads them now."""
        for n in st.nodes.values():
            for lid in _lids(n):
                if lid in self._reg.sessions:
                    self._reg.set_quiet(lid, False)

    async def _tell_open(self, st: RunState) -> None:
        """Tell the main agent each open item it has not been told, once:
        the woke record follows the delivery, so a crash between the two
        tells it again and never loses it. With no main agent left, the
        run fails: nobody can decide."""
        untold = [(to, o) for to, o in st.open.items() if not o.get("told")]
        if not untold or st.status not in LIVE:
            return
        owner = self.owner(st)
        if owner is None and all(_news(o) for _, o in untold):
            # News with nobody to read it: the person's gate still waits on
            # the person, so the run goes on.
            for to, _ in untold:
                self._log(st.run, {"ev": "woke", "to": to})
            return
        if owner is None:
            await self._end(
                st,
                "failed",
                "The main agent's session is closed, so nobody can decide.",
            )
            return
        for to, o in untold:
            header = (
                f"> from workflow:{self._wid(st)} · {o['kind']} · {to} · {iso_now()}"
            )
            try:
                await owner.deliver(header, self._wake_text(st, to, o))
            except Exception:
                log.exception("aegis: could not tell %s about %s", st.owner, to)
                # Tried again soon, not only at the next 30 s pass.
                n = self._tell_fails[st.run] = self._tell_fails.get(st.run, 0) + 1
                self._due_at(time.time() + tell_backoff(n))
                return
            self._tell_fails.pop(st.run, None)
            self._log(st.run, {"ev": "woke", "to": to})

    def _wake_text(self, st: RunState, to: str, o: dict) -> str:
        """What the main agent reads about one open item, ending with the
        exact call that answers it."""
        n = st.nodes.get(to) or NodeState()
        call = f'workflow_answer(run="{st.run}", to="{to}"'
        if o["kind"] == "edit":
            if o.get("outcome") == "applied":
                return (
                    f"The person applied your change {to}: the run follows it now. "
                    "A failed step it changed runs again; the story says which. "
                    "Nothing to answer."
                )
            return (
                f"The person kept the plan: your change {to} was not applied, and "
                "the run goes on as it was. Nothing to answer."
            )
        if o["kind"] == "passed":
            value = o.get("value")
            got = f" with {json.dumps(value)}" if value not in (None, "") else ""
            return (
                f'The person continued at step "{n.say}"{got}: the run goes on. '
                "Nothing to answer."
            )
        if o["kind"] == "gate" and o.get("by") == "person":
            return (
                f'Step "{n.say}" waits for the person on its card: '
                f"{(n.gate or {}).get('ask') or ''}\n"
                "Only the person answers it, so there is nothing to answer here: "
                "you are told when they continue, and the run's end tells you if "
                "they stop it. If they hand it to you on the card, it comes back "
                "to you as a gate to answer."
            )
        if o["kind"] == "gate":
            g = n.gate or {}
            shown = "".join(
                f"{k}:\n```json\n{json.dumps(v, indent=2)}\n```\n"
                for k, v in (g.get("shown") or {}).items()
            )
            return (
                f'Step "{n.say}" asks you: {g.get("ask") or ""}\n'
                + shown
                + f'Go on with {call}, choice="continue", value=...): value is '
                f"optional, and later steps read it as {{{{{template_key(to).split('/')[-1]}.value}}}}.\n"
                f'Stop the run there with {call}, choice="abort").\n'
                "You may ask the person first, or propose a change with workflow_edit."
            )
        if o["kind"] == "ask":
            asker = st.nodes.get(o["node"]) or NodeState()
            return (
                f'Step "{asker.say}" asks you ({to}): {o.get("question") or ""}\n'
                f"Answer with {call}, text=...): the text reaches that step as its "
                "next message. It waits meanwhile; other steps go on."
            )
        lines = [f'Step "{n.say}" failed: {o.get("error") or n.error}']
        if n.attempts:
            lines[0] += f" (attempt {n.attempts + 1})"
        if n.tail:
            lines.append(f"Its output ended:\n```text\n{n.tail}\n```")
        h = self._reg.sessions.get(n.log_id or "")
        if h is not None:
            lines.append(f"Its session is {h.handle}; read it with peer_read.")
        wf = self.defs.get(st.run)
        gated = wf is not None and self._person_gated(st, wf, to)
        lines += [
            f"Decide with {call}, choice=...):",
            '- "retry" runs it again (change it first with workflow_edit if it needs a new brief);'
            if not gated
            else '- "retry" runs it again and asks the person at its gate again;',
            *(
                []
                if gated
                else [
                    '- "skip" goes on without it; give value matching its result schema if later steps read it;'
                ]
            ),
            *(
                ['- "more" runs one more round (value=n for n more rounds);']
                if wf is not None and self._spent(st, wf, to)
                else []
            ),
            '- "fail" ends the run.',
            "Other branches keep running meanwhile.",
        ]
        if gated:
            lines.insert(
                -1, "It holds a gate only the person passes, so it is never skipped."
            )
        return "\n".join(lines)

    async def answer(
        self, s: Session, run: str, to: str, choice: str | None, value, text: str
    ) -> None:
        """The main agent's answer to an open item. It can end the run, so
        it takes the lock a pass holds and never lands mid-pass."""
        async with self._lock:
            st = self._get(run)
            o = st.open.get(to)
            if o is None or st.status != "running":
                raise OpError(
                    "not_open",
                    f"nothing in run {run} waits on {to}; workflow_status lists what does",
                )
            text = (text or "").strip()
            wf = self.defs[run]
            if o["kind"] == "failed" and choice == "retry":
                reset = self._reset_list(st, wf, to)
                self._log(
                    st.run, {"ev": "retried", "node": to, "reset": reset, "text": text}
                )
                self.changed()
            elif (
                o["kind"] == "failed"
                and choice == "skip"
                and self._person_gated(st, wf, to)
            ):
                raise OpError(
                    "bad_choice",
                    f"{to} holds a gate only the person passes: retry it to ask "
                    "the person again, or fail the run",
                )
            elif o["kind"] == "failed" and choice == "skip":
                node = self._template(wf, to)
                schema = node.body.get("result") if node.kind == "agent" else None
                if (
                    value is not None
                    and schema
                    and (problems := check_value(value, schema))
                ):
                    raise OpError(
                        "bad_result",
                        "the value does not match the step's result schema: "
                        + "; ".join(problems),
                    )
                summary = text or "Skipped by the main agent."
                self._log(
                    st.run,
                    {"ev": "skipped", "node": to, "value": value, "summary": summary},
                )
                self.changed()
            elif o["kind"] == "ask" and choice is None:
                if not text:
                    raise OpError("bad_answer", "a question's answer is text")
                self._log(st.run, {"ev": "answered", "q": to, "text": text})
                n = st.nodes.get(o["node"]) or NodeState()
                node_s = self._reg.sessions.get(n.log_id or "")
                if node_s is None or n.status != "running":
                    return  # the node has moved on; the question is closed
                await node_s.deliver(
                    f"> from workflow:{self._wid(st)} · answer {to} · {iso_now()}",
                    text,
                    through_lock=True,
                )
                # Marked once delivered: a delivery that failed brings no
                # turn, and the mark would hold the step running forever.
                self._answered.add((st.run, o["node"]))
            elif o["kind"] == "gate" and choice in ("continue", "abort"):
                await self._answer_gate(
                    st, to, choice == "continue", text, "main", value
                )
            elif o["kind"] == "failed" and choice == "more":
                if not self._spent(st, wf, to):
                    raise OpError(
                        "bad_choice",
                        f"{to} is not a loop out of rounds: more takes one whose "
                        "until did not pass",
                    )
                more = 1 if value is None else value
                n = st.nodes[to]
                if (
                    isinstance(more, bool)
                    or not isinstance(more, int)
                    or not 1 <= more <= MAX_ROUNDS - n.round
                ):
                    raise OpError(
                        "bad_value",
                        f"more takes a whole number of rounds from 1 to "
                        f"{MAX_ROUNDS - n.round}",
                    )
                self._log(
                    st.run,
                    {"ev": "extended", "node": to, "max": n.round + more, "text": text},
                )
                self.changed()
            elif o["kind"] == "failed" and choice == "fail":
                n = st.nodes[to]
                why = f"{n.say} failed: {n.error}" + (
                    f" You ended the run: {text}" if text else " You ended the run."
                )
                await self._end(st, "failed", why)
            else:
                takes = {
                    "failed": "retry, skip or fail"
                    + (", or more" if self._spent(st, wf, to) else ""),
                    "gate": "continue or abort",
                    "ask": "none: answer it with text",
                    "passed": "none: it only tells you the person continued",
                }.get(o["kind"], "")
                raise OpError(
                    "bad_choice", f"{to} is a {o['kind']} item: it takes choice {takes}"
                )

    async def answer_gate(
        self, run: str, inst: str, go: bool, text: str, by: str
    ) -> None:
        """A person's answer at a gate: go on, or stop the run there. The
        typed answer is the gate's value. Takes the lock, as a stop does."""
        async with self._lock:
            st = self._get(run)
            await self._answer_gate(st, inst, go, text, by, text)

    async def delegate_gate(self, run: str, inst: str) -> None:
        """The person hands an open gate of theirs to the main agent, and
        with it every later opening of the same step: each item of a map,
        each round, each retry. The main agent is woken to answer it."""
        async with self._lock:
            st = self._get(run)
            o = st.open.get(inst)
            if o is None or o["kind"] != "gate" or st.status != "running":
                raise OpError(
                    "not_open", f"no gate {inst} waits for an answer in run {run}"
                )
            if o.get("by") != "person":
                raise OpError("not_yours_to_answer", "this gate is the main agent's")
            self._log(
                run, {"ev": "gate_delegated", "node": inst, "key": template_key(inst)}
            )
            self._owner_card(st)
            self.changed()

    def _spent(self, st: RunState, wf: Workflow, inst: str) -> bool:
        """Whether a failed instance is a loop whose until never passed in
        all its rounds, which a round more may still pass."""
        n = st.nodes.get(inst)
        return (
            n is not None
            and n.status == "failed"
            and n.max is not None
            and n.round >= n.max
            and n.error.startswith("until did not pass")
            and self._template(wf, inst).kind == "loop"
        )

    async def _answer_gate(
        self, st: RunState, inst: str, go: bool, text: str, by: str, value
    ) -> None:
        o = st.open.get(inst)
        if o is None or o["kind"] != "gate" or st.status != "running":
            raise OpError(
                "not_open", f"no gate {inst} waits for an answer in run {st.run}"
            )
        if o.get("by") != by:
            who = "the person" if o.get("by") == "person" else "the main agent"
            raise OpError("not_yours_to_answer", f"this gate is answered by {who}")
        text = (text or "").strip()
        self._log(
            st.run,
            {
                "ev": "gate_answered",
                "node": inst,
                "by": by,
                "go": go,
                "value": value,
                "text": text,
                "tell": True,
            },
        )
        if go:
            self.changed()
            return
        say = st.nodes[inst].say
        await self._end(
            st, "stopped", f"Stopped at {say}" + (f": {text}" if text else ".")
        )

    def _reset_list(self, st: RunState, wf: Workflow, inst: str) -> list[str]:
        """What a retry sends back to pending: every logged instance inside
        the node, itself included, so a group runs again whole."""
        return [k for k, _ in self._under(st, self._template(wf, inst), inst)]

    def _person_gated(self, st: RunState, wf: Workflow, inst: str) -> bool:
        """Whether an instance holds a person's gate the person has not
        passed: only the person makes one count as passed, so the main
        agent never skips it, whether it failed itself (a deadline, a
        render) or a group around it did."""
        node = self._template(wf, inst)
        gates = {
            g.key
            for g in walk(node)
            if g.kind == "gate"
            and g.body.get("by") == "person"
            and g.key not in st.delegated
        }
        if not gates:
            return False
        prefix = inst[: -len(node.id)]
        return any(
            r["node"] in gates
            and r["id"].startswith(prefix)
            and (r.get("each") or r["state"] not in ("done", "skipped"))
            for r in self._rows(st, wf)
        )

    async def stop_node(self, run: str, inst: str) -> None:
        """Stop one running leaf: interrupt it and fail it as told, so the
        main agent edits, retries or skips it. The run goes on."""
        async with self._lock:
            st = self._get(run)
            wf = self.defs.get(run)
            n = st.nodes.get(inst)
            tpl = wf.by_key.get(template_key(inst)) if wf else None
            if (
                st.status != "running"
                or n is None
                or n.status != "running"
                or tpl is None
                or tpl.kind not in LEAVES
            ):
                raise OpError(
                    "not_running", f"{inst} is not a running step of run {run}"
                )
            if tpl.kind == "gate":
                who = "the person" if tpl.body.get("by") == "person" else "you"
                raise OpError(
                    "not_running",
                    f"{inst} is a gate: {who} answer{'s' if who != 'you' else ''} it; "
                    "it is never stopped",
                )
            await self._halt(st, inst, n)
            self._fail(st, inst, "stopped by the main agent", retry=False, by="main")

    async def _tell_owner(self, st: RunState, outcome: str, body: str) -> bool:
        """Whether the main agent was told."""
        owner = self.owner(st)
        if owner is None:
            return False
        try:
            await owner.deliver(
                f"> from workflow:{self._wid(st)} · {outcome} · {iso_now()}", body
            )
        except Exception:
            log.exception("aegis: could not tell %s how run %s ended", st.owner, st.run)
            return False
        return True

    def _fail(
        self,
        st: RunState,
        node: str,
        why: str,
        say: str = "",
        retry: bool = True,
        by: str = "",
        tail: str = "",
    ) -> None:
        """Fail one node, or send it back to pending while it has retries
        left. ``retry`` False marks a failure a retry would only repeat (a
        reference with no value, a cut command). ``by`` main marks a failure
        the main agent caused itself, so it is not told of it."""
        rec = (
            {"ev": "failed", "node": node, "error": why}
            | ({"say": say} if say else {})
            | ({"by": by} if by else {})
            | ({"tail": tail} if tail else {})
        )
        self._forget_probes(st.run, node)
        wf = self.defs.get(st.run)
        tpl = wf.by_key.get(template_key(node)) if wf else None
        cur = st.nodes.get(node) or NodeState()
        tried = cur.attempts - cur.armed_at  # since it was last armed
        if retry and tpl is not None and tried < tpl.retries:
            # One record fails it and sends it back to pending (workflow_log).
            self._log(st.run, rec | {"retrying": tried + 2, "of": tpl.retries + 1})
            self.changed()
            return
        # It waits on the main agent now; what waited inside it no longer does.
        # Only a map or a loop adds a segment to an id, so the instances
        # under a seq or a par are found by their template, not a prefix.
        under = {k for k, _ in self._under(st, tpl, node)} if tpl else {node}
        closes = [k for k in st.open if k in under and k != node] + [
            k
            for k, o in st.open.items()
            if o["kind"] == "ask" and o.get("node") in under
        ]
        self._log(st.run, rec | ({"closes": closes} if closes else {}))
        # A failed node's session keeps its card and gets a tab, as a failed
        # queue worker does (spec §05).
        n = st.nodes.get(node)
        for lid in _lids(n) if n is not None else []:
            if lid in self._reg.sessions:
                self._reg.set_quiet(lid, False)
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

    def _check_permission(self, owner: Session, alias: str, path: str) -> None:
        """An agent a node or a judge runs never above the arming session."""
        agents, default = self._app._agents()
        spec = resolve(
            agents,
            default,
            alias,
            {},
            Path(owner.spec.cwd),
            owner=owner.spec.owner,
        )
        if PERMISSION_ORDER.index(spec.permission) > PERMISSION_ORDER.index(
            owner.spec.permission
        ):
            raise OpError(
                "not_allowed",
                f"{path}: agent {alias} runs with {spec.permission}, above your {owner.spec.permission}",
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
        if rec["ev"] in STARTS:
            # A start sets the begin of every deadline over it: the next
            # pass reads it and asks for a pass at the earliest end.
            self.changed()
        self._publish(run)

    def _publish(self, run: str) -> None:
        """Mark the run changed; its wire goes out at the next flush, at most
        PUBLISH_S from now. Building the wire walks the whole tree, so a map
        of many items rebuilt it, and every browser redrew it, per record."""
        self._dirty.add(run)
        if self._flush_at is not None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self._flush_publish()  # a sync caller with no loop: at once
            return
        self._flush_at = loop.call_later(PUBLISH_S, self._flush_publish)

    def _publish_now(self) -> None:
        """Send every changed run's wire now, not at the next flush."""
        if self._flush_at is not None:
            self._flush_at.cancel()
        self._flush_publish()

    def _flush_publish(self) -> None:
        """One run's wire that raises is logged, once until it goes out, and
        stays changed: it rides the next flush another publish arms, and the
        others go out now. It never arms a flush itself, or a wire that fails
        on its run's state would be retried, and logged, every PUBLISH_S."""
        self._flush_at = None
        runs, self._dirty = self._dirty, set()
        # Three channels: `workflow-runs` has every run's summary, and
        # `workflow:<run>` one run whole, for a browser that shows its card;
        # `workflows` has every run whole, for a client older than #525. A
        # whole run walks its tree, so it is built only for a subscriber.
        subscribers = self._app.channels.subscribers
        whole = subscribers("workflows") > 0
        ops, summaries, failed = [], [], []
        for r in sorted(runs):
            if r not in self.runs:
                continue
            watched = subscribers(f"workflow:{r}") > 0
            try:
                summary = self.wire(r, detail=False)
                full = self.wire(r) if whole or watched else None
            except Exception:
                if r not in self._unpublishable:
                    log.exception("aegis: workflow run %s could not be published", r)
                    self._unpublishable.add(r)
                failed.append(r)
                continue
            self._unpublishable.discard(r)
            summaries.append({"upsert": summary})
            if whole:
                ops.append({"upsert": full})
            if watched:
                self._app.publish(f"workflow:{r}", [{"set": full}])
        if summaries:
            self._app.publish("workflow-runs", summaries)
        if ops:
            self._app.publish("workflows", ops)
        self._dirty.update(failed)
