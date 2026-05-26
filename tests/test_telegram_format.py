from aegis.telegram.format import Spillover, status_line


def test_status_line_shape():
    s = status_line("lucid-knuth", "working", "·opus·high", "↑0 (–) ↓0")
    assert s == "⏳ lucid-knuth · working · ·opus·high ↑0 (–) ↓0"


def test_spillover_carries_raw_md_and_rendered_html():
    s = Spillover(raw_md="# title\n\nbody", rendered_html="<b>title</b>\n\nbody")
    assert s.raw_md == "# title\n\nbody"
    assert s.rendered_html == "<b>title</b>\n\nbody"
