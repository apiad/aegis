from __future__ import annotations

import asyncio

from aegis.hosts.launcher import LOCAL, LocalLauncher


def test_local_launcher_identity():
    assert LOCAL.host_key == "local"
    assert LocalLauncher().host_key == "local"


def test_persona_root_falls_back_to_cwd():
    # With no explicit local_root, persona resolution is unchanged from
    # today: relative persona paths resolve under the session cwd.
    assert LocalLauncher().persona_root("/tmp/proj") == "/tmp/proj"


def test_persona_root_prefers_explicit_local_root():
    lau = LocalLauncher(local_root="/home/me/proj")
    assert lau.persona_root("/remote/tree") == "/home/me/proj"


def test_local_launcher_spawns_a_real_process():
    async def go():
        proc = await LocalLauncher().spawn(
            ["sh", "-c", "printf hello"], cwd="/tmp", env=None)
        out, _ = await proc.communicate()
        return out

    assert asyncio.run(go()) == b"hello"


def test_local_launcher_passes_cwd():
    async def go():
        proc = await LocalLauncher().spawn(
            ["sh", "-c", "pwd"], cwd="/tmp", env=None)
        out, _ = await proc.communicate()
        return out.decode().strip()

    assert asyncio.run(go()).endswith("/tmp")


class FakeLauncher:
    """Records what it was asked to spawn, then delegates locally."""

    host_key = "fake"
    local_root = None

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], str, dict | None]] = []

    def persona_root(self, cwd: str) -> str:
        return cwd

    async def spawn(self, argv, *, cwd, env):
        self.calls.append((list(argv), cwd, env))
        return await LocalLauncher().spawn(
            ["sh", "-c", "sleep 30"], cwd=cwd, env=None)


def test_claude_session_uses_its_launcher(tmp_path):
    from aegis.drivers.claude import ClaudeSession

    fake = FakeLauncher()

    async def go():
        sess = ClaudeSession(["claude", "-p"], str(tmp_path),
                             handle="test-agent", launcher=fake)
        await sess.start()
        await sess.close()

    asyncio.run(go())
    assert len(fake.calls) == 1
    argv, cwd, _env = fake.calls[0]
    # The launcher sees the INNER argv — pre-spawn hooks have run, but no
    # transport wrapping has happened at this layer.
    assert argv == ["claude", "-p"]
    assert cwd == str(tmp_path)


def test_driver_defaults_to_the_local_launcher(tmp_path):
    from aegis.config import Agent
    from aegis.drivers.claude import ClaudeDriver

    agent = Agent(harness="claude-code", model="opus")
    sess = ClaudeDriver().session(
        agent, str(tmp_path), "http://127.0.0.1:1/mcp/", "test-agent")
    assert sess._launcher is LOCAL
