"""Web parity: the ``deliver`` RPC routes `/command` through the shared
dispatcher (returning a command_result frame, never reaching the agent) and
unescapes `//x` to a literal `/x` delivery."""
from __future__ import annotations

import pytest

from aegis.web.wssession import WSSession


class FakeReceipt:
    disposition = "landed"
    depth = 0


class FakeCore:
    def __init__(self):
        self.delivered: list[str] = []

    async def deliver(self, msg):
        self.delivered.append(msg.body)
        return FakeReceipt()


class FakeManager:
    """The AppBridge subset the slash commands touch here."""
    def __init__(self, core):
        self._core = core

    def get(self, handle):
        return self._core

    def list_sessions(self):
        return []

    def list_agents(self):
        return []


def _session(core):
    session = WSSession.__new__(WSSession)   # bypass the full ctor
    session._m = FakeManager(core)
    return session


@pytest.mark.asyncio
async def test_web_slash_command_returns_command_result_and_skips_deliver():
    core = FakeCore()
    session = _session(core)
    res = await session._deliver_or_command("h", "/sessions")
    assert "command_result" in res
    assert res["command_result"]["ok"] is True
    assert core.delivered == []              # never reached the agent


@pytest.mark.asyncio
async def test_web_prompt_command_delivers_not_command_result(tmp_path):
    from aegis.commands import REGISTRY
    from aegis.commands.prompt_loader import load_prompt_commands

    async def _fake_shell(cmd, cwd):
        return ""

    d = tmp_path / ".aegis" / "commands"
    d.mkdir(parents=True)
    (d / "hi.md").write_text("---\ndescription: h\n---\nHi $1", encoding="utf-8")
    names = load_prompt_commands(tmp_path, run_shell=_fake_shell)
    core = FakeCore()
    session = _session(core)
    try:
        res = await session._deliver_or_command("h", "/hi there")
        assert "command_result" not in res
        assert res["delivery"] == "landed"
        assert core.delivered == ["Hi there"]
    finally:
        for n in names:
            REGISTRY.pop(n, None)


@pytest.mark.asyncio
async def test_web_double_slash_delivers_literal():
    core = FakeCore()
    session = _session(core)
    res = await session._deliver_or_command("h", "//hello")
    assert core.delivered == ["/hello"]
    assert "delivery" in res


@pytest.mark.asyncio
async def test_web_plain_message_delivers_normally():
    core = FakeCore()
    session = _session(core)
    res = await session._deliver_or_command("h", "just a message")
    assert core.delivered == ["just a message"]
    assert res["delivery"] == "landed"


@pytest.mark.asyncio
async def test_web_command_frame_includes_effect_key():
    # A no-effect command still carries the effect key (value None).
    core = FakeCore()
    session = _session(core)
    res = await session._deliver_or_command("h", "/sessions")
    assert "effect" in res["command_result"]
    assert res["command_result"]["effect"] is None


@pytest.mark.asyncio
async def test_web_command_frame_carries_effect(monkeypatch):
    import aegis.commands as commands
    from aegis.commands import CommandResult

    async def _fake_dispatch(payload, ctx):
        return CommandResult(True, "theme set",
                             effect={"kind": "theme", "name": "aegis-ink"})
    monkeypatch.setattr(commands, "dispatch", _fake_dispatch)

    session = _session(FakeCore())
    res = await session._deliver_or_command("h", "/themes aegis-ink")
    assert res["command_result"]["effect"] == {"kind": "theme",
                                               "name": "aegis-ink"}
