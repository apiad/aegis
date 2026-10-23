# ruff: noqa: F811  (fixtures imported from test_member_view and test_commands)
"""The memory tools and /remember, through the fake claude's real MCP calls."""

import json

import pytest

from aegis.knowledge import history
from aegis.ops import OpError

from .conftest import until
from .knowledge_samples import mem
from .test_acts_for import BEN, recorded  # noqa: F401
from .test_agents import mcp, turn
from .test_agents import world as agents_world  # noqa: F401
from .test_commands import send
from .test_member_view import ADMIN, homes, spawn, world  # noqa: F401


def ok(said: str):
    assert said.startswith("mcp ok: "), said
    return json.loads(said.removeprefix("mcp ok: "))


async def test_an_agent_saves_searches_reads_and_deletes_a_memory(agents_world):
    app = agents_world.app
    s = await agents_world.spawn()
    saved = ok(
        await turn(
            s,
            mcp(
                "memory_save",
                name="quill-staging",
                description="The staging host for project quill",
                body="It is ben-staging-2.",
                type="project",
            ),
        )
    )
    assert saved == {"saved": "quill-staging", "status": "approved"}
    hits = ok(await turn(s, mcp("memory_search", query="where is quill staged")))
    assert hits["hits"][0]["name"] == "quill-staging"
    got = ok(await turn(s, mcp("memory_read", names=["quill-staging"])))
    text = got["memories"][0]["text"]
    assert "ben-staging-2" in text and "type: project" in text
    assert f"source: session {s.handle}" in text
    root = app.roots.state_root / "knowledge" / app.user
    assert history.log(root, "memories/quill-staging.md")[0]["message"] == (
        "save memory quill-staging"
    )
    gone = ok(await turn(s, mcp("memory_delete", name="quill-staging")))
    assert gone["deleted"] == "quill-staging" and len(gone["sha"]) == 40
    said = await turn(s, mcp("memory_read", names=["quill-staging"]))
    assert said.startswith("mcp error: not_found"), said


async def test_a_temporary_memory_needs_an_expiry(agents_world):
    s = await agents_world.spawn()
    said = await turn(
        s,
        mcp(
            "memory_save",
            name="freeze",
            description="d",
            body="b",
            lifetime="temporary",
        ),
    )
    assert said.startswith("mcp error: bad_params"), said


async def test_a_member_cannot_read_the_admins_memory_by_name(world):
    app = world.app
    await app.knowledge.store.save(app.user, mem("admin-fact", "the admin's fact"), "t")
    ben = app.sessions.sessions[await spawn(world, BEN)]
    said = await turn(ben, mcp("memory_read", names=["admin-fact"]))
    assert said.startswith("mcp error: not_found"), said
    assert ok(await turn(ben, mcp("memory_search", query="admin fact"))) == {"hits": []}
    ok(await turn(ben, mcp("memory_save", name="ben-fact", description="d", body="b")))
    assert app.knowledge.store.get("ben", "ben-fact") is not None
    assert app.knowledge.store.get(app.user, "ben-fact") is None


async def test_a_read_after_a_hint_records_its_rank(agents_world):
    app = agents_world.app
    await app.knowledge.store.save(
        app.user, mem("bot-host", "the bot runs on host b"), "t"
    )
    s = await agents_world.spawn()
    await turn(s, "where does the bot run")  # the send carries the hint
    ok(await turn(s, mcp("memory_read", names=["bot-host"])))
    rows = app.knowledge.usage.rows(app.user, "bot-host")
    assert [(r["event"], r["detail"]) for r in rows] == [
        ("hinted", "rank=1"),
        ("read", "rank=1"),
    ]


async def test_remember_delivers_one_turn_naming_memory_save(agents_world):
    app = agents_world.app
    s = await agents_world.spawn()
    lid = s.log_id
    assert await send(app, lid, "/remember the demo is on friday") == {
        "status": "delivered"
    }

    def inbox():
        return [
            e["md"]
            for e in s.entries()
            if e["kind"] == "inbox" and "> from remember" in (e.get("md") or "")
        ]

    await until(inbox, timeout=5)
    (md,) = inbox()
    assert "the demo is on friday" in md
    assert "memory_save" in md and "asked: true" in md


async def test_read_permission_sessions_may_search_but_not_save(agents_world):
    r = await agents_world.app.registry.call("session.spawn", {"agent": "reviewer"})
    s = agents_world.session(r["log_id"])
    ok(await turn(s, mcp("memory_search", query="anything")))
    ok(await turn(s, mcp("knowledge_stats")))
    said = await turn(s, mcp("memory_save", name="x", description="d", body="b"))
    assert said.startswith("mcp error: read_only"), said


async def test_stats_join_usage_with_items(agents_world):
    app = agents_world.app
    await app.knowledge.store.save(app.user, mem("used"), "t")
    await app.knowledge.store.save(app.user, mem("idle"), "t")
    app.knowledge.usage.add(app.user, "s", "memory", "used", "read")
    s = await agents_world.spawn()
    got = ok(await turn(s, mcp("knowledge_stats", unused_days=30)))
    assert [i["name"] for i in got["items"]] == ["idle"]
    got = ok(await turn(s, mcp("knowledge_stats")))
    assert {i["name"]: i["read"] for i in got["items"]} == {"idle": 0, "used": 1}


async def test_remember_refuses_empty_text_and_read_permission(agents_world):
    app = agents_world.app
    s = await agents_world.spawn()
    for text in ("/remember", "/remember   "):
        with pytest.raises(OpError) as e:
            await send(app, s.log_id, text)
        assert e.value.code == "missing_argument"
    r = await app.registry.call("session.spawn", {"agent": "reviewer"})
    with pytest.raises(OpError) as e:
        await send(app, r["log_id"], "/remember the demo is on friday")
    assert e.value.code == "read_only" and "read permission" in e.value.message
