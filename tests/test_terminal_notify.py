import asyncio
import pytest
from pathlib import Path
from aegis.terminal.manager import TerminalManager, CommandRecord
from aegis.terminal.notify import build_inbox_message, make_terminal_notifier


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    return tmp_path / "state"


def test_build_inbox_message_shape():
    rec = CommandRecord(
        seq=0, cmd="pytest", writer="agent:alice",
        started_at="2026-05-22T14:03:21Z",
        finished_at="2026-05-22T14:03:25Z",
        duration_s=4.2, exit=0,
        stdout="line1\nline2\nline3\n", stderr="",
    )
    msg = build_inbox_message("build", rec)
    assert msg.sender == "term:build"
    assert "pytest" in msg.body
    assert "exit 0" in msg.body
    assert "agent:alice" in msg.body
    assert "line3" in msg.body


def test_build_inbox_message_includes_stderr_block_when_present():
    rec = CommandRecord(
        seq=1, cmd="bad", writer="human",
        started_at="x", finished_at="y", duration_s=0.1, exit=1,
        stdout="", stderr="boom\n",
    )
    msg = build_inbox_message("t", rec)
    assert "stderr" in msg.body
    assert "boom" in msg.body


async def test_notifier_wakes_subscribers_except_writer(state_dir):
    delivered: list[tuple[str, str]] = []

    class FakeRouter:
        async def deliver(self, handle: str, message) -> None:
            delivered.append((handle, message.body))

    mgr = TerminalManager(state_dir=state_dir)
    mgr.set_notifier(make_terminal_notifier(FakeRouter()))
    await mgr.spawn(name="n1", shell="/bin/bash")
    mgr.subscribe("n1", "agent:alice")
    mgr.subscribe("n1", "agent:bob")
    await mgr.run("n1", "true", writer="agent:alice")
    await asyncio.sleep(0.1)
    handles = {h for h, _ in delivered}
    assert "agent:bob" in handles
    assert "agent:alice" not in handles
    await mgr.close("n1")


async def test_human_writer_wakes_all_subscribers(state_dir):
    delivered: list[str] = []

    class FakeRouter:
        async def deliver(self, handle: str, message) -> None:
            delivered.append(handle)

    mgr = TerminalManager(state_dir=state_dir)
    mgr.set_notifier(make_terminal_notifier(FakeRouter()))
    await mgr.spawn(name="n2", shell="/bin/bash")
    mgr.subscribe("n2", "agent:alice")
    mgr.subscribe("n2", "agent:bob")
    await mgr.run("n2", "true", writer="human")
    await asyncio.sleep(0.1)
    assert set(delivered) == {"agent:alice", "agent:bob"}
    await mgr.close("n2")
