"""Unit tests for AegisServer workflow tools.

Tests the server-level workflow management:
- Workflow decorator registration
- Task lifecycle and cleanup
- Global exception handling (basic)
"""

import pytest
from aegis.server import AegisServer, WorkflowContext


class TestWorkflowDecorator:
    """Tests for @server.workflow() decorator."""

    @pytest.fixture
    def server(self):
        """Create server for testing decorator."""
        return AegisServer()

    def test_workflow_decorator_registers_workflow(self, server):
        """@server.workflow() should register the workflow."""
        @server.workflow()
        async def my_workflow(ctx: WorkflowContext):
            """Test workflow docstring."""
            pass

        assert "my_workflow" in server._workflows
        func, max_retries = server._workflows["my_workflow"]
        assert callable(func)
        assert max_retries == 3  # default

    def test_workflow_decorator_with_custom_name(self, server):
        """@server.workflow(name=...) should use custom name."""
        @server.workflow(name="custom_name")
        async def other_func(ctx: WorkflowContext):
            pass

        assert "custom_name" in server._workflows
        assert "other_func" not in server._workflows

    def test_workflow_decorator_with_custom_max_retries(self, server):
        """@server.workflow(max_retries=...) should use custom value."""
        @server.workflow(max_retries=10)
        async def long_retry(ctx: WorkflowContext):
            pass

        func, max_retries = server._workflows["long_retry"]
        assert max_retries == 10

    def test_workflow_decorator_without_name_uses_function_name(self, server):
        """When no name provided, use function's __name__."""
        @server.workflow()
        async def auto_named(ctx: WorkflowContext):
            pass

        assert "auto_named" in server._workflows


class TestWorkflowsMetadata:
    """Tests for workflow storage and metadata."""

    @pytest.fixture
    def server(self):
        srv = AegisServer()

        @srv.workflow(max_retries=5)
        async def custom_workflow(ctx: WorkflowContext):
            """A custom retry workflow."""
            pass

        return srv

    def test_workflow_stores_tuple_of_func_and_retries(self, server):
        """Workflows should be stored as (func, max_retries) tuple."""
        func, max_retries = server._workflows["custom_workflow"]
        assert callable(func)
        assert max_retries == 5

    def test_multiple_workflows_registered(self, server):
        """Multiple workflows can be registered."""
        @server.workflow()
        async def workflow2(ctx: WorkflowContext):
            pass

        assert len(server._workflows) == 2
        assert "custom_workflow" in server._workflows
        assert "workflow2" in server._workflows


class TestWorkflowContextCreation:
    """Tests for WorkflowContext initialization."""

    def test_context_has_default_values(self):
        """WorkflowContext should have sensible defaults."""
        ctx = WorkflowContext()

        assert ctx.cwd == "."
        assert ctx.max_retries == 3
        assert ctx.retry_count == 0
        assert ctx.in_queue is not None
        assert ctx.out_queue is not None

    def test_context_with_custom_values(self):
        """WorkflowContext should accept custom values."""
        ctx = WorkflowContext(cwd="/custom/path", max_retries=10)

        assert ctx.cwd == "/custom/path"
        assert ctx.max_retries == 10

    def test_reset_retry_clears_count(self):
        """reset_retry should set retry_count to 0."""
        ctx = WorkflowContext()
        ctx.retry_count = 5

        ctx.reset_retry()

        assert ctx.retry_count == 0