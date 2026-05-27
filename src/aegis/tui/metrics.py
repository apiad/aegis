from __future__ import annotations

from dataclasses import dataclass

from aegis.events import TokenUsage


def context_window_for(harness: str, model: str) -> int:
    """Tokens the model can ingest per turn. 0 = unknown (skip display).
    Hardcoded; edit when a new model lands or a 1m beta opens up."""
    m = (model or "").lower()
    if harness == "claude-code":
        if "1m" in m:
            return 1_000_000
        return 200_000
    if harness == "gemini":
        return 1_048_576
    if harness == "opencode":
        return 200_000
    return 0


def _fmt_tokens(n: int) -> str:
    if n < 1000:
        return str(n)
    if n < 1_000_000:
        s = f"{n / 1000:.1f}"
        if s.endswith(".0"):
            s = s[:-2]
        return s + "k"
    return f"{n / 1_000_000:.1f}M"


def _fmt_time(seconds: float) -> str:
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    return f"{s // 60}m{s % 60:02d}s"


@dataclass
class SessionMetrics:
    session_start: float | None = None
    tool_calls: int = 0
    tool_errors: int = 0
    turn_start: float | None = None
    last_turn_seconds: float = 0.0
    # committed — authoritative, summed from result.usage per finished turn
    c_in: int = 0
    c_out: int = 0
    c_cached: int = 0
    # provisional — current turn, monotonic MAX over streamed assistant
    # usages (a step's usage repeats across events; summing double-counts).
    p_in: int = 0
    p_out: int = 0
    p_cached: int = 0
    _provisional: bool = False
    # Most recent turn's authoritative true_input. Approximates the live
    # context size the model ingests; combined with context_window gives
    # the % gauge in render().
    last_true_input: int = 0
    context_window: int = 0

    def start_turn(self, now: float) -> None:
        self.turn_start = now

    def record_tool(self) -> None:
        self.tool_calls += 1

    def record_tool_error(self) -> None:
        self.tool_errors += 1

    def observe(self, u: TokenUsage) -> None:
        """A streamed (non-authoritative) usage snapshot — provisional."""
        self.p_in = max(self.p_in, u.true_input)
        self.p_out = max(self.p_out, u.output)
        self.p_cached = max(self.p_cached, u.cache_read)
        self._provisional = True

    def _end_time(self, now: float) -> None:
        if self.turn_start is not None:
            self.last_turn_seconds = now - self.turn_start
        self.turn_start = None

    def commit(self, usage: TokenUsage | None, now: float) -> None:
        """Turn end. `usage` (result.usage) is authoritative; provisional
        is discarded. `None` (error/no-result) commits no tokens."""
        if usage is not None:
            self.c_in += usage.true_input
            self.c_out += usage.output
            self.c_cached += usage.cache_read
            self.last_true_input = usage.true_input
        self.p_in = self.p_out = self.p_cached = 0
        self._provisional = False
        self._end_time(now)

    def cancel_turn(self, now: float) -> None:
        self.p_in = self.p_out = self.p_cached = 0
        self._provisional = False
        self._end_time(now)

    def turn_seconds(self, now: float) -> float:
        if self.turn_start is not None:
            return now - self.turn_start
        return self.last_turn_seconds

    def begin_session(self, now: float) -> None:
        if self.session_start is None:
            self.session_start = now

    def session_seconds(self, now: float) -> float:
        if self.session_start is None:
            return 0.0
        return now - self.session_start

    def render(self, now: float) -> str:
        in_t = self.c_in + self.p_in
        out = self.c_out + self.p_out
        cached = self.c_cached + self.p_cached
        pct = round(100 * cached / in_t) if in_t else 0
        mark = "~" if self._provisional else ""
        tool = f"⚒ {self.tool_calls}"
        if self.tool_errors:
            tool += f" ({self.tool_errors} err)"
        ctx = ""
        if self.context_window > 0:
            live = self.p_in if self._provisional else self.last_true_input
            ctx_pct = round(100 * live / self.context_window)
            ctx = f"ctx {_fmt_tokens(live)} ({ctx_pct}%) · "
        return (
            f"{mark}↑{_fmt_tokens(in_t)} ({pct}% cached) "
            f"↓{_fmt_tokens(out)} · "
            f"{ctx}"
            f"{tool} · "
            f"{_fmt_time(self.turn_seconds(now))} / "
            f"{_fmt_time(self.session_seconds(now))}"
        )
