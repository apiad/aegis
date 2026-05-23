"""WORKFLOWS band — surfaces in-flight workflow runs in Ctrl+D."""
from __future__ import annotations

import asyncio
import time

from textual.widgets import Static

from aegis.workflow.runner import (
    WorkflowRunner, _PendingQuestion, _RunningWorkflow,
)


def _attach_runner(app) -> WorkflowRunner:
    """Construct a WorkflowRunner and attach it to the harness app the
    same way the production MCP build does."""
    runner = WorkflowRunner(bridge=app)
    app.workflow_runner = runner
    return runner


def _fake_running(runner: WorkflowRunner, *, wid: str, name: str,
                 host: str | None, status: str = "running",
                 started_offset_s: float = 1.0,
                 finished_offset_s: float | None = None,
                 result=None, error: str | None = None) -> None:
    """Stuff a _RunningWorkflow into the runner without spinning a real
    asyncio task. Workflow rendering only reads dataclass fields."""
    now = time.monotonic()

    class _Dummy:
        def done(self):
            return status != "running"
        def cancel(self):
            pass

    runner._running[wid] = _RunningWorkflow(
        id=wid, name=name, host=host,
        task=_Dummy(),  # type: ignore[arg-type]
        engine=None,    # type: ignore[arg-type]
        status=status, result=result, error=error,
        started_at=now - started_offset_s,
        finished_at=(now - finished_offset_s
                     if finished_offset_s is not None else None),
    )


async def test_workflows_band_shows_none_when_no_runs(make_dashboard_app):
    app, _ = make_dashboard_app()
    _attach_runner(app)
    async with app.run_test() as pilot:
        await pilot.press("ctrl+d")
        await pilot.pause()
        band = app.screen.query_one("#band-workflows")
        rendered = band.query_one(Static).content.plain
        assert "WORKFLOWS" in rendered
        assert "(none)" in rendered


async def test_workflows_band_shows_none_when_no_runner_attached(
        make_dashboard_app):
    """If MCP hasn't built (so no runner on the app), band is empty,
    not a crash."""
    app, _ = make_dashboard_app()
    # Deliberately do NOT attach a runner.
    async with app.run_test() as pilot:
        await pilot.press("ctrl+d")
        await pilot.pause()
        band = app.screen.query_one("#band-workflows")
        rendered = band.query_one(Static).content.plain
        assert "WORKFLOWS" in rendered
        assert "(none)" in rendered


async def test_workflows_band_lists_running_workflow(make_dashboard_app):
    app, _ = make_dashboard_app()
    runner = _attach_runner(app)
    _fake_running(runner, wid="0000000000ABCDEF", name="tdd_cycle",
                  host="lucid-knuth", status="running",
                  started_offset_s=3.2)
    async with app.run_test() as pilot:
        await pilot.press("ctrl+d")
        await pilot.pause()
        band = app.screen.query_one("#band-workflows")
        rendered = band.query_one(Static).content.plain
        assert "tdd_cycle" in rendered
        assert "ABCDEF" in rendered  # short id suffix
        assert "lucid-knuth" in rendered
        assert "running" in rendered
        # Elapsed allows for a little wall-clock drift between fake_running
        # and the band's render.
        import re
        m = re.search(r"(\d+(?:\.\d)?)s\b", rendered)
        assert m is not None
        secs = float(m.group(1))
        assert 3.0 <= secs <= 4.0


async def test_workflows_band_marks_awaiting_human(make_dashboard_app):
    app, _ = make_dashboard_app()
    runner = _attach_runner(app)
    _fake_running(runner, wid="WID1", name="brainstorm_to_spec",
                  host="h", status="running", started_offset_s=10.0)
    # Register a pending ask_human question for that workflow on its host.
    runner._questions["h"] = __import__("collections").deque([
        _PendingQuestion(workflow_id="WID1", host="h",
                         question="pick one", options=None,
                         fut=asyncio.get_event_loop().create_future()),
    ])
    async with app.run_test() as pilot:
        await pilot.press("ctrl+d")
        await pilot.pause()
        band = app.screen.query_one("#band-workflows")
        rendered = band.query_one(Static).content.plain
        assert "awaiting reply" in rendered


async def test_workflows_band_shows_ok_with_result_tail(make_dashboard_app):
    app, _ = make_dashboard_app()
    runner = _attach_runner(app)
    _fake_running(runner, wid="WID2", name="review_branch",
                  host="h", status="ok", started_offset_s=120.0,
                  finished_offset_s=10.0, result="3 reviews ok, 0 blocked")
    async with app.run_test() as pilot:
        await pilot.press("ctrl+d")
        await pilot.pause()
        band = app.screen.query_one("#band-workflows")
        rendered = band.query_one(Static).content.plain
        assert "review_branch" in rendered
        assert "ok" in rendered
        assert "3 reviews ok, 0 blocked" in rendered


async def test_workflows_band_shows_error_summary(make_dashboard_app):
    app, _ = make_dashboard_app()
    runner = _attach_runner(app)
    _fake_running(runner, wid="WID3", name="execute_plan",
                  host="h", status="error", started_offset_s=30.0,
                  finished_offset_s=5.0,
                  error="PredicateFailed: tests still failing")
    async with app.run_test() as pilot:
        await pilot.press("ctrl+d")
        await pilot.pause()
        band = app.screen.query_one("#band-workflows")
        rendered = band.query_one(Static).content.plain
        assert "execute_plan" in rendered
        assert "PredicateFailed" in rendered
