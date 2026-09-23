"""What the status bar shows, and which provider a finished turn refreshes.

Quota is an account property, so the bar is *not* gated on which agents are
open — the whole point is to tell you which rail to launch on before there is
an agent to ask. Pane harnesses only route the turn-end refresh.
"""
from datetime import datetime, timedelta, timezone

from aegis.themes import AegisColors
from aegis.tui.app import AegisApp
from aegis.tui.fit import strip_markup
from aegis.usage.quota import QuotaSnapshot, QuotaState, QuotaWindow

COLORS = AegisColors(
    ready="green", working="yellow", error="red", accent="blue",
    muted="grey50", ok="green", err="red", user="blue", user_bg="black")


class FakeAgent:
    def __init__(self, harness):
        self.harness = harness


class FakePane:
    """Stands in for a ConversationPane — only the fields the tick reads."""

    def __init__(self, harness):
        self._agent = FakeAgent(harness)
        self.handle = f"pane-{harness}"
        self.quota_tiers = None

    def set_quota(self, tiers, gauges=()):
        # `gauges` is the same reading as `tiers`, in numbers, for the
        # sidebar's bars. Defaulted here the way the real pane defaults it.
        self.quota_tiers = tiers
        self.quota_gauges = gauges


class FakeService:
    def __init__(self, state):
        self._state = state
        self.started = False
        self.refreshes = 0

    def start(self):
        self.started = True

    def current(self):
        return self._state

    def refresh(self, **kw):
        self.refreshes += 1
        return None            # the app hands this to run_worker


def _snapshot(pct):
    return QuotaSnapshot(windows=(
        QuotaWindow("session", pct, "normal", None, True),
        QuotaWindow("rolling", pct, "normal", None, True),
    ), fetched_at=0.0)


def _app(panes, *, claude=None, opencode=None, remote=False):
    app = AegisApp.__new__(AegisApp)          # no Textual boot in a unit test
    app._panes = panes
    app._palette = COLORS
    app._quota_states = {}
    app._quota_last = None
    app._quota_stamp = None
    app._quota_pane = None
    app.quota_services = {
        "claude": FakeService(claude if claude is not None
                              else QuotaState(snapshot=_snapshot(64.0))),
        "opencode-go": FakeService(opencode if opencode is not None
                                   else QuotaState(snapshot=_snapshot(14.0))),
    }
    app.run_worker = lambda *a, **kw: None
    if remote:
        app._remote_manager = object()
    return app


def _text(pane):
    return strip_markup(pane.quota_tiers[0])


def test_quota_shows_with_no_agent_panes_at_all():
    pane = FakePane("claude-code")
    app = _app([])                            # nothing open anywhere
    app._quota_tick(pane)
    assert _text(pane) == "⧗ cc 5h 64% │ oc 5h 14%"


def test_quota_shows_beside_a_harness_that_has_none():
    pane = FakePane("gemini")
    app = _app([pane])
    app._quota_tick(pane)
    assert _text(pane) == "⧗ cc 5h 64% │ oc 5h 14%"


def test_remote_mode_still_shows_quota():
    # Remote agents on the same account spend the same windows, so the local
    # reading is the right one — the old remote exclusion hid it needlessly.
    pane = FakePane("claude-code")
    app = _app([pane], remote=True)
    app._quota_tick(pane)
    assert pane.quota_tiers != ()


def test_a_provider_without_credentials_drops_out():
    pane = FakePane("claude-code")
    app = _app([pane], claude=QuotaState(failure="no_credentials"))
    app._quota_tick(pane)
    assert _text(pane) == "⧗ 5h 14%"          # lone survivor, unlabelled


def test_every_service_is_started():
    app = _app([])
    app._quota_tick(FakePane("claude-code"))
    assert all(s.started for s in app.quota_services.values())


# --- turn-end routing ---------------------------------------------------------

class _State:
    working = "working"
    ready = "ready"


class Core:
    def __init__(self, state):
        self.state = state


def _finish_turn(app, pane):
    """Drive one pane working -> ready across two ticks."""
    from aegis.tui.state import AgentState
    pane._core = Core(AgentState.working)
    app._quota_tick(pane)
    pane._core = Core(AgentState.ready)
    app._quota_tick(pane)


def test_a_finished_claude_turn_refreshes_only_claude():
    pane = FakePane("claude-code")
    app = _app([pane])
    _finish_turn(app, pane)
    assert app.quota_services["claude"].refreshes == 1
    assert app.quota_services["opencode-go"].refreshes == 0


def test_a_finished_opencode_turn_refreshes_only_opencode():
    pane = FakePane("opencode")
    app = _app([pane])
    _finish_turn(app, pane)
    assert app.quota_services["opencode-go"].refreshes == 1
    assert app.quota_services["claude"].refreshes == 0


def test_a_finished_turn_on_a_quotaless_harness_refreshes_nothing():
    pane = FakePane("gemini")
    app = _app([pane])
    _finish_turn(app, pane)
    assert all(s.refreshes == 0 for s in app.quota_services.values())


def test_panes_without_an_agent_are_ignored():
    class Bare:
        pass
    app = _app([Bare()])
    app._quota_tick(FakePane("claude-code"))   # must not raise
    assert all(s.refreshes == 0 for s in app.quota_services.values())


def test_an_unchanged_segment_is_not_pushed_twice():
    pane = FakePane("claude-code")
    app = _app([])
    app._quota_tick(pane)
    pane.quota_tiers = "sentinel"
    app._quota_tick(pane)
    assert pane.quota_tiers == "sentinel"      # no repaint for a stable value


def test_switching_panes_pushes_to_the_new_pane():
    first, second = FakePane("claude-code"), FakePane("opencode")
    app = _app([])
    app._quota_tick(first)
    app._quota_tick(second)
    # Same numbers, different pane — the new one must still be painted.
    assert second.quota_tiers == first.quota_tiers
    assert second.quota_tiers is not None


class TerminalLike:
    """A terminal or file tab: no quota segment of its own."""


def test_the_app_holds_the_quota_with_no_agent_pane_in_front():
    """F10 opened over a terminal tab still needs the quota the app computed."""
    app = _app([])
    app._quota_tick(TerminalLike())
    assert app._quota_last and "cc 5h 64%" in strip_markup(app._quota_last[0])


def test_an_agent_pane_after_a_terminal_tab_is_still_painted():
    """The held value must not suppress the push to the next agent pane."""
    pane = FakePane("claude-code")
    app = _app([])
    app._quota_tick(pane)
    pane.quota_tiers = None
    app._quota_tick(TerminalLike())
    app._quota_tick(pane)
    assert pane.quota_tiers == app._quota_last


def test_a_moving_reset_reaches_the_sidebar_even_when_the_tier_text_does_not():
    """The push was gated on the tier string alone, on the reasoning that
    the gauges derive from the same reading. They do — but `resets_in_s` is
    recomputed against a live clock every tick, while `format_quota_bar`
    prints no countdown at normal severity. So the tier text sits still for
    minutes on a busy session and forever on an idle one, and the sidebar
    kept painting a countdown that had stopped counting.
    """
    def _state(hours):
        at = datetime.now(timezone.utc) + timedelta(hours=hours)
        return QuotaState(snapshot=QuotaSnapshot(windows=(
            QuotaWindow("session", 64.0, "normal", at, True),
            QuotaWindow("rolling", 64.0, "normal", at, True),
        ), fetched_at=0.0))

    pane = FakePane("claude-code")
    app = _app([], claude=_state(3), opencode=_state(3))
    app._quota_tick(pane)
    first = tuple(g.resets_in_s for g in pane.quota_gauges)
    assert first and all(s is not None for s in first)

    # Same percentages, so the same tier text — only the clock moved.
    tiers_before = pane.quota_tiers
    app.quota_services["claude"] = FakeService(_state(1))
    app.quota_services["opencode-go"] = FakeService(_state(1))
    app._quota_tick(pane)

    assert pane.quota_tiers == tiers_before, "the tier text must not have moved"
    second = tuple(g.resets_in_s for g in pane.quota_gauges)
    assert second[0] < first[0] - 3000, (first, second)
