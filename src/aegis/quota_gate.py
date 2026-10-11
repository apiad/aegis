"""Whether a token may start a harness now, from its policy and the pool gauges.

A policy maps a pool (a quota provider's name: ``claude``, ``opencode-go``, or
``codex``, which has no gauge) to a mode. ``closed`` starts nothing there;
``open`` starts anything; ``lines`` compares each listed window of the pool's
reading with a launch line and a stop line. Below every launch line the token
is open. At or above any stop line it is stopped: aegis interrupts its turns
(app.py). In between it is closed: running work goes on, nothing new starts. A
launch line of ``"pace"`` means the window's projected spend at reset is at
most 100%.

A pool the policy does not name is closed, and so is a session that draws on
an account aegis reads no gauge for (OpenCode on a provider other than Go).
Codex has no gauge, so its policy is only ever closed or open. No policy at
all, the admin's case, is open. A reading older than STALE_S closes the gate
and never stops anything: a failed poll must not interrupt work. Pure: the
caller passes the snapshot ``Quota.snapshot()`` returns and the time.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TypeGuard, cast

from .quota import PROVIDERS

STALE_S = 900.0
# Pool name -> the harness that draws on it, and the windows a line may name.
POOLS = {p.name: p.harness for p in PROVIDERS} | {"codex": "codex"}
KINDS = {p.name: tuple(k for k, _ in p.bar_windows) for p in PROVIDERS}
MODES = ("closed", "open", "lines")


def pools_wire() -> list[dict]:
    """The pools a policy names and their windows, for the policy editor. A
    window with a known span can take a pace line."""
    return [
        {
            "name": p.name,
            "label": p.label,
            "windows": [
                {"kind": k, "label": label, "pace": k in p.window_spans}
                for k, label in p.bar_windows
            ],
        }
        for p in PROVIDERS
    ] + [{"name": "codex", "label": "Codex", "windows": []}]


@dataclass(frozen=True)
class Gate:
    state: str  # "open" | "closed" | "stopped"
    why: str = ""
    resets_at: float | None = None

    @property
    def may_start(self) -> bool:
        return self.state == "open"


def pool_for(harness: str, model: str | None) -> str | None:
    """The pool a session draws on; the client's providerFor (gauges.js)."""
    for name, h in POOLS.items():
        if h != harness:
            continue
        if harness == "opencode" and not (model or "").startswith(f"{name}/"):
            continue
        return name
    return None


def _reading(snapshot: dict, pool: str) -> dict | None:
    return next(
        (p for p in snapshot.get("providers", []) if p.get("name") == pool), None
    )


def decide(policy: dict | None, pool: str | None, snapshot: dict, now: float) -> Gate:
    if policy is None:
        return Gate("open")
    if pool is None:
        return Gate("closed", "aegis reads no quota for this model's account")
    pp = policy.get(pool)
    if not isinstance(pp, dict) or pp.get("mode") == "closed":
        return Gate("closed", f"the policy does not open {pool}")
    if errs := validate({pool: pp}):
        return Gate("closed", f"the policy for {pool} is invalid: {'; '.join(errs)}")
    if pp.get("mode") == "open":
        return Gate("open")
    reading = _reading(snapshot, pool)
    if reading is None or reading.get("read_at") is None:
        return Gate("closed", f"there is no reading of {pool} yet")
    name = reading.get("label") or pool
    stale = now - float(reading["read_at"]) > STALE_S
    windows = {x.get("kind"): x for x in reading.get("windows", [])}
    closed: Gate | None = None
    for kind, line in (pp.get("lines") or {}).items():
        x = windows.get(kind)
        if x is None:
            continue
        label = x.get("label") or kind
        pct = float(x.get("percent") or 0)
        if not stale and pct >= float(line["stop"]):
            return Gate(
                "stopped",
                f"{name} {label} is at {pct:.0f}%, stop line {line['stop']}%",
                x.get("resets_at"),
            )
        if closed is not None:
            continue
        launch = line["launch"]
        if launch == "pace":
            proj = x.get("projected")
            if proj is not None and proj > 100:
                closed = Gate(
                    "closed",
                    f"{name} {label} is on pace for {proj}% at reset",
                    x.get("resets_at"),
                )
        elif pct >= float(launch):
            closed = Gate(
                "closed",
                f"{name} {label} is at {pct:.0f}%, launch line {launch}%",
                x.get("resets_at"),
            )
    if stale:
        return Gate(
            "closed",
            f"the last reading of {name} is older than {int(STALE_S // 60)} minutes",
        )
    return closed or Gate("open")


def _clock(ts: float) -> str:
    return time.strftime("%H:%M", time.localtime(ts))


def refusal(owner: str, g: Gate) -> str:
    """Why ``owner`` may start nothing now, and when that changes."""
    text = f"{owner} cannot start a session now: {g.why}."
    if g.resets_at is not None:
        text += (
            " It opens again when the window drops below the line, at the latest"
            f" when it resets at {_clock(g.resets_at)}."
        )
    return text


def continued(owner: str, why: str) -> str:
    """The message a paused session gets when its gate opens again."""
    return (
        f"aegis paused your turn because {owner}'s quota line was reached ({why}).\n"
        "The pool has room again. Continue where you left off."
    )


def _percent(v: object) -> TypeGuard[int]:
    return isinstance(v, int) and not isinstance(v, bool)


def validate(policy: object) -> list[str]:
    """What is wrong with ``policy``; empty when it can be stored."""
    if not isinstance(policy, dict):
        return ["a policy is an object of pools"]
    errs: list[str] = []
    for pool, pp in policy.items():
        if not isinstance(pool, str) or pool not in POOLS:
            errs.append(f"unknown pool {pool!r}")
            continue
        modes = MODES if pool in KINDS else ("closed", "open")
        if not isinstance(pp, dict):
            errs.append(f"{pool}: mode is one of {', '.join(modes)}")
            continue
        pp = cast(dict, pp)
        if pp.get("mode") not in modes:
            errs.append(f"{pool}: mode is one of {', '.join(modes)}")
            continue
        lines = pp.get("lines") or {}
        if not isinstance(lines, dict):
            errs.append(f"{pool}: lines is an object of windows")
            continue
        for kind, line in lines.items():
            if not isinstance(kind, str) or kind not in KINDS[pool]:
                errs.append(f"{pool}: unknown window {kind!r}")
                continue
            line = line if isinstance(line, dict) else {}
            launch, stop = line.get("launch"), line.get("stop")
            if not _percent(stop) or not 1 <= stop <= 100:
                errs.append(f"{pool} {kind}: stop is 1 to 100")
            elif launch != "pace" and (not _percent(launch) or not 0 <= launch < stop):
                errs.append(f"{pool} {kind}: launch is below stop, or pace")
    return errs


def describe(policy: dict | None) -> str:
    """A policy in one line, for ``aegis token list``."""
    if policy is None:
        return "never gated"
    parts = []
    for pool, pp in policy.items():
        pp = pp if isinstance(pp, dict) else {}
        if pp.get("mode") != "lines":
            parts.append(f"{pool}: {pp.get('mode', '?')}")
            continue
        lines = pp.get("lines") or {}
        shown = ", ".join(
            f"{k} {v.get('launch')}/{v.get('stop')}"
            for k, v in lines.items()
            if isinstance(v, dict)
        )
        parts.append(f"{pool}: {shown or 'open'}")
    return "; ".join(parts) or "every pool closed"
