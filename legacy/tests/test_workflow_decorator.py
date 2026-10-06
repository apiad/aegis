from __future__ import annotations

import pytest

from aegis.config import ConfigError
from aegis.workflow import (
    WorkflowError, get_workflow, list_workflows, workflow,
)
from aegis.workflow.decorator import _REGISTRY


@pytest.fixture(autouse=True)
def clean_registry():
    """Each test sees a fresh registry."""
    _REGISTRY.clear()
    yield
    _REGISTRY.clear()


def test_workflow_decorator_registers_under_function_name():
    @workflow
    async def my_flow(engine, *, x):
        return x
    assert "my_flow" in list_workflows()
    assert get_workflow("my_flow") is my_flow


def test_workflow_decorator_rejects_non_async():
    with pytest.raises(TypeError, match="must be async"):
        @workflow
        def sync_flow(engine):
            return None


def test_workflow_decorator_rejects_missing_engine_param():
    with pytest.raises(TypeError, match="first parameter must be 'engine'"):
        @workflow
        async def no_engine(x):
            return x


def test_workflow_decorator_rejects_name_collision():
    @workflow
    async def dup(engine):
        return None
    with pytest.raises(ConfigError, match="dup"):
        @workflow
        async def dup(engine):                          # noqa: F811
            return None


def test_get_workflow_unknown_returns_none():
    assert get_workflow("ghost") is None


def test_workflow_error_is_exception():
    assert issubclass(WorkflowError, Exception)
