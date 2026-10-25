"""One terminal on a real PTY: the reader, the decoder, the cap, input, stop."""

import asyncio
import os

import pytest

from aegis import terminal
from aegis.ops import OpError
from aegis.term_store import CastStore
from aegis.terminal import Terminal

from .conftest import until


class Rec:
    def __init__(self):
        self.ops: list[dict] = []
        self.exits = 0

    def out(self, t, ops):
        self.ops += ops

    def exit(self, t):
        self.exits += 1

    def text(self) -> str:
        return "".join(o.get("o", "") for o in self.ops)


async def run(tmp_path, argv):
    rec = Rec()
    meta = {"id": "t-20261014-101500-a1b2c3", "name": "t", "profile": "t",
            "cols": 120, "rows": 40}  # fmt: skip
    t = Terminal(meta, CastStore(tmp_path / "t"), rec.out, rec.exit)
    env = {"PATH": os.environ["PATH"], "TERM": "xterm-256color", "HOME": str(tmp_path)}
    await t.start(argv, tmp_path, env, None)
    return t, rec


async def test_output_reaches_subscribers_and_the_exit_code_is_kept(tmp_path):
    t, rec = await run(tmp_path, ["sh", "-c", "printf 'hello\\n'; exit 3"])
    await asyncio.wait_for(t.exited.wait(), 3)
    assert "hello\r\n" in rec.text()
    assert t.meta["code"] == 3 and rec.exits == 1 and not t.running


async def test_typed_input_is_echoed_and_run(tmp_path):
    t, rec = await run(tmp_path, ["sh"])
    t.input("echo hi-$((40+2))\r")
    await until(lambda: "hi-42" in rec.text(), what="echo")
    t.input("exit\r")
    await asyncio.wait_for(t.exited.wait(), 3)


async def test_ctrl_c_reaches_the_foreground_program(tmp_path):
    t, rec = await run(tmp_path, ["sh", "-c", "echo ready; exec sleep 100"])
    await until(lambda: "ready" in rec.text(), what="ready")
    t.input("\x03")
    await asyncio.wait_for(t.exited.wait(), 3)
    assert t.meta["code"] == -2  # SIGINT, sent by the PTY it has as its terminal


async def test_a_character_split_across_reads_arrives_whole(tmp_path):
    t, rec = await run(
        tmp_path, ["sh", "-c", "printf '\\303'; sleep 0.2; printf '\\251 \\377 end'"]
    )
    await asyncio.wait_for(t.exited.wait(), 3)
    assert "é � end" in rec.text()


async def test_resize_reaches_the_program(tmp_path):
    t, rec = await run(tmp_path, ["sh"])
    t.resize(132, 38)
    t.input("stty size\r")
    await until(lambda: "38 132" in rec.text(), what="stty size")
    assert any(o.get("r") == [132, 38] for o in rec.ops)
    t.input("exit\r")
    await asyncio.wait_for(t.exited.wait(), 3)


async def test_the_reader_holds_a_flood_to_its_rate_and_loses_nothing(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(terminal, "READ_PER_S", 1024 * 1024)
    loop = asyncio.get_running_loop()
    start = loop.time()
    t, rec = await run(tmp_path, ["sh", "-c", "head -c 524288 /dev/zero | tr '\\0' x"])
    await asyncio.wait_for(t.exited.wait(), 5)
    assert rec.text().count("x") == 524288
    assert loop.time() - start >= 0.4  # 512 KB at 1 MB/s


async def test_a_paste_larger_than_the_pty_buffer_arrives_whole(tmp_path):
    t, rec = await run(
        tmp_path, ["sh", "-c", "stty -echo -icanon; echo ready; head -c 200000 | wc -c"]
    )
    await until(lambda: "ready" in rec.text(), what="ready")
    t.input("y" * 200000)
    await until(lambda: "200000" in rec.text(), timeout=5, what="wc -c")


async def test_stop_kills_a_program_that_ignores_sighup(tmp_path):
    t, rec = await run(tmp_path, ["sh", "-c", "trap '' HUP; echo ready; sleep 100"])
    await until(lambda: "ready" in rec.text(), what="ready")
    loop = asyncio.get_running_loop()
    start = loop.time()
    await t.stop(grace=0.5)
    assert t.exited.is_set() and loop.time() - start < 2


async def test_the_last_line_is_plain_text(tmp_path):
    t, rec = await run(
        tmp_path,
        ["sh", "-c", "printf '\\033[32mok\\033[0m\\nwait 10%%\\rdone 100%%\\n'"],
    )
    await asyncio.wait_for(t.exited.wait(), 3)
    assert t.last_line() == "done 100%"


async def test_a_lone_surrogate_in_input_is_replaced_not_raised(tmp_path):
    t, rec = await run(tmp_path, ["cat"])
    t.input("a\ud83db\r")
    await until(lambda: "a?b" in rec.text(), what="the replaced character")
    await t.stop(grace=0.5)


async def test_input_a_program_does_not_read_is_refused_past_the_cap(tmp_path):
    t, rec = await run(tmp_path, ["sleep", "100"])
    with pytest.raises(OpError) as e:
        for _ in range(30):
            t.input("x" * 100_000)
    assert e.value.code == "busy" and len(t._out) <= terminal.INPUT_MAX
    await t.stop(grace=0.5)


async def test_stop_before_the_child_ran_setsid_does_not_hang(tmp_path, monkeypatch):
    spawn = asyncio.create_subprocess_exec

    def late(*argv, **kw):  # the child is still in aegis's process group
        return spawn("sh", "-c", 'sleep 0.5; exec "$@"', "sh", *argv, **kw)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", late)
    t, rec = await run(tmp_path, ["sh", "-c", "trap '' HUP; sleep 100"])
    await asyncio.wait_for(t.stop(grace=0.1), 2.5)
    assert t.exited.is_set()


async def test_stop_kills_a_foreground_job_in_its_own_process_group(tmp_path):
    t, rec = await run(tmp_path, ["bash", "--norc", "-i"])
    t.input("sh -c 'trap \"\" HUP; echo $$ > job.pid; exec sleep 100'\r")
    pidfile = tmp_path / "job.pid"
    await until(lambda: pidfile.exists() and pidfile.read_text().strip(), what="pid")
    pid = int(pidfile.read_text())
    await asyncio.wait_for(t.stop(grace=0.3), 2.5)
    await until(lambda: not _alive(pid), timeout=1, what="the job to end")


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    with open(f"/proc/{pid}/stat") as f:
        return f.read().split(") ")[1][0] != "Z"


def test_terminal_group_alive_treats_a_permission_error_as_alive(monkeypatch):
    from aegis import terminal

    def denied(pgid, sig):
        raise PermissionError("the server cannot inspect the member group")

    monkeypatch.setattr(terminal.os, "killpg", denied)

    assert terminal._alive(4321)
