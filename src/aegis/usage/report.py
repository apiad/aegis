"""The ``aegis usage`` dashboard: every session in aegis's store, rolled up.

Billed cost comes from Claude's own ``total_cost_usd`` on each turn's result,
a running total per process that restarts on a resume (``segment_cost``). A
token-priced split into new generation and context replay (cache reads) is the
analytical lens, priced through ``prices.py``. A session with no reported cost
falls back to its token price and is marked estimated.
"""

from __future__ import annotations

import statistics
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from .cost import segment_cost, token_cost
from .prices import prices_for
from .store import StoredSession, lines, sessions, split_cache

_TOKEN_KEYS = ("input", "output", "cache_creation", "cache_read")
_DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


@dataclass
class SessionUsage:
    handle: str
    model: str | None
    provider: str
    turns: int
    tools: Counter
    tokens: dict
    billed_usd: Decimal
    gen_usd: Decimal
    replay_usd: Decimal
    est: bool
    duration_ms: int
    errors: int
    first_ts: str | None
    last_ts: str | None


@dataclass
class TurnRecord:
    ts: str | None
    billed_delta: Decimal
    gen_usd: Decimal
    replay_usd: Decimal
    tools: tuple[str, ...]
    duration_ms: int
    is_error: bool


def _read_session(stored: StoredSession):
    """One stored session -> (SessionUsage, list[TurnRecord]), or (None, [])
    for a session with no turns and no tools."""
    model = None
    # The agent's model as aegis spawned it, for a session whose Claude lines
    # never named one.
    spawned: str | None = None
    tokens: Counter = Counter()
    tools: Counter = Counter()
    cost_seq: list[Decimal] = []
    turns = 0
    errors = 0
    dur = 0
    first_ts = last_ts = None
    turn_recs: list[TurnRecord] = []
    pending_tools: list[str] = []
    prev_cost: Decimal | None = None

    for line in lines(stored.path):
        ts = line.ts
        if ts:
            first_ts = first_ts or ts
            last_ts = ts
        if line.src == "aegis" and line.obj.get("kind") == "spawn":
            spawned = line.obj.get("model") or spawned
        if line.src != "claude":
            continue
        obj = line.obj
        kind = obj.get("type")
        if kind == "system" and obj.get("subtype") == "init":
            model = model or obj.get("model")
        elif kind == "assistant":
            message = obj.get("message") or {}
            model = model or message.get("model")
            for block in message.get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    name = str(block.get("name") or "?")
                    tools[name] += 1
                    pending_tools.append(name)
        elif kind == "result":
            turns += 1
            if obj.get("is_error"):
                errors += 1
            dur += obj.get("duration_ms") or 0
            u = obj.get("usage") or {}
            cc5, cc1, _ = split_cache(u)
            usage = {
                "input": int(u.get("input_tokens") or 0),
                "output": int(u.get("output_tokens") or 0),
                "cache_creation": cc5 + cc1,
                "cache_read": int(u.get("cache_read_input_tokens") or 0),
            }
            for k in _TOKEN_KEYS:
                tokens[k] += usage[k]
            prices = (
                prices_for(model or spawned)
                if stored.harness == "claude-code"
                else None
            )
            gen, rep = (
                token_cost({**usage, "cc5": cc5, "cc1": cc1}, prices)
                if prices
                else (Decimal(0), Decimal(0))
            )
            billed_delta = Decimal(0)
            c = obj.get("total_cost_usd")
            if c is not None:
                c = Decimal(str(c))
                cost_seq.append(c)
                # per-turn increment (segment-aware): reset → whole value
                billed_delta = (
                    c if (prev_cost is None or c < prev_cost) else c - prev_cost
                )
                prev_cost = c
            turn_recs.append(
                TurnRecord(
                    ts=ts,
                    billed_delta=billed_delta,
                    gen_usd=gen,
                    replay_usd=rep,
                    tools=tuple(pending_tools),
                    duration_ms=obj.get("duration_ms") or 0,
                    is_error=bool(obj.get("is_error")),
                )
            )
            pending_tools = []

    if turns == 0 and sum(tools.values()) == 0:
        return None, []

    gen_total = sum((tr.gen_usd for tr in turn_recs), Decimal(0))
    rep_total = sum((tr.replay_usd for tr in turn_recs), Decimal(0))
    if cost_seq:
        billed = segment_cost(cost_seq)
        est = False
    else:
        billed = gen_total + rep_total  # token estimate fallback
        est = True

    su = SessionUsage(
        handle=stored.handle,
        model=model or spawned,
        provider=stored.harness,
        turns=turns,
        tools=tools,
        tokens=dict(tokens),
        billed_usd=billed,
        gen_usd=gen_total,
        replay_usd=rep_total,
        est=est,
        duration_ms=dur,
        errors=errors,
        first_ts=first_ts,
        last_ts=last_ts,
    )
    return su, turn_recs


def build_report(
    state_dir: Path,
    *,
    since: str | None = None,
    handle: str | None = None,
) -> "UsageReport":
    found: list[SessionUsage] = []
    turns: list[TurnRecord] = []
    for stored in sessions(state_dir):
        if handle and stored.handle != handle:
            continue
        su, trs = _read_session(stored)
        if su is None:
            continue
        if since and (su.last_ts or "") < since:
            continue
        found.append(su)
        turns.extend(trs)
    first = min((s.first_ts for s in found if s.first_ts), default=None)
    last = max((s.last_ts for s in found if s.last_ts), default=None)
    return UsageReport(sessions=found, turns=turns, first_ts=first, last_ts=last)


@dataclass
class UsageReport:
    sessions: list[SessionUsage]
    turns: list[TurnRecord]
    first_ts: str | None
    last_ts: str | None

    # ---- totals ----
    def total_billed(self) -> Decimal:
        return sum((s.billed_usd for s in self.sessions), Decimal(0))

    def total_gen(self) -> Decimal:
        return sum((s.gen_usd for s in self.sessions), Decimal(0))

    def total_replay(self) -> Decimal:
        return sum((s.replay_usd for s in self.sessions), Decimal(0))

    def total_turns(self) -> int:
        return sum(s.turns for s in self.sessions)

    def total_tools(self) -> Counter:
        c = Counter()
        for s in self.sessions:
            c.update(s.tools)
        return c

    def total_errors(self) -> int:
        return sum(s.errors for s in self.sessions)

    def total_tokens(self) -> dict:
        """Token counts summed across sessions, keyed by ``_TOKEN_KEYS``
        (input / output / cache_creation / cache_read)."""
        c = Counter()
        for s in self.sessions:
            c.update(s.tokens)
        return dict(c)

    # ---- breakdowns ----
    def by_model(self) -> list[tuple[str, dict]]:
        agg: dict[str, dict] = {}
        for s in self.sessions:
            k = s.model or "unknown"
            a = agg.setdefault(k, {"billed": Decimal(0), "turns": 0, "sessions": 0})
            a["billed"] += s.billed_usd
            a["turns"] += s.turns
            a["sessions"] += 1
        return sorted(agg.items(), key=lambda kv: -kv[1]["billed"])

    def distribution(self) -> dict:
        cs = sorted(float(s.billed_usd) for s in self.sessions)
        if not cs:
            return {"n": 0, "min": 0, "p50": 0, "p90": 0, "p99": 0, "max": 0, "mean": 0}

        def pct(p):
            return cs[min(len(cs) - 1, int(p / 100 * len(cs)))]

        return {
            "n": len(cs),
            "min": cs[0],
            "p50": pct(50),
            "p90": pct(90),
            "p99": pct(99),
            "max": cs[-1],
            "mean": statistics.mean(cs),
        }

    def tool_correlation(self, min_turns: int = 1) -> list[tuple[str, float, int]]:
        buckets: dict[str, list[float]] = {}
        for tr in self.turns:
            for name in set(tr.tools):
                buckets.setdefault(name, []).append(float(tr.gen_usd + tr.replay_usd))
        rows = [
            (n, statistics.mean(v), len(v))
            for n, v in buckets.items()
            if len(v) >= min_turns
        ]
        return sorted(rows, key=lambda r: -r[1])

    # ---- temporal (local tz) ----
    def _local(self, ts: str, tz: ZoneInfo | None):
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.astimezone(tz)  # tz=None → system local

    def by_bucket(self, kind: str, tz: ZoneInfo | None = None) -> list[tuple[str, int]]:
        counts: Counter = Counter()
        for tr in self.turns:
            if not tr.ts:
                continue
            dt = self._local(tr.ts, tz)
            if kind == "month":
                key = dt.strftime("%Y-%m")
            elif kind == "dow":
                key = _DOW[dt.weekday()]
            elif kind == "hour":
                key = f"{dt.hour:02d}:00"
            else:
                raise ValueError(kind)
            counts[key] += 1
        if kind == "dow":
            keys = [d for d in _DOW if d in counts]
        elif kind == "hour":
            keys = [f"{h:02d}:00" for h in range(24) if f"{h:02d}:00" in counts]
        else:
            keys = sorted(counts)
        return [(k, counts[k]) for k in keys]

    def by_dow(self, tz=None):
        return self.by_bucket("dow", tz)

    def by_month(self, tz=None):
        return self.by_bucket("month", tz)

    def by_hour(self, tz=None):
        return self.by_bucket("hour", tz)

    def by_day(self, tz: ZoneInfo | None = None) -> list[tuple[str, float]]:
        daily: dict[str, float] = {}
        for tr in self.turns:
            if not tr.ts:
                continue
            k = self._local(tr.ts, tz).strftime("%Y-%m-%d")
            daily[k] = daily.get(k, 0.0) + float(tr.billed_delta)
        return sorted(daily.items())
