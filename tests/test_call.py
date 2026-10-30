"""`aegis call` and `aegis tools` from a session's own shell: the fake claude
runs them through its Bash tool (/sh), so the environment is the one aegis
gave the process, and the server is the real app under uvicorn."""

import json
import os
import subprocess
import sys

import pytest

from aegis.transcript.entries import NOTICE_HEADER

from .test_agents import CONFIG, World, mcp, turn
from .test_notices import NOTICED

AEGIS = f"{sys.executable} -m aegis"


@pytest.fixture
async def world(tmp_path, fake_claude):
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    w = await World(tmp_path, fake_claude).start()
    yield w
    await w.stop()


async def sh(s, tmp_path, cmd: str) -> tuple[int, str, str]:
    """Run cmd in the session's shell; its exit code, stdout and stderr."""
    out, err, rc = (tmp_path / n for n in ("out", "err", "rc"))
    await turn(s, f"/sh ({cmd}) > {out} 2> {err}; echo $? > {rc}")
    return int(rc.read_text()), out.read_text(), err.read_text()


async def test_a_call_from_the_shell_runs_as_the_session(world, tmp_path):
    s = await world.spawn()
    rc, out, _ = await sh(s, tmp_path, f"{AEGIS} call session_list")
    assert rc == 0
    me = [row for row in json.loads(out) if row.get("you")]
    assert [row["handle"] for row in me] == [s.handle]


async def test_arguments_as_pairs_as_one_object_and_from_stdin(world, tmp_path):
    s = await world.spawn()
    items = '[{"text": "a", "state": "done"}, {"text": "b", "state": "doing"}]'
    rc, out, _ = await sh(s, tmp_path, f"{AEGIS} call plan_update items='{items}'")
    assert (rc, out.strip()) == (
        0,
        "plan saved: 1 of 2 done. The current plan:\n"
        "  ✓ a\n"
        "  ▶ b\n"
        "Call plan_update again whenever an item starts, finishes, "
        "or the work changes.",
    )
    obj = json.dumps({"items": [{"text": "a", "state": "done"}]})
    rc, out, _ = await sh(s, tmp_path, f"{AEGIS} call plan_update '{obj}'")
    assert (rc, out.split("\n")[0]) == (0, "plan saved: 1 of 1 done. The current plan:")
    rc, out, _ = await sh(s, tmp_path, f"echo '{obj}' | {AEGIS} call plan_update -")
    assert (rc, out.split("\n")[0]) == (0, "plan saved: 1 of 1 done. The current plan:")


async def test_a_tool_error_exits_1_with_the_tools_message(world, tmp_path):
    s = await world.spawn()
    rc, out, err = await sh(s, tmp_path, f"{AEGIS} call plan_update items=nope")
    assert rc == 1 and out == "" and "bad_params" in err
    rc, _, err = await sh(s, tmp_path, f"{AEGIS} call no_such_tool")
    assert rc == 1 and "no_such_tool" in err


async def test_a_token_that_is_not_a_sessions_is_refused(world, tmp_path):
    s = await world.spawn()
    rc, _, err = await sh(
        s, tmp_path, f"AEGIS_SESSION_TOKEN=forged {AEGIS} call session_list"
    )
    assert rc == 1 and "unknown session" in err


def test_outside_a_session_it_says_so_and_exits_2():
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("AEGIS_MCP_URL", "AEGIS_SESSION_TOKEN")
    }
    r = subprocess.run(
        [sys.executable, "-m", "aegis", "call", "session_list"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 2 and "not inside an aegis session" in r.stderr


async def test_tools_lists_the_agent_tools_and_shows_one_schema(world, tmp_path):
    s = await world.spawn()
    rc, out, _ = await sh(s, tmp_path, f"{AEGIS} tools")
    names = {line.split("\t")[0] for line in out.splitlines()}
    assert rc == 0
    assert names == {op.tool_name for op in world.app.registry.agent_ops()}
    rc, out, _ = await sh(s, tmp_path, f"{AEGIS} tools plan_update")
    assert rc == 0 and '"items"' in out


@pytest.fixture
async def noticed(tmp_path, fake_claude):
    (tmp_path / ".aegis.yaml").write_text(NOTICED)
    w = await World(tmp_path, fake_claude).start()
    yield w
    await w.stop()


async def test_aegis_call_output_carries_no_notice(noticed, tmp_path):
    s = await noticed.spawn()
    rc, out, _ = await sh(s, tmp_path, f"{AEGIS} call session_list")
    assert rc == 0 and NOTICE_HEADER not in out
    json.loads(out)
    # Still owed: the agent's next tool call of its own carries it.
    await turn(s, mcp("session_list"))
    rows = [e["title"] for e in s.entries() if e["kind"] == "inbox"]
    assert rows and rows[0].startswith("aegis:notice · disk · warn")
