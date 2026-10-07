"""Agents using aegis through its MCP endpoint, end to end.

The real app runs under uvicorn in the test's event loop; sessions run the fake
claude, whose `/mcp <tool> <json>` POSTs a real tools/call with the token from
its --mcp-config. So identity, the inbox, monitors and queues are exercised the
way a real Claude Code process reaches them.
"""

import asyncio
import json
import socket
from pathlib import Path

import httpx
import pytest
import uvicorn

from aegis.app import App
from aegis.roots import make_roots
from aegis.web import build_web

from .conftest import until

CONFIG = """\
default_agent: opus
agents:
  opus: {model: opus, effort: high, permission: full}
queues:
  general: {agent: opus, max_parallel: 2}
  solo: {agent: opus, max_parallel: 1}
"""


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class World:
    def __init__(self, root: Path, fake: str):
        self.root, self.fake = root, fake
        self.port = _free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self.app: App | None = None
        self._server = None
        self._task = None

    async def start(self) -> "World":
        self.app = App(
            make_roots(self.root, None),
            claude_bin=self.fake,
            base_url=self.base,
            interrupt_timeout=1,
        )
        web = build_web(self.app, "tok", {f"127.0.0.1:{self.port}"})
        self._server = uvicorn.Server(
            uvicorn.Config(web, host="127.0.0.1", port=self.port, log_level="warning")
        )
        self._task = asyncio.create_task(self._server.serve())
        await until(lambda: self._server.started, timeout=10, what="uvicorn")
        return self

    async def stop(self) -> None:
        self._server.should_exit = True
        await asyncio.wait_for(self._task, 20)

    async def restart(self) -> None:
        await self.stop()
        await self.start()

    async def spawn(self) -> "object":
        r = await self.app.registry.call("session.spawn", {"profile": "opus"})
        return self.app.sessions.sessions[r["log_id"]]

    def session(self, log_id):
        return self.app.sessions.sessions.get(log_id)


@pytest.fixture
async def world(tmp_path, fake_claude):
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    w = await World(tmp_path, fake_claude).start()
    yield w
    await w.stop()


def mcp(tool: str, **args) -> str:
    return f"/mcp {tool} {json.dumps(args)}"


async def turn(s, text: str, timeout: float = 8) -> str:
    """Send a prompt, wait for the session's next idle, return its last prose."""
    before = len([e for e in s.entries() if e["kind"] == "prose"])
    await s.send(text)
    await until(
        lambda: (
            s.status == "idle"
            and len([e for e in s.entries() if e["kind"] == "prose"]) > before
        ),
        timeout=timeout,
        what=f"the turn {text[:40]!r}",
    )
    return [e["md"] for e in s.entries() if e["kind"] == "prose"][-1]


def inbox(s) -> list[dict]:
    return [e for e in s.entries() if e["kind"] == "inbox"]


# -- the endpoint -----------------------------------------------------------------
async def test_the_tools_are_named_after_their_operations_and_take_no_handle(world):
    async with httpx.AsyncClient() as c:
        r = await c.post(
            world.base + "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={"accept": "application/json, text/event-stream"},
        )
    tools = {t["name"]: t for t in r.json()["result"]["tools"]}
    assert {
        "monitor_start",
        "monitor_cancel",
        "monitor_list",
        "queue_enqueue",
        "task_status",
        "task_cancel",
        "task_resume",
        "peer_handoff",
        "peer_read",
        "session_list",
        "session_rename",
        "meta",
        "file_send",
    } <= set(tools)
    assert "session_spawn" not in tools and "session_close" not in tools
    for t in tools.values():
        assert "from_handle" not in t["inputSchema"].get("properties", {})


async def test_a_call_without_a_valid_token_is_refused(world):
    async with httpx.AsyncClient() as c:
        for headers in ({}, {"X-Aegis2-Session": "forged"}):
            r = await c.post(
                world.base + "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": "session_list", "arguments": {}},
                },
                headers={"accept": "application/json, text/event-stream", **headers},
            )
            res = r.json()["result"]
            assert res["isError"] and "unknown session" in res["content"][0]["text"]


async def test_a_call_acts_as_its_own_session(world):
    a, b = await world.spawn(), await world.spawn()
    said = await turn(a, mcp("session_list"))
    listed = json.loads(said.removeprefix("mcp ok: "))
    assert {x["handle"]: x["you"] for x in listed} == {a.handle: True, b.handle: False}
    said = await turn(a, mcp("session_rename", title="Renamed by itself"))
    assert a.title == "Renamed by itself"
    said = await turn(a, mcp("session_rename", log_id=b.log_id, title="nope"))
    assert said.startswith("mcp error: not_yours")
    rows = [e for e in a.entries() if e["kind"] == "tool"]
    assert rows[0]["title"] == "session_list" and rows[0]["glyph"] == "⇄"


# -- the inbox ----------------------------------------------------------------------
async def test_messages_to_a_busy_session_are_held_and_arrive_once_in_one_turn(world):
    a, b = await world.spawn(), await world.spawn()
    await b.send("/sleep 1")
    await until(
        lambda: b.status == "working" and any(e["kind"] == "tool" for e in b.entries()),
        what="b busy",
    )
    await turn(a, mcp("peer_handoff", target=b.handle, context="first note"))
    await turn(a, mcp("peer_handoff", target=b.handle, context="second note"))
    assert len(b.held) == 2
    await until(lambda: inbox(b), timeout=8, what="the delivery")
    await until(lambda: b.status == "idle" and not b.busy, what="b idle")
    (row,) = inbox(b)
    assert row["md"].count(f"> from agent:{a.handle}") == 2
    assert row["md"].index("first note") < row["md"].index("second note")
    assert b.held == []


async def test_peer_read_shows_the_other_side(world):
    a, b = await world.spawn(), await world.spawn()
    await turn(b, "hello from b")
    said = await turn(a, mcp("peer_read", target=b.handle, last=5))
    assert "user: hello from b" in said and "agent: You said:" in said


# -- monitors ----------------------------------------------------------------------
async def test_a_monitor_wakes_its_owner_with_the_roster(world, tmp_path):
    a = await world.spawn()
    flag = tmp_path / "flag"
    said = await turn(
        a,
        mcp(
            "monitor_start",
            description="wait for the flag",
            done=f"test -f {flag}",
            interval_s=1,
        ),
    )
    first = json.loads(said.removeprefix("mcp ok: "))["monitor_id"]
    await turn(
        a, mcp("monitor_start", description="never", done="false", interval_s=60)
    )
    assert [m["description"] for m in a.wire()["monitors"]] == [
        "wait for the flag",
        "never",
    ]
    flag.touch()
    await until(lambda: inbox(a), timeout=10, what="the wake")
    (wake,) = inbox(a)
    assert wake["title"].startswith(f"monitor:{first} · ok")
    assert "Your other live monitors" in wake["md"] and "never" in wake["md"]


async def test_monitor_progress_and_fail(world, tmp_path):
    a = await world.spawn()
    (tmp_path / "pct").write_text("42\n")
    await turn(
        a,
        mcp(
            "monitor_start",
            description="build",
            done="false",
            fail="test -f broke",
            progress="cat pct",
            interval_s=1,
        ),
    )
    await until(
        lambda: a.wire()["monitors"] and a.wire()["monitors"][0]["progress"] == 42,
        what="progress",
    )
    (tmp_path / "broke").touch()
    await until(lambda: inbox(a), timeout=10, what="the fail wake")
    assert " · fail · " in inbox(a)[0]["title"]


async def test_an_agent_cannot_cancel_another_agents_monitor(world):
    a, b = await world.spawn(), await world.spawn()
    said = await turn(
        a, mcp("monitor_start", description="mine", done="false", interval_s=60)
    )
    mid = json.loads(said.removeprefix("mcp ok: "))["monitor_id"]
    said = await turn(b, mcp("monitor_cancel", monitor_id=mid))
    assert said.startswith("mcp error: not_yours")
    said = await turn(a, mcp("monitor_cancel", monitor_id=mid))
    assert json.loads(said.removeprefix("mcp ok: "))["remaining"] == []


async def test_a_monitor_survives_a_restart_and_resumes_a_stopped_owner(
    world, tmp_path
):
    a = await world.spawn()
    flag = tmp_path / "flag2"
    await turn(
        a,
        mcp(
            "monitor_start",
            description="after restart",
            done=f"test -f {flag}",
            interval_s=1,
        ),
    )
    log_id = a.log_id
    await world.restart()
    a2 = world.session(log_id)
    assert (
        a2.status == "stopped"
        and a2.wire()["monitors"][0]["description"] == "after restart"
    )
    flag.touch()
    await until(lambda: inbox(a2), timeout=12, what="the wake after the restart")
    await until(lambda: a2.status == "idle", what="the resumed turn")


# -- queues ------------------------------------------------------------------------
async def test_a_worker_reports_back_and_is_archived(world):
    a = await world.spawn()
    said = await turn(
        a, mcp("queue_enqueue", queue="general", payload="summarize the README")
    )
    task_id = json.loads(said.removeprefix("mcp ok: "))["task_id"]
    await until(lambda: inbox(a), timeout=12, what="the callback")
    (cb,) = inbox(a)
    assert cb["title"].startswith(f"queue:general · task#{task_id} · ok")
    assert "You said: **summarize the README**" in cb["md"]
    t = world.app.queues.tasks[task_id]
    assert t.status == "completed" and t.worker in world.app.sessions.archived


async def test_a_worker_with_a_live_monitor_is_not_finished(world, tmp_path):
    a = await world.spawn()
    flag = tmp_path / "worker-flag"
    payload = mcp(
        "monitor_start",
        description="worker waits",
        done=f"test -f {flag}",
        interval_s=1,
    )
    said = await turn(a, mcp("queue_enqueue", queue="general", payload=payload))
    task_id = json.loads(said.removeprefix("mcp ok: "))["task_id"]
    t = world.app.queues.tasks[task_id]
    await until(
        lambda: (
            t.worker
            and world.session(t.worker)
            and world.session(t.worker).status == "idle"
            and any(e["kind"] == "tool" for e in world.session(t.worker).entries())
        ),
        timeout=10,
        what="worker waiting",
    )
    await asyncio.sleep(1.5)
    assert t.status == "running" and not inbox(a), (
        "a turn ending with a live monitor finished the task"
    )
    flag.touch()
    await until(
        lambda: t.status == "completed" and inbox(a),
        timeout=12,
        what="completion after the wake",
    )
    assert "monitor:" in inbox(a)[0]["md"]  # the worker's last prose answered its wake


async def test_a_worker_with_an_open_background_task_is_not_finished(world):
    a = await world.spawn()
    said = await turn(a, mcp("queue_enqueue", queue="general", payload="/bgtask 2"))
    t = world.app.queues.tasks[json.loads(said.removeprefix("mcp ok: "))["task_id"]]
    await until(
        lambda: t.worker and world.session(t.worker).open_tasks,
        timeout=10,
        what="the background task",
    )
    await asyncio.sleep(1)
    assert t.status == "running"
    await until(
        lambda: t.status == "completed" and inbox(a), timeout=10, what="completion"
    )
    assert "the background task finished" in inbox(a)[0]["md"]


async def test_a_dead_worker_fails_its_task_and_keeps_its_tab(world):
    a = await world.spawn()
    said = await turn(a, mcp("queue_enqueue", queue="general", payload="/exit 4"))
    t = world.app.queues.tasks[json.loads(said.removeprefix("mcp ok: "))["task_id"]]
    await until(lambda: inbox(a), timeout=10, what="the error callback")
    assert (
        " · error · " in inbox(a)[0]["title"]
        and "exited with code 4" in inbox(a)[0]["md"]
    )
    assert t.status == "failed" and t.worker in world.app.sessions.sessions


async def test_the_parallel_limit_holds_tasks_back(world):
    a = await world.spawn()
    ids = []
    for i in range(2):
        said = await turn(
            a, mcp("queue_enqueue", queue="solo", payload="/sleep 1", callback=False)
        )
        ids.append(json.loads(said.removeprefix("mcp ok: "))["task_id"])
    first, second = (world.app.queues.tasks[i] for i in ids)
    assert first.status == "running" and second.status == "pending"
    await until(lambda: second.status == "running", timeout=12, what="the second task")


async def test_agents_cancel_only_their_own_tasks(world):
    a, b = await world.spawn(), await world.spawn()
    said = await turn(
        a, mcp("queue_enqueue", queue="solo", payload="/sleep 30", callback=False)
    )
    task_id = json.loads(said.removeprefix("mcp ok: "))["task_id"]
    said = await turn(b, mcp("task_cancel", task_id=task_id))
    assert said.startswith("mcp error: not_yours")
    said = await turn(a, mcp("task_cancel", task_id=task_id))
    assert json.loads(said.removeprefix("mcp ok: "))["status"] == "cancelled"


async def test_a_running_task_resumes_after_a_restart(world):
    a = await world.spawn()
    said = await turn(a, mcp("queue_enqueue", queue="general", payload="/sleep 30"))
    task_id = json.loads(said.removeprefix("mcp ok: "))["task_id"]
    t = world.app.queues.tasks[task_id]
    await until(
        lambda: (
            t.worker
            and any(e["kind"] == "tool" for e in world.session(t.worker).entries())
        ),
        what="worker",
    )
    await world.restart()
    t2 = world.app.queues.tasks[task_id]
    worker = world.session(t2.worker)
    await until(
        lambda: any("server restarted" in (e.get("md") or "") for e in inbox(worker)),
        timeout=12,
        what="the nudge",
    )
    a2 = world.session(a.log_id)
    await until(lambda: inbox(a2), timeout=12, what="the callback after the restart")
    assert world.app.queues.tasks[task_id].status == "completed"


# -- files -------------------------------------------------------------------------
def files_sent(s) -> list[dict]:
    return [e for e in s.entries() if e["kind"] == "file"]


async def test_an_agent_sends_a_file_and_the_link_serves_its_bytes(world, tmp_path):
    a = await world.spawn()
    (tmp_path / "out").mkdir()
    chart = tmp_path / "out" / "chart.png"
    chart.write_bytes(b"first png")
    said = await turn(a, mcp("file_send", path="out/chart.png", caption="Weekly"))
    first = json.loads(said.removeprefix("mcp ok: "))
    assert first["name"] == "chart.png" and first["size"] == 9
    assert first["mime"] == "image/png" and first["url"].startswith("/files/")
    (e,) = files_sent(a)
    assert e["md"] == "Weekly" and e["detail"]["url"] == first["url"]

    chart.write_bytes(b"second png")
    said = await turn(a, mcp("file_send", path=str(chart)))
    second = json.loads(said.removeprefix("mcp ok: "))
    assert second["url"] != first["url"]
    async with httpx.AsyncClient() as c:
        assert (await c.get(world.base + first["url"])).content == b"first png"
        assert (await c.get(world.base + second["url"])).content == b"second png"


async def test_a_relative_path_resolves_against_the_session_cwd(world, tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "notes.md").write_text("# Notes\n")
    r = await world.app.registry.call(
        "session.spawn", {"profile": "opus", "cwd": "sub"}
    )
    s = world.session(r["log_id"])
    said = await turn(s, mcp("file_send", path="notes.md"))
    assert json.loads(said.removeprefix("mcp ok: "))["name"] == "notes.md"
    assert files_sent(s)[0]["detail"]["excerpt"] == "# Notes"


async def test_a_directory_is_not_a_file(world, tmp_path):
    a = await world.spawn()
    said = await turn(a, mcp("file_send", path="."))
    assert said.startswith("mcp error: not_a_file")
    assert files_sent(a) == []


async def test_a_missing_relative_path_says_where_it_looked(world, tmp_path):
    # The agent wrote out/chart.png, then sent chart.png: its shell's cd does
    # not carry over, so the name resolves against the session's cwd.
    a = await world.spawn()
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "chart.png").write_bytes(b"png")
    said = await turn(a, mcp("file_send", path="chart.png"))
    assert said.startswith("mcp error: not_found")
    assert f"session's working directory, {tmp_path}" in said
    assert "absolute path" in said
