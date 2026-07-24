from aegis.tui.fit import Segment, fit, plain_width, strip_markup


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
