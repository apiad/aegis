"""Budget dataclass + config-time parser/validator."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from aegis.budget.windows import parse_window


class BudgetConfigError(ValueError):
    """Raised when a queue's `budgets:` config is malformed."""


@dataclass(frozen=True)
class Budget:
    constraint: str         # "usd" or "output_tokens"
    limit:      Decimal
    window_str: str         # verbatim from config
    window:     timedelta   # parsed


def parse_budgets(raw) -> list[Budget]:
    if not raw:
        return []
    if not isinstance(raw, list):
        raise BudgetConfigError(
            f"budgets must be a list, got {type(raw).__name__}")

    out: list[Budget] = []
    seen: set[tuple[str, str]] = set()
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise BudgetConfigError(
                f"budgets[{i}] must be a dict, got {type(entry).__name__}")
        has_usd = "usd" in entry
        has_tok = "output_tokens" in entry
        if has_usd == has_tok:
            raise BudgetConfigError(
                f"budgets[{i}] must have exactly one of 'usd' or "
                f"'output_tokens' (got both or neither)")
        if "window" not in entry:
            raise BudgetConfigError(f"budgets[{i}] missing 'window'")
        try:
            window = parse_window(entry["window"])
        except ValueError as e:
            raise BudgetConfigError(f"budgets[{i}] window: {e}")
        if has_usd:
            constraint = "usd"
            raw_limit = entry["usd"]
        else:
            constraint = "output_tokens"
            raw_limit = entry["output_tokens"]
        try:
            limit = Decimal(str(raw_limit))
        except (InvalidOperation, ValueError):
            raise BudgetConfigError(
                f"budgets[{i}] {constraint} must be numeric, "
                f"got {raw_limit!r}")
        if limit <= 0:
            raise BudgetConfigError(
                f"budgets[{i}] {constraint} must be positive, got {limit}")
        key = (constraint, entry["window"])
        if key in seen:
            raise BudgetConfigError(
                f"budgets[{i}] duplicate ({constraint!r}, "
                f"{entry['window']!r})")
        seen.add(key)
        out.append(Budget(constraint=constraint, limit=limit,
                          window_str=entry["window"], window=window))
    return out
