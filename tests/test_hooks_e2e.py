"""End-to-end: plugin folder with hooks at all four events loads, runs."""
from __future__ import annotations

import asyncio
import textwrap
from pathlib import Path

import pytest

from aegis.config.yaml_loader import AegisConfig, import_plugins
from aegis.hooks.decorator import _REGISTRY, _reset_registry_for_tests
from tests.test_session_hook_wiring import FakeHarnessSession


@pytest.fixture(autouse=True)
def _clean():
    _reset_registry_for_tests()
    yield
    _reset_registry_for_tests()


def _drop_plugin(plug_root: Path, marker: Path) -> None:
    p = plug_root / "test-plugin"
    p.mkdir(parents=True)
    (p / "plugin.toml").write_text(
        '[plugin]\nname = "test-plugin"\nversion = "0.0.1"\n'
    )
    (p / "hooks.py").write_text(textwrap.dedent(f"""
        from aegis.hooks import hook, PreTurnResult
        from pathlib import Path

        MARKER = Path({str(marker)!r})

        @hook("pre_turn")
        async def pre(ctx):
            MARKER.write_text((MARKER.read_text() if MARKER.exists() else "") + "pre\\n")
            return PreTurnResult(prepend_system="HELLO")

        @hook("post_turn")
        async def post(ev):
            MARKER.write_text((MARKER.read_text() if MARKER.exists() else "") + "post\\n")

        @hook("session_start")
        async def s_start(ev):
            MARKER.write_text((MARKER.read_text() if MARKER.exists() else "") + "start\\n")

        @hook("session_end")
        async def s_end(ev):
            MARKER.write_text((MARKER.read_text() if MARKER.exists() else "") + "end\\n")
    """))


@pytest.mark.asyncio
async def test_full_lifecycle_fires_all_events(tmp_path: Path) -> None:
    marker = tmp_path / "marker.txt"
    plug = tmp_path / "plugins"
    _drop_plugin(plug, marker)

    cfg = AegisConfig(plugin_dirs=[plug])
    import_plugins(cfg)
    assert len(_REGISTRY["pre_turn"]) == 1
    assert len(_REGISTRY["post_turn"]) == 1
    assert len(_REGISTRY["session_start"]) == 1
    assert len(_REGISTRY["session_end"]) == 1

    class FakeAgent:
        def __init__(self, profile, harness):
            self.profile = profile
            self.harness = harness
            self.model = "sonnet"

    from aegis.core.session import AgentSession
    harness = FakeHarnessSession()
    session = AgentSession(
        harness, FakeAgent("p", "claude"), "p", "t",
        project_root=tmp_path,
    )
    await session.send_and_wait("hi")
    await session.close(reason="done")
    await asyncio.sleep(0.05)

    events = [line for line in marker.read_text().splitlines() if line]
    assert events == ["start", "pre", "post", "end"]
    assert "HELLO" in harness.sent[0]
