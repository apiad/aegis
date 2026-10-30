"""The four ways aegis starts a process (launch.py), each against a real child."""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from aegis import launch
from aegis.harness import OOM_SCORE_ADJ


async def test_child_runs_argv_and_returns_the_process(tmp_path):
    p = await launch.child(
        ["sh", "-c", "echo hi"], cwd=tmp_path, stdout=launch.PIPE, user=None, via=None
    )
    out, _ = await p.communicate()
    assert out == b"hi\n" and p.returncode == 0


async def test_child_without_env_inherits_the_servers(monkeypatch):
    monkeypatch.setenv("AEGIS_LAUNCH_PROBE", "x")
    p = await launch.child(
        ["sh", "-c", "echo $AEGIS_LAUNCH_PROBE"],
        stdout=launch.PIPE,
        user=None,
        via=None,
    )
    assert (await p.communicate())[0] == b"x\n"


async def test_child_with_a_missing_binary_raises_file_not_found(tmp_path):
    with pytest.raises(FileNotFoundError):
        await launch.child(["no-such-binary-aegis"], via=None, user=None)
    with pytest.raises(FileNotFoundError):
        await launch.child(
            ["no-such-binary-aegis"], cgroup=tmp_path, via=None, user=None
        )


async def test_child_with_oom_raises_the_score():
    p = await launch.child(["sleep", "5"], oom=True, via=None, user=None)
    try:
        score = Path(f"/proc/{p.pid}/oom_score_adj").read_text().strip()
        assert score == str(OOM_SCORE_ADJ)
    finally:
        p.kill()
        await p.wait()


async def test_child_with_new_session_leads_its_own_session():
    p = await launch.child(["sleep", "5"], new_session=True, via=None, user=None)
    try:
        assert os.getsid(p.pid) == p.pid
    finally:
        p.kill()
        await p.wait()


async def test_child_with_die_with_parent_ends_when_its_parent_does(tmp_path):
    """The parent starts a child that would sleep a minute, prints its pid and
    exits at once without a word to it (#620)."""
    parent = tmp_path / "parent.py"
    parent.write_text(
        "import asyncio, os\n"
        "from aegis import launch\n"
        "async def main():\n"
        "    p = await launch.child(\n"
        "        ['sleep', '60'], user=None, via=None, stdout=launch.DEVNULL, die_with_parent=True\n"
        "    )\n"
        "    print(p.pid, flush=True)\n"
        "    os._exit(0)\n"
        "asyncio.run(main())\n"
    )
    p = await launch.child(
        [sys.executable, str(parent)], stdout=launch.PIPE, via=None, user=None
    )
    out, _ = await p.communicate()
    pid = int(out)
    deadline = time.monotonic() + 5
    while _alive(pid) and time.monotonic() < deadline:
        await asyncio.sleep(0.05)
    alive = _alive(pid)
    if alive:
        os.kill(pid, 9)
    assert not alive


async def test_child_with_die_with_parent_keeps_a_missing_binary_an_error():
    with pytest.raises(FileNotFoundError):
        await launch.child(
            ["no-such-binary-aegis"], via=None, user=None, die_with_parent=True
        )


def _alive(pid: int) -> bool:
    """Running: an orphan's zombie, reaped by whoever adopted it, is not."""
    try:
        return "State:\tZ" not in Path(f"/proc/{pid}/status").read_text()
    except OSError:
        return False


async def test_pty_gives_the_child_the_terminal(tmp_path):
    master, slave = os.openpty()
    try:
        p = await launch.pty(
            ["sh", "-c", "tty"], tty=slave, cwd=tmp_path, env=None, user=None, via=None
        )
    finally:
        os.close(slave)
    out = b""
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        try:
            chunk = os.read(master, 1024)
        except OSError:  # EIO: the child closed its side
            break
        if not chunk:
            break
        out += chunk
    os.close(master)
    await p.wait()
    assert out.startswith(b"/dev/pts/")


def test_run_captures_and_feeds_input():
    r = launch.run(["cat"], input=b"abc", user=None)
    assert r.stdout == b"abc" and r.returncode == 0


def test_run_timeout_raises_timeout_expired():
    with pytest.raises(subprocess.TimeoutExpired):
        launch.run(["sleep", "5"], timeout=0.2, user=None)


def test_signal_process_retries_a_group_signal_as_its_owner(monkeypatch):
    seen = []

    def denied(pgid, sig):
        raise PermissionError("the server cannot signal the member")

    def run(argv, *, user, timeout, text):
        seen.append((argv, user, timeout, text))
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(launch.os, "killpg", denied)
    monkeypatch.setattr(launch, "run", run)

    launch.signal_process(4321, signal.SIGTERM, user="ben", group=True)

    assert seen == [(["kill", "-s", "TERM", "--", "-4321"], "ben", 5, True)]


def test_signal_process_does_not_run_kill_when_the_process_is_gone(monkeypatch):
    def gone(pid, sig):
        raise ProcessLookupError

    monkeypatch.setattr(launch.os, "kill", gone)
    monkeypatch.setattr(
        launch,
        "run",
        lambda *a, **kw: pytest.fail("a missing process needs no fallback"),
    )

    launch.signal_process(4321, signal.SIGTERM, user="ben")


def test_signal_process_reports_a_failed_owner_fallback(monkeypatch):
    def denied(pid, sig):
        raise PermissionError("the server cannot signal the member")

    def run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 1, "", "kill: Operation not permitted")

    monkeypatch.setattr(launch.os, "kill", denied)
    monkeypatch.setattr(launch, "run", run)

    with pytest.raises(PermissionError, match="Operation not permitted"):
        launch.signal_process(4321, signal.SIGTERM, user="ben")


def test_detach_starts_a_new_session_and_does_not_wait():
    p = launch.detach(["sleep", "5"], user=None)
    try:
        assert os.getsid(p.pid) == p.pid and p.poll() is None
    finally:
        p.kill()
        p.wait()


def test_launch_calls_asyncio_through_its_module(monkeypatch):
    """tests/test_terminal.py patches asyncio.create_subprocess_exec; the
    launcher must look it up on the module at each call."""
    seen = []
    real = asyncio.create_subprocess_exec

    def spy(*argv, **kw):
        seen.append(argv)
        return real(*argv, **kw)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spy)

    async def go():
        p = await launch.child(["true"], via=None, user=None)
        await p.wait()

    asyncio.run(go())
    assert seen == [("true",)]


def test_browser_hands_the_url_to_webbrowser(monkeypatch):
    import webbrowser

    seen = []
    monkeypatch.setattr(webbrowser, "open", lambda url: seen.append(url) or True)
    launch.browser("http://127.0.0.1:1/", user=None)
    assert seen == ["http://127.0.0.1:1/"]


# -- multi-user mode ----------------------------------------------------------
import pwd  # noqa: E402
import shutil  # noqa: E402
import webbrowser  # noqa: E402

from aegis import users  # noqa: E402
from aegis.cgroups import wrap  # noqa: E402

ME = pwd.getpwuid(os.getuid())
SETPRIV = shutil.which("setpriv") or "setpriv"


@pytest.fixture
def seen(monkeypatch):
    """The argv and env every spawn is asked for, without starting anything."""

    class Calls(list):
        cwds: list

    calls: Calls = Calls()
    calls_cwd: list = []

    class Proc:
        pid = 4242
        returncode = 0

    async def fake_exec(*argv, env=None, **kw):
        calls.append((list(argv), env))
        calls_cwd.append(kw.get("cwd"))
        return Proc()

    def fake_run(argv, env=None, **kw):
        calls.append((list(argv), env))
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(launch, "raise_oom_score", lambda pid: None)
    calls.cwds = calls_cwd  # type: ignore[attr-defined]
    return calls


@pytest.fixture
def mode(tmp_path, monkeypatch):
    pool = tmp_path / "pool.env"
    pool.write_text("K=from-pool\n")
    real = pwd.getpwnam

    def getpwnam(name):
        return ME if name == "ben" else real(name)

    monkeypatch.setattr(pwd, "getpwnam", getpwnam)
    monkeypatch.setenv("AEGIS_TEST_SERVER_ONLY", "1")
    launch.configure(users.MultiUser(pool))
    yield
    launch.configure(None)


async def test_off_the_argv_and_env_are_unchanged(seen):
    await launch.child(["git"], via=None, user="ben", env={"A": "1"})
    assert seen == [(["git"], {"A": "1"})]


async def test_on_a_switched_child_gets_the_prefix_and_the_users_env(seen, mode):
    await launch.child(
        ["git", "status"], via=None, user="ben", env={**os.environ, "A": "1"}
    )
    argv, env = seen[0]
    assert argv == [*users.setpriv_argv(ME, SETPRIV), shutil.which("git"), "status"]
    assert env["HOME"] == ME.pw_dir and env["A"] == "1" and env["K"] == "from-pool"
    assert "AEGIS_TEST_SERVER_ONLY" not in env


async def test_on_a_server_child_drops_caps_and_keeps_its_env(seen, mode):
    await launch.child(["git"], via=None, user=None, env={"A": "1"})
    assert seen == [
        ([*users.setpriv_argv(None, SETPRIV), shutil.which("git")], {"A": "1"})
    ]


async def test_on_the_cgroup_join_comes_first_and_the_terminal_keeps_setsid(
    seen, mode, tmp_path
):
    await launch.child(["git"], via=None, user="ben", cgroup=tmp_path)
    assert seen[0][0] == wrap(
        tmp_path, [*users.setpriv_argv(ME, SETPRIV), shutil.which("git")]
    )
    await launch.pty(
        ["sh"], via=None, user="ben", tty=0, cwd=tmp_path, env=None, cgroup=tmp_path
    )
    argv = seen[1][0]
    assert argv[:5] == wrap(tmp_path, ["setsid"])[:5]
    assert argv[argv.index("setsid") + 1 :][:2] == ["--ctty", SETPRIV]


def test_on_run_is_switched_too(seen, mode):
    launch.run(["git", "log"], user="ben")
    assert seen[0][0][: len(users.setpriv_argv(ME, SETPRIV))] == users.setpriv_argv(
        ME, SETPRIV
    )


async def test_on_a_user_that_is_gone_raises_permission_error(seen, mode):
    with pytest.raises(PermissionError, match="aegis-no-such-user"):
        await launch.child(["git"], via=None, user="aegis-no-such-user")
    assert seen == []


async def test_on_a_missing_binary_still_raises_file_not_found(seen, mode):
    with pytest.raises(FileNotFoundError):
        await launch.child(["aegis-no-such-binary"], via=None, user="ben")


def test_on_detach_and_browser_are_refused(mode, monkeypatch):
    monkeypatch.setattr(webbrowser, "open", lambda url: pytest.fail("opened"))
    with pytest.raises(PermissionError, match="off in multi-user mode"):
        launch.detach(["true"], user=None)
    with pytest.raises(PermissionError, match="off in multi-user mode"):
        launch.browser("http://x", user=None)


async def test_on_a_switched_child_enters_its_cwd_after_the_switch(
    seen, mode, tmp_path
):
    """The server may not be able to enter a member's home: the cd happens
    as the member, after setpriv, and the program is exec'd so its pid stays."""
    await launch.child(["git", "status"], via=None, user="ben", cwd=tmp_path)
    argv = seen[0][0]
    prefix = users.setpriv_argv(ME, SETPRIV)
    assert seen.cwds == [None]
    assert argv[: len(prefix)] == prefix
    assert argv[len(prefix) :] == [
        "/bin/sh", "-c", 'cd -- "$0" && exec "$@"', str(tmp_path),
        shutil.which("git"), "status",
    ]  # fmt: skip
    await launch.child(["git"], via=None, user=None, cwd=tmp_path)
    assert seen.cwds[1] == tmp_path


async def test_a_call_without_via_is_refused():
    """Required like ``user``, so a new call site has to say whether it exits."""
    with pytest.raises(TypeError, match="via"):
        await launch.child(["true"], user=None)  # type: ignore[call-arg]
