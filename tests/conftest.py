"""Shared test fixtures and configuration."""

# Wire in workflow fixtures (fake_bridge*, workflow_test_harness).
from tests.conftest_workflows import *  # noqa: F401,F403,E402

import asyncio
import itertools
import os
import shutil

import pytest
import json
from typing import Any, AsyncIterator
from unittest.mock import AsyncMock, MagicMock
from asyncio import Queue
from fastmcp import Client
from textual.app import App
from textual.widgets import Label

from aegis.queue.digest import QueueDigest
from aegis.queue.schema import Queue as _AegisQueue
from aegis.tui.dashboard import QueueDashboard
from aegis.tui.themes import aegis_colors, INK


def pytest_addoption(parser):
    parser.addoption(
        "--run-live", action="store_true", default=False,
        help="run tests marked live (real agent CLIs, models, remote hosts)")
    parser.addoption(
        "--max-unmarked-duration", type=float, default=None,
        metavar="SECONDS",
        help="fail the run when a test not marked slow takes longer than "
             "this, setup and teardown included")


def pytest_collection_modifyitems(config, items):
    """Live tests are opt-in. Skipping them only when their CLI was off PATH
    meant a bare `pytest` on a dev machine, where every CLI is installed,
    spent real quota and pushed schedules to a remote host."""
    if config.getoption("--run-live"):
        return
    skip = pytest.mark.skip(reason="live: pass --run-live (make test-live)")
    for item in items:
        if item.get_closest_marker("live"):
            item.add_marker(skip)


# nodeid -> seconds across setup, call and teardown; filled on the controller
# under xdist, since reports reach it from every worker.
_durations: dict[str, float] = {}
_marked_slow: set[str] = set()


def pytest_runtest_logreport(report):
    _durations[report.nodeid] = _durations.get(report.nodeid, 0.0) \
        + report.duration
    if "slow" in report.keywords:
        _marked_slow.add(report.nodeid)


def _over_budget(config) -> list[tuple[float, str]]:
    budget = config.getoption("--max-unmarked-duration")
    if budget is None:
        return []
    return sorted(((d, n) for n, d in _durations.items()
                   if n not in _marked_slow and d > budget), reverse=True)


def pytest_sessionfinish(session, exitstatus):
    """The slow marker is only worth something if it stays true. A test that
    grows past the budget without the marker fails the run, instead of
    quietly making the fast lane slower."""
    if hasattr(session.config, "workerinput"):
        return
    if _over_budget(session.config) and session.exitstatus == 0:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    over = _over_budget(config)
    if not over:
        return
    budget = config.getoption("--max-unmarked-duration")
    terminalreporter.section(
        f"unmarked tests over {budget:g}s", sep="=", red=True)
    for seconds, nodeid in over:
        terminalreporter.line(f"{seconds:6.2f}s  {nodeid}")
    terminalreporter.line(
        "make them faster, or mark them @pytest.mark.slow")


@pytest.fixture(autouse=True)
def no_real_provider_accounts(request, tmp_path, monkeypatch):
    """Keep hermetic tests off the operator's accounts.

    Every AegisApp starts a quota poller on its first tick. With real
    credentials on disk it called api.anthropic.com and opencode.ai with the
    user's own token — 29 tests per run on zion, and never on CI, which has no
    credentials. Model pickers likewise shelled out to the real `opencode
    models`. Live tests keep the real accounts; that is what they are for.
    """
    if request.node.get_closest_marker("live"):
        return
    monkeypatch.setenv("CLAUDE_CREDS", str(tmp_path / "no-claude-creds.json"))
    monkeypatch.setenv("OPENCODE_AUTH", str(tmp_path / "no-opencode-auth.json"))
    monkeypatch.setattr("aegis.models._run_opencode_models", lambda: None)
    _refuse_real_oneshot(monkeypatch)


# Resolved once, before any test prepends a fake `claude` to PATH.
_REAL_CLAUDE = shutil.which("claude")


def _refuse_real_oneshot(monkeypatch):
    """A hermetic test must never run the operator's real `claude -p`.

    Since every finished turn is recapped (2026-09-16), any test that runs a
    turn on a session holding an agent roster reaches the real one-shot
    driver unless it patches `recap_for`. It billed the operator's account
    and hung the event loop's teardown waiting on the call. Refused here
    rather than patched per test, so a new test cannot forget. A test that
    puts its own fake `claude` first on PATH, or patches
    `create_subprocess_exec` itself, is unaffected.
    """
    real_exec = asyncio.create_subprocess_exec

    async def guarded(program, *args, **kwargs):
        resolved = shutil.which(program) if isinstance(program, str) else None
        if (
            _REAL_CLAUDE
            and resolved
            and os.path.realpath(resolved) == os.path.realpath(_REAL_CLAUDE)
            and "-p" in args
        ):
            raise RuntimeError("hermetic test tried to run the real `claude -p`")
        return await real_exec(program, *args, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", guarded)


class MockQueue:
    """Mock async queue for testing workflow step behavior."""

    def __init__(self, values: list | None = None):
        self._all_values = list(values) if values else []
        self._put_values: list = []
        self._current_index = 0

    async def get(self) -> Any:
        """Get the next value from the queue."""
        if self._current_index >= len(self._all_values):
            return None
        value = self._all_values[self._current_index]
        self._current_index += 1
        return value

    def get_nowait(self) -> Any:
        """Get the next value from the queue without waiting."""
        if self._current_index >= len(self._all_values):
            raise Exception("Queue is empty")
        value = self._all_values[self._current_index]
        self._current_index += 1
        return value

    def empty(self) -> bool:
        """Return True if the queue is empty."""
        return self._current_index >= len(self._all_values)

    def put_nowait(self, value: Any) -> None:
        """Capture values put into the queue."""
        self._put_values.append(value)

    def get_put_values(self) -> list:
        """Get all values that were put into the queue."""
        return self._put_values

    def reset(self, values: list | None = None) -> None:
        """Reset the queue with new values."""
        self._all_values = list(values) if values else []
        self._current_index = 0
        self._put_values = []


class MockContext:
    """Mock FastMCP Context for testing server tools."""

    def __init__(self, state: dict[str, Any] | None = None):
        self._state = state or {}
        self._calls: list[tuple[str, dict]] = []

    async def get_state(self, key: str) -> Any:
        """Get state value."""
        return self._state.get(key)

    async def set_state(self, key: str, value: Any) -> None:
        """Set state value."""
        self._state[key] = value

    async def delete_state(self, key: str) -> None:
        """Delete state value."""
        self._state.pop(key, None)

    def get_calls(self) -> list[tuple[str, dict]]:
        """Get all method calls made to this context."""
        return self._calls


class WorkflowRunner:
    """Helper to advance a workflow in tests."""

    def __init__(self, client: Client, initial_result: str):
        self.client = client
        self.last_result = initial_result

    async def step(self, data: Any = None) -> str:
        """Advance the workflow with the given data."""
        result = await self.client.call_tool("workflow_step", {"data": data})
        self.last_result = result.data
        return self.last_result

    async def run_to_end(self, responses: list[Any]) -> AsyncIterator[str]:
        """Run the workflow to the end with a list of responses."""
        yield self.last_result
        for response in responses:
            yield await self.step(response)


class AegisClient(Client):
    """Custom MCP Client with workflow helpers."""

    async def start_workflow(self, name: str, cwd: str | None = None) -> WorkflowRunner:
        """Start a workflow and return a runner."""
        args = {"name": name}
        if cwd:
            args["cwd"] = cwd
        result = await self.call_tool("workflow_start", args)
        return WorkflowRunner(self, result.data)

    async def run_workflow(self, name: str, steps: list[Any], cwd: str | None = None) -> AsyncIterator[str]:
        """Start and run a workflow to completion."""
        runner = await self.start_workflow(name, cwd)
        async for result in runner.run_to_end(steps):
            yield result


@pytest.fixture(autouse=True)
def isolated_project_dir(tmp_path, monkeypatch):
    """Run every test in its own project directory.

    Anything that resolves state from ``Path.cwd()`` — chiefly
    ``AegisApp``, whose state dir is ``cwd/.aegis/state`` — otherwise shares
    the repo's own state dir across the whole suite. One test's saved
    workspace is then resumed by the next test's app, and by the next *run*
    too, since nothing cleans it up: a leftover terminal entry alone is
    enough to hang a test that just asserts its pane count. Tests that need
    a specific project dir chdir again on top of this one.
    """
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def mock_in_queue():
    """Create a mock input queue that returns values in sequence."""
    return MockQueue()


@pytest.fixture
def mock_out_queue():
    """Create a mock output queue that captures put values."""
    return MockQueue()


@pytest.fixture
def workflow_context(mock_in_queue, mock_out_queue):
    """Create a WorkflowContext with mocked queues."""
    from aegis.server import WorkflowContext

    ctx = WorkflowContext(cwd="/tmp/test", max_retries=3)
    ctx.in_queue = mock_in_queue
    ctx.out_queue = mock_out_queue
    return ctx


@pytest.fixture
def sample_model():
    """Create a sample Pydantic model for testing."""
    from pydantic import BaseModel

    class TestData(BaseModel):
        name: str
        value: int

    return TestData


@pytest.fixture
def sample_model_json():
    """JSON string matching TestData schema."""
    return '{"name": "test", "value": 42}'


@pytest.fixture
def invalid_json():
    """Invalid JSON string for testing validation."""
    return "not valid json"


@pytest.fixture
def mock_context():
    """Create a mock FastMCP Context."""
    return MockContext()


class _FakeQueueManager:
    """Fake QueueManager — emits events on demand, no real workers."""

    def __init__(self, queues):
        self._queues = queues
        self._subs = []

    def subscribe(self, cb):
        self._subs.append(cb)

        def _unsub():
            if cb in self._subs:
                self._subs.remove(cb)

        return _unsub

    def emit(self, ev):
        for cb in list(self._subs):
            cb(ev)


class _DashboardHarness(App):
    BINDINGS = [
        ("ctrl+d", "open_dashboard", "Queues"),
    ]

    def __init__(self, digest, sm=None):
        super().__init__()
        self.queue_digest = digest
        self._pal = aegis_colors(INK)
        self.session_manager = sm

    @property
    def palette(self):
        return self._pal

    def compose(self):
        yield Label("home")

    async def action_open_dashboard(self):
        await self.push_screen(QueueDashboard())


@pytest.fixture
def make_dashboard_app():
    def _factory(queues=None, sm=None):
        q = queues if queues is not None else {
            "tasks": _AegisQueue("tasks", "claude", 2)}
        fake = _FakeQueueManager(q)
        digest = QueueDigest(fake)
        digest.start()
        app = _DashboardHarness(digest, sm=sm)
        return app, fake

    return _factory


@pytest.fixture
async def live_session_manager(tmp_path):
    """Real SessionManager wired against the live claude-code driver.

    Spawns no agents — the test does that via ``sm.groups.spawn``. Cleans
    up sessions + MCP server on teardown. Used only by ``-m live`` tests.
    """
    from aegis.config import Agent
    from aegis.config.roots import AegisRoots
    from aegis.core.manager import SessionManager
    from aegis.drivers import get_driver
    from aegis.mcp import AegisMCP
    from aegis.queue import InboxRouter

    agent = Agent(harness="claude-code", model="sonnet",
                  effort="low", permission="full")
    agents = {"default": agent}
    inbox = InboxRouter(state_dir=tmp_path)
    mcp = AegisMCP()

    def make_session(profile, mcp_url, handle):
        return get_driver(profile.harness).session(
            profile, str(tmp_path), mcp_url, handle)

    mgr = SessionManager(agents, "default", make_session, mcp, inbox=inbox,
                         roots=AegisRoots.for_project(tmp_path))
    mcp.bind(mgr)
    await mcp.start()
    try:
        yield mgr
    finally:
        await mgr.close_all()
        await mcp.stop()


@pytest.fixture
def pane_app():
    """A booted AegisApp and its first ConversationPane.

    ``app._panes[0]`` under ``app.run_test()`` is how the other pane tests
    reach one (see tests/test_background_costs.py). Events go in through
    ``_on_core_event(_core, ev)``, the same entry the SessionManager uses.
    Passing ``events`` replays them off a fake log instead, which is the
    resume path — a different code path from the live one, and the one
    that used to leave a tool block unclickable.
    """
    from contextlib import asynccontextmanager

    from aegis.config import Agent
    from aegis.events import Result
    from aegis.state.session_log import EventReplay
    from aegis.tui.app import AegisApp

    class FakeSession:
        async def start(self):
            pass

        async def send(self, text):
            pass

        async def events(self):
            yield Result(duration_ms=1, is_error=False)

        async def close(self):
            pass

    class FakeMCP:
        url = "http://127.0.0.1:0/mcp/"

        def bind(self, bridge):
            pass

        async def start(self):
            pass

        async def stop(self):
            pass

    @asynccontextmanager
    async def _make(events=()):
        agent = Agent(harness="claude-code", model="opus", effort="high",
                      permission="auto")
        app = AegisApp({"default": agent}, "default",
                       lambda *a, **kw: FakeSession(), FakeMCP())
        async with app.run_test() as pilot:
            pane = app._panes[0]
            if events:
                pane._replay = EventReplay(events=list(events),
                                           interrupted=False)
                pane._replayed = False
                pane._mount_replay()
            await pilot.pause()
            yield pane, pilot

    return _make


# ---------------------------------------------------------------------------
# aegis usage repo / aegis usage repos — a two-repo workspace with three
# aegis sessions: one inside the target repo, one mixed, one entirely
# elsewhere. Shared by tests/test_cost_measure.py and tests/test_cost_cli.py.
# ---------------------------------------------------------------------------


def _cost_commit(repo, message, paths, date):
    import subprocess

    stamp = f"{date}T12:00:00+00:00"
    subprocess.run(
        ("git", "add", "--", *paths), cwd=repo, check=True, capture_output=True
    )
    subprocess.run(
        ("git", "commit", "-q", "-m", message),
        cwd=repo,
        check=True,
        capture_output=True,
        env={
            "PATH": "/usr/bin:/bin",
            "HOME": str(repo),
            "GIT_AUTHOR_DATE": stamp,
            "GIT_COMMITTER_DATE": stamp,
            "GIT_AUTHOR_NAME": "Tester",
            "GIT_AUTHOR_EMAIL": "t@example.com",
            "GIT_COMMITTER_NAME": "Tester",
            "GIT_COMMITTER_EMAIL": "t@example.com",
        },
    )


@pytest.fixture
def cost_tree(tmp_path):
    import json
    import subprocess

    def ev(ts, **event):
        return json.dumps({"v": 1, "aegis_ts": ts, "event": event})

    def usage(n):
        return {"input": n, "cache_creation": 0, "cache_read": n * 10, "output": n}

    for name in ("aegis", "une-tools"):
        repo = tmp_path / "repos" / name
        repo.mkdir(parents=True)
        subprocess.run(
            ("git", "init", "-q", "-b", "main"), cwd=repo, check=True,
            capture_output=True,
        )
        for key, value in (("user.email", "t@example.com"), ("user.name", "Tester")):
            subprocess.run(
                ("git", "config", key, value), cwd=repo, check=True,
                capture_output=True,
            )
        (repo / "src").mkdir()
        (repo / "src" / "main.py").write_text("a = 1\n")
        _cost_commit(repo, "feat: start", ("src/main.py",), "2026-06-01")

    sessions = tmp_path / ".aegis" / "state" / "sessions"
    sessions.mkdir(parents=True)
    # Working inside the target repo: share 1.0.
    (sessions / "inside.jsonl").write_text(
        "\n".join([
            ev("2026-06-02T10:00:00.000000Z", t="SessionMeta", handle="inside",
               provider="claude-code", cwd=str(tmp_path / "repos" / "aegis")),
            ev("2026-06-02T10:00:01.000000Z", t="SystemInit",
               model="claude-opus-4-7"),
            ev("2026-06-02T10:00:02.000000Z", t="AssistantText", text="x",
               message_id="m1", usage=usage(1000)),
        ]) + "\n"
    )
    # Two records name aegis, one names une-tools: share 2/3. Locality
    # counts records, so m2's two aegis paths are still one record.
    (sessions / "mixed.jsonl").write_text(
        "\n".join([
            ev("2026-06-02T11:00:00.000000Z", t="SessionMeta", handle="mixed",
               provider="claude-code", cwd=str(tmp_path)),
            ev("2026-06-02T11:00:01.000000Z", t="SystemInit",
               model="claude-opus-4-7"),
            ev("2026-06-02T11:00:02.000000Z", t="AssistantText",
               text="repos/aegis/src/a.py repos/aegis/src/b.py",
               message_id="m2", usage=usage(1000)),
            ev("2026-06-02T11:00:03.000000Z", t="AssistantText",
               text="repos/aegis/src/c.py", message_id="m3", usage=usage(1)),
            ev("2026-06-02T11:00:04.000000Z", t="AssistantText",
               text="repos/une-tools/app.py", message_id="m4", usage=usage(1)),
        ]) + "\n"
    )
    # Never names the target repo: share 0.
    (sessions / "elsewhere.jsonl").write_text(
        "\n".join([
            ev("2026-06-02T12:00:00.000000Z", t="SessionMeta", handle="elsewhere",
               provider="claude-code", cwd=str(tmp_path)),
            ev("2026-06-02T12:00:01.000000Z", t="SystemInit",
               model="claude-opus-4-7"),
            ev("2026-06-02T12:00:02.000000Z", t="AssistantText",
               text="repos/une-tools/app.py", message_id="m5", usage=usage(1000)),
        ]) + "\n"
    )
    return tmp_path


# ---------------------------------------------------------------------------
# The queue recovery rig. Tasks 6-11 of the ephemeral-agent-recovery plan all
# drive a real QueueManager through the StubSM double from
# tests/test_queue_e2e.py, so the wiring is built once here.
# ---------------------------------------------------------------------------


def make_queue_rig(tmp_path, *, max_attempts=2, recoverable_ttl_s=86400,
                   rebuild_fails=False):
    """A QueueManager with one `impl` queue, persisting under tmp_path.

    Workers do NOT autostart: a test ends their turn by hand with
    `sm.finish(...)` / `sm.fail(...)`, which is the only way to reach a bad
    turn end at all. Handles are `w1`, `w2`, … in dispatch order, so a log
    read in a failure message is legible.
    """
    # Aliased: this module already binds `Queue` to asyncio's at import.
    from aegis.queue import InboxRouter, QueueManager
    from aegis.queue.schema import Queue as QueueSpec
    from tests.test_queue_e2e import StubSM

    sm = StubSM(autostart=False)
    sm.rebuild_fails = rebuild_fails
    inbox = InboxRouter(state_dir=tmp_path)
    sm.inbox = inbox
    minted = itertools.count(1)
    qm = QueueManager(
        {"impl": QueueSpec(name="impl", agent_profile="claude-impl",
                           max_parallel=1, max_attempts=max_attempts,
                           recoverable_ttl_s=recoverable_ttl_s)},
        sm, inbox, state_dir=tmp_path,
        handle_factory=lambda used: f"w{next(minted)}",
    )
    return qm, sm


def record_parks(qm):
    """Stand in for `QueueManager._park`, which lands in Task 8 of the
    ephemeral-agent-recovery plan. Returns the list it appends to.

    After Task 7 `_finalize` cannot produce `failed` from a bad turn end: it
    stalls and rebuilds, or parks once `max_attempts` is spent. Tests that
    pin the terminal path therefore need something on the other end of it in
    the meantime, and two of the callers they exercise BLOCK until it
    arrives — `QueueManager.run` on a `QueueCompleted`, and
    `WorkflowEngine.delegate` on an error callback — so a no-op recorder
    hangs them. This does the two things the real `_park` does that anyone
    is waiting for, and nothing else: no `recoverable` status, no `parked`
    origin, no slot release. Delete it when Task 8 lands; the tests using it
    are written to pass unchanged once it is real.
    """
    from aegis.queue.manager import _handle_of
    from aegis.queue.events import QueueCompleted
    from aegis.queue.schema import InboxMessage, sender_queue

    parked: list[tuple[str, int, str]] = []

    async def park(session, task, *, reason):
        parked.append((task.id, task.attempts, reason))
        qm._emit(
            QueueCompleted(
                task_id=task.id,
                queue=task.queue,
                outcome="recoverable",
                result=None,
                error=reason,
                completed_at=qm._now(),
            )
        )
        if task.callback:
            await qm._inbox.deliver(
                _handle_of(task.enqueued_by),
                InboxMessage(
                    sender=sender_queue(task.queue),
                    timestamp=qm._now(),
                    body=reason,
                    task_id=task.id,
                    status="error",
                ),
            )

    qm._park = park
    return parked


def worker_handle(qm, task_id):
    """The handle dispatch minted for this task. `status()` does not carry
    it, and a test that hardcoded `w1` would lie the moment a rig
    dispatched twice."""
    return qm._all[task_id].worker_handle


@pytest.fixture
def queue_rig(tmp_path):
    return make_queue_rig(tmp_path)


@pytest.fixture
def queue_rig_max_attempts_1(tmp_path):
    return make_queue_rig(tmp_path, max_attempts=1)


@pytest.fixture
def queue_rig_rebuild_always_fails(tmp_path):
    return make_queue_rig(tmp_path, max_attempts=1, rebuild_fails=True)
