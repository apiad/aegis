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

# `pgrep`/`pkill` matching the FULL command line (-f / -af / --full).
# Anchored at a command position — start of string, after a shell separator,
# optionally negated — so the name quoted inside someone else's argument
# (`grep -q 'pgrep -f' log`) is not mistaken for the real thing. The flag is
# searched only up to the next separator, so it stays inside one command.
_SELF_MATCH = re.compile(
    r"(?:^|[;&|(]|\b(?:then|do|while|until|if|elif)\b)\s*(?:!\s*)?"
    r"(?:pgrep|pkill)\b[^;&|]*?(?:\s--full\b|\s-[a-z]*f\b)",
    re.IGNORECASE)

_SELF_MATCH_HELP = (
    "`pgrep -f` / `pkill -f` cannot be used in a monitor condition: the "
    "condition runs in a shell whose own command line contains the pattern, "
    "so the pattern matches itself. `pgrep -f 'pytest -q'` is true even when "
    "nothing is running, which makes the `! pgrep -f ...` spelling of "
    "\"it finished\" false forever — the monitor never trips and just times "
    "out, looking like a hang that never happened.\n"
    "Use a completion marker instead: launch as "
    "`nohup bash -c 'mycmd > run.log 2>&1; echo \"DONE rc=$?\" >> run.log' &` "
    "and pass `done: grep -q DONE run.log` (plus "
    "`fail: grep -q 'DONE rc=[^0]' run.log`). Or watch the PID you already "
    "have: `done: ! kill -0 <pid> 2>/dev/null`. `pgrep -x <name>` is fine — "
    "it matches the process name, not the command line."
)


def condition_error(cond: str | None) -> str | None:
    """Why ``cond`` is unusable as a monitor condition, or None if it's fine.

    Deterministic rather than advisory: the self-match failure is silent and
    reads as "the process is still running", so it costs a whole timeout to
    discover and is easy to misdiagnose.
    """
    if not cond:
        return None
    if _SELF_MATCH.search(cond):
        return _SELF_MATCH_HELP
    return None


def terminal_label(state: str) -> str:
    return _TERMINAL_LABEL.get(state, state)


def format_elapsed(seconds: float) -> str:
    """``754`` → ``12m``. Coarse on purpose: this is a staleness cue."""
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m"
    return f"{s // 3600}h{(s % 3600) // 60}m"


def roster_block(rows: list[dict]) -> str:
    """The agent's OTHER live monitors, rendered for an inbox wake.

    Empty string when there are none, so the common single-monitor wake stays
    exactly as terse as it was. Ids are printed in full: the whole point is
    that the next thing the agent may want to do is cancel one.

    Elapsed is shown beside pct because that pair is what exposes an orphan —
    a monitor frozen at 60% for twenty minutes is watching for a marker its
    process will never write.
    """
    if not rows:
        return ""
    lines = [f"\n\nStill watching ({len(rows)}):"]
    for r in rows:
        pct = f" · {r['pct']:.0f}%" if r.get("pct") is not None else ""
        lines.append(f"  · {r['id']} — {r['description']}"
                     f"{pct} · {format_elapsed(r['elapsed_s'])}")
    lines.append("Cancel any whose process you already killed or superseded "
                 "— aegis_monitor_cancel(monitor_id) — or it will sit there "
                 "until it times out.")
    return "\n".join(lines)


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
    interrupt: bool = False
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
