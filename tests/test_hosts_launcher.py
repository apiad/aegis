from __future__ import annotations

import asyncio
import shlex

from aegis.hosts.launcher import (
    LOCAL,
    LocalLauncher,
    env_delta,
    remote_command,
    ssh_argv,
)
from aegis.hosts.models import HostSpec

SPEC = HostSpec(name="vps", ssh="vps.apiad.net", cwd="/home/apiad/Workspace")


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


# --- ssh argv composition -------------------------------------------------


def test_remote_command_cds_and_execs():
    cmd = remote_command(["claude", "-p"], cwd="/srv/app", env={})
    assert cmd == "cd /srv/app && exec claude -p"


def test_remote_command_quotes_a_cwd_with_spaces():
    cmd = remote_command(["true"], cwd="/srv/my app", env={})
    assert "'/srv/my app'" in cmd
    assert cmd.startswith("cd '/srv/my app' && exec ")


def test_remote_command_quotes_argv_with_quotes_and_newlines():
    # The claude primer is a realistic worst case: a multi-line string
    # containing apostrophes, passed as one --append-system-prompt value.
    primer = "You are 'agent-one'.\nCall aegis_meta() first."
    cmd = remote_command(
        ["claude", "--append-system-prompt", primer], cwd="/srv", env={})
    # Round-trip through the shell's own parser rather than asserting on
    # the exact escaping: what matters is that sh reconstructs the value.
    parsed = shlex.split(cmd)
    assert parsed[-1] == primer
    assert parsed[-2] == "--append-system-prompt"


def test_remote_command_emits_env_before_the_argv():
    cmd = remote_command(["true"], cwd="/srv", env={"FOO": "bar"})
    assert cmd == "cd /srv && exec env FOO=bar true"


def test_remote_command_quotes_env_values():
    cmd = remote_command(["true"], cwd="/srv", env={"K": "a b"})
    assert "'K=a b'" in cmd


def test_env_delta_keeps_only_what_differs_from_the_local_environment():
    # Shipping the whole local environ over ssh would clobber the remote
    # shell's own environment. Only driver-injected and hook-added keys
    # should cross.
    base = {"PATH": "/usr/bin", "HOME": "/home/me"}
    got = env_delta({"PATH": "/usr/bin", "HOME": "/home/me",
                     "OPENROUTER_API_KEY": "sk-x"}, base)
    assert got == {"OPENROUTER_API_KEY": "sk-x"}


def test_env_delta_of_none_is_empty():
    assert env_delta(None, {"PATH": "/usr/bin"}) == {}


def test_env_delta_includes_a_changed_value():
    base = {"MODEL": "old"}
    assert env_delta({"MODEL": "new"}, base) == {"MODEL": "new"}


def test_ssh_argv_shape():
    argv = ssh_argv(SPEC, "/run/x.sock", "cd /srv && exec true")
    assert argv[0] == "ssh"
    assert "-T" in argv                       # no PTY: clean byte stream
    assert "ControlPath=/run/x.sock" in argv
    assert argv[-2] == "vps.apiad.net"
    assert argv[-1] == "cd /srv && exec true"


def test_ssh_argv_appends_host_ssh_opts():
    spec = HostSpec(name="vps", ssh="h", cwd="/x",
                    ssh_opts=["-o", "ServerAliveInterval=15"])
    argv = ssh_argv(spec, "/run/x.sock", "true")
    assert "ServerAliveInterval=15" in argv
    # opts land before the destination, where ssh expects them
    assert argv.index("ServerAliveInterval=15") < argv.index("h")
