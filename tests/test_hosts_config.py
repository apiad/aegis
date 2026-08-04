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


def test_add_host_writes_a_loadable_entry(tmp_path):
    from aegis.config.edit import add_host

    _write(tmp_path, "")
    add_host(tmp_path, "vps", ssh="vps.apiad.net",
             cwd="/home/apiad/Workspace")
    cfg = load_config(tmp_path)
    assert cfg.hosts["vps"].ssh == "vps.apiad.net"
    assert cfg.hosts["vps"].cwd == "/home/apiad/Workspace"
    assert cfg.hosts["vps"].login_shell is True


def test_add_host_preserves_comments(tmp_path):
    from aegis.config.edit import add_host

    _write(tmp_path, "# keep me\ndefault_agent: main\n"
                     "agents:\n  main:\n    harness: claude-code\n"
                     "    model: opus\n")
    add_host(tmp_path, "vps", ssh="h", cwd="/w")
    assert "# keep me" in (tmp_path / ".aegis.yaml").read_text()


def test_add_host_carries_optional_fields(tmp_path):
    from aegis.config.edit import add_host

    _write(tmp_path, "")
    add_host(tmp_path, "vps", ssh="h", cwd="/w",
             ssh_opts=["-o", "ServerAliveInterval=15"], login_shell=False)
    spec = load_config(tmp_path).hosts["vps"]
    assert spec.ssh_opts == ["-o", "ServerAliveInterval=15"]
    assert spec.login_shell is False


def test_add_duplicate_host_is_fail_loud(tmp_path):
    from aegis.config.edit import add_host

    _write(tmp_path, "")
    add_host(tmp_path, "vps", ssh="h", cwd="/w")
    with pytest.raises(ConfigError, match="already exists"):
        add_host(tmp_path, "vps", ssh="h2", cwd="/w2")


def test_add_host_named_local_is_refused(tmp_path):
    from aegis.config.edit import add_host

    _write(tmp_path, "")
    with pytest.raises(ConfigError, match="local"):
        add_host(tmp_path, "local", ssh="h", cwd="/w")


def test_remove_host(tmp_path):
    from aegis.config.edit import add_host, remove_host

    _write(tmp_path, "")
    add_host(tmp_path, "vps", ssh="h", cwd="/w")
    remove_host(tmp_path, "vps")
    assert load_config(tmp_path).hosts == {}


def test_remove_unknown_host_is_fail_loud(tmp_path):
    from aegis.config.edit import remove_host

    _write(tmp_path, "")
    with pytest.raises(ConfigError, match="not"):
        remove_host(tmp_path, "nope")


def test_remove_host_referenced_by_an_agent_is_fail_loud(tmp_path):
    """Removing a host out from under a profile that names it would leave
    the config unloadable — the loader validates that reference."""
    from aegis.config.edit import add_host, remove_host

    _write(tmp_path, "default_agent: main\n"
                     "agents:\n  main:\n    harness: claude-code\n"
                     "    model: opus\n    host: vps\n")
    add_host(tmp_path, "vps", ssh="h", cwd="/w")
    with pytest.raises(ConfigError):
        remove_host(tmp_path, "vps")


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
