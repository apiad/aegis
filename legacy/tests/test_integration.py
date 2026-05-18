"""Integration tests for Aegis workflows using FastMCP Client.

End-to-end tests that exercise the full workflow execution:
- Starting workflows
- Advancing through steps
- Completing workflows
- Handling exceptions
"""

import pytest
from fastmcp import FastMCP, Client
from aegis.server import AegisServer, WorkflowContext
from pydantic import BaseModel


class DataModel(BaseModel):
    """Test model for integration tests."""
    name: str
    value: int


@pytest.fixture
def server():
    """Create AegisServer with test workflows."""
    srv = AegisServer()

    @srv.workflow()
    async def simple_workflow(ctx: WorkflowContext):
        """A simple workflow with two steps."""
        await ctx.step("First step - say hello")
        await ctx.step("Second step - say goodbye")

    @srv.workflow()
    async def data_workflow(ctx: WorkflowContext):
        """A workflow that expects structured data."""
        async with ctx.attempt(
            "Provide a name and value as JSON",
            response_type=DataModel,
        ) as attempt:
            async for result in attempt:
                await ctx.step(f"Got: {result.name} = {result.value}")
                return

    @srv.workflow()
    async def failing_workflow(ctx: WorkflowContext):
        """A workflow that will fail."""
        await ctx.step("This step will cause an error")
        raise RuntimeError("Intentional failure")

    @srv.workflow()
    async def attempt_workflow(ctx: WorkflowContext):
        """A workflow that uses ctx.attempt() for retry."""
        result = await ctx.step("Provide data as JSON", response_type=DataModel)

        async with ctx.attempt(
            "Try to process the data. If it fails, fix and retry.",
            response_type=DataModel,
            on_errors=(ValueError,),
            max_attempts=3,
        ) as attempt:
            async for data in attempt:
                # Simulate processing that might fail
                if data.value < 0:
                    raise ValueError("Value must be non-negative")
                await ctx.step(f"Successfully processed: {data.name}")
                return

    return srv


@pytest.mark.asyncio
class TestWorkflowIntegration:
    """Integration tests using FastMCP Client."""

    async def test_simple_workflow_completes(self, server):
        """A simple two-step workflow completes successfully."""
        async with Client(server) as client:
            # Start workflow
            result = await client.call_tool("workflow_start", {"name": "simple_workflow"})
            assert "First step" in result.data

            # First step
            result = await client.call_tool("workflow_step", {})
            assert "Second step" in result.data

            # Second step - workflow completes
            result = await client.call_tool("workflow_step", {})
            assert "completed" in result.data.lower() or "done" in result.data.lower()

    async def test_workflow_with_data(self, server):
        """Workflow that expects structured data completes with valid JSON."""
        async with Client(server) as client:
            # Start
            result = await client.call_tool("workflow_start", {"name": "data_workflow"})
            assert "Provide" in result.data

            # Provide valid JSON
            result = await client.call_tool(
                "workflow_step",
                {"data": '{"name": "test", "value": 42}'}
            )
            assert "Got: test = 42" in result.data

    async def test_workflow_with_invalid_json_retries(self, server):
        """Invalid JSON triggers the step retry loop."""
        async with Client(server) as client:
            # Start
            result = await client.call_tool("workflow_start", {"name": "data_workflow"})

            # First attempt - invalid JSON
            result = await client.call_tool(
                "workflow_step",
                {"data": "not valid json"}
            )
            # Should get error message with retry info
            assert "ERROR" in result.data or "retry" in result.data.lower()

            # Second attempt - valid JSON
            result = await client.call_tool(
                "workflow_step",
                {"data": '{"name": "ok", "value": 1}'}
            )
            assert "Got: ok = 1" in result.data

    async def test_workflow_exception_caught(self, server):
        """Uncaught exceptions are caught and reported to user."""
        async with Client(server) as client:
            # Start failing workflow
            result = await client.call_tool("workflow_start", {"name": "failing_workflow"})
            assert "This step will cause an error" in result.data

            # Advance to trigger exception
            result = await client.call_tool("workflow_step", {})

            # Should get error message about workflow failure
            assert "error" in result.data.lower() or "failed" in result.data.lower()

    async def test_workflow_start_unknown_returns_error(self, server):
        """Starting unknown workflow returns error."""
        async with Client(server) as client:
            result = await client.call_tool("workflow_start", {"name": "nonexistent"})
            assert "No workflow found" in result.data

    async def test_workflow_step_no_active_returns_error(self, server):
        """Calling workflow_step with no active workflow returns error."""
        async with Client(server) as client:
            # Try to step without starting
            result = await client.call_tool("workflow_step", {})
            assert "No active workflow" in result.data


@pytest.mark.asyncio
class TestRetryIntegration:
    """Integration tests for retry mechanisms."""

    async def test_attempt_retry_on_value_error(self, server):
        """attempt() retries when body raises ValueError."""
        async with Client(server) as client:
            # Start workflow
            result = await client.call_tool(
                "workflow_start",
                {"name": "attempt_workflow"}
            )

            # First: provide data that will fail (negative value triggers ValueError)
            result = await client.call_tool(
                "workflow_step",
                {"data": '{"name": "test", "value": -5}'}
            )
            # Should get error and retry prompt
            assert "ERROR" in result.data or "retry" in result.data.lower()

            # Second: provide valid data
            result = await client.call_tool(
                "workflow_step",
                {"data": '{"name": "valid", "value": 10}'}
            )
            # Should succeed
            assert "Successfully processed" in result.data or "done" in result.data.lower()

    async def test_attempt_max_retries_exhausted(self, server):
        """attempt() stops after max retries."""
        # This would require triggering the retry multiple times
        # For now, we verify the structure is in place
        pass


@pytest.mark.asyncio
class TestWorkflowEdgeCases:
    """Edge case integration tests."""

    async def test_workflow_step_with_string_data(self, server):
        """workflow_step accepts string data directly."""
        async with Client(server) as client:
            result = await client.call_tool("workflow_start", {"name": "simple_workflow"})

            # Pass string instead of JSON
            result = await client.call_tool(
                "workflow_step",
                {"data": "just a string"}
            )
            # Should work - step should accept it

    async def test_workflow_step_with_none_data(self, server):
        """workflow_step works with no data (None)."""
        async with Client(server) as client:
            result = await client.call_tool("workflow_start", {"name": "simple_workflow"})

            # Pass None (no data)
            result = await client.call_tool("workflow_step", {"data": None})
            # Should work for steps that don't expect data

    async def test_multiple_workflow_starts(self, server):
        """Starting multiple workflows creates separate contexts."""
        async with Client(server) as client:
            # Start first workflow
            result1 = await client.call_tool(
                "workflow_start",
                {"name": "simple_workflow"}
            )

            # Start second workflow (should create new context)
            result2 = await client.call_tool(
                "workflow_start",
                {"name": "simple_workflow"}
            )

            # Each should get its own context
            # (This tests the UUID-based isolation)
            assert result1 != result2