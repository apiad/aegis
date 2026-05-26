"""Pure-function evaluator for per-queue budgets over a JSONL tail."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Iterable

from aegis.budget.budgets import Budget

_TERMINAL_EVENTS = ("completed", "failed")


@dataclass(frozen=True)
class BudgetCheck:
    constraint:    str
    limit:         Decimal
    spent:         Decimal
    window_str:    str
    window_start:  datetime
    allowed:       bool
    headroom:      Decimal
    unblock_at:    datetime | None


@dataclass(frozen=True)
class Decision:
    allowed:    bool
    checks:     list[BudgetCheck]
    blocked_by: list[BudgetCheck]
    unblock_at: datetime | None


_ZERO = Decimal("0")


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None


def _record_value(rec: dict, constraint: str) -> Decimal:
    cost = rec.get("cost") or {}
    if "error" in cost:
        return _ZERO
    if constraint == "usd":
        try:
            return Decimal(cost.get("usd", "0"))
        except Exception:
            return _ZERO
    if constraint == "output_tokens":
        try:
            return Decimal(int(cost.get("output_tokens", 0) or 0))
        except (ValueError, TypeError):
            return _ZERO
    return _ZERO


def _evaluate_one(records: list[dict], budget: Budget,
                  now: datetime) -> BudgetCheck:
    window_start = now - budget.window
    inside: list[tuple[datetime, Decimal]] = []
    for rec in records:
        if rec.get("event") not in _TERMINAL_EVENTS:
            continue
        ts = _parse_iso(rec.get("completed_at"))
        if ts is None or ts <= window_start or ts > now:
            continue
        inside.append((ts, _record_value(rec, budget.constraint)))
    inside.sort(key=lambda p: p[0])
    spent = sum((v for _, v in inside), start=_ZERO)
    allowed = spent < budget.limit
    headroom = budget.limit - spent

    unblock_at: datetime | None = None
    if not allowed:
        running = spent
        for ts, value in inside:
            running -= value
            if running < budget.limit:
                unblock_at = ts + budget.window
                break

    return BudgetCheck(
        constraint=budget.constraint, limit=budget.limit, spent=spent,
        window_str=budget.window_str, window_start=window_start,
        allowed=allowed, headroom=headroom, unblock_at=unblock_at,
    )


def evaluate_budgets(jsonl_tail: Iterable[dict],
                     budgets: list[Budget],
                     now: datetime) -> Decision:
    records = list(jsonl_tail)
    checks = [_evaluate_one(records, b, now) for b in budgets]
    blocked_by = [c for c in checks if not c.allowed]
    decision_unblock: datetime | None = None
    if blocked_by:
        eligible = [c.unblock_at for c in blocked_by if c.unblock_at]
        decision_unblock = max(eligible) if eligible else None
    return Decision(
        allowed=not blocked_by, checks=checks, blocked_by=blocked_by,
        unblock_at=decision_unblock,
    )
