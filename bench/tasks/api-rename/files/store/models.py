from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Book:
    id: int
    title: str
    author: str
    year: int | None
    copies: int

    @classmethod
    def from_row(cls, row: dict) -> "Book":
        return cls(row["id"], row["title"], row["author"], row["year"], row["copies"])


@dataclass(frozen=True)
class Member:
    id: int
    name: str
    email: str | None
    joined: date

    @classmethod
    def from_row(cls, row: dict) -> "Member":
        return cls(row["id"], row["name"], row["email"], date.fromisoformat(row["joined"]))


@dataclass(frozen=True)
class Loan:
    id: int
    book_id: int
    member_id: int
    out: date
    due: date
    returned: date | None

    @classmethod
    def from_row(cls, row: dict) -> "Loan":
        ret = row["returned"]
        return cls(
            row["id"], row["book_id"], row["member_id"], date.fromisoformat(row["out"]),
            date.fromisoformat(row["due"]), date.fromisoformat(ret) if ret else None,
        )
