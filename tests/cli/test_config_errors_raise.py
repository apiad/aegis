"""The boot loader raises; only the CLI wrappers exit.

`embed()` (task 9) calls the same loader from inside a host process. A
loader that reaches for `typer.Exit`/`sys.exit` would take that process
down over a config typo, so the assertion here is `ConfigError` — a test
catching `SystemExit` or `typer.Exit` would pass against exactly the bug
this task removes.
"""
from __future__ import annotations

import pytest
from typer.testing import CliRunner

from aegis.config import ConfigError
from aegis.config.roots import AegisRoots

_GOOD = ("agents:\n  a:\n    provider: claude-code\n    model: opus\n"
         "default_agent: a\n")
# Malformed YAML raises ruamel's ParserError, which the CLI never caught
# either — so a *ConfigError* fixture has to be a config the loader parses
# and then rejects. (The plan's `agents: [this is not a mapping` raises
# ParserError and cannot satisfy `pytest.raises(ConfigError)`.)
_BAD = "- not a mapping\n"


def test_bad_config_raises_rather_than_exiting(tmp_path):
    """A library caller must not have the process exited out from under it."""
    (tmp_path / ".aegis.yaml").write_text(_BAD, encoding="utf-8")
    from aegis.cli import load_boot_config
    with pytest.raises(ConfigError):
        load_boot_config(AegisRoots.for_project(tmp_path))


def test_unknown_default_agent_raises(tmp_path):
    (tmp_path / ".aegis.yaml").write_text(
        "agents:\n  a:\n    provider: claude-code\n    model: opus\n"
        "default_agent: nonexistent\n", encoding="utf-8")
    from aegis.cli import load_boot_config
    with pytest.raises(ConfigError):
        load_boot_config(AegisRoots.for_project(tmp_path))


def test_boot_config_carries_web_and_is_token_gated(tmp_path):
    """BootConfig must carry `web`, or this task silently kills the web
    frontend in a plan whose contract is "the web client is untouched"."""
    from aegis.cli import load_boot_config

    (tmp_path / ".aegis.yaml").write_text(
        _GOOD + "web:\n  bind: 127.0.0.1\n  port: 8899\n  token: secret\n",
        encoding="utf-8")
    assert load_boot_config(AegisRoots.for_project(tmp_path)).web is not None

    # A token-less block must stay None, or serve exposes an unauthed UI.
    (tmp_path / ".aegis.yaml").write_text(
        _GOOD + "web:\n  bind: 127.0.0.1\n  port: 8899\n", encoding="utf-8")
    assert load_boot_config(AegisRoots.for_project(tmp_path)).web is None


def test_boot_config_carries_the_rest_of_the_boot_surface(tmp_path):
    """Every field `_serve` is handed. A loader that dropped one would boot
    a brain missing a subsystem without failing anything."""
    from aegis.cli import load_boot_config

    (tmp_path / ".aegis.yaml").write_text(
        _GOOD
        + "queues:\n  work:\n    agent: a\n    max_parallel: 1\n"
        + "schedules:\n  nightly:\n    cron: '0 3 * * *'\n"
          "    workflow: noop\n",
        encoding="utf-8")
    boot = load_boot_config(AegisRoots.for_project(tmp_path))
    assert set(boot.agents) == {"a"}
    assert boot.default_agent == "a"
    assert set(boot.queues) == {"work"}
    assert set(boot.schedules) == {"nightly"}
    assert "nightly" in boot.inline_schedule_names


def test_cli_still_exits_1_on_bad_config(monkeypatch, tmp_path):
    """Moving the exit out of the loader must not remove it: `aegis` on a
    broken config still prints and exits 1."""
    from aegis.cli import app as cli_app

    (tmp_path / ".aegis.yaml").write_text(_BAD, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "aegis.tui.app.AegisApp.run",
        lambda self: (_ for _ in ()).throw(
            AssertionError("the TUI was launched on a broken config")))
    r = CliRunner().invoke(cli_app, [])
    assert r.exit_code == 1, r.output
    assert "top level must be a mapping" in r.output


def test_serve_still_exits_1_on_bad_config(monkeypatch, tmp_path):
    from aegis.cli import app as cli_app

    (tmp_path / ".aegis.yaml").write_text(_BAD, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    r = CliRunner().invoke(cli_app, ["serve"])
    assert r.exit_code == 1, r.output
