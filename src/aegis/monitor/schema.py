"""Records for the process-monitor substrate.

A monitor watches an agent-launched process (aegis never owns it) by polling
agent-supplied bash: ``done`` (exit 0 ⇒ complete), optional ``fail`` (exit 0 ⇒
failed), optional ``progress`` (echoes 0–100). On any terminal state the agent
is woken via an inbox callback.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# Terminal states an agent is notified about; "watching" is the only live one.
WATCHING = "watching"
DONE = "done"
FAILED = "failed"
TIMED_OUT = "timed_out"
CANCELLED = "cancelled"

_TERMINAL_LABEL = {
    DONE: "✓ done",
    FAILED: "✗ failed",
    TIMED_OUT: "⏱ timed out",
    CANCELLED: "⊘ cancelled",
}

_NUM = re.compile(r"[-+]?\d*\.?\d+")


def terminal_label(state: str) -> str:
    return _TERMINAL_LABEL.get(state, state)


def parse_pct(out: str) -> float | None:
    """First number in ``out``, clamped to 0–100. None if there's no number."""
    m = _NUM.search(out or "")
    if m is None:
        return None
    try:
        return max(0.0, min(100.0, float(m.group())))
    except ValueError:
        return None


def eta_seconds(pct: float, elapsed_s: float) -> float | None:
    """Linear extrapolation: seconds remaining at the current average rate."""
    if pct <= 0.0:
        return None
    return elapsed_s * (100.0 - pct) / pct


@dataclass
class Monitor:
    id: str
    from_handle: str
    description: str
    done: str
    started_at: float             # monotonic seconds
    fail: str | None = None
    progress: str | None = None
    cwd: str | None = None
    interval_s: float = 2.0
    timeout_s: float = 3600.0
    state: str = WATCHING
    pct: float | None = None
    eta_s: float | None = None
    ended_at: float | None = None


@dataclass(frozen=True)
class MonitorView:
    """Immutable snapshot item the TUI strip renders."""
    id: str
    description: str
    state: str
    pct: float | None
    eta_s: float | None
    elapsed_s: float
