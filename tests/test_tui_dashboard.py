from textual.app import App
from textual.widgets import Label

from aegis.queue.digest import QueueDigest
from aegis.queue.schema import Queue
from aegis.tui.dashboard import QueueDashboard


class _FakeManager:
    def __init__(self):
        self._queues = {"tasks": Queue("tasks", "claude", 2)}
    def subscribe(self, cb):
        return lambda: None


class _Harness(App):
    def __init__(self, digest, session_manager):
        super().__init__()
        self.queue_digest = digest
        self.session_manager = session_manager

    def compose(self):
        yield Label("home")

    async def on_mount(self):
        await self.push_screen(QueueDashboard())


class _SM:
    def get(self, handle): return None
    def focus(self, handle): pass


async def test_dashboard_pushes_and_dismisses():
    digest = QueueDigest(_FakeManager()); digest.start()
    app = _Harness(digest, _SM())
    async with app.run_test() as pilot:
        await pilot.pause()
        assert isinstance(app.screen, QueueDashboard)
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, QueueDashboard)
