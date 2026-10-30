"""The sweep of orphaned OpenCode servers (opencode/sweep.py, #620), against
a /proc built in a temporary directory: a sweep of the real one would end the
strays of every other run on the machine."""

from __future__ import annotations

import asyncio
from pathlib import Path

from aegis import launch
from aegis.opencode.sweep import OWNER, SERVE_ARGS, orphans, sweep
from aegis.roots import make_roots

SERVE = ["/usr/bin/opencode", *SERVE_ARGS]


def fake_proc(
    root: Path,
    pid: int,
    *,
    argv: list[str] = SERVE,
    env: dict[str, str] | None = None,
    ppid: int = 500,
    started: int = 1000,
    comm: str = "opencode",
) -> None:
    d = root / str(pid)
    d.mkdir(parents=True)
    (d / "cmdline").write_bytes("\0".join(argv).encode() + b"\0")
    (d / "environ").write_bytes(
        b"".join(f"{k}={v}\0".encode() for k, v in (env or {}).items())
    )
    # pid (comm) state ppid ... with starttime the 22nd field.
    rest = ["S", str(ppid), *["0"] * 17, str(started)]
    (d / "stat").write_text(f"{pid} ({comm}) {' '.join(rest)}\n")
    (d / "comm").write_text(comm + "\n")


def test_a_server_whose_owner_is_alive_is_kept(tmp_path):
    fake_proc(tmp_path, 500, argv=["python", "aegis"], started=77, comm="python")
    fake_proc(tmp_path, 600, env={OWNER: "500:77"})
    assert orphans(tmp_path) == []


def test_a_server_whose_owner_ended_or_whose_pid_was_reused_is_found(tmp_path):
    fake_proc(tmp_path, 500, argv=["python", "aegis"], started=99, comm="python")
    fake_proc(tmp_path, 600, env={OWNER: "500:77"})  # 500 is someone else now
    fake_proc(tmp_path, 601, env={OWNER: "404:12"})  # 404 is gone
    assert sorted(orphans(tmp_path)) == [600, 601]


def test_what_the_server_started_is_never_taken_for_it(tmp_path):
    """Its shell tools and MCP servers inherit OWNER; only the server goes."""
    fake_proc(tmp_path, 600, argv=["bash", "-c", "make"], env={OWNER: "404:12"})
    fake_proc(tmp_path, 601, argv=[*SERVE[:-1], "4096"], env={OWNER: "404:12"})
    assert orphans(tmp_path) == []


def test_a_server_from_before_the_owner_tag_is_found_once_orphaned(tmp_path):
    fake_proc(tmp_path, 2379, argv=["systemd", "--user"], comm="systemd")
    fake_proc(tmp_path, 500, argv=["python", "aegis"], comm="python")
    fake_proc(tmp_path, 600, env={"AEGIS_SESSION": "a"}, ppid=2379)
    fake_proc(tmp_path, 601, env={"AEGIS_SESSION": "b"}, ppid=1)
    fake_proc(tmp_path, 602, env={"AEGIS_SESSION": "c"}, ppid=500)  # a live server's
    fake_proc(tmp_path, 603, ppid=1)  # not aegis's
    assert sorted(orphans(tmp_path)) == [600, 601]


async def test_sweep_ends_an_orphan(tmp_path):
    p = await launch.child(["sleep", "60"], via=None, user=None)
    try:
        fake_proc(tmp_path, p.pid, env={OWNER: "404:12"})
        assert sweep(tmp_path) == [p.pid]
        await asyncio.wait_for(p.wait(), 5)
        assert p.returncode == -15
    finally:
        if p.returncode is None:
            p.kill()
            await p.wait()


async def test_a_server_that_boots_sweeps(tmp_path, fake_claude, monkeypatch):
    """App.boot runs the sweep, before any session starts its own."""
    import aegis.app

    seen = []
    monkeypatch.setattr(aegis.app.opencode_sweep, "sweep", lambda: seen.append(1))
    a = aegis.app.App(make_roots(tmp_path, None), claude_bin=fake_claude)
    await a.boot()
    try:
        assert seen == [1]
    finally:
        await a.shutdown()
