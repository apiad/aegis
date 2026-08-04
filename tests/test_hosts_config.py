from __future__ import annotations

import pytest

from aegis.config import ConfigError
from aegis.config.yaml_loader import load_config


def _write(root, text: str):
    (root / ".aegis.yaml").write_text(text)
    return root


def test_hosts_section_loads(tmp_path):
    _write(tmp_path, """
hosts:
  vps:
    ssh: vps.apiad.net
    cwd: /home/apiad/Workspace
  smaug:
    ssh: smaug.local
    cwd: /home/apiad/work
    ssh_opts: ["-o", "ServerAliveInterval=15"]
""")
    cfg = load_config(tmp_path)
    assert set(cfg.hosts) == {"vps", "smaug"}
    assert cfg.hosts["vps"].ssh == "vps.apiad.net"
    assert cfg.hosts["vps"].cwd == "/home/apiad/Workspace"
    assert cfg.hosts["vps"].ssh_opts == []
    assert cfg.hosts["smaug"].ssh_opts == ["-o", "ServerAliveInterval=15"]


def test_hosts_default_to_empty(tmp_path):
    _write(tmp_path, "")
    assert load_config(tmp_path).hosts == {}


def test_host_named_local_is_refused(tmp_path):
    _write(tmp_path, """
hosts:
  local:
    ssh: somewhere
    cwd: /tmp
""")
    with pytest.raises(ConfigError, match="local"):
        load_config(tmp_path)


def test_host_requires_ssh_and_cwd(tmp_path):
    _write(tmp_path, """
hosts:
  vps:
    ssh: vps.apiad.net
""")
    with pytest.raises(ConfigError, match="cwd"):
        load_config(tmp_path)


def test_agent_host_default_must_reference_a_declared_host(tmp_path):
    _write(tmp_path, """
default_agent: main
agents:
  main:
    harness: claude-code
    model: opus
    host: nowhere
""")
    with pytest.raises(ConfigError, match="nowhere"):
        load_config(tmp_path)


def test_agent_host_default_accepts_local(tmp_path):
    _write(tmp_path, """
default_agent: main
agents:
  main:
    harness: claude-code
    model: opus
    host: local
""")
    assert load_config(tmp_path).agents["main"].host == "local"


def test_agent_host_default_resolves_against_a_declared_host(tmp_path):
    _write(tmp_path, """
default_agent: main
hosts:
  vps:
    ssh: vps.apiad.net
    cwd: /w
agents:
  main:
    harness: claude-code
    model: opus
    host: vps
""")
    assert load_config(tmp_path).agents["main"].host == "vps"


def test_hosts_overlay_merges(tmp_path):
    _write(tmp_path, "")
    d = tmp_path / ".aegis" / "hosts"
    d.mkdir(parents=True)
    (d / "vps.yaml").write_text(
        "ssh: vps.apiad.net\ncwd: /home/apiad/Workspace\n")
    cfg = load_config(tmp_path)
    assert cfg.hosts["vps"].ssh == "vps.apiad.net"


def test_hosts_overlay_collision_is_fail_loud(tmp_path):
    _write(tmp_path, """
hosts:
  vps:
    ssh: a
    cwd: /tmp
""")
    d = tmp_path / ".aegis" / "hosts"
    d.mkdir(parents=True)
    (d / "vps.yaml").write_text("ssh: b\ncwd: /tmp\n")
    with pytest.raises(ConfigError, match="vps"):
        load_config(tmp_path)
