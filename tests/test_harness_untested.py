"""A harness version outside the tested range (#446): unattended launches
start and say so in the transcript."""

import json
import os
import time
from pathlib import Path

import pytest

from aegis.app import App

from .conftest import until
from .test_agents import CONFIG, World, mcp, turn


def untested(monkeypatch, fake: str, version: str) -> None:
    """Make ``fake`` report ``version`` from now on: the env the fake reads,
    and a new mtime so the version cache reads it again."""
    monkeypatch.setenv("AEGIS_FAKE_HARNESS_VERSION", version)
    ns = time.time_ns() + 10**9
    os.utime(fake, ns=(ns, ns))


def notices(s) -> list[dict]:
    return [e for e in s.entries() if e["detail"].get("untested")]


@pytest.fixture
async def world(tmp_path, fake_claude):
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    w = await World(tmp_path, fake_claude).start()
    yield w
    await w.stop()


async def agent_spawn(world, parent) -> tuple[dict, object]:
    said = await turn(parent, mcp("session_spawn", agent="opus"))
    result = json.loads(said[said.index("{") :])
    return result, world.session(result["log_id"])


async def test_agent_spawn_outside_range_starts_with_notice(world, monkeypatch):
    parent = await world.spawn()
    untested(monkeypatch, world.fake, "99.0.0")
    result, child = await agent_spawn(world, parent)
    assert child.running
    (n,) = notices(child)
    text = n["summary"] + "\n" + n["detail"]["tail"]
    assert str(Path(world.fake).resolve()) in text
    assert "npm install -g @anthropic-ai/claude-code@" in text
    assert "nobody was there to ask" in text
    assert "Claude Code 99.0.0 is newer" in result["untested"]


async def test_agent_off_writes_no_notice(tmp_path, fake_claude, monkeypatch):
    (tmp_path / ".aegis.yaml").write_text(CONFIG + "harness_versions: {agent: off}\n")
    w = await World(tmp_path, fake_claude).start()
    try:
        parent = await w.spawn()
        untested(monkeypatch, fake_claude, "99.0.0")
        result, child = await agent_spawn(w, parent)
        assert notices(child) == [] and "untested" not in result
    finally:
        await w.stop()


async def test_in_range_writes_nothing(world):
    parent = await world.spawn()
    result, child = await agent_spawn(world, parent)
    assert notices(child) == [] and notices(parent) == []
    assert "untested" not in result


async def test_relaunch_same_version_no_second_notice(world, monkeypatch):
    parent = await world.spawn()
    untested(monkeypatch, world.fake, "99.0.0")
    _, child = await agent_spawn(world, parent)
    await child.stop()
    await turn(child, "hello")
    assert len(notices(child)) == 1
    assert world.app.sessions.metas.path(child.log_id).exists()
    meta = json.loads(world.app.sessions.metas.path(child.log_id).read_text())
    assert meta["checked_version"] == "99.0.0"


async def test_version_changed_under_session_notices_again(world, monkeypatch):
    parent = await world.spawn()
    untested(monkeypatch, world.fake, "99.0.0")
    _, child = await agent_spawn(world, parent)
    await child.stop()
    untested(monkeypatch, world.fake, "99.1.0")
    await turn(child, "hello")
    got = notices(child)
    assert len(got) == 2 and "99.1.0" in got[1]["summary"]


class CodexWorld(World):
    """A World whose codex is the fake codex."""

    def __init__(self, root: Path, fake: str, codex: str):
        super().__init__(root, fake)
        self.codex = codex

    async def start(self) -> "World":
        real = App.__init__

        def init(app, *a, **k):
            real(app, *a, **{**k, "codex_bin": self.codex})

        App.__init__ = init
        try:
            return await super().start()
        finally:
            App.__init__ = real


async def test_codex_without_version_flag_is_unknown(
    tmp_path, fake_claude, fake_codex, monkeypatch
):
    (tmp_path / ".aegis.yaml").write_text(
        "default_agent: cx\nagents:\n"
        "  cx: {harness: codex, model: openai/gpt-5.5, effort: medium, permission: full}\n"
    )
    monkeypatch.setenv("AEGIS_FAKE_HARNESS_VERSION", "")
    w = await CodexWorld(tmp_path, fake_claude, fake_codex).start()
    try:
        r = await w.app.registry.call("session.spawn", {"agent": "cx"})
        s = w.session(r["log_id"])
        await until(lambda: s.running, timeout=10, what="codex running")
        (n,) = notices(s)
        assert "could not be read" in n["summary"]
    finally:
        await w.stop()
