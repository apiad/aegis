"""Hook registration: decorator, registry, name collisions, strict flag."""
from __future__ import annotations

import pytest

from aegis.hooks import _REGISTRY, hook
from aegis.hooks.decorator import HookEntry, _reset_registry_for_tests


@pytest.fixture(autouse=True)
def _clean() -> None:
    _reset_registry_for_tests()
    yield
    _reset_registry_for_tests()


def test_hook_registers() -> None:
    @hook("pre_turn")
    async def my_hook(ctx):
        return None

    entries = _REGISTRY["pre_turn"]
    assert len(entries) == 1
    assert entries[0].func is my_hook
    assert entries[0].strict is False


def test_strict_flag_is_recorded() -> None:
    @hook("pre_turn", strict=True)
    async def my_hook(ctx):
        return None

    assert _REGISTRY["pre_turn"][0].strict is True


def test_unknown_event_fails_loud() -> None:
    with pytest.raises(ValueError, match="unknown hook event"):
        @hook("not_a_real_event")
        async def my_hook(ctx):
            return None


def test_duplicate_qualified_name_fails_loud() -> None:
    @hook("pre_turn")
    async def my_hook(ctx):
        return None

    with pytest.raises(ValueError, match="duplicate hook"):
        @hook("pre_turn")
        async def my_hook(ctx):  # noqa: F811
            return None


def test_observer_events_register() -> None:
    @hook("post_turn")
    async def a(ev): return None

    @hook("session_start")
    async def b(ev): return None

    @hook("session_end")
    async def c(ev): return None

    assert len(_REGISTRY["post_turn"]) == 1
    assert len(_REGISTRY["session_start"]) == 1
    assert len(_REGISTRY["session_end"]) == 1


def test_entries_preserve_declaration_order() -> None:
    @hook("pre_turn")
    async def first(ctx): return None

    @hook("pre_turn")
    async def second(ctx): return None

    @hook("pre_turn")
    async def third(ctx): return None

    funcs = [e.func.__name__ for e in _REGISTRY["pre_turn"]]
    assert funcs == ["first", "second", "third"]
