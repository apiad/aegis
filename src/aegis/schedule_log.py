"""What a schedule is and what happened to it, with no server around it.

The definitions (``Definition``, ``Schedule``) are what ``<state>/schedules.json``
holds, written whole on each change. What happens is the run log,
``<state>/schedule-runs.jsonl``: one JSON record per event, append-only,
replayed at boot like ``tasks.jsonl``. The runtime state of a schedule (its
last tick, the hash of its last fire's stdout, the held tick, its live runs
and its errors in a row) is folded from the log by ``apply`` and never stored
twice, so a restart rebuilds exactly what the log says.

Every record has ``t`` (when it happened, local ISO time), ``s`` (the
schedule) and ``ev``. A tick record also has ``tick``, the tick's own time in
whole seconds, and ``out``: fired, quiet, held, skipped, error or missed.

Boot compacts the log: a stretch of quiet ticks becomes one record with
``from``, ``to`` and ``n``, each schedule keeps its last 100 fires and
everything after the oldest of them, and a deleted schedule's records go.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from .clock import ClockError, clock_from

CHECK_TIMEOUT_MAX = 300
KEEP_FIRES = 100
# The tick outcomes a person reads as nothing happening.
QUIET = "quiet"


class _Strict(BaseModel):
    model_config = {"extra": "forbid"}


class Check(_Strict):
    bash: str = Field(min_length=1, description="Bash run on each tick.")
    timeout_s: int = Field(30, ge=1, le=CHECK_TIMEOUT_MAX)


class Fire(_Strict):
    target: Literal["new", "existing"] = "new"
    # new: the agent, None for default_agent; where it runs, None for the
    # creator's directory (filled in at creation); the close rule; whether the
    # run may start other work.
    agent: str | None = None
    cwd: str | None = None
    close: Literal["done", "always", "never"] = "done"
    spawn: bool = False
    # existing: the session, by log id once created (a handle is accepted
    # and resolved, so a rename does not lose it).
    session: str | None = None

    @model_validator(mode="after")
    def _target(self) -> "Fire":
        if self.target == "existing" and not self.session:
            raise ValueError("an existing target names its session")
        return self


class Definition(_Strict):
    clock: dict[str, str] = Field(
        description='One of {"every": "5m"}, {"cron": "0 6 * * 1-5"}, '
        '{"at": "2026-10-15 09:00"}, in the server\'s local time.'
    )
    overlap: Literal["wait", "skip", "parallel"] = "wait"
    check: Check | None = None
    fire: Fire
    prompt: str = Field(min_length=1)

    @model_validator(mode="after")
    def _valid(self) -> "Definition":
        try:
            clock_from(self.clock)
        except ClockError as e:
            raise ValueError(str(e)) from None
        if self.overlap == "parallel" and self.fire.target == "existing":
            raise ValueError(
                "parallel needs a new session per fire; an existing session's "
                "inbox already holds a message until its turn ends"
            )
        return self


@dataclass
class Schedule:
    name: str
    owner: dict  # {"kind": "user"|"agent", "principal", "log_id"?, "ceiling"?}
    created_at: int
    edited_at: int  # an every counts from here
    d: Definition
    paused: dict | None = None  # {"by": "user"|"errors", "at": iso}
    finished_at: int | None = None  # an at whose tick went through the layers

    def dump(self) -> dict:
        return {
            "owner": self.owner,
            "created_at": self.created_at,
            "edited_at": self.edited_at,
            "paused": self.paused,
            "finished_at": self.finished_at,
            **self.d.model_dump(exclude_none=True),
        }

    @classmethod
    def load(cls, name: str, raw: dict) -> "Schedule":
        keys = ("clock", "overlap", "check", "fire", "prompt")
        return cls(
            name=name,
            owner=raw["owner"],
            created_at=int(raw["created_at"]),
            edited_at=int(raw.get("edited_at") or raw["created_at"]),
            d=Definition.model_validate({k: raw[k] for k in keys if k in raw}),
            paused=raw.get("paused"),
            finished_at=raw.get("finished_at"),
        )


@dataclass
class Run:
    run: int
    log_id: str
    started: float
    target: str  # "new" | "existing"
    tick: int


@dataclass
class State:
    last_tick: int = 0
    sha: str | None = None  # of the last fire's stdout
    held: str | None = None  # "overlap" | "quota"
    held_tick: int | None = None
    errors: int = 0  # check or fire errors in a row
    runs: int = 0  # the highest run number so far
    live: dict[int, Run] = field(default_factory=dict)


def iso(t: float) -> str:
    return datetime.fromtimestamp(t).isoformat(timespec="seconds")


def at(s: str) -> float:
    return datetime.fromisoformat(s).timestamp()


def apply(st: State, r: dict) -> None:
    """Fold one record into a schedule's state."""
    ev = r.get("ev")
    if ev in ("created", "edited", "resumed"):
        st.last_tick = int(r["tick"])
        if ev == "resumed":
            st.errors = 0
    elif ev == "tick":
        st.last_tick = max(st.last_tick, int(r["tick"]))
        out = r.get("out")
        if out == "error":
            st.errors += 1
        elif out in ("fired", QUIET):
            st.errors = 0
        if out == "held":
            st.held, st.held_tick = r.get("why") or "overlap", int(r["tick"])
        elif out in ("fired", QUIET, "skipped", "error"):
            st.held = st.held_tick = None
        if out == "fired" and r.get("sha"):
            st.sha = r["sha"]
    elif ev == "fired":
        st.runs = max(st.runs, int(r["run"]))
        st.live[int(r["run"])] = Run(
            int(r["run"]),
            r["log_id"],
            at(r["t"]),
            r.get("target") or "new",
            int(r.get("tick") or 0),
        )
    elif ev == "finished":
        st.live.pop(int(r["run"]), None)


def fold(records: list[dict]) -> dict[str, State]:
    out: dict[str, State] = {}
    for r in records:
        if isinstance(r.get("s"), str):
            apply(out.setdefault(r["s"], State()), r)
    return out


def stretches(rs: list[dict]) -> list[dict]:
    """One schedule's records, in order, with each run of quiet ticks merged
    into one record carrying ``from``, ``to`` and ``n``."""
    out: list[dict] = []
    for r in rs:
        prev = out[-1] if out else None
        if (
            r.get("ev") == "tick"
            and r.get("out") == QUIET
            and prev is not None
            and prev.get("ev") == "tick"
            and prev.get("out") == QUIET
        ):
            out[-1] = {
                **r,
                "from": prev.get("from", prev["t"]),
                "to": r["t"],
                "n": prev.get("n", 1) + 1,
            }
            continue
        out.append(r)
    return out


def compact(records: list[dict], names: set[str]) -> list[dict]:
    by: dict[str, list[dict]] = {}
    for r in records:
        if r.get("s") in names:
            by.setdefault(r["s"], []).append(r)
    out: list[dict] = []
    for rs in by.values():
        fires = [i for i, r in enumerate(rs) if r.get("ev") == "fired"]
        if len(fires) > KEEP_FIRES:
            rs = rs[fires[-KEEP_FIRES] :]
        out += stretches(rs)
    return out


def rows(rs: list[dict], handle: Callable[[str], str | None]) -> list[dict]:
    """One schedule's records as the runs table draws them, oldest first: a
    fire is one row with its end joined in, a quiet stretch is one row, and a
    tick that fired is left out because its fire is the row."""
    ends = {r["run"]: r for r in rs if r.get("ev") == "finished"}
    out: list[dict] = []
    for r in stretches(rs):
        ev = r.get("ev")
        if ev == "tick":
            if r.get("out") == "fired":
                continue
            row = {"t": r["t"], "kind": r["out"]}
            for k in ("n", "from", "to", "rc", "err", "why", "manual"):
                if k in r:
                    row[k] = r[k]
        elif ev == "fired":
            end = ends.get(r["run"])
            row = {
                "t": r["t"],
                "kind": "fired",
                "run": r["run"],
                "log_id": r["log_id"],
                "handle": handle(r["log_id"]),
                "stdout": r.get("stdout") or "",
                "live": end is None,
            }
            if end is not None:
                row |= {
                    k: end.get(k) for k in ("attention", "closed", "secs", "cost_usd")
                }
        elif ev in ("paused", "resumed", "edited", "created"):
            row = {"t": r["t"], "kind": ev}
            if "by" in r:
                row["by"] = r["by"]
        else:
            continue
        out.append(row)
    return out


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
                continue  # a damaged line never takes the log down
            if isinstance(rec, dict):
                out.append(rec)
        return out

    def of(self, name: str) -> list[dict]:
        return [r for r in self.read() if r.get("s") == name]

    def rewrite(self, records: list[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text("".join(json.dumps(r) + "\n" for r in records))
        os.replace(tmp, self.path)
