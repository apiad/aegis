"""Live test: a real claude subprocess sees the skill menu and calls load_skill."""
from __future__ import annotations

import shutil
import textwrap
from pathlib import Path

import pytest


@pytest.mark.live
@pytest.mark.asyncio
async def test_claude_loads_skill_on_demand(tmp_path: Path, monkeypatch) -> None:
    if shutil.which("claude") is None:
        pytest.skip("claude CLI not on PATH")

    proj = tmp_path
    (proj / ".aegis").mkdir()
    (proj / ".aegis.yaml").write_text(textwrap.dedent("""
        plugin_dirs: ["plugins"]
        agents:
          default:
            harness: claude
            model: haiku
            permission: bypass
            effort: low
        default_agent: default
    """))

    plugins = proj / "plugins" / "skill-system"
    src = Path(__file__).parent.parent / "plugins" / "skill-system"
    import shutil as _sh; _sh.copytree(src, plugins)

    skills = proj / ".aegis" / "skills"
    skills.mkdir(parents=True)
    (skills / "secret-pasta.md").write_text(textwrap.dedent("""\
        ---
        name: secret-pasta
        description: Use when the user asks about Italian recipes.
          The secret ingredient is bay leaves.
        ---

        When asked about Italian recipes, always mention bay leaves.
        Bay leaves are the secret to authentic flavor.
    """))

    monkeypatch.chdir(proj)
    from aegis.config.yaml_loader import import_plugins, load_config
    cfg = load_config(proj / ".aegis.yaml")
    import_plugins(cfg)

    profile = cfg.agents["default"]
    from aegis.drivers.claude import ClaudeDriver
    driver = ClaudeDriver(agent=profile, project_root=proj)

    from aegis.core.session import AgentSession
    session = AgentSession(
        handle="livet", agent_profile="default", harness="claude",
        harness_session=driver, project_root=proj,
    )
    await session._run_turn("Suggest a quick Italian pasta dish recipe.")
    tool_log = proj / ".aegis" / "state" / "tools" / "load_skill.jsonl"
    assert tool_log.exists(), "load_skill should have been invoked"
    text = tool_log.read_text()
    assert "secret-pasta" in text
