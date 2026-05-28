"""skill-system plugin: pre_turn injects menu; load_skill returns body."""
from __future__ import annotations

import importlib.util
import sys
import textwrap
from pathlib import Path

import pytest

from aegis.hooks.contexts import PreTurnContext, SessionHandle
from aegis.hooks.decorator import _REGISTRY as _HOOK_REG, _reset_registry_for_tests
from aegis.tools.decorator import _REGISTRY as _TOOL_REG, _reset_registry_for_tests as _reset_tools


def _load_skill_system():
    """Manually import the plugin module so its decorators fire."""
    repo_root = Path(__file__).parent.parent
    path = repo_root / "plugins" / "skill-system" / "skill_system.py"
    spec = importlib.util.spec_from_file_location("_test_skill_system", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["_test_skill_system"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def fresh():
    _reset_registry_for_tests()
    _reset_tools()
    yield
    _reset_registry_for_tests()
    _reset_tools()


def _drop_skill(folder: Path, name: str, description: str, body: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{name}.md").write_text(textwrap.dedent(f"""\
        ---
        name: {name}
        description: {description}
        ---

        {body}
    """))


@pytest.mark.asyncio
async def test_preturn_menu_listed(tmp_path: Path, fresh) -> None:
    _drop_skill(
        tmp_path / ".aegis/skills",
        "brainstorming", "Use before any creative work.",
        "Brainstorming body.",
    )
    _drop_skill(
        tmp_path / ".aegis/skills",
        "tdd", "Use when implementing features.",
        "TDD body.",
    )

    _load_skill_system()
    pre = _HOOK_REG["pre_turn"][0].func

    ctx = PreTurnContext(
        session=SessionHandle(handle="t", agent_profile="p", harness="claude"),
        user_message="help me design a feature",
        history=(), project_root=tmp_path, prior_results=(),
    )
    result = await pre(ctx)
    assert result is not None
    assert "brainstorming" in result.prepend_system
    assert "tdd" in result.prepend_system
    assert "Use before any creative work." in result.prepend_system
    assert "load_skill" in result.prepend_system


@pytest.mark.asyncio
async def test_preturn_no_skills_returns_none(tmp_path: Path, fresh) -> None:
    _load_skill_system()
    pre = _HOOK_REG["pre_turn"][0].func
    ctx = PreTurnContext(
        session=SessionHandle(handle="t", agent_profile="p", harness="claude"),
        user_message="anything",
        history=(), project_root=tmp_path, prior_results=(),
    )
    assert await pre(ctx) is None


@pytest.mark.asyncio
async def test_load_skill_returns_body(tmp_path: Path, fresh, monkeypatch) -> None:
    _drop_skill(
        tmp_path / ".aegis/skills",
        "brainstorming", "desc", "## Brainstorming -- full body here.",
    )
    monkeypatch.chdir(tmp_path)
    _load_skill_system()
    load = _TOOL_REG["load_skill"].func
    body = await load(name="brainstorming")
    assert "## Brainstorming" in body
    assert "full body here." in body


@pytest.mark.asyncio
async def test_load_skill_unknown_raises(tmp_path: Path, fresh, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _load_skill_system()
    load = _TOOL_REG["load_skill"].func
    with pytest.raises(FileNotFoundError):
        await load(name="never-existed")
