"""What happened to one workflow run, with no server around it.

One run is one file, ``<state>/workflow-runs/<run>.jsonl``: append-only, one
JSON record per event, folded at boot into a ``RunState``. Nothing is stored
twice: the run's status, each node's state and the story the card tells are
all folded from these records (the schedules' rule, schedule_log.py).

The story is one line per event a person reads as news, each built from the
node's own ``say`` and the worker's ``summary``: no model writes it.

A map's items are recorded once, when it starts, so a restart never renders
its ``over`` again; an if's branch, a loop's max and each round's until are
recorded the same way, so a restart never asks a probe again once its answer
is logged. Seq, par and map have no state of their own here: the engine
derives it from their leaves. A ``retry`` record sends a leaf back to pending
with one more attempt, keeping its early outputs: a step may have started on
them.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

LIVE = frozenset({"armed", "running"})


@dataclass
class NodeState:
    # pending | running | done | failed | stopped; queued is never stored.
    status: str = "pending"
    log_id: str | None = None
    started: float | None = None
    ended: float | None = None
    say: str = ""
    result: object = None
    summary: str = ""
    error: str = ""
    nudged: bool = False
    outputs: dict = field(default_factory=dict)  # early outputs, by name
    items: list | None = None  # a map's [[key, value], ...], once it starts
    cmd: str = ""  # an sh's or sh check's command, rendered
    pid: int | None = None  # a running command's process, for boot to end
    pstart: int | None = None  # its start in clock ticks, so a reused pid is spared
    until: float | None = None  # a wait's end, wall time
    attempts: int = 0  # retries so far
    took: str | None = None  # an if's branch: then, else or none
    round: int = 0  # a loop's current round
    max: int | None = None  # a loop's max, read once when it starts
    untils: dict[int, dict] = field(default_factory=dict)  # round -> {pass, why}
    # A judge's session and verdict by round ("" for a check or an if).
    judges: dict[str, dict] = field(default_factory=dict)


@dataclass
class RunState:
    run: str
    owner: str  # the main agent's log id
    text: str  # the definition, as armed
    args: dict
    status: str = "armed"
    created: float = 0.0
    cwd: str = ""  # where its commands run: the main agent's cwd at arm
    started: float | None = None
    ended: float | None = None
    nodes: dict[str, NodeState] = field(default_factory=dict)
    story: list[dict] = field(default_factory=list)


def _tell(st: RunState, t: float, mark: str, text: str) -> None:
    st.story.append({"t": t, "mark": mark, "text": text})


def apply(st: RunState, r: dict) -> None:
    ev, t = r.get("ev"), float(r.get("t") or 0)
    if ev == "approved":
        st.status, st.started = "running", t
    elif ev == "rejected":
        st.status, st.ended = "rejected", t
    elif ev == "stopped":
        st.status, st.ended = "stopped", t
        # A node the stop interrupted ends with the run, not running forever.
        for n in st.nodes.values():
            if n.status == "running":
                n.status, n.ended = "stopped", t
        _tell(st, t, "bad", "Stopped.")
    elif ev == "finished":
        st.status, st.ended = r.get("status") or "done", t
        if st.status == "done":
            _tell(st, t, "ok", "The workflow is done.")
        else:
            # A failure ends the nodes still running beside it, as a stop does.
            for n in st.nodes.values():
                if n.status == "running":
                    n.status, n.ended = "stopped", t
    if "node" not in r:
        return
    node = st.nodes.setdefault(r["node"], NodeState())
    if ev == "node_started":
        node.status, node.log_id, node.started = "running", r.get("log_id"), t
        node.say = r.get("say") or ""
        node.cmd, node.until = r.get("cmd") or "", r.get("until")
    elif ev == "result":
        node.result, node.summary = r.get("value"), r.get("summary") or ""
    elif ev == "nudged":
        node.nudged = True
    elif ev == "node_done":
        node.status, node.ended = "done", t
        _tell(st, t, "ok", f"{node.say} {node.summary}".strip())
    elif ev == "output":
        node.outputs[r["name"]] = r.get("value")
        text = f"{node.say} Early output {r['name']}: {r.get('summary') or ''}"
        _tell(st, t, "ok", text.strip())
    elif ev == "map_items":
        node.items, node.started = r.get("items") or [], t
    elif ev == "failed":
        node.status, node.ended, node.error = "failed", t, r.get("error") or ""
        node.say = node.say or r.get("say") or ""
        text = f"{node.say} Failed: {node.error}"
        if r.get("retrying"):
            text += f" Retrying, attempt {r['retrying']} of {r.get('of')}."
        _tell(st, t, "bad", text.strip())
    elif ev == "pid":
        node.pid, node.pstart = r.get("pid"), r.get("start")
    elif ev == "judge_started":
        node.judges.setdefault(r.get("round", ""), {})["log_id"] = r.get("log_id")
        node.started = node.started or t
    elif ev == "judge_nudged":
        node.judges.setdefault(r.get("round", ""), {})["nudged"] = True
    elif ev == "verdict":
        j = node.judges.setdefault(r.get("round", ""), {})
        j |= {"pass": bool(r.get("pass")), "why": r.get("why") or ""}
        j.pop("nudged", None)
        _tell(st, t, "ok" if j["pass"] else "bad", f"Judge: {j['why']}".strip())
    elif ev == "branch":
        node.took, node.say = r.get("took"), node.say or r.get("say") or ""
        node.status = "done" if node.took == "none" else "running"
        node.started = node.started or t
        _tell(st, t, "ok", f"{node.say} Took {node.took}: {r.get('why') or ''}".strip())
    elif ev == "loop_started":
        node.max, node.round, node.started = r.get("max"), 0, t
        node.status, node.say = "running", r.get("say") or ""
    elif ev == "round":
        node.round = int(r.get("n") or 0)
    elif ev == "until":
        n = int(r.get("n") or 0)
        node.untils[n] = {"pass": bool(r.get("pass")), "why": r.get("why") or ""}
        _tell(
            st,
            t,
            "ok" if r.get("pass") else "bad",
            f"{node.say} Round {n}: {r.get('why') or ''}".strip(),
        )
    elif ev == "retry":
        attempts, outputs, say = node.attempts + 1, node.outputs, node.say
        st.nodes[r["node"]] = NodeState(attempts=attempts, outputs=outputs, say=say)


def fold(records: list[dict]) -> RunState | None:
    st = None
    for r in records:
        if r.get("ev") == "armed":
            st = RunState(
                r["run"],
                r["owner"],
                r.get("text") or "",
                r.get("args") or {},
                created=float(r.get("t") or 0),
                cwd=r.get("cwd") or "",
            )
        elif st is not None:
            apply(st, r)
    return st


class RunLog:
    def __init__(self, path: Path) -> None:
        self.path = path

    def append(self, rec: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a") as f:
            f.write(json.dumps(rec) + "\n")

    def read(self) -> list[dict]:
        if not self.path.exists():
            return []
        out = []
        for line in self.path.open(errors="replace"):
            try:
                rec = json.loads(line)
            except ValueError:
                continue  # a damaged line never takes the run down
            if isinstance(rec, dict):
                out.append(rec)
        return out
