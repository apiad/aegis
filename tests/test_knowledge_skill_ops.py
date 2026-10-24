# ruff: noqa: F811  (fixtures imported from test_member_view and test_agents)
"""The skill tools, through the fake claude's real MCP calls."""

import base64
import json

from .knowledge_samples import skill
from .test_acts_for import BEN, recorded  # noqa: F401
from .test_agents import mcp, turn
from .test_agents import world as agents_world  # noqa: F401
from .test_member_view import homes, spawn, world  # noqa: F401


def ok(said: str):
    assert said.startswith("mcp ok: "), said
    return json.loads(said.removeprefix("mcp ok: "))


async def test_skill_read_marks_the_skill_loaded_once(agents_world):
    app = agents_world.app
    await app.knowledge.store.save(
        app.user,
        skill("release", "Cut a release", "1. bump\n2. tag\n"),
        "t",
        extra={"bump.py": b"print('bumped')\n"},
    )
    s = await agents_world.spawn()
    hits = ok(await turn(s, mcp("skill_search", query="cut a release")))
    assert hits["hits"][0]["name"] == "release"
    got = ok(await turn(s, mcp("skill_read", name="release")))
    assert "1. bump" in got["text"] and got["files"] == ["SKILL.md", "bump.py"]
    assert s.skills == ["release"]
    ok(await turn(s, mcp("skill_read", name="release")))
    assert s.skills == ["release"]
    one = ok(await turn(s, mcp("skill_read", name="release", file="bump.py")))
    assert one == {"name": "release", "file": "bump.py", "text": "print('bumped')\n"}
    assert s.wire()["skills"] == ["release"]
    rows = app.knowledge.usage.rows(app.user, "release")
    assert [r["event"] for r in rows] == ["read", "read"]
    said = await turn(s, mcp("skill_read", name="release", file="../x"))
    assert said.startswith("mcp error: bad_params"), said


async def test_skills_survive_a_restart_in_the_meta(agents_world):
    app = agents_world.app
    await app.knowledge.store.save(app.user, skill("release"), "t")
    await app.knowledge.store.save(app.user, skill("deploy"), "t")
    s = await agents_world.spawn()
    ok(await turn(s, mcp("skill_read", name="deploy")))
    ok(await turn(s, mcp("skill_read", name="release")))
    lid = s.log_id
    await agents_world.restart()
    assert agents_world.session(lid).skills == ["deploy", "release"]


async def test_an_agent_saves_a_skill_with_files(agents_world):
    app = agents_world.app
    s = await agents_world.spawn()
    saved = ok(
        await turn(
            s,
            mcp(
                "skill_save",
                name="release",
                description="Use when cutting a release",
                body="1. run bump.py\n",
                files={"bump.py": base64.b64encode(b"print(1)\n").decode()},
                aegis={"scripts": ["bump.py"]},
            ),
        )
    )
    assert saved == {"saved": "release", "status": "approved"}
    it = app.knowledge.store.get(app.user, "release")
    assert it.kind == "skill" and it.meta["aegis"] == {"scripts": ["bump.py"]}
    assert (
        app.knowledge.store.read_file(app.user, "release", "bump.py") == b"print(1)\n"
    )
    said = await turn(
        s, mcp("skill_save", name="x", description="d", body="b", files={"a": "!!"})
    )
    assert said.startswith("mcp error: bad_params"), said


async def test_a_member_cannot_read_anothers_skill(world):
    app = world.app
    await app.knowledge.store.save(
        app.user, skill("admin-release", "Cut a release"), "t"
    )
    ben = app.sessions.sessions[await spawn(world, BEN)]
    said = await turn(ben, mcp("skill_read", name="admin-release"))
    assert said.startswith("mcp error: not_found"), said
    assert ok(await turn(ben, mcp("skill_search", query="cut a release"))) == {
        "hits": []
    }
    assert ben.skills == []


async def test_read_permission_sessions_read_skills_but_not_save(agents_world):
    app = agents_world.app
    await app.knowledge.store.save(app.user, skill("release"), "t")
    r = await app.registry.call("session.spawn", {"agent": "reviewer"})
    s = agents_world.session(r["log_id"])
    ok(await turn(s, mcp("skill_read", name="release")))
    said = await turn(s, mcp("skill_save", name="x", description="d", body="b"))
    assert said.startswith("mcp error: read_only"), said


async def test_reading_a_file_over_a_mebibyte_is_too_large(agents_world):
    app = agents_world.app
    await app.knowledge.store.save(
        app.user,
        skill("bulky", "Has a big file"),
        "t",
        extra={"big.bin": b"x" * (2**20 + 1), "fits.txt": b"y" * 2**20},
    )
    s = await agents_world.spawn()
    said = await turn(s, mcp("skill_read", name="bulky", file="big.bin"))
    assert said.startswith("mcp error: too_large"), said[:200]
    got = ok(await turn(s, mcp("skill_read", name="bulky", file="fits.txt")))
    assert len(got["text"]) == 2**20


async def test_a_skills_files_are_capped_at_twenty_mebibytes(agents_world):
    import pytest

    from aegis.ops import OpError

    app = agents_world.app
    chunk = base64.b64encode(b"z" * (10 * 2**20)).decode()
    params = {"name": "huge", "description": "Too big", "body": "x"}
    with pytest.raises(OpError) as e:
        await app.registry.call(
            "skill.save", params | {"files": {"a": chunk, "b": chunk, "c": "eg=="}}
        )
    assert e.value.code == "too_large"
    assert app.knowledge.store.get(app.user, "huge") is None
    await app.registry.call("skill.save", params | {"files": {"a": chunk, "b": chunk}})
    assert app.knowledge.store.files(app.user, "huge") == ["SKILL.md", "a", "b"]
