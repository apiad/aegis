from aegis.tui.fit import Segment, fit, fit_rows, plain_width, strip_markup


def test_plain_width_ignores_markup():
    assert plain_width("[dim]abc[/]") == 3
    assert plain_width("plain") == 5


def test_strip_markup_removes_tags():
    assert strip_markup("[dim]abc[/] [red]d[/]") == "abc d"


def _segs():
    return [
        Segment("identity", ("aegis 0.21.0 opus", "opus"), 20),
        Segment("state", ("working",), 60),
        Segment("system", ("CPU 1% RAM 2%", "1/2%"), 10),
    ]


def test_unmeasured_width_renders_widest():
    assert fit(_segs(), 0) == "aegis 0.21.0 opus    working    CPU 1% RAM 2%"


def test_degrades_lowest_priority_first():
    # Widest is 45 cols. At 40 only the lowest-priority segment narrows.
    assert fit(_segs(), 40) == "aegis 0.21.0 opus    working    1/2%"


def test_drops_when_narrowest_still_does_not_fit():
    # system is already narrowest, so it is dropped before identity narrows.
    assert fit(_segs(), 30) == "aegis 0.21.0 opus    working"


def test_degrades_next_priority_after_a_drop():
    assert fit(_segs(), 20) == "opus    working"


def test_never_drops_the_highest_priority_segment():
    assert fit(_segs(), 5) == "worki"


def test_empty_tier_strings_are_skipped():
    segs = [Segment("a", ("",), 10), Segment("b", ("bee",), 20)]
    assert fit(segs, 0) == "bee"


def test_render_order_follows_list_order_not_priority():
    segs = [Segment("low", ("zzz",), 1), Segment("high", ("aaa",), 99)]
    assert fit(segs, 0) == "zzz    aaa"


# --- fit_rows: the vertical counterpart --------------------------------
# Rows do not share horizontal space, so segments do not compete and
# priority is never consulted. Each is considered on its own.


def _rowsegs():
    return [
        Segment("identity", ("aegis 0.32.0 opus high", "opus high", "opus"), 20),
        Segment("metrics", ("142k/200k · $1.84 · 12 turns", "$1.84"), 30),
        Segment("empty", (), 50),
    ]


def test_rows_unmeasured_width_renders_widest():
    assert fit_rows(_rowsegs(), 0) == [
        "aegis 0.32.0 opus high", "142k/200k · $1.84 · 12 turns"]


def test_rows_picks_the_widest_tier_that_fits():
    # 26 columns: identity's widest is 22 and fits; metrics' widest is 28
    # and does not, so it falls to its second tier.
    assert fit_rows(_rowsegs(), 26) == ["aegis 0.32.0 opus high", "$1.84"]


def test_rows_keeps_a_tier_that_is_exactly_the_width():
    seg = [Segment("exact", ("abcde",), 10)]
    assert fit_rows(seg, 5) == ["abcde"]


def test_rows_drops_a_segment_whose_narrowest_still_overflows():
    """Half a number reads as a number — the segment goes instead."""
    seg = [Segment("wide", ("aaaaaaaa", "aaaaaa"), 10),
           Segment("fits", ("ok",), 10)]
    assert fit_rows(seg, 4) == ["ok"]


def test_rows_skips_a_segment_with_no_tiers():
    assert fit_rows([Segment("none", (), 10)], 80) == []


def test_rows_ignores_markup_when_measuring():
    seg = [Segment("m", ("[dim]12345[/]",), 10)]
    assert fit_rows(seg, 5) == ["[dim]12345[/]"]


def test_rows_never_consults_priority():
    """The ordering guarantee: unlike fit, a low-priority segment is not
    sacrificed for a high-priority one, because they cost different rows."""
    segs = [Segment("low", ("aaaa",), 1), Segment("high", ("bbbb",), 99)]
    assert fit_rows(segs, 4) == ["aaaa", "bbbb"]
