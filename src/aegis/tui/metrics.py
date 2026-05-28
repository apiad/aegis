from __future__ import annotations

from dataclasses import dataclass

from aegis.events import TokenUsage


def context_window_for(harness: str, model: str) -> int:
    """Tokens the model can ingest per turn. 0 = unknown (skip display).

    Backed by the YAML registry at ``src/aegis/data/models.yaml`` (see
    ``aegis.models``); the values refresh from GitHub every 24h so new
    models land without a release."""
    from aegis.models import get_context_window
    return get_context_window(harness, model)


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


def _fmt_cost(usd) -> str:
    """Adaptive formatting for the status-line cost segment.
    Sub-cent values render as ``X.Y¢`` so they're visible at a glance;
    larger values use ``$X.XX``. ``usd`` is a Decimal."""
    from decimal import Decimal
    cents = usd * Decimal(100)
    if cents < Decimal("1"):
        # Sub-cent: show one decimal place in cents (0.1¢ resolution).
        return f"{cents.quantize(Decimal('0.1'))}¢"
    if usd < Decimal("1"):
        # Whole cents at 1¢–99¢: show as cents with no decimals.
        return f"{int(cents)}¢"
    return f"${usd.quantize(Decimal('0.01'))}"


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
    # Cache-creation tokens — billed at the cache_write rate, tracked
    # separately from c_cached (cache-read hits) so the cost computation
    # can multiply each class against its own rate.
    c_cache_write: int = 0
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
    # provider + model strings drive the cost lookup. Empty strings mean
    # cost rendering is skipped (no $ segment in the status line).
    provider: str = ""
    model: str = ""

    # ----- properties consumed by aegis.budget.cost.compute() -----
    # The Cost computation reads input_tokens / output_tokens /
    # cache_hit_tokens / cache_write_tokens / thinking_tokens via getattr.
    # SessionMetrics maps its internal counters to those attribute names
    # so cost.compute(self, ...) works without an adapter.

    @property
    def input_tokens(self) -> int:
        """Uncached input tokens (true_input minus the two cached classes)."""
        return max(0, self.c_in - self.c_cached - self.c_cache_write)

    @property
    def output_tokens(self) -> int:
        return self.c_out

    @property
    def cache_hit_tokens(self) -> int:
        return self.c_cached

    @property
    def cache_write_tokens(self) -> int:
        return self.c_cache_write

    @property
    def thinking_tokens(self) -> int:
        # We don't track thinking tokens separately yet — Anthropic
        # surfaces them under cache_creation/output in the headline
        # usage, so leaving this at 0 avoids double-billing.
        return 0

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
            self.c_cache_write += usage.cache_creation
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
        cost = self._render_cost()
        return (
            f"{mark}↑{_fmt_tokens(in_t)} ({pct}% cached) "
            f"↓{_fmt_tokens(out)} · "
            f"{ctx}"
            f"{cost}"
            f"{tool} · "
            f"{_fmt_time(self.turn_seconds(now))} / "
            f"{_fmt_time(self.session_seconds(now))}"
        )

    def _render_cost(self) -> str:
        """Status-line segment showing accumulated session cost in USD.
        Empty string when (provider, model) aren't set or the lookup
        fails — silent so an unknown model never breaks the render."""
        if not (self.provider and self.model):
            return ""
        try:
            from aegis.budget.cost import compute
            cost = compute(self, self.provider, self.model)
        except Exception:  # noqa: BLE001 — UnknownPriceError + anything else
            return ""
        return f"{_fmt_cost(cost.usd)} · "
