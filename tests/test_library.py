"""aegis as a library: a Server in this process, driven through Session and
srv.ops, with the fake claude behind it (spec 2026-10-23-library-server)."""

import asyncio
import signal
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

from aegis import OpError, Server, Session, SessionNotFound, StartError, Turn
from aegis.api import (
    Prose,
    RunInfo,
    ScheduleInfo,
    ScheduleNotFound,
    SessionInfo,
    TaskInfo,
    TaskNotFound,
    ToolCall,
    TurnEnded,
    UserMessage,
)
from aegis.api.stubs import render

CONFIG = """\
default_agent: opus
agents:
  opus: {harness: claude-code, model: opus, effort: low, permission: full}
queues:
  work: {agent: opus, max_parallel: 1}
"""

STUB = Path(__file__).parents[1] / "src" / "aegis" / "api" / "ops.pyi"


@pytest.fixture
async def srv(tmp_path: Path, fake_claude: str):
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    async with Server(tmp_path, claude=fake_claude, name="lab") as s:
        yield s


async def test_it_listens_on_a_free_port_and_serves_the_client(srv: Server):
    assert srv.base_url.startswith("http://127.0.0.1:")
    async with httpx.AsyncClient(follow_redirects=True) as c:
        r = await c.get(srv.url)
    assert r.status_code == 200


async def test_a_spawned_turn_ends_with_its_text(srv: Server):
    s = await srv.sessions.spawn("/md hello")
    turn = await s.wait(timeout=20)
    assert isinstance(turn, Turn)
    assert turn.text == "hello"
    assert turn.attention == "done"
    info = await s.info()
    assert isinstance(info, SessionInfo)
    assert (info.log_id, info.state, info.agent) == (s.log_id, "idle", "opus")


async def test_ask_returns_only_its_own_turn(srv: Server):
    s = await srv.sessions.spawn("/md first")
    await s.wait(timeout=20)
    turn = await s.ask("/md second", timeout=20)
    assert turn.text == "second"
    assert isinstance(turn.entries[0], UserMessage)
    assert turn.entries[0].md == "/md second"
    assert turn.cost_usd is not None and turn.cost_usd > 0


async def test_events_follow_a_tool_turn_in_order(srv: Server):
    # No prompt: the first message is the harness's first turn. Its card can
    # be read idle while the turn's rows are still queued behind it, and every
    # row must still come before TurnEnded (failed 24 of 24 under load).
    s = await srv.sessions.spawn()
    await s.wait(timeout=20)
    seen: list = []

    async def follow() -> None:
        async for ev in s.events():
            seen.append(ev)
            if isinstance(ev, TurnEnded):
                return

    follower = asyncio.create_task(follow())
    await asyncio.sleep(0.1)  # subscribed before the message goes out
    await s.send("/bash listing => a.txt")
    await asyncio.wait_for(follower, 20)
    kinds = [type(e) for e in seen if type(e) in (UserMessage, ToolCall, Prose)]
    assert kinds == [UserMessage, ToolCall]
    assert isinstance(seen[-1], TurnEnded)
    tool = next(e for e in seen if isinstance(e, ToolCall))
    assert tool.status == "ok"


async def test_interrupt_ends_a_long_turn(srv: Server):
    s = await srv.sessions.spawn("/busy 30")
    await asyncio.sleep(0.5)
    await s.interrupt()
    turn = await s.wait(timeout=10)
    assert turn.attention != "working"


async def test_get_list_and_close(srv: Server):
    s = await srv.sessions.spawn("/md hi")
    await s.wait(timeout=20)
    info = await s.info()
    assert await srv.sessions.get(info.handle) == s == await srv.sessions.get(s.log_id)
    assert s in await srv.sessions.list()
    await s.close()
    assert s not in await srv.sessions.list()
    with pytest.raises(SessionNotFound):
        await s.info()
    with pytest.raises(SessionNotFound):
        await srv.sessions.get("no-such-session")


async def test_every_operation_is_a_typed_method(srv: Server):
    assert await srv.ops.server.version()
    assert await srv.ops.meta()  # an operation with no dot
    with pytest.raises(OpError) as e:
        await srv.ops.session.spawn(bogus=1)
    assert e.value.code == "bad_params"
    with pytest.raises(AttributeError):
        srv.ops.session.no_such_op
    names = {f"{ns}.{m}" for ns in dir(srv.ops) for m in dir(getattr(srv.ops, ns))}
    dotted = {n for n in srv.app.registry.names() if "." in n}
    assert {n.replace(".", "_").replace("_", ".", 1) for n in dotted} <= names | dotted


async def test_the_committed_stub_matches_the_registry(srv: Server):
    fresh = subprocess.run(
        [sys.executable, "-m", "ruff", "format", "--stdin-filename", "ops.pyi", "-"],
        input=render(srv.app.registry),
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert fresh == STUB.read_text(), "run `make ops-stubs` and commit ops.pyi"


async def test_one_server_per_process(tmp_path: Path, fake_claude: str):
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    async with Server(tmp_path, claude=fake_claude):
        with pytest.raises(RuntimeError):
            async with Server(tmp_path, claude=fake_claude):
                pass
    async with Server(tmp_path, claude=fake_claude) as again:
        assert again.token


async def test_a_failed_check_raises_instead_of_exiting(tmp_path: Path):
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    state = tmp_path / ".aegis" / "state"
    state.mkdir(parents=True)
    (state / "daemon.lock").write_text("")
    with pytest.raises(StartError) as e:
        async with Server(tmp_path):
            pass
    assert "before 2.0" in e.value.lines[0]


async def test_the_host_keeps_its_signal_handlers(tmp_path: Path, fake_claude: str):
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    before = signal.getsignal(signal.SIGINT)
    async with Server(tmp_path, claude=fake_claude):
        assert signal.getsignal(signal.SIGINT) is before
    assert signal.getsignal(signal.SIGINT) is before


def test_session_handles_compare_by_log_id():
    a, b = Session(None, "x"), Session(None, "x")  # type: ignore[arg-type]
    assert a == b and hash(a) == hash(b) and a != Session(None, "y")  # type: ignore[arg-type]


# -- queues, workflows and schedules (slice 2) ------------------------------


async def test_a_task_runs_on_a_worker_and_returns_its_result(srv: Server):
    task = await srv.queues.enqueue("work", "/md task done")
    info = await task.wait(timeout=20)
    assert isinstance(info, TaskInfo)
    assert (info.status, info.result, info.queue) == ("completed", "task done", "work")
    assert info.worker is not None
    assert await srv.queues.get(task.id) == task
    with pytest.raises(TaskNotFound):
        await srv.queues.get("task-nope")


async def test_a_pending_task_cancels(srv: Server):
    busy = await srv.queues.enqueue("work", "/busy 30")
    queued = await srv.queues.enqueue("work", "/md never")
    assert (await queued.info()).status == "pending"  # max_parallel: 1
    assert (await queued.cancel()).status == "cancelled"
    assert (await busy.cancel()).status == "cancelled"


HELLO = """id: hello
title: Say hello
say: Print hello.
root:
  sh: "echo hello"
  id: greet
  say: Print hello.
"""

GATED = """id: gated
title: Ask, then print
say: Ask the main agent, then print.
root:
  seq:
    - gate: {by: main, ask: "Go on?"}
      id: ok
      say: Ask the main agent to go on.
    - sh: "echo after"
      id: after
      say: Print after.
  say: Ask, then print.
"""


async def test_a_workflow_runs_once_approved(srv: Server):
    owner = await srv.sessions.spawn("/md ready")
    await owner.wait(timeout=20)
    run = await srv.workflows.arm(HELLO, owner=owner, approve=True)
    info = await run.wait(timeout=20)
    assert isinstance(info, RunInfo)
    assert info.status == "done"
    assert [(n.id, n.state, n.summary) for n in info.nodes] == [
        ("greet", "done", "exit 0: hello")
    ]
    assert await run.owner() == owner
    assert run in await srv.workflows.list()


async def test_a_rejected_workflow_never_runs(srv: Server):
    owner = await srv.sessions.spawn("/md ready")
    await owner.wait(timeout=20)
    run = await srv.workflows.arm(HELLO, owner=owner)
    assert (await run.info()).status == "armed"
    assert (await run.reject()).status == "rejected"


async def test_wait_stops_at_a_gate_and_answer_passes_it(srv: Server):
    owner = await srv.sessions.spawn("/md ready")
    await owner.wait(timeout=20)
    run = await srv.workflows.arm(GATED, owner=owner, approve=True)
    info = await run.wait(timeout=20)
    assert info.status == "running"
    assert [o.kind for o in info.open] == ["gate"]
    await run.answer(info.open[0].to, choice="continue")
    info = await run.wait(timeout=20)
    assert info.status == "done"


async def test_a_schedule_is_created_fired_paused_and_deleted(srv: Server):
    sch = await srv.schedules.create("hourly-tick", every="1h", prompt="/md tick")
    info = await sch.info()
    assert isinstance(info, ScheduleInfo)
    assert (info.clock, info.paused) == ({"every": "1h"}, None)
    assert info.next is not None
    before = len(await srv.sessions.list())
    assert await sch.fire()
    assert len(await srv.sessions.list()) == before + 1
    assert (await sch.pause()).paused is not None
    assert (await sch.resume()).paused is None
    with pytest.raises(ValueError):
        await srv.schedules.create(
            "bad-clock", every="1h", cron="0 6 * * *", prompt="x"
        )
    await sch.delete()
    with pytest.raises(ScheduleNotFound):
        await srv.schedules.get("hourly-tick")
