from rich.console import Console

from aegis.recap import Recap
from aegis.render import render_recap
from aegis.tui.themes import INK, aegis_colors

C = aegis_colors(INK)


def _text(r) -> str:
    con = Console(record=True, width=100)
    con.print(r)
    return con.export_text()


def _header(out: str) -> str:
    return next(ln for ln in out.splitlines() if ln.strip(" ▏"))


def test_the_header_names_the_category():
    out = _text(render_recap(Recap(line="Asked which fields ship.", attention="needs_input", ok=True), C))
    header = _header(out)
    i, j, k = header.find("recap"), header.find("?"), header.find("needs you")
    assert -1 < i < j < k, header
    assert "Asked which fields ship." in out


def test_the_border_takes_the_category_colour():
    panel = render_recap(Recap(line="x", attention="error", ok=True), C)
    assert str(panel.border_style) == C.error


def test_a_recap_without_a_category_looks_as_today():
    panel = render_recap(Recap(line="x", ok=True), C)
    assert str(panel.border_style) == C.rule
    assert "·" not in _header(_text(panel))
