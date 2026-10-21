"""The four ways aegis starts a process (launch.py), each against a real child."""

from __future__ import annotations

import asyncio
import os
import subprocess
import time
from pathlib import Path

import pytest

from aegis import launch
from aegis.harness import OOM_SCORE_ADJ


async def test_child_runs_argv_and_returns_the_process(tmp_path):
    p = await launch.child(["sh", "-c", "echo hi"], cwd=tmp_path, stdout=launch.PIPE)
    out, _ = await p.communicate()
    assert out == b"hi\n" and p.returncode == 0


async def test_child_without_env_inherits_the_servers(monkeypatch):
    monkeypatch.setenv("AEGIS_LAUNCH_PROBE", "x")
    p = await launch.child(["sh", "-c", "echo $AEGIS_LAUNCH_PROBE"], stdout=launch.PIPE)
    assert (await p.communicate())[0] == b"x\n"


async def test_child_with_a_missing_binary_raises_file_not_found(tmp_path):
    with pytest.raises(FileNotFoundError):
        await launch.child(["no-such-binary-aegis"])
    with pytest.raises(FileNotFoundError):
        await launch.child(["no-such-binary-aegis"], cgroup=tmp_path)


async def test_child_with_oom_raises_the_score():
    p = await launch.child(["sleep", "5"], oom=True)
    try:
        score = Path(f"/proc/{p.pid}/oom_score_adj").read_text().strip()
        assert score == str(OOM_SCORE_ADJ)
    finally:
        p.kill()
        await p.wait()


async def test_child_with_new_session_leads_its_own_session():
    p = await launch.child(["sleep", "5"], new_session=True)
    try:
        assert os.getsid(p.pid) == p.pid
    finally:
        p.kill()
        await p.wait()


async def test_pty_gives_the_child_the_terminal(tmp_path):
    master, slave = os.openpty()
    try:
        p = await launch.pty(["sh", "-c", "tty"], tty=slave, cwd=tmp_path, env=None)
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
    r = launch.run(["cat"], input=b"abc")
    assert r.stdout == b"abc" and r.returncode == 0


def test_run_timeout_raises_timeout_expired():
    with pytest.raises(subprocess.TimeoutExpired):
        launch.run(["sleep", "5"], timeout=0.2)


def test_detach_starts_a_new_session_and_does_not_wait():
    p = launch.detach(["sleep", "5"])
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
        p = await launch.child(["true"])
        await p.wait()

    asyncio.run(go())
    assert seen == [("true",)]
