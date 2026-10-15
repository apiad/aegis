"""What happened to one workflow run, with no server around it.

One run is one file, ``<state>/workflow-runs/<run>.jsonl``: append-only, one
JSON record per event, folded at boot into a ``RunState``. Nothing is stored
twice: the run's status, each node's state and the story the card tells are
all folded from these records (the schedules' rule, schedule_log.py).

The story is one line per event a person reads as news, each built from the
node's own ``say`` and the worker's ``summary``: no model writes it.

A map's items are recorded once, when it starts, so a restart never renders
its ``over`` again. Seq, par and map have no state of their own here: the
engine derives it from their leaves.
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


@dataclass
class RunState:
    run: str
    owner: str  # the main agent's log id
    text: str  # the definition, as armed
    args: dict
    status: str = "armed"
    created: float = 0.0
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
        _tell(st, t, "bad", f"{node.say} Failed: {node.error}".strip())


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
