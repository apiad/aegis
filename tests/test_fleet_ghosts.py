"""A queue worker can live forty seconds. Without a ghost it appears and
vanishes between two glances, and the dashboard lies by omission."""

from aegis.fleet.ghosts import GHOST_TTL, GhostBook
from aegis.fleet.models import CardView, FleetSnapshot, Origin

Q = Origin(kind="queue", by="general", detail="a3f2")


def card(handle, origin=Q, **kw):
    return CardView(handle=handle, origin=origin, **kw)


def test_a_live_fleet_leaves_no_ghosts():
    b = GhostBook()
    b.observe((card("w1"),), now=100.0)
    assert b.alive(now=100.0) == {}


def test_a_departed_ephemeral_becomes_a_ghost():
    b = GhostBook()
    b.observe((card("w1"),), now=100.0)
    b.observe((), now=140.0)
    ghosts = b.alive(now=141.0)
    assert list(ghosts) == ["w1"]
    assert ghosts["w1"][0].ghost_since == 140.0
    assert ghosts["w1"][0].tab_index == 0, "a ghost has no tab to open"


def test_a_ghost_expires_after_its_ttl():
    b = GhostBook()
    b.observe((card("w1"),), now=100.0)
    b.observe((), now=140.0)
    assert b.alive(now=140.0 + GHOST_TTL - 1) != {}
    assert b.alive(now=140.0 + GHOST_TTL + 1) == {}


def test_an_operator_tab_that_closes_leaves_no_ghost():
    """Only the substrate-born come and go on their own. A tab you closed,
    you closed — showing it back for a minute would read as a bug."""
    b = GhostBook()
    b.observe((card("mine", origin=Origin()),), now=100.0)
    b.observe((), now=140.0)
    assert b.alive(now=141.0) == {}


def test_a_worker_that_comes_back_is_not_also_a_ghost():
    """Handles are recycled out of a finite pool."""
    b = GhostBook()
    b.observe((card("w1"),), now=100.0)
    b.observe((), now=140.0)
    b.observe((card("w1"),), now=150.0)
    assert b.alive(now=151.0) == {}


# --- beyond the brief: the screen holds the book ---

from aegis.tui.fleet_screen import FleetScreen  # noqa: E402


def test_the_screen_keeps_a_departed_worker_as_a_ghost(monkeypatch):
    """The screen observes each live snapshot and hands the book's ghosts
    back to the builder, with the same monotonic ``now``."""
    import aegis.tui.fleet_screen as fs

    clock = iter([100.0, 140.0])
    monkeypatch.setattr(fs.time, "monotonic", lambda: next(clock))
    fleets = [(card("w1", tab_index=1),), ()]
    calls = []

    def snap(*, now=None, ghosts=None):
        calls.append((now, ghosts))
        live = fleets[0]
        cards = live + tuple(c for c, _ in (ghosts or {}).values())
        return FleetSnapshot(cards=cards)

    scr = FleetScreen(snap)
    scr.refresh_fleet()
    fleets.pop(0)
    scr.refresh_fleet()
    assert [c.handle for c in scr._current.cards] == ["w1"]
    assert scr._current.cards[0].ghost_since == 140.0
    assert all(now in (100.0, 140.0) for now, _ in calls)
    assert calls[-1][0] == 140.0 and "w1" in calls[-1][1]
