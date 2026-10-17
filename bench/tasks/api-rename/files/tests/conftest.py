import pytest

from store.db import connect


@pytest.fixture
def conn():
    c = connect()
    c.executemany(
        "INSERT INTO books (id, title, author, year, copies) VALUES (?, ?, ?, ?, ?)",
        [
            (1, "The Salt Road", "Ana Ruiz", 2004, 2),
            (2, "Night Ferry", "Tom Berg", 1998, 1),
            (3, "A Sea of Lamps", "Ana Ruiz", 2015, 1),
            (4, "Paper Harbour", "Lea Moss", 2021, 3),
        ],
    )
    c.executemany(
        "INSERT INTO members (id, name, email, joined) VALUES (?, ?, ?, ?)",
        [(1, "Kim Novak", "kim@example.com", "2024-01-10"), (2, "Ravi Shah", "ravi@example.com", "2025-03-02")],
    )
    c.executemany(
        "INSERT INTO loans (id, book_id, member_id, out, due, returned) VALUES (?, ?, ?, ?, ?, ?)",
        [
            (1, 1, 1, "2026-01-02", "2026-01-16", "2026-01-15"),
            (2, 3, 2, "2026-02-01", "2026-02-15", None),
            (3, 1, 2, "2026-02-03", "2026-02-17", None),
            (4, 4, 1, "2026-03-01", "2026-03-15", None),
            (5, 1, 1, "2026-03-20", "2026-04-03", "2026-04-01"),
        ],
    )
    c.commit()
    return c
