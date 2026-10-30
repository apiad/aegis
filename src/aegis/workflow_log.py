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
derives it from their leaves. A failure that says ``retrying`` sends its leaf
back to pending with one more attempt, keeping its early outputs (a step may
have started on them), in that one record, so no crash lands between the two.

What a run waits on is folded into ``open``, keyed by the id the main agent
answers to: a failure that will not retry, a gate, a question, an edit's
outcome, a person's gate opening or passed, a run stuck on room. ``woke`` (and ``approved_told``, for an approval) records that the main agent was told, so a restart tells
again only what was never delivered; the answers (``retried``, ``skipped``,
``extended``, ``gate_answered``, ``answered``, ``edit_applied``,
``edit_rejected``) close the items, and a notice (an edit's outcome, a
person's gate passed, a stuck report) closes once told. A record that wakes for news the
main agent was never woken for before carries ``tell``, so an older log
folds as it did and a restart tells nothing it had kept quiet.
``gate_delegated`` hands a person's gate to the main agent: the open one,
and every later opening of the same step (``delegated``, by template key). A stop or an ending stops only leaves: a loop or an if keeps its
round and branch, so arming a run again goes on from there.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

LIVE = frozenset({"armed", "running"})
NOTICES = frozenset(
    {"edit", "passed", "stuck"}
)  # open only until the main agent is told


@dataclass
class NodeState:
    # pending | running | done | failed | stopped | skipped; queued is never stored.
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
    # Its attempts when the run was last armed again: automatic retries
    # count from here, so arming again gives a step its retries back.
    armed_at: int = 0
    took: str | None = None  # an if's branch: then, else or none
    round: int = 0  # a loop's current round
    max: int | None = None  # a loop's max, read once when it starts
    untils: dict[int, dict] = field(default_factory=dict)  # round -> {pass, why}
    # A judge's session and verdict by round ("" for a check or an if).
    judges: dict[str, dict] = field(default_factory=dict)
    gate: dict | None = None  # {by, ask, shown} once a gate opened
    tail: str = ""  # a failed or timed-out sh's last lines of output
    main: bool = False  # a session: main step, run in the main agent's session


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
    version: int = 1
    replaced_by: str | None = None
    asks: int = 0  # questions asked so far, for the next q id
    # What the run waits on, by the id the main agent answers to: a failed
    # node's or a gate's instance path, a question's q<n>, an edit's v<n>.
    open: dict[str, dict] = field(default_factory=dict)
    proposal: dict | None = None  # the edit waiting for the person
    edits: list[dict] = field(default_factory=list)  # {v, diff, status, t}
    saved: dict | None = None  # {id, sha, source}: the saved workflow it runs
    # Who launched it, with no click: {typed, by: person, principal, line}
    # or {typed, by: schedule, name, run, line}. Never the token.
    launch: dict | None = None
    # The last stuck report, {nodes, held_by, full}: the same one is not told twice.
    stuck: dict | None = None
    # A rejection logged and not yet told to the main agent.
    reject_untold: bool = False
    approve_untold: bool = False
    # The text and version a person last approved: arming again compares
    # with these, so a rejected change shows again when it is armed again.
    approved: dict | None = None
    # The person's gates the person handed to the main agent, by template
    # key: each later opening of one is the main agent's.
    delegated: set[str] = field(default_factory=set)


def _tell(st: RunState, t: float, mark: str, text: str, **extra) -> None:
    st.story.append({"t": t, "mark": mark, "text": text, **extra})


def _stop_leaves(st: RunState, t: float) -> None:
    """A stop or an ending stops the leaves still running. A loop or an
    if keeps its own state (round, branch), so arming again keeps it."""
    for n in st.nodes.values():
        if n.status == "running" and n.max is None and n.took is None:
            n.status, n.ended = "stopped", t


def _reset(st: RunState, insts: list[str], again: bool = False) -> None:
    """Each instance back to pending with one attempt more, keeping its early
    outputs: a retry by the main agent, or arming the run again, which
    also starts its automatic retries afresh."""
    for k in insts:
        n = st.nodes.get(k)
        if n is not None:
            at = n.attempts + 1
            st.nodes[k] = NodeState(
                attempts=at, outputs=n.outputs, armed_at=at if again else n.armed_at
            )


def _edit_status(st: RunState, v, status: str) -> None:
    for e in st.edits:
        if e["v"] == v:
            e["status"] = status


def _drop_proposal(st: RunState) -> None:
    """A change still waiting when the run stops, ends or is armed again
    is dropped: arming again brings no live buttons back for it."""
    if st.proposal is not None:
        _edit_status(st, st.proposal["v"], "dropped")
        st.proposal = None


def _started_by(launch: dict) -> str:
    """The story's first line for a launched run: "Started by your /loop
    line: check the deploy every 5m", or "Started by schedule nightly, run 4"."""
    if launch.get("by") == "schedule":
        return f"Started by schedule {launch.get('name')}, run {launch.get('run')}"
    cmd = (launch.get("line") or "/workflow").split()[0]
    return f"Started by your {cmd} line: {launch.get('typed') or ''}".rstrip(": ")


def apply(st: RunState, r: dict) -> None:
    """Fold one record. A story line told by a record about a node carries
    that node's instance, so the card can fold a long map's lines with it."""
    told = len(st.story)
    _apply(st, r)
    if isinstance(node := r.get("node"), str):
        for s in st.story[told:]:
            s.setdefault("node", node)


def _apply(st: RunState, r: dict) -> None:
    ev, t = r.get("ev"), float(r.get("t") or 0)
    if ev == "approved":
        st.status, st.started = "running", t
        st.approve_untold = bool(r.get("tell"))
        st.approved = {"text": st.text, "v": st.version}
        if r.get("by") and st.launch:
            _tell(st, t, "ok", _started_by(st.launch))
    elif ev == "rejected":
        st.status, st.ended = "rejected", t
        st.reject_untold = bool(r.get("tell"))
    elif ev == "rejected_told":
        st.reject_untold = False
    elif ev == "approved_told":
        st.approve_untold = False
    elif ev == "stopped":
        st.status, st.ended = "stopped", t
        # A node the stop interrupted ends with the run, not running forever.
        _stop_leaves(st, t)
        st.open = {}  # an ended run waits on nothing
        _drop_proposal(st)
        _tell(st, t, "bad", "Stopped.")
    elif ev == "finished":
        st.status, st.ended = r.get("status") or "done", t
        st.open = {}
        _drop_proposal(st)
        if st.status == "done":
            _tell(st, t, "ok", "The workflow is done.")
        else:
            # A failure ends the nodes still running beside it, as a stop does.
            _stop_leaves(st, t)
            if r.get("why"):
                _tell(st, t, "bad", r["why"])
    elif ev == "replaced":
        st.status, st.ended, st.replaced_by = "replaced", t, r.get("by")
    elif ev == "rearmed":
        st.status, st.ended = "armed", None
        st.text, st.version = r.get("text") or st.text, int(r.get("v") or st.version)
        _reset(st, r.get("reset") or [], again=True)
        for n in st.nodes.values():
            # A judge the stop cut off never answers: arming again asks anew.
            n.judges = {k: j for k, j in n.judges.items() if "pass" in j}
        _drop_proposal(st)
        st.open = {}
        kept = sum(n.status in ("done", "skipped") for n in st.nodes.values())
        # A new definition's diff rides on the line, for the again card.
        change = {k: r[k] for k in ("diff", "commands") if r.get(k)}
        _tell(
            st,
            t,
            "ok",
            f"Armed again: {kept} done step{'s' if kept != 1 else ''} kept.",
            **change,
        )
    elif ev == "note":
        _tell(st, t, "note", r.get("text") or "")
    elif ev == "answered":
        q, text = str(r.get("q")), r.get("text") or ""
        st.open.pop(q, None)
        _tell(st, t, "ok", f"Answered {q}: {text}".strip())
    elif ev == "woke":
        to = str(r.get("to"))
        o = st.open.get(to)
        if o is not None:
            o["told"] = True
            if o.get("kind") in NOTICES:
                del st.open[to]
    elif ev == "stuck":
        st.stuck = {k: r.get(k) for k in ("nodes", "held_by", "full")}
        text = r.get("text") or ""
        st.open[str(r.get("to"))] = {
            "kind": "stuck",
            "text": text,
            "full": r.get("full"),
            "here": r.get("here"),
            "elsewhere": r.get("elsewhere"),
            "told": False,
        }
        _tell(st, t, "note", f"Stuck: {text}")
    elif ev == "edit_proposed":
        if st.proposal is not None:
            _edit_status(st, st.proposal["v"], "replaced")
        st.proposal = {
            k: r.get(k) for k in ("v", "text", "diff", "touched", "commands")
        }
        st.edits.append(
            {"v": r.get("v"), "diff": r.get("diff") or [], "status": "proposed", "t": t}
        )
    elif ev in ("edit_applied", "edit_rejected"):
        v, outcome = r.get("v"), "applied" if ev == "edit_applied" else "rejected"
        if ev == "edit_applied" and st.proposal is not None and st.proposal["v"] == v:
            st.text, st.version = st.proposal.get("text") or st.text, int(v or 0)
            st.approved = {"text": st.text, "v": st.version}
        if st.proposal is not None and st.proposal["v"] == v:
            st.proposal = None
        _edit_status(st, v, outcome)
        st.open[f"v{v}"] = {"kind": "edit", "outcome": outcome, "told": False}
    if "node" not in r:
        return
    node = st.nodes.setdefault(r["node"], NodeState())
    if ev == "node_started":
        node.status, node.log_id, node.started = "running", r.get("log_id"), t
        node.say = r.get("say") or ""
        node.cmd, node.until = r.get("cmd") or "", r.get("until")
        node.main = bool(r.get("main"))
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
        node.tail = r.get("tail") or ""
        node.say = node.say or r.get("say") or ""
        text = f"{node.say} Failed: {node.error}"
        if r.get("retrying"):
            text += f" Retrying, attempt {r['retrying']} of {r.get('of')}."
        _tell(st, t, "bad", text.strip())
        if r.get("retrying"):
            _retry(st, r["node"])
        else:
            # It waits on the main agent; what failed inside it waits no more.
            for k in r.get("closes") or []:
                st.open.pop(k, None)
            st.open[r["node"]] = {
                "kind": "failed",
                "node": r["node"],
                "error": node.error,
                "told": r.get("by") == "main",
            }
    elif ev == "retried":
        st.open.pop(r["node"], None)
        say, text = node.say, r.get("text") or ""
        _reset(st, r.get("reset") or [r["node"]])
        _tell(st, t, "ok", f"Retrying {say}" + (f" {text}" if text else ""))
    elif ev == "skipped":
        st.open.pop(r["node"], None)
        node.status, node.ended = "skipped", t
        node.result, node.summary = r.get("value"), r.get("summary") or ""
        _tell(st, t, "ok", f"{node.say} Skipped: {node.summary}".strip())
    elif ev == "gate_opened":
        node.status, node.started = "running", t
        node.say = r.get("say") or node.say
        node.gate = {k: r.get(k) for k in ("by", "ask", "shown")}
        st.open[r["node"]] = {
            "kind": "gate",
            "node": r["node"],
            "by": r.get("by"),
            # A person's gate tells the main agent it waits, not what to
            # answer; one logged before that tell stays quiet.
            "told": r.get("by") == "person" and not r.get("tell"),
        }
        who = "the person" if r.get("by") == "person" else "the main agent"
        _tell(st, t, "ask", f"{node.say} Asks {who}: {r.get('ask') or ''}".strip())
    elif ev == "gate_delegated":
        st.delegated.add(r.get("key") or "")
        o = st.open.get(r["node"])
        if o is not None and o.get("kind") == "gate":
            o["by"], o["told"] = "main", False
        if node.gate is not None:
            node.gate["by"] = "main"
        _tell(
            st,
            t,
            "ok",
            f"{node.say} Handed to the main agent by the person, now and each later time.",
        )
    elif ev == "gate_answered":
        st.open.pop(r["node"], None)
        text = r.get("text") or ""
        if r.get("go"):
            node.status, node.ended = "done", t
            node.result = {"value": r.get("value"), "by": r.get("by")}
            who = "the person" if r.get("by") == "person" else "the main agent"
            node.summary = f"Continued by {who}" + (f": {text}" if text else ".")
            _tell(st, t, "ok", f"{node.say} {node.summary}")
            if r.get("by") == "person" and r.get("tell"):
                st.open[r["node"]] = {
                    "kind": "passed",
                    "node": r["node"],
                    "value": r.get("value"),
                    "told": False,
                }
        else:
            node.status, node.ended = "stopped", t
            _tell(
                st,
                t,
                "bad",
                f"{node.say} Stopped there" + (f": {text}" if text else "."),
            )
    elif ev == "asked":
        st.asks += 1
        st.open[r["q"]] = {
            "kind": "ask",
            "node": r["node"],
            "question": r.get("question") or "",
            "told": False,
        }
        q = r.get("question") or ""
        _tell(st, t, "ask", f"{node.say} Asks the main agent ({r['q']}): {q}".strip())
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
    elif ev == "probe_started":
        # An if asking its command probe is running, and its deadline counts
        # from here; the branch record tells the story once it answers.
        node.status, node.started = "running", node.started or t
        node.say = node.say or r.get("say") or ""
    elif ev == "branch":
        node.took, node.say = r.get("took"), node.say or r.get("say") or ""
        node.status = "done" if node.took == "none" else "running"
        node.started = node.started or t
        why = f": {r['why']}" if r.get("why") else "."
        _tell(st, t, "ok", f"{node.say} Took {node.took}{why}".strip())
    elif ev == "loop_started":
        # Round 1 begins with the loop, so a crash after this record still
        # runs round 1, not a round 0. Older logs follow with a round record.
        node.max, node.round, node.started = r.get("max"), int(r.get("round") or 1), t
        node.status, node.say = "running", r.get("say") or ""
    elif ev == "round":
        node.round = int(r.get("n") or 0)
    elif ev == "extended":
        # A loop out of rounds gets more: it runs again from its next round.
        st.open.pop(r["node"], None)
        node.max, node.status, node.ended, node.error = (
            r.get("max"),
            "running",
            None,
            "",
        )
        _tell(st, t, "ok", f"{node.say} More rounds: up to {node.max}.".strip())
    elif ev == "until":
        n = int(r.get("n") or 0)
        node.untils[n] = {"pass": bool(r.get("pass")), "why": r.get("why") or ""}
        _tell(
            st,
            t,
            "ok" if r.get("pass") else "bad",
            (
                f"{node.say} Round {n}" + (f": {r['why']}" if r.get("why") else ".")
            ).strip(),
        )
    elif ev == "retry" and node.status == "failed":
        _retry(st, r["node"])  # older logs: the failure did not retry itself


def _retry(st: RunState, inst: str) -> None:
    """Back to pending with one more attempt, keeping its early outputs."""
    n = st.nodes[inst]
    st.nodes[inst] = NodeState(
        attempts=n.attempts + 1, outputs=n.outputs, say=n.say, armed_at=n.armed_at
    )


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
                version=int(r.get("v") or 1),
                saved=r.get("saved"),
                launch=r.get("launch"),
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
