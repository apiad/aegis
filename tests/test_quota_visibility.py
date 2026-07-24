from aegis.tui.app import AegisApp


class FakeAgent:
    def __init__(self, harness):
        self.harness = harness


class FakePane:
    """Stands in for a ConversationPane — only the fields the predicate reads."""

    def __init__(self, harness):
        self._agent = FakeAgent(harness)
        self.handle = f"pane-{harness}"
        self.quota_tiers = None

    def set_quota(self, tiers):
        self.quota_tiers = tiers


def _app(panes, remote=False):
    app = AegisApp.__new__(AegisApp)      # no Textual boot in a unit test
    app._panes = panes
    if remote:
        app._remote_manager = object()
    return app


def test_no_panes_means_no_quota():
    assert _app([])._quota_enabled() is False


def test_a_claude_pane_enables_quota():
    assert _app([FakePane("claude-code")])._quota_enabled() is True


def test_non_claude_panes_do_not_enable_quota():
    panes = [FakePane("gemini"), FakePane("lovelaice")]
    assert _app(panes)._quota_enabled() is False


def test_one_claude_pane_among_others_enables_quota():
    panes = [FakePane("gemini"), FakePane("claude-code")]
    assert _app(panes)._quota_enabled() is True


def test_remote_mode_never_enables_quota():
    app = _app([FakePane("claude-code")], remote=True)
    assert app._quota_enabled() is False


def test_panes_without_an_agent_are_ignored():
    class Bare:
        pass
    assert _app([Bare()])._quota_enabled() is False


def test_disabled_quota_clears_the_segment():
    pane = FakePane("gemini")
    app = _app([pane])
    app._quota_tick(pane)
    assert pane.quota_tiers == ()
