"""Shared test fixtures and configuration."""

import pytest
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from asyncio import Queue


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


from typing import Any