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


def test_only_keys_js_listens_for_keys_on_the_page():
    """A page-wide key binding lives in the registry in js/keys.js, so the ?
    list and the command palette show it (#248). A widget may answer keys in
    its own field, as the command menu and the palette do, but a listener on
    the document or the window anywhere else is a key nobody can find."""
    found = [
        f"{p.name}:{n}"
        for p in JS.glob("*.js")
        if p.name != "keys.js"
        for n, line in enumerate(p.read_text().splitlines(), 1)
        if re.search(
            r"""(document|window|self)\.addEventListener\(\s*["']key(down|up|press)""",
            line,
        )
    ]
    assert found == []


def test_the_client_draws_no_native_select():
    """A native select pops up in the operating system's chrome, outside the
    theme, and answers typing by jumping to the next option starting with that
    letter; the client's chips are <pick-chip> (js/pick.js)."""
    files = [*JS.glob("*.js"), JS.parent / "index.html"]
    found = [
        f"{p.name}:{n}"
        for p in files
        for n, line in enumerate(p.read_text().splitlines(), 1)
        if re.search(r'<select\b|<datalist\b|\("select"|\("datalist"', line)
    ]
    assert found == []
