"""Agents using aegis through its MCP endpoint, end to end.

The real app runs under uvicorn in the test's event loop; sessions run the fake
claude, whose `/mcp <tool> <json>` POSTs a real tools/call with the token from
its --mcp-config. So identity, the inbox, monitors and queues are exercised the
way a real Claude Code process reaches them.
"""

import asyncio
import json
import os
import socket
from pathlib import Path

import httpx
import pytest
import uvicorn

from aegis import files
from aegis.app import App
from aegis.ops import OpError
from aegis.roots import make_roots
from aegis.web import build_web

from .conftest import until

CONFIG = """\
default_agent: opus
agents:
  opus: {harness: claude-code, model: opus, effort: high, permission: full}
  reviewer: {harness: claude-code, model: opus, effort: high, permission: read, priming: You review.}
queues:
  general: {agent: opus, max_parallel: 2}
  solo: {agent: opus, max_parallel: 1}
  reviewing: {agent: reviewer, max_parallel: 1}
  broken: {agent: opus}
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
        r = await self.app.registry.call("session.spawn", {"agent": "opus"})
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
        "monitor_sessions",
        "queue_enqueue",
        "task_status",
        "task_cancel",
        "task_resume",
        "peer_handoff",
        "peer_read",
        "session_list",
        "session_rename",
        "meta",
        "quota_read",
        "file_send",
        "plan_update",
        "turn_end",
        "artifact_create",
        "artifact_send",
        "artifact_read",
        "artifact_update",
        "artifact_close",
    } <= set(tools)
    assert {"session_spawn", "agents_list", "session_close"} <= set(tools)
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
    assert rows[0]["title"] == "session_list" and rows[0]["glyph"] == "swap"


async def test_quota_read_returns_the_snapshot(world):
    a = await world.spawn()
    said = await turn(a, mcp("quota_read"))
    # conftest leaves no credentials, so no provider has anything to say.
    assert json.loads(said.removeprefix("mcp ok: ")) == {"providers": []}


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
            progress=None,
            interval_s=1,
        ),
    )
    first = json.loads(said.removeprefix("mcp ok: "))["monitor_id"]
    await turn(
        a,
        mcp(
            "monitor_start",
            description="never",
            done="false",
            progress=None,
            interval_s=60,
        ),
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


@pytest.mark.slow  # three polls a second apart
async def test_a_monitors_card_carries_its_commands_readings_and_eta(world, tmp_path):
    a = await world.spawn()
    pct = tmp_path / "pct"
    pct.write_text("0\n")
    await turn(
        a,
        mcp(
            "monitor_start",
            description="build",
            done="false",
            progress="cat pct",
            interval_s=1,
        ),
    )
    for prev, v in ((0, 20), (20, 40)):
        await until(
            lambda p=prev: a.wire()["monitors"][0]["progress"] == p,
            timeout=5,
            what=f"{prev}%",
        )
        pct.write_text(f"{v}\n")
    await until(
        lambda: a.wire()["monitors"][0]["progress"] == 40, timeout=5, what="40%"
    )
    (m,) = a.wire()["monitors"]
    assert [r[1] for r in m["readings"]] == [0, 20, 40]
    assert m["eta_at"] > m["readings"][-1][0]
    assert m["eta_basis"].endswith("since progress first moved")
    assert m["cwd"] == str(tmp_path) and m["timeout_s"] == 3600
    checks = {c["kind"]: c for c in m["checks"]}
    assert checks["done"]["cmd"] == "false"
    assert checks["done"]["verdict"] == "not yet" and not checks["done"]["bad"]
    assert checks["progress"]["verdict"] == "printed 40"
    assert checks["fail"]["cmd"] is None
    assert m["broken"] is False


async def test_a_check_that_cannot_run_shows_its_exit_code_and_stderr(world):
    """A missing command exits 127 on every poll; without this it looks exactly
    like a monitor still waiting, until its timeout (#174)."""
    a = await world.spawn()
    await turn(
        a,
        mcp(
            "monitor_start",
            description="never passes",
            done="no-such-aegis-cmd --status",
            progress=None,
            interval_s=1,
        ),
    )
    await until(
        lambda: a.wire()["monitors"][0]["broken"],
        what="the broken check reaches the card",
    )
    (m,) = a.wire()["monitors"]
    done = next(c for c in m["checks"] if c["kind"] == "done")
    assert done["rc"] == 127 and done["verdict"] == "command not found" and done["bad"]
    assert "no-such-aegis-cmd" in done["err"]
    assert done["since"] >= m["started_at"]


async def test_a_monitor_needs_progress_or_an_explicit_null(world):
    """Leaving `progress` out is refused, so an agent decides to opt out instead of
    forgetting: 44 of 51 monitors armed on zion in early October had none (#165)."""
    a = await world.spawn()
    said = await turn(a, mcp("monitor_start", description="forgot", done="false"))
    assert said.startswith("mcp error") and "progress" in said
    assert a.wire()["monitors"] == []
    said = await turn(
        a, mcp("monitor_start", description="opted out", done="false", progress=None)
    )
    assert said.startswith("mcp ok: ")
    assert [m["description"] for m in a.wire()["monitors"]] == ["opted out"]


async def test_an_agent_cannot_cancel_another_agents_monitor(world):
    a, b = await world.spawn(), await world.spawn()
    said = await turn(
        a,
        mcp(
            "monitor_start",
            description="mine",
            done="false",
            progress=None,
            interval_s=60,
        ),
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
            progress=None,
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


# -- waiting on sessions ------------------------------------------------------------
def wait_on(*handles: str, **kw) -> str:
    return mcp(
        "monitor_sessions",
        description="the others",
        sessions=list(handles),
        interval_s=1,
        **kw,
    )


async def hold(s) -> str:
    """Keep ``s`` running: a live monitor of its own makes it ``waiting``."""
    said = await turn(
        s,
        mcp(
            "monitor_start",
            description="hold",
            done="false",
            progress=None,
            interval_s=60,
        ),
    )
    return json.loads(said.removeprefix("mcp ok: "))["monitor_id"]


async def test_waiting_on_sessions_wakes_ok_once_every_one_finished(world):
    a, b, c = await world.spawn(), await world.spawn(), await world.spawn()
    held = await hold(b)
    assert b.wire()["attention"] == "waiting"
    await turn(c, mcp("turn_end", attention="done", line="Done."))
    said = await turn(a, wait_on(b.handle, c.handle))
    assert said.startswith("mcp ok: ")
    await until(
        lambda: a.wire()["monitors"] and a.wire()["monitors"][0]["progress"] == 50,
        what="1 of 2 finished",
    )
    (m,) = a.wire()["monitors"]
    assert [(r["handle"], r["state"]) for r in m["sessions"]] == [
        (b.handle, "running"),
        (c.handle, "finished"),
    ]
    assert m["checks"] == []
    assert inbox(a) == []
    await turn(b, mcp("monitor_cancel", monitor_id=held))
    await turn(b, mcp("turn_end", attention="done", line="Done."))
    await until(lambda: inbox(a), timeout=5, what="the wake")
    (wake,) = inbox(a)
    assert " · ok · " in wake["title"]
    assert f"{b.handle} done" in wake["md"] and f"{c.handle} done" in wake["md"]
    assert a.wire()["monitors"] == []


async def test_a_session_that_needs_the_person_ends_the_wait_blocked(world):
    a, b = await world.spawn(), await world.spawn()
    await hold(b)
    await turn(a, wait_on(b.handle))
    await until(
        lambda: (
            a.wire()["monitors"]
            and a.wire()["monitors"][0]["sessions"][0]["state"] == "running"
        ),
        what="b running",
    )
    await turn(b, mcp("turn_end", attention="needs_you", line="Rebase or merge?"))
    await until(lambda: inbox(a), timeout=5, what="the blocked wake")
    (wake,) = inbox(a)
    assert " · blocked · " in wake["title"]
    assert f"{b.handle} needs you: Rebase or merge?" in wake["md"]


async def test_sessions_already_finished_wake_the_waiter_after_its_turn(world):
    """Review focus 3: nothing to wait for still answers, once the turn ends."""
    a, b = await world.spawn(), await world.spawn()
    await turn(b, mcp("turn_end", attention="review", line="Read the diff."))
    await turn(a, wait_on(b.handle))
    await until(lambda: inbox(a), timeout=5, what="the wake")
    # The wake can land as soon as the arming turn ends, so the last prose may
    # already be the answer to it; the tool's reply is the one before.
    prose = [e["md"] for e in a.entries() if e["kind"] == "prose"]
    assert any(x.startswith("mcp ok: ") for x in prose)
    assert " · ok · " in inbox(a)[0]["title"]


async def test_a_watched_session_is_followed_through_a_rename_and_a_close(world):
    """Review focus 1, 2 and 4: kept by log id, listed once, closed is finished."""
    a, b = await world.spawn(), await world.spawn()
    await hold(b)
    await turn(a, wait_on(b.handle, b.handle))
    (m,) = a.wire()["monitors"]
    assert len(m["sessions"]) == 1
    await turn(b, mcp("session_rename", handle="renamed-peer"))
    await until(
        lambda: a.wire()["monitors"][0]["sessions"][0]["handle"] == "renamed-peer",
        timeout=3,
        what="the new handle on the card",
    )
    await world.app.registry.call("session.close", {"log_id": b.log_id})
    await until(lambda: inbox(a), timeout=5, what="the wake")
    (wake,) = inbox(a)
    assert " · ok · " in wake["title"] and "renamed-peer closed" in wake["md"]


async def test_waiting_on_sessions_refuses_itself_unknown_far_and_archived(world):
    """Review focus 5, and the addresses target() already refuses."""
    a, b = await world.spawn(), await world.spawn()
    cases = (
        ([a.handle], "not_yourself"),
        (["no-such-one"], "no_session"),
        ([f"{b.handle}@far"], "not_across_links"),
    )
    for handles, code in cases:
        said = await turn(a, wait_on(*handles))
        assert said.startswith(f"mcp error: {code}"), said
    await world.app.registry.call("session.close", {"log_id": b.log_id})
    said = await turn(a, wait_on(b.handle))
    assert said.startswith("mcp error: archived"), said
    said = await turn(a, mcp("monitor_sessions", description="x", sessions=[]))
    assert said.startswith("mcp error"), said
    assert a.wire()["monitors"] == []


async def test_a_turn_that_ends_without_turn_end_is_not_finished(world):
    """A turn ended to wait on a queue task, or interrupted by the person, says
    nothing; reading that silence as done released the waiter early (review I1,
    I2). Only an explicit turn_end finishes a watched session."""
    a, b = await world.spawn(), await world.spawn()
    held = await hold(b)
    await turn(a, wait_on(b.handle))
    # Its wait ends in a turn that calls no turn_end: idle, and silent.
    await turn(b, mcp("monitor_cancel", monitor_id=held))
    await until(
        lambda: a.wire()["monitors"][0]["sessions"][0]["attention"] == "idle",
        timeout=3,
        what="b read as idle",
    )
    (m,) = a.wire()["monitors"]
    assert m["sessions"][0]["state"] == "running" and inbox(a) == []
    await turn(b, mcp("turn_end", attention="done", line="Shipped."))
    await until(lambda: inbox(a), timeout=5, what="the wake")
    assert " · ok · " in inbox(a)[0]["title"]


async def test_a_session_silent_past_the_grace_ends_the_wait_blocked(
    world, monkeypatch
):
    """Silence held past the grace is not a transient: the waiter is told,
    rather than released early or left waiting until the timeout. Haiku at low
    effort ends turns with no turn_end (seen in the live test)."""
    import aegis.monitors

    monkeypatch.setattr(aegis.monitors, "SILENT_GRACE_S", 0.5)
    a, b = await world.spawn(), await world.spawn()
    held = await hold(b)
    await turn(a, wait_on(b.handle))
    await turn(b, mcp("monitor_cancel", monitor_id=held))
    await until(lambda: inbox(a), timeout=5, what="the blocked wake")
    (wake,) = inbox(a)
    assert " · blocked · " in wake["title"]
    assert f"{b.handle} went idle without saying it finished" in wake["md"]


@pytest.mark.slow  # a restart
async def test_a_session_monitor_survives_a_restart(world):
    a, b = await world.spawn(), await world.spawn()
    await hold(b)
    await turn(a, wait_on(b.handle))
    await world.restart()
    (m,) = world.session(a.log_id).wire()["monitors"]
    assert m["sessions"][0]["handle"] == b.handle


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
        progress=None,
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
    a2 = world.session(a.log_id)
    await until(lambda: inbox(a2), timeout=12, what="the callback after the restart")
    t2 = world.app.queues.tasks[task_id]
    assert t2.status == "completed"
    # The fake answers the nudge at once, so the worker can complete and be
    # archived tens of ms after the restart (#266): read it through the
    # registry, which keeps an archived session's transcript.
    worker = world.app.sessions.transcript(t2.worker)()["entries"]
    assert any(
        "server restarted" in (e.get("md") or "")
        for e in worker
        if e["kind"] == "inbox"
    )


# -- files -------------------------------------------------------------------------
def files_sent(s) -> list[dict]:
    return [e for e in s.entries() if e["kind"] == "file"]


async def test_an_agent_sends_a_file_and_the_link_serves_its_bytes(world, tmp_path):
    a = await world.spawn()
    (tmp_path / "out").mkdir()
    chart = tmp_path / "out" / "chart.png"
    chart.write_bytes(b"first png")
    said = await turn(a, mcp("file_send", paths=["out/chart.png"], caption="Weekly"))
    (first,) = json.loads(said.removeprefix("mcp ok: "))
    assert first["name"] == "chart.png" and first["size"] == 9
    assert first["mime"] == "image/png" and first["url"].startswith("/files/")
    (e,) = files_sent(a)
    assert e["md"] == "Weekly" and e["detail"]["files"][0]["url"] == first["url"]

    chart.write_bytes(b"second png")
    said = await turn(a, mcp("file_send", paths=[str(chart)]))
    (second,) = json.loads(said.removeprefix("mcp ok: "))
    assert second["url"] != first["url"]
    async with httpx.AsyncClient() as c:
        assert (await c.get(world.base + first["url"])).content == b"first png"
        assert (await c.get(world.base + second["url"])).content == b"second png"


async def test_several_files_go_out_as_one_entry_in_the_order_sent(world, tmp_path):
    a = await world.spawn()
    for name in ("b.png", "a.pdf", "c.md"):
        (tmp_path / name).write_bytes(name.encode())
    said = await turn(
        a, mcp("file_send", paths=["b.png", "a.pdf", "c.md"], caption="Three")
    )
    sent = json.loads(said.removeprefix("mcp ok: "))
    assert [f["name"] for f in sent] == ["b.png", "a.pdf", "c.md"]
    (e,) = files_sent(a)
    assert e["md"] == "Three" and e["summary"] == "3 files"
    assert [f["name"] for f in e["detail"]["files"]] == ["b.png", "a.pdf", "c.md"]
    assert [f["url"] for f in e["detail"]["files"]] == [f["url"] for f in sent]
    assert [f["preview"] for f in e["detail"]["files"]] == ["image", "pdf", "markdown"]
    assert e["detail"]["files"][2]["excerpt"] == "c.md"
    async with httpx.AsyncClient() as c:
        for f in sent:
            assert (await c.get(world.base + f["url"])).content == f["name"].encode()


async def test_one_bad_path_fails_the_call_before_any_file_is_copied(world, tmp_path):
    a = await world.spawn()
    (tmp_path / "a.png").write_bytes(b"png")
    (tmp_path / "big.bin").write_bytes(b"")
    stored = world.app.roots.state_root / "files"
    before = set(stored.iterdir()) if stored.exists() else set()
    said = await turn(a, mcp("file_send", paths=["a.png", "gone.png"]))
    assert said.startswith("mcp error: not_found") and "gone.png" in said
    assert "session's working directory" in said
    os.truncate(tmp_path / "big.bin", files.MAX_BYTES + 1)
    said = await turn(a, mcp("file_send", paths=["a.png", "big.bin"]))
    assert said.startswith("mcp error: too_large") and "big.bin" in said
    assert files_sent(a) == []
    assert (set(stored.iterdir()) if stored.exists() else set()) == before


async def test_a_person_peeks_at_the_file_a_tool_row_read(world, tmp_path):
    a = await world.spawn()
    notes = tmp_path / "notes.md"
    notes.write_text("# Notes\n")
    await turn(a, f"/read {notes}")
    await turn(a, "/fail")
    read, bash = [e for e in a.entries() if e["kind"] == "tool"]
    assert read["detail"]["path"] == str(notes)

    notes.write_text("# Notes, edited since\n")
    peek = world.app.registry.call
    sent = await peek("file.peek", {"log_id": a.log_id, "entry_id": read["id"]})
    (f,) = a.fold().entry(read["id"])["detail"]["peek"]["files"]
    assert sent == [{"url": f["url"], "name": "notes.md", "size": 22}]
    assert f["excerpt"] == "# Notes, edited since"
    assert files_sent(a) == [], "a peek is not a file the agent sent"
    async with httpx.AsyncClient() as c:
        assert (await c.get(world.base + f["url"])).content == notes.read_bytes()

    with pytest.raises(OpError) as e:
        await peek("file.peek", {"log_id": a.log_id, "entry_id": bash["id"]})
    assert e.value.code == "no_path"
    notes.unlink()
    stored = set((world.app.roots.state_root / "files").iterdir())
    with pytest.raises(OpError) as e:
        await peek("file.peek", {"log_id": a.log_id, "entry_id": read["id"]})
    assert e.value.code == "not_found"
    assert set((world.app.roots.state_root / "files").iterdir()) == stored


async def test_a_relative_path_resolves_against_the_session_cwd(world, tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "notes.md").write_text("# Notes\n")
    r = await world.app.registry.call("session.spawn", {"agent": "opus", "cwd": "sub"})
    s = world.session(r["log_id"])
    said = await turn(s, mcp("file_send", paths=["notes.md"]))
    assert json.loads(said.removeprefix("mcp ok: "))[0]["name"] == "notes.md"
    assert files_sent(s)[0]["detail"]["files"][0]["excerpt"] == "# Notes"


async def test_a_directory_is_not_a_file(world, tmp_path):
    a = await world.spawn()
    said = await turn(a, mcp("file_send", paths=["."]))
    assert said.startswith("mcp error: not_a_file")
    assert files_sent(a) == []


async def test_a_missing_relative_path_says_where_it_looked(world, tmp_path):
    # The agent wrote out/chart.png, then sent chart.png: its shell's cd does
    # not carry over, so the name resolves against the session's cwd.
    a = await world.spawn()
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "chart.png").write_bytes(b"png")
    said = await turn(a, mcp("file_send", paths=["chart.png"]))
    assert said.startswith("mcp error: not_found")
    assert f"session's working directory, {tmp_path}" in said
    assert "absolute path" in said


async def test_an_agent_spawns_with_overrides_its_cwd_and_the_agents_priming(
    world, tmp_path
):
    (tmp_path / "sub").mkdir()
    r = await world.app.registry.call("session.spawn", {"agent": "opus", "cwd": "sub"})
    a = world.session(r["log_id"])
    said = await turn(
        a,
        mcp("session_spawn", agent="reviewer", effort="max", cwd=".", prompt="/argv"),
    )
    child = world.session(json.loads(said.removeprefix("mcp ok: "))["log_id"])
    assert child.spec.spawned_by == a.log_id
    assert child.spec.cwd == (tmp_path / "sub").resolve()
    assert child.spec.overridden == ("effort",)
    await until(
        lambda: any(e["kind"] == "prose" for e in child.entries()),
        timeout=8,
        what="the child's first turn",
    )
    md = next(e["md"] for e in child.entries() if e["kind"] == "prose")
    argv = json.loads(md.removeprefix("argv: "))
    assert argv[argv.index("--effort") + 1] == "max"
    system = argv[argv.index("--append-system-prompt") + 1]
    assert system.startswith("You are running inside aegis")
    assert system.endswith("\n\nYou review.")


async def test_an_agent_lists_the_agents(world):
    a = await world.spawn()
    said = await turn(a, mcp("agents_list"))
    listed = json.loads(said.removeprefix("mcp ok: "))
    assert [x["name"] for x in listed["agents"]] == ["opus", "reviewer"]


async def test_a_worker_gets_its_agents_priming(world):
    a = await world.spawn()
    await turn(a, mcp("queue_enqueue", queue="reviewing", payload="/argv"))
    await until(lambda: inbox(a), timeout=12, what="the callback")
    (cb,) = inbox(a)
    assert "You review." in cb["md"]


async def test_a_queue_missing_a_field_says_which(world):
    a = await world.spawn()
    said = await turn(a, mcp("queue_enqueue", queue="broken", payload="x"))
    assert said.startswith("mcp error: bad_config")
    assert "max_parallel is missing" in said


async def test_a_logged_task_on_a_queue_that_broke_does_not_stop_dispatch(world):
    # A task logged while `broken` was valid must not crash the dispatcher
    # with a KeyError on max_parallel; the other queues keep working.
    from aegis.queues import Task

    t = Task(
        id="task-old",
        queue="broken",
        payload="x",
        callback=False,
        enqueuer=None,
        cwd=str(world.root),
    )
    world.app.queues.tasks[t.id] = t
    a = await world.spawn()
    said = await turn(
        a, mcp("queue_enqueue", queue="general", payload="hi", callback=False)
    )
    assert said.startswith("mcp ok")
    # And the stranded task fails with the queue's error instead of waiting
    # forever for a slot the queue can no longer give it.
    old = world.app.queues.tasks["task-old"]
    assert old.status == "failed"
    assert "max_parallel is missing" in old.error


async def test_an_agent_spawns_with_at_most_its_own_permission(world):
    r = await world.app.registry.call("session.spawn", {"agent": "reviewer"})
    reader = world.session(r["log_id"])  # permission: read
    said = await turn(reader, mcp("session_spawn", agent="opus"))
    assert said.startswith("mcp error: not_allowed")
    assert "at most read" in said and "full" in said
    said = await turn(reader, mcp("session_spawn", agent="opus", permission="read"))
    child = world.session(json.loads(said.removeprefix("mcp ok: "))["log_id"])
    assert child.spec.permission == "read"
    # A full-permission agent may hand out less, and a person is not limited.
    full = await world.spawn()
    said = await turn(full, mcp("session_spawn", agent="reviewer"))
    assert said.startswith("mcp ok")


async def test_a_queue_added_on_disk_takes_tasks_without_a_restart(world, tmp_path):
    (tmp_path / ".aegis.yaml").write_text(
        CONFIG + "  late: {agent: opus, max_parallel: 1}\n"
    )
    r = await world.app.registry.call(
        "queue.enqueue", {"queue": "late", "payload": "say hi"}
    )
    t = world.app.queues.tasks[r["task_id"]]
    await until(
        lambda: t.status == "completed", timeout=12, what="the late queue's task"
    )


async def test_raising_max_parallel_on_disk_starts_a_waiting_task(world, tmp_path):
    flag = tmp_path / "hold"
    hold = mcp(
        "monitor_start",
        description="hold",
        done=f"test -f {flag}",
        progress=None,
        interval_s=1,
    )
    ids = [
        (
            await world.app.registry.call(
                "queue.enqueue", {"queue": "solo", "payload": hold}
            )
        )["task_id"]
        for _ in range(2)
    ]
    first, second = (world.app.queues.tasks[i] for i in ids)
    await until(lambda: first.status == "running", timeout=8, what="the first worker")
    assert second.status == "pending"
    (tmp_path / ".aegis.yaml").write_text(
        CONFIG.replace(
            "solo: {agent: opus, max_parallel: 1}",
            "solo: {agent: opus, max_parallel: 2}",
        )
    )
    await until(lambda: second.status == "running", timeout=5, what="the raised limit")
    flag.touch()


async def test_a_broken_file_keeps_spawning_with_the_last_good_agents(world, tmp_path):
    (tmp_path / ".aegis.yaml").write_text("agents: [1, 2\n")
    roster = await world.app.registry.call("agents.list", {})
    assert [a["name"] for a in roster["agents"]] == ["opus", "reviewer"]
    assert ".aegis.yaml" in roster["config_error"]
    await world.spawn()


def _held(tmp_path, n: int, flag):
    from aegis.queues import Task

    hold = mcp(
        "monitor_start",
        description="hold",
        done=f"test -f {flag}",
        progress=None,
        interval_s=1,
    )
    return [
        Task(
            id=f"task-h{i}",
            queue="solo",
            payload=hold,
            callback=False,
            enqueuer=None,
            cwd=str(tmp_path),
            created_at=i,
        )
        for i in range(n)
    ]


async def test_a_moment_without_the_file_fails_no_pending_task(world, tmp_path):
    # Vim and Emacs rename the old file away before writing the new one, and a
    # git checkout unlinks and rewrites it: a read in that gap sees no file.
    q = world.app.queues
    t = _held(tmp_path, 1, tmp_path / "hold")[0]
    q.tasks[t.id] = t
    world.app._config_task.cancel()
    (tmp_path / ".aegis.yaml").unlink()
    await q.dispatch()
    assert t.status == "pending"


async def test_a_config_change_during_a_dispatch_is_not_lost(
    world, tmp_path, monkeypatch
):
    flag = tmp_path / "hold"
    q = world.app.queues
    first, second = _held(tmp_path, 2, flag)
    q.tasks[first.id], q.tasks[second.id] = first, second
    world.app._config_task.cancel()  # only the dispatch under test may see the change
    real = world.app.sessions.spawn
    raised = []

    async def spawn(*args, **kwargs):
        if not raised:
            raised.append(True)
            (tmp_path / ".aegis.yaml").write_text(
                CONFIG.replace(
                    "solo: {agent: opus, max_parallel: 1}",
                    "solo: {agent: opus, max_parallel: 2}",
                )
            )
            await q.dispatch()  # what _on_config schedules; it lands mid-dispatch
        return await real(*args, **kwargs)

    monkeypatch.setattr(world.app.sessions, "spawn", spawn)
    await q.dispatch()
    try:
        await until(
            lambda: second.status == "running", timeout=5, what="the raised limit"
        )
    finally:
        flag.touch()
