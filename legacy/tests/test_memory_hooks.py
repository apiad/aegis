"""Hermetic tests for memory_system pre_turn / session_start hooks."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from aegis.hooks.contexts import (
    PreTurnContext, PreTurnResult, SessionHandle, Turn,
)
from aegis.hooks.decorator import _reset_registry_for_tests as _reset_hooks
from aegis.tools.decorator import _reset_registry_for_tests as _reset_tools


@pytest.fixture(autouse=True)
def _isolate_registries():
    _reset_hooks(); _reset_tools()
    yield
    _reset_hooks(); _reset_tools()


SH = SessionHandle(handle="test-handle",
                   agent_profile="test", harness="claude")


def _load(monkeypatch, tmp_path: Path):
    _reset_hooks()
    _reset_tools()
    monkeypatch.chdir(tmp_path)
    repo_root = Path(__file__).parent.parent
    path = repo_root / "plugins" / "memory-system" / "memory_system.py"
    spec = importlib.util.spec_from_file_location("_test_memory_system", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["_test_memory_system"] = module
    spec.loader.exec_module(module)
    return module


def _ctx(tmp_path: Path, message: str, history: tuple[Turn, ...] = ()) -> PreTurnContext:
    return PreTurnContext(
        session=SH, user_message=message,
        history=history, project_root=tmp_path,
    )


@pytest.mark.asyncio
async def test_turn_zero_injects_soul_user_index_primer(
        tmp_path, monkeypatch) -> None:
    m = _load(monkeypatch, tmp_path)
    (tmp_path / m.MEMORY_SUBDIR).mkdir(parents=True)
    (tmp_path / m.MEMORY_SUBDIR / "SOUL.md").write_text("# Voice\n\nConcise.\n")
    (tmp_path / m.MEMORY_SUBDIR / "USER.md").write_text("User: Alex.\n")
    m.write_entry(tmp_path, "user", "name", "Goes by Alex", "body")
    m.rebuild_index(tmp_path)
    result = await m.inject_memory(_ctx(tmp_path, "hello"))
    assert isinstance(result, PreTurnResult)
    text = result.prepend_system
    assert "Concise." in text
    assert "User: Alex." in text
    assert "## Index" in text or "## Memory index" in text
    assert "[name](entries/user_name.md)" in text
    assert "# Memory" in text  # the primer header


@pytest.mark.asyncio
async def test_turn_zero_skips_missing_files(tmp_path, monkeypatch) -> None:
    m = _load(monkeypatch, tmp_path)
    result = await m.inject_memory(_ctx(tmp_path, "hello"))
    # Nothing on disk → primer-only injection
    assert isinstance(result, PreTurnResult)
    assert "# Memory" in result.prepend_system


@pytest.mark.asyncio
async def test_turn_ge_one_injects_top_5_teasers(tmp_path, monkeypatch) -> None:
    m = _load(monkeypatch, tmp_path)
    m.write_entry(tmp_path, "feedback", "load-bearing",
                  "no load-bearing phrase", "avoid it")
    m.write_entry(tmp_path, "user", "name",
                  "Goes by Alex", "use Alex")
    history = (Turn(role="user", content="prior"),
               Turn(role="assistant", content="ok"))
    result = await m.inject_memory(
        _ctx(tmp_path, "let's drop the load-bearing thing", history))
    assert isinstance(result, PreTurnResult)
    text = result.prepend_system
    assert "## Possibly relevant memory" in text
    assert "load-bearing" in text
    # Body NOT included — only name + description
    assert "avoid it" not in text


@pytest.mark.asyncio
async def test_turn_ge_one_returns_none_when_no_match(
        tmp_path, monkeypatch) -> None:
    m = _load(monkeypatch, tmp_path)
    m.write_entry(tmp_path, "user", "name", "Goes by Alex", "use Alex")
    history = (Turn(role="user", content="prior"),)
    result = await m.inject_memory(_ctx(tmp_path, "xyzzy nothing matches", history))
    assert result is None


@pytest.mark.asyncio
async def test_turn_ge_one_caps_word_count(tmp_path, monkeypatch) -> None:
    m = _load(monkeypatch, tmp_path)
    # 20 entries, each with a long description matching "widget"
    for i in range(20):
        m.write_entry(tmp_path, "fact", f"w{i}",
                      "widget " + " ".join(["lorem"] * 200),
                      "body")
    history = (Turn(role="user", content="prior"),)
    result = await m.inject_memory(_ctx(tmp_path, "tell me about widgets", history))
    assert result is not None
    word_count = len(result.prepend_system.split())
    assert word_count <= 1000
