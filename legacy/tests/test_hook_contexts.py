"""Frozen-dataclass behavior + field shape for hook context types."""
from __future__ import annotations

from pathlib import Path

import pytest

from aegis.hooks.contexts import (
    PostTurnEvent, PreTurnContext, PreTurnResult,
    SessionEndEvent, SessionHandle, SessionStartEvent, Turn,
)


def test_preturn_context_is_frozen() -> None:
    ctx = PreTurnContext(
        session=SessionHandle(handle="lucid-knuth", agent_profile="claude-sonnet", harness="claude"),
        user_message="hello",
        history=(),
        project_root=Path("/tmp"),
        prior_results=(),
    )
    with pytest.raises((AttributeError, Exception)):
        ctx.user_message = "no"


def test_preturn_result_all_fields_default_none() -> None:
    r = PreTurnResult()
    assert r.prepend_system is None
    assert r.rewrite_user is None
    assert r.block is None
    assert r.extend_history is None


def test_session_handle_carries_harness() -> None:
    h = SessionHandle(handle="x", agent_profile="p", harness="claude")
    assert h.harness == "claude"


def test_turn_carries_role_and_content() -> None:
    t = Turn(role="user", content="hi")
    assert t.role == "user"
    assert t.content == "hi"
