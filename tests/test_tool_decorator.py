"""Tool registration: decorator, registry, name collisions."""
from __future__ import annotations

import pytest

from aegis.tools import _REGISTRY, tool
from aegis.tools.decorator import _reset_registry_for_tests


@pytest.fixture(autouse=True)
def _clean():
    _reset_registry_for_tests()
    yield
    _reset_registry_for_tests()


def test_tool_registers_under_function_name() -> None:
    @tool
    async def load_skill(name: str) -> str:
        """Load a skill body."""
        return ""
    assert "load_skill" in _REGISTRY
    assert _REGISTRY["load_skill"].func.__name__ == "load_skill"


def test_explicit_name_override() -> None:
    @tool(name="custom_name")
    async def foo() -> str:
        return ""
    assert "custom_name" in _REGISTRY
    assert "foo" not in _REGISTRY


def test_duplicate_name_fails_loud() -> None:
    @tool
    async def x() -> str: return ""
    with pytest.raises(ValueError, match="duplicate tool"):
        @tool
        async def x() -> str:  # noqa: F811
            return ""


def test_collision_with_aegis_builtin_fails_loud() -> None:
    with pytest.raises(ValueError, match="reserved"):
        @tool(name="aegis_enqueue")
        async def x() -> str:
            return ""


def test_default_timeout_30s_explicit_override() -> None:
    @tool
    async def a() -> str: return ""
    @tool(timeout=10.0)
    async def b() -> str: return ""
    assert _REGISTRY["a"].timeout == 30.0
    assert _REGISTRY["b"].timeout == 10.0


def test_sync_function_also_supported() -> None:
    @tool
    def plain() -> str:
        return "hi"
    assert "plain" in _REGISTRY
