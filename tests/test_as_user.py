"""as_user.py: file code run with one Linux user's credentials (#555). The
real switch needs CAP_SETUID and real users: tests/multiuser/test_files.py."""

from __future__ import annotations

import copy
import os
import pwd
import threading

import pytest

from aegis import as_user, users

ME = pwd.getpwuid(os.getuid())


@pytest.fixture
def became(monkeypatch):
    """``become`` replaced by a recorder of (thread, user)."""
    seen: list[tuple[int, str]] = []
    monkeypatch.setattr(
        as_user, "become", lambda pw: seen.append((threading.get_ident(), pw.pw_name))
    )
    monkeypatch.setattr(
        as_user.pwd, "getpwnam", lambda name: copy.replace(ME, pw_name=name)
    )
    return seen


async def test_no_user_runs_in_another_thread():
    here = threading.get_ident()
    assert await as_user.Runner(None)(threading.get_ident) != here


async def test_an_exception_crosses_as_itself():
    def boom():
        raise FileNotFoundError(2, "gone", "/x")

    with pytest.raises(FileNotFoundError):
        await as_user.Runner(None)(boom)


async def test_a_user_gets_one_runner_whose_threads_became_it(became):
    runners = as_user.Runners()
    try:
        r = runners.get("ben")
        assert runners.get("ben") is r and runners.get(None).user is None
        tid = await r(threading.get_ident)
        assert (tid, "ben") in became
        assert all(name == "ben" for _, name in became)
    finally:
        runners.shutdown()


async def test_a_failed_switch_refuses_every_call(monkeypatch):
    def refuse(pw):
        raise PermissionError("no")

    monkeypatch.setattr(as_user, "become", refuse)
    monkeypatch.setattr(
        as_user.pwd, "getpwnam", lambda name: copy.replace(ME, pw_name=name)
    )
    runners = as_user.Runners()
    try:
        with pytest.raises(PermissionError, match="ben"):
            await runners.get("ben")(lambda: 1)
        with pytest.raises(PermissionError, match="ben"):
            await runners.get("ben")(lambda: 1)
    finally:
        runners.shutdown()


async def test_a_user_that_is_gone_is_refused(monkeypatch):
    def gone(name):
        raise KeyError(name)

    monkeypatch.setattr(as_user.pwd, "getpwnam", gone)
    runners = as_user.Runners()
    with pytest.raises(PermissionError, match="aegis-no-such"):
        await runners.get("aegis-no-such")(lambda: 1)


def test_boot_refuses_an_architecture_without_a_setgroups_number(tmp_path):
    full = "CapEff:\t00000000000001c0\nCapAmb:\t00000000000001c0\n"
    out = users.boot_problems(
        tmp_path, users.MultiUser(), status=full, uid=os.getuid(), machine="riscv64"
    )
    assert any("riscv64" in line for line in out)
    assert not any(
        "riscv64" in line
        for line in users.boot_problems(
            tmp_path, users.MultiUser(), status=full, uid=os.getuid(), machine="x86_64"
        )
    )
