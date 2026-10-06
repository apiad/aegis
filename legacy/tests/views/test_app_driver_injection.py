"""AegisApp must be able to run on a ViewDriver. It calls
super().__init__() with no arguments today (app.py:322), so driver_class
never reaches Textual."""
from aegis.tui.app import AegisApp
from aegis.views.driver import ViewDriver, view_driver_for

from tests.views.conftest import FakeMCP


def _app(**kw):
    # NOT mcp=None: the local plane calls self._mcp.bind(self) at
    # app.py:499, so None raises AttributeError before any assertion here
    # can run — and the failure is indistinguishable from the step-2
    # "driver_class is not a parameter" failure this task is watching for.
    return AegisApp(agents={}, default_agent="", make_session=None,
                    mcp=FakeMCP(), **kw)


def test_driver_class_reaches_textual():
    frames = []
    cls = view_driver_for(frames.append)
    app = _app(driver_class=cls)
    assert app.driver_class is cls


def test_no_driver_class_keeps_auto_detection():
    """The single-view path must be observably identical."""
    app = _app()
    assert app.driver_class is not None
    assert not issubclass(app.driver_class, ViewDriver)
