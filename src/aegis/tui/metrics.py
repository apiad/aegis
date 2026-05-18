from __future__ import annotations

from dataclasses import dataclass

from aegis.events import Result


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
    in_tokens: int = 0
    out_tokens: int = 0
    tool_calls: int = 0
    tool_errors: int = 0
    turn_start: float | None = None
    last_turn_seconds: float = 0.0

    def start_turn(self, now: float) -> None:
        self.turn_start = now

    def record_tool(self) -> None:
        self.tool_calls += 1

    def record_tool_error(self) -> None:
        self.tool_errors += 1

    def end_turn(self, result: Result, now: float) -> None:
        self.in_tokens += result.input_tokens or 0
        self.out_tokens += result.output_tokens or 0
        if self.turn_start is not None:
            self.last_turn_seconds = now - self.turn_start
        self.turn_start = None

    def cancel_turn(self, now: float) -> None:
        if self.turn_start is not None:
            self.last_turn_seconds = now - self.turn_start
        self.turn_start = None

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
        tool = f"⚒ {self.tool_calls}"
        if self.tool_errors:
            tool += f" ({self.tool_errors} err)"
        return (
            f"↑{_fmt_tokens(self.in_tokens)} "
            f"↓{_fmt_tokens(self.out_tokens)} · "
            f"{tool} · "
            f"{_fmt_time(self.turn_seconds(now))} / "
            f"{_fmt_time(self.session_seconds(now))}"
        )
