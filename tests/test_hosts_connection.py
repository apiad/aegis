from __future__ import annotations

import pytest

from aegis.hosts.connection import (
    master_argv,
    parse_allocated_port,
    preflight_command,
)
from aegis.hosts.models import HostSpec

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
