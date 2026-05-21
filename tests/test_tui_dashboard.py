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


from aegis.queue.events import (
    QueueCompleted, QueueDispatched, QueueEnqueued, QueueStarted)
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


async def test_inflight_band_lists_running_tasks(make_dashboard_app):
    app, manager = make_dashboard_app()
    async with app.run_test() as pilot:
        await pilot.press("ctrl+d")
        await pilot.pause()
        manager.emit(QueueEnqueued(
            task_id="t1", queue="tasks",
            payload="summarize TASKS.md", enqueued_by="agent:c"))
        manager.emit(QueueDispatched(
            task_id="t1", queue="tasks",
            worker_handle="brisk-curie", agent_slug="claude"))
        manager.emit(QueueStarted(task_id="t1", queue="tasks"))
        await pilot.pause()
        band = app.screen.query_one("#band-inflight")
        rendered = band.query_one(Static).content.plain
        assert "brisk-curie" in rendered
        assert "summarize TASKS.md" in rendered


async def test_queued_band_lists_pending_tasks(make_dashboard_app):
    app, manager = make_dashboard_app()
    async with app.run_test() as pilot:
        await pilot.press("ctrl+d")
        await pilot.pause()
        for i in range(3):
            manager.emit(QueueEnqueued(
                task_id=f"q{i}", queue="tasks",
                payload=f"payload {i}", enqueued_by="agent:c"))
        await pilot.pause()
        band = app.screen.query_one("#band-queued")
        rendered = band.query_one(Static).content.plain
        for i in range(3):
            assert f"payload {i}" in rendered


async def test_recent_band_shows_completed_in_reverse_time(make_dashboard_app):
    app, manager = make_dashboard_app()
    async with app.run_test() as pilot:
        await pilot.press("ctrl+d")
        await pilot.pause()
        for i, outcome in enumerate(["completed", "failed", "completed"]):
            manager.emit(QueueEnqueued(
                task_id=f"r{i}", queue="tasks",
                payload=f"p {i}", enqueued_by="agent:c"))
            manager.emit(QueueDispatched(
                task_id=f"r{i}", queue="tasks",
                worker_handle=f"w{i}", agent_slug="claude"))
            manager.emit(QueueStarted(task_id=f"r{i}", queue="tasks"))
            manager.emit(QueueCompleted(
                task_id=f"r{i}", queue="tasks", outcome=outcome,
                result=None, error=None,
                completed_at=f"2026-05-21T12:00:0{i}Z"))
        await pilot.pause()
        band = app.screen.query_one("#band-recent")
        rendered = band.query_one(Static).content.plain
        idx0 = rendered.index("p 0")
        idx2 = rendered.index("p 2")
        assert idx2 < idx0


async def test_arrow_keys_move_cursor(make_dashboard_app):
    app, manager = make_dashboard_app()
    async with app.run_test() as pilot:
        await pilot.press("ctrl+d")
        for i in range(2):
            manager.emit(QueueEnqueued(
                task_id=f"t{i}", queue="tasks",
                payload=f"p{i}", enqueued_by="agent:c"))
        await pilot.pause()
        screen = app.screen
        assert screen.selected_task_id == "t0"
        await pilot.press("down"); await pilot.pause()
        assert screen.selected_task_id == "t1"
        await pilot.press("up"); await pilot.pause()
        assert screen.selected_task_id == "t0"
