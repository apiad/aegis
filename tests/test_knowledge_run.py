# ruff: noqa: F811  (fixtures imported from test_agents and test_member_view)
"""skill_run: the scripts a loaded skill declares, run as the session's user.
The real user switch is in tests/multiuser/test_switch.py."""

import json
import os
import signal
import time

from .knowledge_samples import skill
from .test_acts_for import BEN, recorded  # noqa: F401
from .test_agents import mcp, turn
from .test_agents import world as agents_world  # noqa: F401
from .test_member_view import homes, spawn, world  # noqa: F401

SCRIPT = b"""import os, sys
print("args", sys.argv[1:], "cwd-is-temp", os.getcwd() != "/")
print(open("data/note.txt").read().strip())
print("oops", file=sys.stderr)
sys.exit(3)
"""


def ok(said: str):
    assert said.startswith("mcp ok: "), said
    return json.loads(said.removeprefix("mcp ok: "))


async def save_release(app, owner):
    await app.knowledge.store.save(
        owner,
        skill(
            "release",
            "Cut a release",
            aegis={"scripts": ["bump.py", "loop.py", "big.py", "escape.py"]},
        ),
        "t",
        extra={
            "bump.py": SCRIPT,
            "data/note.txt": b"from the folder\n",
            "loop.py": b"import time\nprint('started', flush=True)\ntime.sleep(60)\n",
            "other.py": b"print('never')\n",
            "big.py": b"print('x' * 500000)\n",
            # A child in a session of its own outlives the group's kill and
            # holds the pipes open.
            "escape.py": b"import subprocess, time\n"
            b"p = subprocess.Popen(['setsid', 'sleep', '30'])\n"
            b"print('child', p.pid, flush=True)\ntime.sleep(60)\n",
        },
    )


async def test_run_refuses_an_unloaded_skill(agents_world):
    app = agents_world.app
    await save_release(app, app.user)
    s = await agents_world.spawn()
    said = await turn(s, mcp("skill_run", skill="release", script="bump.py"))
    assert said.startswith("mcp error: not_loaded"), said
    said = await turn(s, mcp("skill_run", skill="nothing", script="bump.py"))
    assert said.startswith("mcp error: not_found"), said


async def test_run_refuses_an_undeclared_script(agents_world):
    app = agents_world.app
    await save_release(app, app.user)
    s = await agents_world.spawn()
    ok(await turn(s, mcp("skill_read", name="release")))
    for script in ("other.py", "../../memories/x.md", "data/note.txt"):
        said = await turn(s, mcp("skill_run", skill="release", script=script))
        assert said.startswith("mcp error: not_declared"), said


async def test_run_returns_output_and_exit_code(agents_world):
    app = agents_world.app
    await save_release(app, app.user)
    s = await agents_world.spawn()
    ok(await turn(s, mcp("skill_read", name="release")))
    got = ok(
        await turn(
            s, mcp("skill_run", skill="release", script="bump.py", args=["v2", "-x"])
        )
    )
    assert got["exit"] == 3
    assert got["stdout"] == "args ['v2', '-x'] cwd-is-temp True\nfrom the folder\n"
    assert got["stderr"] == "oops\n"
    assert s.skill_runs == {"release": 1}
    assert s.wire()["skill_runs"] == {"release": 1}
    rows = app.knowledge.usage.rows(app.user, "release")
    assert [(r["event"], r["detail"]) for r in rows][-1] == ("ran", "bump.py")
    got = ok(
        await turn(
            s,
            mcp("skill_run", skill="release", script="loop.py", timeout_s=1),
            timeout=15,
        )
    )
    assert got["exit"] is None and got["stdout"] == "started\n"
    assert "killed after 1s" in got["stderr"]
    assert s.skill_runs == {"release": 2}
    got = ok(await turn(s, mcp("skill_run", skill="release", script="big.py")))
    assert got["exit"] == 0
    assert got["stdout"] == "x" * 20000 + "\n[cut at 20000 characters]"


async def test_a_members_run_asks_the_launcher_for_the_member(world, recorded):
    app = world.app
    await save_release(app, "ben")
    ben = app.sessions.sessions[await spawn(world, BEN)]
    ok(await turn(ben, mcp("skill_read", name="release")))
    got = ok(await turn(ben, mcp("skill_run", skill="release", script="bump.py")))
    assert got["exit"] == 3
    assert recorded.of("skill-run") == {"ben"}


async def test_a_write_session_may_not_run_a_script(agents_world):
    app = agents_world.app
    await save_release(app, app.user)
    r = await app.registry.call(
        "session.spawn", {"agent": "opus", "permission": "write"}
    )
    s = agents_world.session(r["log_id"])
    ok(await turn(s, mcp("skill_read", name="release")))
    said = await turn(s, mcp("skill_run", skill="release", script="bump.py"))
    assert said.startswith("mcp error: not_allowed"), said
    assert "full permission" in said and s.skill_runs == {}


async def test_a_timed_out_run_returns_though_a_child_holds_its_pipes(agents_world):
    app = agents_world.app
    await save_release(app, app.user)
    s = await agents_world.spawn()
    ok(await turn(s, mcp("skill_read", name="release")))
    started = time.monotonic()
    got = ok(
        await turn(
            s,
            mcp("skill_run", skill="release", script="escape.py", timeout_s=1),
            timeout=20,
        )
    )
    took = time.monotonic() - started
    pid = int(got["stdout"].split()[1])
    try:
        assert got["timed_out"] is True and got["exit"] is None
        assert got["stdout"].startswith("child ")
        assert "killed after 1s" in got["stderr"]
        assert took < 12, took
    finally:
        os.kill(pid, signal.SIGKILL)
