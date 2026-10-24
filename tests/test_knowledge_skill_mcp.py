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
    assert [t["name"] for t in got["tools"]] == ["echo"]
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
