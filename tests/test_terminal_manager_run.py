import asyncio
import pytest
from pathlib import Path
from aegis.terminal.manager import TerminalManager, TerminalNotFound


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    return tmp_path / "aegis" / "state" / "terminals"


async def test_run_returns_exit_zero_for_true(state_dir):
    mgr = TerminalManager(state_dir=state_dir)
    await mgr.spawn(name="t1", shell="/bin/bash")
    rec = await mgr.run("t1", "true", writer="agent:tester")
    assert rec.exit == 0
    assert rec.cmd == "true"
    assert rec.writer == "agent:tester"
    await mgr.close("t1")


async def test_run_returns_nonzero_exit_for_false(state_dir):
    mgr = TerminalManager(state_dir=state_dir)
    await mgr.spawn(name="t2", shell="/bin/bash")
    rec = await mgr.run("t2", "false", writer="agent:tester")
    assert rec.exit == 1
    await mgr.close("t2")


async def test_run_captures_stdout(state_dir):
    mgr = TerminalManager(state_dir=state_dir)
    await mgr.spawn(name="t3", shell="/bin/bash")
    rec = await mgr.run("t3", "echo hello", writer="agent:tester")
    assert rec.exit == 0
    assert "hello" in rec.stdout
    await mgr.close("t3")


async def test_run_serializes_concurrent_calls(state_dir):
    mgr = TerminalManager(state_dir=state_dir)
    await mgr.spawn(name="t4", shell="/bin/bash")
    r1, r2 = await asyncio.gather(
        mgr.run("t4", "echo first; sleep 0.05", writer="agent:a"),
        mgr.run("t4", "echo second", writer="agent:b"),
    )
    records = mgr.read("t4", last_n=10)
    cmds = [r.cmd for r in records]
    assert cmds.index("echo first; sleep 0.05") < cmds.index("echo second")
    await mgr.close("t4")


async def test_read_last_n(state_dir):
    mgr = TerminalManager(state_dir=state_dir)
    await mgr.spawn(name="t5", shell="/bin/bash")
    for i in range(3):
        await mgr.run("t5", f"echo {i}", writer="human")
    recs = mgr.read("t5", last_n=2)
    assert len(recs) == 2
    assert recs[-1].cmd == "echo 2"
    await mgr.close("t5")


async def test_read_since_seq(state_dir):
    mgr = TerminalManager(state_dir=state_dir)
    await mgr.spawn(name="t6", shell="/bin/bash")
    for i in range(3):
        await mgr.run("t6", f"echo {i}", writer="human")
    recs = mgr.read("t6", since_seq=1)
    assert [r.cmd for r in recs] == ["echo 2"]
    await mgr.close("t6")


async def test_run_unknown_terminal_errors(state_dir):
    mgr = TerminalManager(state_dir=state_dir)
    with pytest.raises(TerminalNotFound):
        await mgr.run("nope", "true", writer="human")
