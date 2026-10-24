# ruff: noqa: F811  (fixtures imported from test_agents)
"""The MCP server a skill brings, reached through skill_tool, for the session
that loaded the skill only."""

import json
import os
import signal
from pathlib import Path

from .conftest import until
from .knowledge_samples import skill
from .test_agents import mcp, turn
from .test_agents import world as agents_world  # noqa: F401

ECHO = (Path(__file__).parent / "fixtures" / "skill_mcp_echo.py").read_bytes()


def call(skill: str, tool: str, args: dict) -> str:
    """The /mcp line for skill_tool; test_agents.mcp names its own first
    argument tool."""
    return "/mcp skill_tool " + json.dumps({"skill": skill, "tool": tool, "args": args})


def ok(said: str):
    assert said.startswith("mcp ok: "), said
    return json.loads(said.removeprefix("mcp ok: "))


async def save_echoer(app):
    await app.knowledge.store.save(
        app.user,
        skill(
            "echoer",
            "Echo text through a tool",
            aegis={"mcp": {"command": ["python3", "server.py"]}},
        ),
        "t",
        extra={"server.py": ECHO},
    )


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    with open(f"/proc/{pid}/stat") as f:
        return f.read().split()[2] != "Z"


async def test_a_loaded_skills_mcp_tool_is_callable(agents_world):
    app = agents_world.app
    await save_echoer(app)
    s = await agents_world.spawn()
    said = await turn(s, call("echoer", "echo", {}))
    assert said.startswith("mcp error: not_loaded"), said
    got = ok(await turn(s, mcp("skill_read", name="echoer")))
    assert [t["name"] for t in got["tools"]] == ["echo", "pinged"]
    assert got["tools"][0]["input_schema"]["required"] == ["text"]
    got = ok(await turn(s, call("echoer", "echo", {"text": "hi"})))
    assert got == {"is_error": False, "content": [{"type": "text", "text": "echo: hi"}]}
    said = await turn(s, call("echoer", "nope", {}))
    assert said.startswith("mcp error: no_such_tool"), said
    assert s.skill_runs == {"echoer": 1}
    rows = app.knowledge.usage.rows(app.user, "echoer")
    assert [(r["event"], r["detail"]) for r in rows][-1] == ("ran", "tool=echo")


async def test_another_session_does_not_see_it(agents_world):
    app = agents_world.app
    await save_echoer(app)
    a = await agents_world.spawn()
    b = await agents_world.spawn()
    ok(await turn(a, mcp("skill_read", name="echoer")))
    assert app.skill_servers.running(a.log_id) == ["echoer"]
    said = await turn(b, call("echoer", "echo", {"text": "x"}))
    assert said.startswith("mcp error: not_loaded"), said
    assert app.skill_servers.running(b.log_id) == []
    # A read session loads the skill but starts no server.
    r = await app.registry.call("session.spawn", {"agent": "reviewer"})
    rv = agents_world.session(r["log_id"])
    got = ok(await turn(rv, mcp("skill_read", name="echoer")))
    assert "tools" not in got and app.skill_servers.running(rv.log_id) == []


async def test_the_server_stops_with_the_session(agents_world):
    app = agents_world.app
    await save_echoer(app)
    s = await agents_world.spawn()
    ok(await turn(s, mcp("skill_read", name="echoer")))
    server = app.skill_servers._servers[(s.log_id, "echoer")]
    pid = server.proc.pid
    assert alive(pid)
    # The server runs in a fresh folder, not in the store.
    store = app.knowledge.store.dir(app.user)
    assert not (store / "skills" / "echoer" / "pid.txt").exists()
    # The server is python3 under the shell; its cwd is the unpacked folder.
    (child,) = Path(f"/proc/{pid}/task/{pid}/children").read_text().split()
    folder = Path(os.readlink(f"/proc/{child}/cwd"))
    assert (folder / "server.py").is_file()
    await app.registry.call("session.close", {"log_id": s.log_id})
    await until(lambda: not alive(pid), what="the server's end")
    await until(lambda: not folder.exists(), what="its folder removed")
    assert app.skill_servers.running(s.log_id) == []


async def test_a_server_that_died_starts_again_on_the_next_call(agents_world):
    app = agents_world.app
    await save_echoer(app)
    s = await agents_world.spawn()
    ok(await turn(s, mcp("skill_read", name="echoer")))
    first = app.skill_servers._servers[(s.log_id, "echoer")]
    # The whole group, shell and server: the shell alone would leave the
    # server holding the pipes.
    os.killpg(first.proc.pid, signal.SIGKILL)
    await first.proc.wait()
    got = ok(await turn(s, call("echoer", "echo", {"text": "again"})))
    assert got["content"][0]["text"] == "echo: again"
    assert app.skill_servers._servers[(s.log_id, "echoer")] is not first


async def test_a_write_session_reads_the_skill_but_starts_no_server(agents_world):
    app = agents_world.app
    await save_echoer(app)
    r = await app.registry.call(
        "session.spawn", {"agent": "opus", "permission": "write"}
    )
    s = agents_world.session(r["log_id"])
    got = ok(await turn(s, mcp("skill_read", name="echoer")))
    assert "Echo text through a tool" in got["text"] and "tools" not in got
    assert "full permission" in got["tools_error"]
    assert s.skills == ["echoer"] and app.skill_servers.running(s.log_id) == []
    said = await turn(s, call("echoer", "echo", {"text": "x"}))
    assert said.startswith("mcp error: not_allowed"), said
    assert app.skill_servers.running(s.log_id) == []


async def test_a_refused_signal_never_stops_close_or_shutdown(
    agents_world, monkeypatch
):
    """A member's server runs as another Linux user, whose group the server
    may not signal itself: the stop goes through the launcher as that user,
    and close and shutdown finish either way."""
    app = agents_world.app
    await save_echoer(app)
    s = await agents_world.spawn()
    ok(await turn(s, mcp("skill_read", name="echoer")))
    pid = app.skill_servers._servers[(s.log_id, "echoer")].proc.pid
    real = os.killpg

    def refused(pgid, sig):
        if pgid == pid:
            raise PermissionError(1, "Operation not permitted")
        real(pgid, sig)

    monkeypatch.setattr(os, "killpg", refused)
    await app.registry.call("session.close", {"log_id": s.log_id})
    assert s.log_id in app.sessions.archived
    await until(lambda: not alive(pid), what="the server's end, through kill")
    # A stop that fails outright is logged, and shutdown goes on to the end.
    t = await agents_world.spawn()
    ok(await turn(t, mcp("skill_read", name="echoer")))

    async def broken(log_id):
        raise RuntimeError("cannot stop")

    monkeypatch.setattr(app.skill_servers, "stop_session", broken)
    stopped, stop = [], app.knowledge.usage.stop
    monkeypatch.setattr(
        app.knowledge.usage, "stop", lambda: (stopped.append(1), stop())
    )
    await app.registry.call("session.close", {"log_id": t.log_id})
    assert t.log_id in app.sessions.archived
    monkeypatch.setattr(app.skill_servers, "shutdown", broken)
    await app.shutdown()
    assert stopped == [1]


async def test_a_long_answer_is_cut_and_the_server_answers_on(agents_world):
    app = agents_world.app
    await save_echoer(app)
    s = await agents_world.spawn()
    ok(await turn(s, mcp("skill_read", name="echoer")))
    got = ok(await turn(s, call("echoer", "echo", {"text": "y" * 70000})))
    (text,) = [c["text"] for c in got["content"]]
    assert text == "echo: " + "y" * 19994 + "\n[cut at 20000 characters]"
    got = ok(await turn(s, call("echoer", "echo", {"text": "small"})))
    assert got["content"][0]["text"] == "echo: small"


async def test_a_line_over_the_limit_fails_the_call_and_restarts_the_server(
    agents_world, monkeypatch
):
    from aegis import skill_mcp

    monkeypatch.setattr(skill_mcp, "READ_LIMIT", 4096)
    app = agents_world.app
    await save_echoer(app)
    s = await agents_world.spawn()
    ok(await turn(s, mcp("skill_read", name="echoer")))
    first = app.skill_servers._servers[(s.log_id, "echoer")]
    said = await turn(s, call("echoer", "echo", {"text": "z" * 10000}))
    assert said.startswith("mcp error: mcp_failed"), said
    assert not first.alive
    got = ok(await turn(s, call("echoer", "echo", {"text": "again"})))
    assert got["content"][0]["text"] == "echo: again"
    assert app.skill_servers._servers[(s.log_id, "echoer")] is not first


async def test_a_servers_own_requests_are_answered_not_taken_for_answers(agents_world):
    app = agents_world.app
    await save_echoer(app)
    s = await agents_world.spawn()
    ok(await turn(s, mcp("skill_read", name="echoer")))
    got = ok(await turn(s, call("echoer", "pinged", {})))
    assert got["content"][0]["text"] == "ping {}, roots/list -32601"


async def test_a_changed_skill_restarts_its_server(agents_world):
    app = agents_world.app
    await save_echoer(app)
    s = await agents_world.spawn()
    ok(await turn(s, mcp("skill_read", name="echoer")))
    first = app.skill_servers._servers[(s.log_id, "echoer")]
    ok(await turn(s, call("echoer", "echo", {"text": "a"})))
    assert app.skill_servers._servers[(s.log_id, "echoer")] is first
    louder = ECHO.replace(b'f"echo: {text}"', b'f"ECHO: {text}"')
    await app.knowledge.store.save(
        app.user,
        skill(
            "echoer",
            "Echo text through a tool",
            aegis={"mcp": {"command": ["python3", "server.py"]}},
        ),
        "t",
        extra={"server.py": louder},
    )
    got = ok(await turn(s, call("echoer", "echo", {"text": "b"})))
    assert got["content"][0]["text"] == "ECHO: b"
    assert app.skill_servers._servers[(s.log_id, "echoer")] is not first
    await until(lambda: not alive(first.proc.pid), what="the old server's end")


async def test_a_start_racing_the_sessions_close_leaves_no_server(agents_world):
    import asyncio

    import pytest

    from aegis.skill_mcp import SkillToolError

    app = agents_world.app
    servers = app.skill_servers
    started, go, made = asyncio.Event(), asyncio.Event(), []
    real = servers._start

    async def slow(*args):
        server = await real(*args)
        made.append(server)
        started.set()
        await go.wait()
        return server

    servers._start = slow

    async def tar():
        from aegis.knowledge_ops import _tar

        return _tar({"server.py": ECHO})

    task = asyncio.create_task(
        servers.ensure("L1", "echoer", ["python3", "server.py"], tar, None, 1)
    )
    await started.wait()
    await servers.stop_session("L1")
    go.set()
    with pytest.raises(SkillToolError, match="closing"):
        await task
    assert servers.running("L1") == [] and ("L1", "echoer") not in servers._servers
    await until(lambda: not alive(made[0].proc.pid), what="the late server's end")
