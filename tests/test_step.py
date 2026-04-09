"""Unit tests for WorkflowContext.step() method.

Tests the validation retry loop behavior of the step method:
- Valid JSON parsing returns parsed data
- Invalid JSON triggers retry with error message
- None response when expected triggers retry
- Data provided when not expected triggers retry
- Max retries exhausted raises MaxRetriesExceededError
"""

import pytest
from unittest.mock import patch, MagicMock
from aegis.server import WorkflowContext, MaxRetriesExceededError


class TestStepWithResponseType:
    """Tests for step() when response_type is specified."""

    async def test_step_with_valid_json_returns_parsed_model(
        self, workflow_context, mock_in_queue
    ):
        """When valid JSON is provided, return parsed Pydantic model."""
        from pydantic import BaseModel

        class TestData(BaseModel):
            name: str
            value: int

        mock_in_queue._all_values = ['{"name": "test", "value": 42}']

        result = await workflow_context.step("Get data", response_type=TestData)

        assert result is not None
        assert result.name == "test"
        assert result.value == 42

    async def test_step_with_invalid_json_triggers_retry(
        self, workflow_context, mock_in_queue, mock_out_queue
    ):
        """When invalid JSON is provided, retry loop with error message."""
        from pydantic import BaseModel

        class TestData(BaseModel):
            name: str

        # First response invalid JSON, second is valid
        mock_in_queue._all_values = ["invalid json", '{"name": "ok"}']

        result = await workflow_context.step("Get data", response_type=TestData)

        assert result is not None
        assert result.name == "ok"
        # Should have put error message in out_queue
        puts = mock_out_queue.get_put_values()
        # First put is original instruction, second is error message
        assert len(puts) >= 2

    async def test_step_with_none_response_triggers_retry(
        self, workflow_context, mock_in_queue, mock_out_queue
    ):
        """When response_type is set but user sends None, trigger retry."""
        from pydantic import BaseModel

        class TestData(BaseModel):
            name: str

        # First response None (error), second response valid JSON
        mock_in_queue._all_values = [None, '{"name": "ok"}']

        result = await workflow_context.step("Get data", response_type=TestData)

        assert result is not None
        assert result.name == "ok"

    async def test_step_validation_error_includes_retries_remaining(
        self, workflow_context, mock_in_queue, mock_out_queue
    ):
        """Error message should include remaining retry count."""
        from pydantic import BaseModel

        class TestData(BaseModel):
            name: str

        mock_in_queue._all_values = ["invalid", '{"name": "ok"}']

        await workflow_context.step("Get data", response_type=TestData)

        puts = mock_out_queue.get_put_values()
        # Second put (error message after first failure) should have retry count
        assert len(puts) >= 2
        assert "retry" in puts[1].lower()


class TestStepMaxRetries:
    """Tests for step() max retry behavior."""

    async def test_step_max_retries_exhausted_raises_error(
        self, workflow_context, mock_in_queue
    ):
        """When max retries exhausted, raise MaxRetriesExceededError."""
        from pydantic import BaseModel

        class TestData(BaseModel):
            name: str

        # max_retries is 3, so we need 4 invalid responses
        mock_in_queue._all_values = ["invalid"] * 4

        with pytest.raises(MaxRetriesExceededError) as exc_info:
            await workflow_context.step("Get data", response_type=TestData)

        assert "Step failed after" in str(exc_info.value)
        assert "3 retries" in str(exc_info.value)

    async def test_step_retries_reset_after_success(
        self, workflow_context, mock_in_queue, mock_out_queue
    ):
        """Retry count should reset to 0 after successful parse."""
        from pydantic import BaseModel

        class TestData(BaseModel):
            name: str

        # First step: invalid, then valid (retry succeeds)
        # We need to reset the queue between calls because step() reads multiple times
        mock_in_queue._all_values = ["invalid", '{"name": "first"}']

        result1 = await workflow_context.step("First", response_type=TestData)
        assert result1.name == "first"
        assert workflow_context.retry_count == 0


class TestStepNoResponseType:
    """Tests for step() when response_type is None (no data expected)."""

    async def test_step_no_response_type_with_none_returns_none(
        self, workflow_context, mock_in_queue, mock_out_queue
    ):
        """When response_type is None and user provides no data (None), return None."""
        mock_in_queue._all_values = [None]

        result = await workflow_context.step("Say hello")

        assert result is None

    async def test_step_no_response_type_with_data_provides_triggers_retry(
        self, workflow_context, mock_in_queue, mock_out_queue
    ):
        """When response_type is None but user provides data, trigger retry loop."""
        # First response has data (error), second response is None (success)
        mock_in_queue._all_values = ["some data", None]

        result = await workflow_context.step("Do something")

        # Should have gotten error message in first put, then succeeded on retry
        puts = mock_out_queue.get_put_values()
        assert len(puts) >= 1
        # Second put should have the original instruction (after retry failed)
        assert "[ERROR]" in puts[-1] or "Expected no data" in str(puts)
        assert result is None  # Second response was None


class TestStepEdgeCases:
    """Edge case tests for step()."""

    async def test_step_with_empty_string_triggers_retry(
        self, workflow_context, mock_in_queue, mock_out_queue
    ):
        """Empty string when response_type is set triggers retry (no valid JSON)."""
        from pydantic import BaseModel

        class TestData(BaseModel):
            name: str

        mock_in_queue._all_values = ["", '{"name": "ok"}']

        result = await workflow_context.step("Get data", response_type=TestData)

        # Empty string is not valid JSON, so triggers retry
        assert result is not None
        assert result.name == "ok"

    async def test_step_with_complex_valid_json(
        self, workflow_context, mock_in_queue
    ):
        """Complex nested JSON should parse correctly."""
        from pydantic import BaseModel
        from typing import Optional

        class ComplexData(BaseModel):
            name: str
            items: list[str]
            metadata: Optional[dict] = None

        complex_json = '{"name": "test", "items": ["a", "b"], "metadata": {"key": "val"}}'
        mock_in_queue._all_values = [complex_json]

        result = await workflow_context.step("Get complex", response_type=ComplexData)

        assert result.name == "test"
        assert result.items == ["a", "b"]
        assert result.metadata == {"key": "val"}

    async def test_step_cwd_passed_correctly(self, workflow_context):
        """WorkflowContext should pass cwd to step."""
        assert workflow_context.cwd == "/tmp/test"

    async def test_step_max_retries_configurable(self, mock_in_queue, mock_out_queue):
        """max_retries should be configurable in WorkflowContext."""
        from aegis.server import WorkflowContext

        ctx = WorkflowContext(cwd="/tmp", max_retries=5)
        ctx.in_queue = mock_in_queue
        ctx.out_queue = mock_out_queue

        assert ctx.max_retries == 5
        assert ctx.retry_count == 0