"""F10 reads the app's own numbers and this view's seen-ness."""

from types import SimpleNamespace

from aegis.fleet.models import CardView, FleetSnapshot


def test_pending_attention_is_per_view():
    from aegis.tui.app import _with_attention

    snap = FleetSnapshot(cards=(CardView(handle="a"), CardView(handle="b")))
    cores = {
        "a": SimpleNamespace(effective_attention="needs_input", attention_seq=2),
        "b": SimpleNamespace(effective_attention="review", attention_seq=1),
    }
    acked = {"a": 1, "b": 1}
    got = _with_attention(snap, cores, acked)
    assert [c.attention for c in got.cards] == ["needs_input", ""]


async def test_the_band_row_carries_raw_stats_and_gauges(tmp_path, monkeypatch):
    """After one tick the band row holds the sample itself and the quota
    gauges, not the F3 strings formatted from them."""
    from aegis.tui.app import AegisApp
    from aegis.tui.sysmeter import SystemStats
    from tests.test_fleet_screen import _standalone

    monkeypatch.setattr(AegisApp, "_quota_tick", lambda self, active: None)
    app = _standalone(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app._tick()
        row = app._fleet_system_row()
        assert set(row) == {"stats", "gauges", "build"}
        assert isinstance(row["stats"], SystemStats)
        assert isinstance(row["gauges"], tuple)
        assert row["build"]


async def test_the_fleet_snapshot_reads_this_views_seen_ness(tmp_path, monkeypatch):
    """A category the view has acked is not pending on its card."""
    from aegis.tui.app import AegisApp
    from tests.test_fleet_screen import _standalone

    monkeypatch.setattr(AegisApp, "_quota_tick", lambda self, active: None)
    app = _standalone(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        pane = app._panes[0]
        pane._core.attention, pane._core.attention_seq = "review", 3
        pane.attention_acked = 2
        assert app._fleet_snapshot().cards[0].attention == "review"
        pane.attention_acked = 3
        assert app._fleet_snapshot().cards[0].attention == ""
