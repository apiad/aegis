from __future__ import annotations

import pytest

from aegis.hosts.connection import (
    master_argv,
    parse_allocated_port,
    preflight_command,
)
from aegis.hosts.errors import HostError
from aegis.hosts.launcher import LocalLauncher, SshLauncher
from aegis.hosts.models import HostSpec, Place
from aegis.hosts.registry import HostRegistry

SPEC = HostSpec(name="vps", ssh="vps.apiad.net",
                cwd="/home/apiad/Workspace")


def test_parses_the_allocated_port_line():
    line = "Allocated port 41573 for remote forward to 127.0.0.1:8931"
    assert parse_allocated_port(line) == 41573


def test_parses_the_line_with_a_debug_prefix():
    line = ("debug1: Allocated port 41573 for remote forward to "
            "127.0.0.1:8931")
    assert parse_allocated_port(line) == 41573


def test_ignores_unrelated_stderr():
    assert parse_allocated_port("Warning: Permanently added 'vps'") is None
    assert parse_allocated_port("") is None
    assert parse_allocated_port("Allocated port for remote forward") is None


def test_master_argv_requests_a_dynamic_reverse_forward():
    argv = master_argv(SPEC, "/run/x.sock", 8931)
    assert "-R" in argv
    assert argv[argv.index("-R") + 1] == "0:127.0.0.1:8931"


def test_master_argv_pins_the_port_when_the_spec_says_so():
    spec = HostSpec(name="vps", ssh="h", cwd="/x", remote_mcp_port=9999)
    argv = master_argv(spec, "/run/x.sock", 8931)
    assert argv[argv.index("-R") + 1] == "9999:127.0.0.1:8931"


def test_master_argv_fails_loudly_on_a_broken_forward():
    # Without this, a failed forward leaves a live master and every
    # session on it gets an unreachable MCP URL.
    assert "ExitOnForwardFailure=yes" in master_argv(SPEC, "/s", 1)


def test_master_argv_forces_loglevel_info():
    # The allocated-port line is an INFO-level message. A user's
    # ~/.ssh/config setting LogLevel=QUIET would otherwise swallow it and
    # the parse would hang until it timed out.
    assert "LogLevel=INFO" in master_argv(SPEC, "/s", 1)


def test_master_argv_is_a_backgroundable_master():
    argv = master_argv(SPEC, "/run/x.sock", 8931)
    assert "-M" in argv and "-N" in argv
    assert "ControlPath=/run/x.sock" in argv
    assert "ControlPersist=60s" in argv
    assert argv[-1] == "vps.apiad.net"


def test_preflight_checks_both_the_binary_and_the_directory():
    cmd = preflight_command("claude", "/home/apiad/Workspace")
    assert "command -v claude" in cmd
    assert "/home/apiad/Workspace" in cmd
    assert "test -d" in cmd


def test_preflight_quotes_a_cwd_with_spaces():
    assert "'/srv/my app'" in preflight_command("claude", "/srv/my app")


def test_preflight_honours_the_login_shell():
    # The preflight must resolve PATH the same way the real spawn will.
    # A preflight that green-lights a spawn which then fails is worse than
    # no preflight at all.
    cmd = preflight_command("claude", "/w", login_shell=True)
    assert cmd.startswith("bash -lc ")
    import shlex
    assert "command -v claude" in shlex.split(cmd)[2]


def test_host_spec_defaults_to_a_login_shell():
    assert HostSpec(name="v", ssh="h", cwd="/w").login_shell is True


# --- registry -------------------------------------------------------------


def _registry(tmp_path):
    reg = HostRegistry({"vps": SPEC}, state_dir=tmp_path / "state",
                       local_root="/local/proj")
    reg.set_mcp_port(8931)
    return reg


def test_local_place_gets_a_local_launcher_and_the_url_unchanged(tmp_path):
    lau, url = _registry(tmp_path).launcher_for(
        Place("local", "/local/proj"), "http://127.0.0.1:8931/mcp/")
    assert isinstance(lau, LocalLauncher)
    assert url == "http://127.0.0.1:8931/mcp/"
    assert lau.persona_root("/local/proj") == "/local/proj"


def test_remote_place_gets_an_ssh_launcher(tmp_path):
    lau, _url = _registry(tmp_path).launcher_for(
        Place("vps", "/home/apiad/Workspace"), "http://127.0.0.1:8931/mcp/")
    assert isinstance(lau, SshLauncher)
    assert lau.host_key == "vps"
    # Persona files live locally even though the harness runs remotely.
    assert lau.persona_root("/home/apiad/Workspace") == "/local/proj"


def test_remote_url_is_deferred_until_the_tunnel_is_up(tmp_path):
    # launcher_for is synchronous (it is called from _sync_spawn) and the
    # allocated port is not known until the master opens. The URL is
    # therefore a placeholder resolved inside spawn().
    _lau, url = _registry(tmp_path).launcher_for(
        Place("vps", "/x"), "http://127.0.0.1:8931/mcp/")
    assert url == ""      # sentinel: resolved at spawn time


def test_one_connection_is_reused_per_host(tmp_path):
    reg = _registry(tmp_path)
    a, _ = reg.launcher_for(Place("vps", "/x"), "u")
    b, _ = reg.launcher_for(Place("vps", "/y"), "u")
    assert a._conn is b._conn


def test_control_path_lives_under_the_state_dir(tmp_path):
    reg = _registry(tmp_path)
    lau, _ = reg.launcher_for(Place("vps", "/x"), "u")
    assert str(tmp_path / "state") in lau._conn.control_path
    assert lau._conn.control_path.endswith("vps.sock")


def test_unknown_host_is_a_loud_error(tmp_path):
    with pytest.raises(HostError, match="nowhere"):
        _registry(tmp_path).launcher_for(Place("nowhere", "/x"), "u")


def test_a_registry_with_no_mcp_port_refuses_a_remote_launcher(tmp_path):
    reg = HostRegistry({"vps": SPEC}, state_dir=tmp_path / "state",
                       local_root="/local/proj")
    with pytest.raises(HostError, match="MCP port"):
        reg.launcher_for(Place("vps", "/x"), "u")
