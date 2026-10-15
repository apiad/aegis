"""The terminals: block of .aegis.yaml, its memory cap and aegis doctor."""

from aegis.config import TOP_KEYS, Snapshot
from aegis.doctor import doctor
from aegis.roots import make_roots
from aegis.term_profiles import argv, terminals_from, with_shell


def test_a_profile_takes_cmd_and_cwd_and_restart_defaults_to_no():
    got, err = terminals_from(
        {"api": {"cmd": "uvicorn app:app", "cwd": "app", "env": {"PORT": 8000}}}
    )
    assert err is None
    assert got["api"] == {
        "cmd": "uvicorn app:app",
        "cwd": "app",
        "restart": "no",
        "env": {"PORT": "8000"},
        "access": {},
    }


def test_a_profile_missing_cmd_is_kept_with_an_error_naming_it():
    got, _ = terminals_from(
        {"api": {"cwd": "app"}, "logs": {"cmd": "journalctl -f", "cwd": "/"}}
    )
    assert got["api"] == {"error": "cmd is missing"}
    assert "error" not in got["logs"]


def test_unknown_keys_and_unknown_restarts_are_errors():
    got, _ = terminals_from(
        {
            "a": {"cmd": "x", "cwd": ".", "restrat": "always"},
            "b": {"cmd": "x", "cwd": ".", "restart": "sometimes"},
        }
    )
    assert got["a"]["error"].startswith("restrat: not a profile key")
    assert (
        got["b"]["error"] == "restart 'sometimes' is not one of no, on-failure, always"
    )


def test_a_block_that_is_not_a_mapping_is_an_error():
    assert terminals_from(["shell"]) == (
        {},
        "terminals must be a mapping of a name to a profile",
    )


def test_the_builtin_shell_is_offered_unless_a_profile_is_named_shell(monkeypatch):
    monkeypatch.setenv("SHELL", "/bin/bash")
    assert list(with_shell({"api": {}})) == ["shell", "api"]
    assert argv(with_shell({})["shell"]) == ["/bin/bash", "-l"]
    mine = {"shell": {"cmd": "zsh", "cwd": "."}}
    assert with_shell(mine) is mine
    assert argv(mine["shell"]) == ["/bin/sh", "-c", "zsh"]


def test_terminals_and_their_memory_cap_are_keys_aegis_reads(tmp_path):
    snap = Snapshot.parse(
        tmp_path / ".aegis.yaml",
        None,
        {"terminals": {"api": {"cmd": "x", "cwd": "."}}, "terminal_memory_max": "2G"},
    )
    assert {"terminals", "terminal_memory_max"} <= set(TOP_KEYS)
    assert snap.unknown_keys == ()
    assert snap.terminals["api"]["cmd"] == "x"
    assert snap.terminal_memory_max == "2G" and snap.terminal_memory_error is None


async def test_doctor_checks_each_profiles_command_and_cwd(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / ".aegis.yaml").write_text(
        "terminals:\n"
        "  ok: {cmd: 'sh -c true', cwd: app}\n"
        "  nocmd: {cmd: 'no-such-binary-xyz --flag', cwd: app}\n"
        "  nocwd: {cmd: sh, cwd: gone}\n"
        "  broken: {cwd: app}\n"
    )
    found = {f.where: f for f in await doctor(make_roots(tmp_path, None), {})}
    assert found["terminal.ok"].level == "ok"
    assert found["terminal.nocmd"].level == "warn"
    assert "no-such-binary-xyz" in found["terminal.nocmd"].message
    assert found["terminal.nocwd"].level == "error"
    assert "gone" in found["terminal.nocwd"].message
    assert found["terminal.broken"].message == "cmd is missing"
