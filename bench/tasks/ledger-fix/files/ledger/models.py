from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass(frozen=True)
class Transaction:
    day: date
    account: str
    payee: str
    amount: Decimal  # negative for money leaving the account
    category: str = "uncategorized"
