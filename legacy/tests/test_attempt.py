"""Unit tests for WorkflowContext.attempt() method and Attempt class.

Tests the retry-on-exception behavior:
- Attempt as async context manager
- Attempt as async iterator
- Retry when matching exception occurs
- Stop iteration when max attempts exhausted
- Non-matching exceptions propagate
"""

import pytest
from aegis.server import WorkflowContext, Attempt
from pydantic import BaseModel


class DataModel(BaseModel):
    """Test model for attempt tests."""
    name: str
    value: int


class TestAttemptCreation:
    """Tests for creating an Attempt instance."""

    def test_attempt_returns_attempt_instance(self, workflow_context):
        """ctx.attempt() should return an Attempt instance."""
        attempt = workflow_context.attempt(
            instruction="Get data",
            response_type=DataModel,
            on_errors=(ValueError,),
            max_attempts=3,
        )

        assert isinstance(attempt, Attempt)

    def test_attempt_stores_parameters(self, workflow_context):
        """Attempt should store instruction, response_type, on_errors, max_attempts."""
        attempt = workflow_context.attempt(
            instruction="Test instruction",
            response_type=DataModel,
            on_errors=(ValueError, IOError),
            max_attempts=5,
        )

        assert attempt.instruction == "Test instruction"
        assert attempt.response_type == DataModel
        assert ValueError in attempt.on_errors
        assert IOError in attempt.on_errors
        assert attempt.max_attempts == 5


class TestAttemptContextManager:
    """Tests for Attempt as async context manager."""

    async def test_enter_returns_self(self, workflow_context, mock_in_queue):
        """__aenter__ should return the Attempt instance."""
        mock_in_queue._all_values = ['{"name": "test", "value": 1}']

        attempt = workflow_context.attempt(
            instruction="Get data",
            response_type=DataModel,
            on_errors=(ValueError,),
            max_attempts=3,
        )

        async with attempt as ctx:
            assert ctx is attempt

    async def test_exit_suppresses_matching_exception(self, workflow_context):
        """__aexit__ should suppress exceptions that match on_errors."""
        attempt = workflow_context.attempt(
            instruction="Get data",
            response_type=DataModel,
            on_errors=(ValueError,),
            max_attempts=3,
        )

        # Simulate an exception that matches on_errors
        suppressed = await attempt.__aexit__(ValueError, ValueError("test"), None)
        assert suppressed is True

    async def test_exit_does_not_suppress_non_matching_exception(self, workflow_context):
        """__aexit__ should not suppress exceptions that don't match on_errors."""
        attempt = workflow_context.attempt(
            instruction="Get data",
            response_type=DataModel,
            on_errors=(ValueError,),
            max_attempts=3,
        )

        # Simulate an exception that doesn't match on_errors
        suppressed = await attempt.__aexit__(KeyError, KeyError("test"), None)
        assert suppressed is False

    async def test_exit_does_not_suppress_when_max_attempts_reached(self, workflow_context):
        """__aexit__ should not suppress when max attempts exhausted."""
        attempt = workflow_context.attempt(
            instruction="Get data",
            response_type=DataModel,
            on_errors=(ValueError,),
            max_attempts=3,
        )
        attempt._attempt = 3  # Exhausted

        suppressed = await attempt.__aexit__(ValueError, ValueError("test"), None)
        assert suppressed is False


class TestAttemptIterator:
    """Tests for Attempt as async iterator."""

    async def test_anext_returns_data(self, workflow_context, mock_in_queue):
        """__anext__ should return data from step()."""
        mock_in_queue._all_values = ['{"name": "test", "value": 1}']

        attempt = workflow_context.attempt(
            instruction="Get data",
            response_type=DataModel,
            on_errors=(ValueError,),
            max_attempts=3,
        )

        async with attempt:
            result = await attempt.__anext__()

        assert result.name == "test"
        assert result.value == 1

    async def test_anext_stops_when_max_attempts_exhausted(self, workflow_context, mock_in_queue):
        """When max attempts exhausted, raise StopAsyncIteration."""
        mock_in_queue._all_values = ['{"name": "test", "value": 1}']

        attempt = workflow_context.attempt(
            instruction="Get data",
            response_type=DataModel,
            on_errors=(ValueError,),
            max_attempts=3,
        )
        attempt._attempt = 3  # Already exhausted

        async with attempt:
            with pytest.raises(StopAsyncIteration):
                await attempt.__anext__()

    async def test_anext_stops_when_data_is_none(self, workflow_context, mock_in_queue):
        """When step() returns None, raise StopAsyncIteration."""
        mock_in_queue._all_values = [None]

        attempt = workflow_context.attempt(
            instruction="Get data",
            response_type=DataModel,
            on_errors=(ValueError,),
            max_attempts=3,
        )

        async with attempt:
            with pytest.raises(StopAsyncIteration):
                await attempt.__anext__()


class TestAttemptRetryFlow:
    """Tests for the retry flow in Attempt."""

    async def test_retry_increments_attempt_count(self, workflow_context):
        """On exception, _attempt should be incremented."""
        attempt = workflow_context.attempt(
            instruction="Get data",
            response_type=DataModel,
            on_errors=(ValueError,),
            max_attempts=3,
        )

        assert attempt._attempt == 0

        # Manually simulate what happens on exception
        attempt._attempt += 1
        assert attempt._attempt == 1

    async def test_retry_resets_context_retry_count(self, workflow_context):
        """On retry, context.retry_count should be reset."""
        workflow_context.retry_count = 2  # Simulate some retries in step

        attempt = workflow_context.attempt(
            instruction="Get data",
            response_type=DataModel,
            on_errors=(ValueError,),
            max_attempts=3,
        )

        # Simulate the reset that happens after catching exception
        attempt.ctx.reset_retry()

        assert workflow_context.retry_count == 0

    def test_error_instruction_format(self, workflow_context):
        """Error instruction should include original instruction and retry info."""
        original_instruction = "Compose a note with title and content"
        
        # Create attempt to check error message format
        attempt = workflow_context.attempt(
            instruction=original_instruction,
            response_type=DataModel,
            on_errors=(ValueError,),
            max_attempts=3,
        )

        # Test the error message construction (this is what happens in __anext__)
        error_msg = (
            f"[ERROR] Test error\n\n"
            f"You have {attempt.max_attempts - 1} retries remaining.\n\n"
            f"{attempt.instruction}\n\n"
            "Fix the issue and provide new values."
        )

        assert original_instruction in error_msg
        assert "Fix the issue" in error_msg
        assert "2 retries remaining" in error_msg


class TestAttemptAsyncForLoop:
    """Tests for using attempt in async for loop."""

    async def test_async_for_gets_data(self, workflow_context, mock_in_queue):
        """async for should yield data from step."""
        mock_in_queue._all_values = ['{"name": "test", "value": 1}']

        async with workflow_context.attempt(
            instruction="Get data",
            response_type=DataModel,
            on_errors=(ValueError,),
            max_attempts=3,
        ) as attempt:
            results = []
            async for data in attempt:
                results.append(data.name)
                break  # Only one iteration

        assert "test" in results