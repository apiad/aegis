"""Rules over the client's source that a test can hold without a browser."""

import re
from pathlib import Path

JS = Path(__file__).parents[1] / "src" / "aegis" / "client" / "js"


def test_the_client_never_opens_a_native_dialog():
    """A native confirm, alert or prompt looks foreign in the installed app and
    blocks the page; the client asks through js/dialog.js."""
    found = [
        f"{p.name}:{n}"
        for p in JS.glob("*.js")
        for n, line in enumerate(p.read_text().splitlines(), 1)
        if re.search(r"(?:^|[^\w.]|window\.)(confirm|alert|prompt)\(", line)
    ]
    assert found == []
