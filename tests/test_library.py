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
from aegis.api import Prose, SessionInfo, ToolCall, TurnEnded, UserMessage
from aegis.api.stubs import render

CONFIG = """\
default_agent: opus
agents:
  opus: {harness: claude-code, model: opus, effort: low, permission: full}
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
    s = await srv.sessions.spawn()
    await s.wait(timeout=20)  # the harness's start is busy too
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
