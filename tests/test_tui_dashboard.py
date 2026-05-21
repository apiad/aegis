from textual.app import App
from textual.widgets import Label

from aegis.queue.digest import QueueDigest
from aegis.queue.schema import Queue
from aegis.tui.dashboard import QueueDashboard
from aegis.tui.themes import aegis_colors, INK


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
        self._pal = aegis_colors(INK)

    @property
    def palette(self):
        return self._pal

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


from aegis.queue.events import QueueDispatched, QueueEnqueued, QueueStarted
from textual.widgets import Static


async def test_queues_band_renders_config_and_counts(make_dashboard_app):
    app, manager = make_dashboard_app()
    async with app.run_test() as pilot:
        await pilot.press("ctrl+d")
        await pilot.pause()
        manager.emit(QueueEnqueued(
            task_id="t1", queue="tasks", payload="p",
            enqueued_by="agent:c"))
        manager.emit(QueueDispatched(
            task_id="t1", queue="tasks",
            worker_handle="w1", agent_slug="claude"))
        manager.emit(QueueStarted(task_id="t1", queue="tasks"))
        await pilot.pause()
        band = app.screen.query_one("#band-queues")
        rendered = band.query_one(Static).content.plain
        assert "tasks" in rendered
        assert "claude" in rendered
        assert "parallel 2" in rendered
        assert "running 1" in rendered
