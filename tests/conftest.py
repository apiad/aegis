"""Shared test fixtures and configuration."""

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
