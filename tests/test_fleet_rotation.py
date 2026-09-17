from dataclasses import replace

from aegis.fleet.models import CardView, FleetSnapshot, MonitorRow
from aegis.fleet.rotation import Rotator


def snap(*cards):
    return FleetSnapshot(cards=tuple(cards))


A = CardView(handle="a", state="ready", did="x")
B = CardView(handle="b", state="ready", did="y")
W = CardView(handle="w", state="working", doing="z")


def test_auto_is_on_at_open_and_off_for_two_minutes_after_a_key():
    r = Rotator()
    assert r.is_auto(0.0)
    r.touch(10.0)
    assert not r.is_auto(129.0)
    assert r.is_auto(130.0)


def test_holds_at_least_the_dwell_even_when_something_is_new():
    r = Rotator()
    r.observe(snap(A, B), 0.0)
    assert r.pick(0.0, current=None) in ("a", "b")
    first = r.pick(0.0, current=None)
    r.observe(snap(replace(A, did="new a"), replace(B, did="new b")), 5.0)
    assert r.pick(19.0, current=first) == first
    assert r.pick(21.0, current=first) != first


def test_needs_input_beats_error_beats_a_new_did():
    r = Rotator()
    base = snap(A, B, W)
    r.observe(base, 0.0)
    for c in base.cards:
        r._record(c, 0.0)
    r.observe(
        snap(
            replace(A, did="changed"),
            replace(B, attention="error"),
            replace(W, attention="needs_input"),
        ),
        1.0,
    )
    assert r.pick(30.0, current="zzz") == "w"


def test_a_shown_session_is_not_new_until_it_changes_again():
    r = Rotator()
    r.observe(snap(A), 0.0)
    for c in (A,):
        r._record(c, 0.0)
    r.observe(snap(replace(A, did="changed")), 1.0)
    assert r.pick(30.0, current=None) == "a"
    r.observe(snap(replace(A, did="changed")), 31.0)
    assert r.pick(60.0, current="a") == "a"


def test_with_nothing_new_it_cycles_working_sessions_every_thirty_seconds():
    r = Rotator()
    w2 = CardView(handle="w2", state="working", doing="q")
    s = snap(A, W, w2)
    r.observe(s, 0.0)
    for c in s.cards:
        r._record(c, 0.0)
    r._show("w", 0.0)
    assert r.pick(25.0, current="w") == "w"
    assert r.pick(31.0, current="w") == "w2"


def test_with_nothing_new_and_nothing_working_it_holds():
    r = Rotator()
    s = snap(A, B)
    r.observe(s, 0.0)
    for c in s.cards:
        r._record(c, 0.0)
    assert r.pick(300.0, current="a") == "a"


def test_a_finished_monitor_ranks_above_review():
    r = Rotator()
    m = MonitorRow("m1", "pytest", 50.0, 10.0, 5.0)
    s = snap(replace(A, monitors=(m,)), B)
    r.observe(s, 0.0)
    for c in s.cards:
        r._record(c, 0.0)
    r.observe(snap(replace(A, monitors=()), replace(B, attention="review")), 1.0)
    assert r.pick(30.0, current="zzz") == "a"


def test_ghosts_are_never_picked():
    r = Rotator()
    g = CardView(handle="g", state="ready", did="new", ghost_since=1.0)
    r.observe(snap(g), 0.0)
    assert r.pick(30.0, current=None) is None


def test_countdown_is_none_outside_auto():
    r = Rotator()
    r.touch(0.0)
    assert r.countdown(1.0) is None


def test_an_acked_category_is_not_new():
    r = Rotator()
    s = snap(replace(A, attention="review"))
    r.observe(s, 0.0)
    r._record(s.cards[0], 0.0)
    r.observe(snap(replace(A, attention="")), 1.0)
    assert r._rank(r._cards["a"]) is None
