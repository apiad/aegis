"""Aggregation engine for ``aegis usage``.

Reads per-tab session logs (``<state>/sessions/<handle>.jsonl``) and rolls
them up into a UsageReport. Billed cost is authoritative from
``Result.cost_usd`` (segment-aware); a token-priced generation/replay split
is the analytical lens.
"""
from __future__ import annotations

import json
import statistics
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from aegis.models import ProviderPrices, UnknownPriceError, get_prices
from aegis.usage.cost import segment_cost, token_cost

# Substring → canonical family key fallback, applied when an exact/alias
# lookup misses (e.g. a model newer than the shipped registry).
_FAMILIES = ("opus", "sonnet", "haiku", "gemini")
_TOKEN_KEYS = ("input", "output", "cache_creation", "cache_read")
_DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def resolve_prices(provider: str, model: str | None) -> ProviderPrices | None:
    if not model:
        return None
    try:
        return get_prices(provider, model)
    except UnknownPriceError:
        pass
    low = model.lower()
    for fam in _FAMILIES:
        if fam in low:
            try:
                return get_prices(provider, fam)
            except UnknownPriceError:
                return None
    return None


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


def _read_session(path: Path, *, default_model, default_provider):
    """Parse one .jsonl → (SessionUsage, list[TurnRecord]) or (None, []) if
    the session has no turns and no tools (empty shell)."""
    model = None
    tokens = Counter()
    tools = Counter()
    cost_seq: list[Decimal] = []
    turns = 0
    errors = 0
    dur = 0
    first_ts = last_ts = None
    turn_recs: list[TurnRecord] = []
    pending_tools: list[str] = []
    prev_cost: Decimal | None = None

    for line in path.open(encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue  # skip corrupt/partial line
        ts = rec.get("aegis_ts")
        ev = rec.get("event") or {}
        t = ev.get("t")
        if ts:
            first_ts = first_ts or ts
            last_ts = ts
        if t == "SystemInit":
            model = model or ev.get("model")
        elif t == "ToolUse":
            name = ev.get("name", "?")
            tools[name] += 1
            pending_tools.append(name)
        elif t == "Result":
            turns += 1
            if ev.get("is_error"):
                errors += 1
            dur += ev.get("duration_ms") or 0
            u = ev.get("usage") or {}
            for k in _TOKEN_KEYS:
                tokens[k] += u.get(k, 0)
            prices = resolve_prices(default_provider, model or default_model)
            gen, rep = (token_cost(u, prices) if prices
                        else (Decimal(0), Decimal(0)))
            billed_delta = Decimal(0)
            c = ev.get("cost_usd")
            if c is not None:
                c = Decimal(str(c))
                cost_seq.append(c)
                # per-turn increment (segment-aware): reset → whole value
                billed_delta = c if (prev_cost is None or c < prev_cost) \
                    else c - prev_cost
                prev_cost = c
            turn_recs.append(TurnRecord(
                ts=ts, billed_delta=billed_delta, gen_usd=gen,
                replay_usd=rep, tools=tuple(pending_tools),
                duration_ms=ev.get("duration_ms") or 0,
                is_error=bool(ev.get("is_error"))))
            pending_tools = []

    if turns == 0 and sum(tools.values()) == 0:
        return None, []

    gen_total = sum((tr.gen_usd for tr in turn_recs), Decimal(0))
    rep_total = sum((tr.replay_usd for tr in turn_recs), Decimal(0))
    if cost_seq:
        billed = segment_cost(cost_seq)
        est = False
    else:
        billed = gen_total + rep_total   # token estimate fallback
        est = True

    su = SessionUsage(
        handle=path.stem, model=model, provider=default_provider,
        turns=turns, tools=tools, tokens=dict(tokens), billed_usd=billed,
        gen_usd=gen_total, replay_usd=rep_total, est=est,
        duration_ms=dur, errors=errors, first_ts=first_ts, last_ts=last_ts)
    return su, turn_recs


def build_report(state_dir: Path, *, default_model: str,
                 default_provider: str, since: str | None = None,
                 handle: str | None = None) -> "UsageReport":
    sess_dir = state_dir / "sessions"
    sessions: list[SessionUsage] = []
    turns: list[TurnRecord] = []
    if sess_dir.is_dir():
        for p in sorted(sess_dir.glob("*.jsonl")):
            if handle and p.stem != handle:
                continue
            su, trs = _read_session(
                p, default_model=default_model,
                default_provider=default_provider)
            if su is None:
                continue
            if since and (su.last_ts or "") < since:
                continue
            sessions.append(su)
            turns.extend(trs)
    first = min((s.first_ts for s in sessions if s.first_ts), default=None)
    last = max((s.last_ts for s in sessions if s.last_ts), default=None)
    return UsageReport(sessions=sessions, turns=turns,
                       first_ts=first, last_ts=last)


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
            a = agg.setdefault(k, {"billed": Decimal(0), "turns": 0,
                                   "sessions": 0})
            a["billed"] += s.billed_usd
            a["turns"] += s.turns
            a["sessions"] += 1
        return sorted(agg.items(), key=lambda kv: -kv[1]["billed"])

    def distribution(self) -> dict:
        cs = sorted(float(s.billed_usd) for s in self.sessions)
        if not cs:
            return {"n": 0, "min": 0, "p50": 0, "p90": 0, "p99": 0,
                    "max": 0, "mean": 0}

        def pct(p):
            return cs[min(len(cs) - 1, int(p / 100 * len(cs)))]
        return {"n": len(cs), "min": cs[0], "p50": pct(50), "p90": pct(90),
                "p99": pct(99), "max": cs[-1], "mean": statistics.mean(cs)}

    def tool_correlation(self, min_turns: int = 1) -> list[tuple[str, float, int]]:
        buckets: dict[str, list[float]] = {}
        for tr in self.turns:
            for name in set(tr.tools):
                buckets.setdefault(name, []).append(
                    float(tr.gen_usd + tr.replay_usd))
        rows = [(n, statistics.mean(v), len(v))
                for n, v in buckets.items() if len(v) >= min_turns]
        return sorted(rows, key=lambda r: -r[1])

    # ---- temporal (local tz) ----
    def _local(self, ts: str, tz: ZoneInfo | None):
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.astimezone(tz)  # tz=None → system local

    def by_bucket(self, kind: str, tz: ZoneInfo | None = None
                  ) -> list[tuple[str, int]]:
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
            keys = [f"{h:02d}:00" for h in range(24)
                    if f"{h:02d}:00" in counts]
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
