"""The pane hands the session's drafted reply to its input box."""

from aegis.tui.pane import ConversationPane
from aegis.tui.widgets import GrowingInput


class _Input:
    def __init__(self):
        self.suggestion = ""


class _Pane:
    """Only the collaborator ``_on_suggestion`` touches."""

    def __init__(self):
        self._inp = _Input()

    def input_widget(self):
        return self._inp


def test_on_suggestion_sets_the_widget():
    p = _Pane()
    ConversationPane._on_suggestion(p, None, "one call, fifth field")
    assert p._inp.suggestion == "one call, fifth field"


def test_clearing_reaches_the_widget_too():
    p = _Pane()
    p._inp.suggestion = "stale"
    ConversationPane._on_suggestion(p, None, "")
    assert p._inp.suggestion == ""


def test_on_suggestion_survives_a_pruned_pane():
    """The observer fires off Textual's dispatch, so a torn-down pane must
    not raise into the session's emit loop."""

    class _Gone:
        def input_widget(self):
            raise RuntimeError("pruned")

    ConversationPane._on_suggestion(_Gone(), None, "x")  # must not raise


def test_growing_input_is_the_real_target():
    """Guards the duck-typed panes above against the property being renamed."""
    assert isinstance(GrowingInput.suggestion, property)


def test_the_pane_releases_the_observer():
    """A detached view that kept it would go on writing into a dead box."""
    import inspect

    src = inspect.getsource(ConversationPane.release_core_observers)
    assert '"remove_suggestion_observer"' in src
