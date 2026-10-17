from collections import defaultdict
from decimal import Decimal

from .models import Transaction


def balances(txs: list[Transaction], opening: dict[str, Decimal] | None = None) -> dict[str, Decimal]:
    out: dict[str, Decimal] = defaultdict(Decimal)
    for acct, amt in (opening or {}).items():
        out[acct] += amt
    for t in txs:
        out[t.account] += t.amount
    return dict(out)


def running(txs: list[Transaction], account: str, opening: Decimal = Decimal(0)):
    """Yield (transaction, balance after it) for one account, oldest first."""
    bal = opening
    for t in sorted((t for t in txs if t.account == account), key=lambda t: t.day):
        bal += t.amount
        yield t, bal
