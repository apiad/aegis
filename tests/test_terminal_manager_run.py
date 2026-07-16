import asyncio
import os
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


# --- P2: the captured stdout excludes the echoed command line ---------


async def test_stdout_excludes_command_echo(state_dir):
    mgr = TerminalManager(state_dir=state_dir)
    await mgr.spawn(name="e1", shell="/bin/bash")
    rec = await mgr.run("e1", "echo hello", writer="human")
    # The PTY echoes the typed command; the B marker must reset the
    # capture so the record holds only real output.
    assert rec.stdout.strip() == "hello"
    assert "echo hello" not in rec.stdout
    await mgr.close("e1")


# --- P2: multi-line commands are rejected, not silently corrupted -----


async def test_multiline_command_rejected(state_dir):
    mgr = TerminalManager(state_dir=state_dir)
    await mgr.spawn(name="ml", shell="/bin/bash")
    with pytest.raises(ValueError):
        await mgr.run("ml", "echo a\necho b", writer="human")
    await mgr.close("ml")


# --- P1: marker-injection fallback when OSC 133 is unavailable ---------


async def test_run_falls_back_to_injection_without_osc133(state_dir):
    mgr = TerminalManager(state_dir=state_dir)
    await mgr.spawn(name="fb", shell="/bin/bash")
    # Simulate a shell where the init hooks never took (no A/B/D from the
    # prompt cycle). run() must inject its own markers and still resolve.
    mgr._terminals["fb"].osc133_ok = False
    rec = await mgr.run("fb", "sh -c 'exit 5'", writer="human", timeout=5.0)
    assert rec.exit == 5
    assert rec.timed_out is False
    await mgr.close("fb")


# --- P1: a bounded default timeout (no infinite hang) -----------------


async def test_run_times_out(state_dir):
    mgr = TerminalManager(state_dir=state_dir)
    await mgr.spawn(name="to", shell="/bin/bash")
    rec = await mgr.run("to", "sleep 10", writer="human", timeout=0.3)
    assert rec.timed_out is True
    assert rec.exit is None
    await mgr.close("to")


# --- P1: reader-loop death finalizes a pending waiter (no hang) -------


async def test_reader_crash_finalizes_pending(state_dir):
    mgr = TerminalManager(state_dir=state_dir)
    await mgr.spawn(name="rc", shell="/bin/bash")
    state = mgr._terminals["rc"]

    # Force the parser to blow up on the next chunk after the command is
    # written, so the reader loop hits its except-Exception path.
    orig_feed = state.parser.feed
    calls = {"n": 0}

    def boom(chunk):
        calls["n"] += 1
        if calls["n"] >= 1:
            raise RuntimeError("synthetic parser crash")
        return orig_feed(chunk)

    state.parser.feed = boom
    rec = await mgr.run("rc", "echo hi", writer="human", timeout=5.0)
    # Resolved via the crash-finalize path rather than hanging.
    assert rec.exit is None
    await mgr.close("rc")


# --- P3: spawning does not clobber the user's shell integration -------


async def test_spawn_preserves_user_prompt_command(state_dir, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    # A user PROMPT_COMMAND that writes a marker on every prompt cycle.
    beacon = home / "beacon"
    (home / ".bashrc").write_text(
        f'export PROMPT_COMMAND="echo tick >> {beacon}"\n'
    )
    mgr = TerminalManager(state_dir=state_dir)
    await mgr.spawn(name="pc", shell="/bin/bash",
                    env={**os.environ, "HOME": str(home)})
    rec = await mgr.run("pc", "true", writer="human", timeout=5.0)
    # Our OSC 133 exit detection still works …
    assert rec.exit == 0
    # … and the user's PROMPT_COMMAND was preserved (it fired too).
    assert beacon.exists() and beacon.read_text().strip() != ""
    await mgr.close("pc")
