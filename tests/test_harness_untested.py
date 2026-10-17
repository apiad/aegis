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
        r = await w.app.registry.call(
            "session.spawn", {"agent": "cx", "untested": "once"}
        )
        s = w.session(r["log_id"])
        await until(lambda: s.running, timeout=10, what="codex running")
        (n,) = notices(s)
        assert "could not be read" in n["summary"]
    finally:
        await w.stop()


# -- a person's launch --------------------------------------------------------------
async def person_spawn(world, caller=None, **extra):
    from aegis.ops import Caller

    return await world.app.registry.call(
        "session.spawn", {"agent": "opus", **extra}, caller or Caller("user")
    )


async def test_person_spawn_outside_range_is_refused_with_payload(world, monkeypatch):
    from aegis.ops import OpError

    untested(monkeypatch, world.fake, "99.0.0")
    before = set(world.app.sessions.sessions)
    with pytest.raises(OpError) as e:
        await person_spawn(world)
    assert e.value.code == "harness_untested"
    assert set(e.value.data) >= {
        "harness", "version", "path", "min", "max", "state", "install", "notes",
        "may_always",
    }  # fmt: skip
    assert e.value.data["version"] == "99.0.0" and e.value.data["may_always"] is True
    assert set(world.app.sessions.sessions) == before, "no session was created"


async def test_person_once_starts_with_notice(world, monkeypatch):
    untested(monkeypatch, world.fake, "99.0.0")
    r = await person_spawn(world, untested="once")
    s = world.session(r["log_id"])
    assert s.running and len(notices(s)) == 1
    assert "nobody was there" not in notices(s)[0]["summary"]


async def test_person_always_writes_config_and_stops_asking(world, monkeypatch):
    untested(monkeypatch, world.fake, "99.0.0")
    r = await person_spawn(world, untested="always")
    assert notices(world.session(r["log_id"])) == [] and "not_saved" not in r
    text = (world.root / ".aegis.yaml").read_text()
    assert "harness_versions:" in text and "99.0.0" in text
    r = await person_spawn(world)
    assert notices(world.session(r["log_id"])) == []


async def test_always_on_broken_config_starts_and_says_why(world, monkeypatch):
    untested(monkeypatch, world.fake, "99.0.0")
    cfg = world.root / ".aegis.yaml"
    world.app.config.current()  # the good file is in force
    cfg.write_text(cfg.read_text() + "queues: [1, 2\n")
    r = await person_spawn(world, untested="always")
    assert world.session(r["log_id"]).running
    assert "does not parse" in r["not_saved"] or "fix it" in r["not_saved"]


async def test_member_cannot_always(world, monkeypatch):
    from aegis.ops import Caller, OpError, Principal

    world.app.principals.add("ben", "member")
    world.app.principals.set_default_policy({"claude": {"mode": "open"}})
    ben = Caller("user", principal=Principal("ben", "member"))
    untested(monkeypatch, world.fake, "99.0.0")
    with pytest.raises(OpError) as e:
        await person_spawn(world, ben)
    assert e.value.data["may_always"] is False
    r = await person_spawn(world, ben, untested="always")
    assert "admin" in r["not_saved"] and len(notices(world.session(r["log_id"]))) == 1
    assert "99.0.0" not in (world.root / ".aegis.yaml").read_text()


@pytest.mark.parametrize("policy,want", [("warn", 1), ("off", 0)])
async def test_person_warn_and_off(tmp_path, fake_claude, monkeypatch, policy, want):
    (tmp_path / ".aegis.yaml").write_text(
        CONFIG + f"harness_versions: {{person: {policy}}}\n"
    )
    w = await World(tmp_path, fake_claude).start()
    try:
        untested(monkeypatch, fake_claude, "99.0.0")
        r = await person_spawn(w)
        assert len(notices(w.session(r["log_id"]))) == want
    finally:
        await w.stop()


async def test_person_send_to_stopped_session_asks(world, monkeypatch):
    from aegis.ops import OpError

    s = await world.spawn()
    await s.stop()
    untested(monkeypatch, world.fake, "99.0.0")
    call = world.app.registry.call
    with pytest.raises(OpError) as e:
        await call("session.send", {"log_id": s.log_id, "text": "hi"})
    assert e.value.code == "harness_untested" and not s.running
    await call("session.send", {"log_id": s.log_id, "text": "hi", "untested": "once"})
    await until(lambda: s.running, timeout=5, what="started")
    assert len(notices(s)) == 1


async def test_spawn_line_carries_untested(world, monkeypatch):
    from aegis.ops import OpError

    s = await world.spawn()
    untested(monkeypatch, world.fake, "99.0.0")
    call = world.app.registry.call
    with pytest.raises(OpError) as e:
        await call("session.send", {"log_id": s.log_id, "text": "/spawn opus"})
    assert e.value.code == "harness_untested"
    r = await call(
        "session.send",
        {"log_id": s.log_id, "text": "/spawn opus", "untested": "once"},
    )
    assert len(notices(world.session(r["log_id"]))) == 1


async def test_warm_parked_never_asks(world, monkeypatch):
    s = await world.spawn()
    assert await s.park(60) and s.status == "parked"
    untested(monkeypatch, world.fake, "99.0.0")
    await world.app.registry.call("session.warm", {"log_id": s.log_id})
    await until(lambda: s.running, timeout=5, what="warmed")
    (n,) = notices(s)
    assert "nobody was there to ask" in n["summary"]
