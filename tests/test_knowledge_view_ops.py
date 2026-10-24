# ruff: noqa: F811  (pytest fixtures imported from other test modules)
"""The Knowledge view's ops (knowledge_ops.py): a person lists, reads,
edits, restores and removes the items of their own store, an admin's
included, and every change is one commit."""

from __future__ import annotations


import pytest

from aegis.knowledge import history
from aegis.ops import Caller, OpError

from .knowledge_samples import mem, skill
from .test_acts_for import BEN, recorded  # noqa: F401
from .test_agents import turn, world  # noqa: F401  (fixture)
from .test_member_view import ADMIN, homes, spawn  # noqa: F401
from .test_member_view import world as members  # noqa: F401


async def call(world, op: str, caller: Caller = ADMIN, **params):
    return await world.app.registry.call(op, params, caller)


async def refused(world, op: str, caller: Caller = ADMIN, **params) -> OpError:
    with pytest.raises(OpError) as e:
        await call(world, op, caller, **params)
    return e.value


async def seed(world, *items, owner: str | None = None, extra=None) -> None:
    app = world.app
    for it in items:
        await app.knowledge.store.save(owner or app.user, it, "test", extra=extra)


def text_of(world, name: str, owner: str | None = None) -> str:
    app = world.app
    it = app.knowledge.store.get(owner or app.user, name)
    assert it is not None and it.path is not None
    return it.path.read_text()


async def test_list_puts_proposals_first_and_filters_by_kind(world):
    await seed(
        world,
        skill("release", "Cut a release of the package"),
        mem("deploy-host", "Where the bot is deployed"),
        mem("merge-after-green", "Merge only after every check", status="proposed"),
    )
    rows = (await call(world, "knowledge.list"))["items"]
    assert [r["name"] for r in rows] == ["merge-after-green", "release", "deploy-host"]
    r = rows[1]
    assert (r["kind"], r["status"], r["flags"], r["reads"]) == (
        "skill",
        "approved",
        [],
        0,
    )
    assert r["description"] == "Cut a release of the package" and r["last_used"] is None
    names = lambda got: [r["name"] for r in got["items"]]  # noqa: E731
    assert names(await call(world, "knowledge.list", kind="skill")) == ["release"]
    assert names(await call(world, "knowledge.list", kind="memory")) == [
        "merge-after-green",
        "deploy-host",
    ]
    assert names(await call(world, "knowledge.list", kind="flagged")) == [
        "merge-after-green"
    ]
    assert names(await call(world, "knowledge.list", query="deployed bot")) == [
        "deploy-host"
    ]


async def test_list_counts_reads_and_marks_an_expired_memory(world):
    app = world.app
    await seed(
        world,
        mem("old-freeze", "No releases", lifetime="temporary", expires="2020-01-01"),
    )
    app.knowledge.usage.add(app.user, "s", "memory", "old-freeze", "read")
    app.knowledge.usage.flush()
    (r,) = (await call(world, "knowledge.list", kind="flagged"))["items"]
    assert r["flags"] == ["expired"] and r["reads"] == 1 and r["last_used"] > 0


async def test_get_returns_the_whole_file_its_stamp_and_a_skills_files(world):
    await seed(world, skill("release", "Cut a release"), extra={"go.py": b"print(1)"})
    got = await call(world, "knowledge.get", name="release")
    assert got["text"] == text_of(world, "release")
    assert got["text"].startswith("---\nname: release\n")
    assert got["files"] == ["SKILL.md", "go.py"] and len(got["stamp"]) == 64
    assert (await refused(world, "knowledge.get", name="nope")).code == "not_found"
    assert (await refused(world, "knowledge.get", name="../x")).code == "bad_name"


async def test_put_saves_the_edit_approved_as_one_commit(world):
    app = world.app
    await seed(world, mem("merge-after-green", "Merge after green", status="proposed"))
    got = await call(world, "knowledge.get", name="merge-after-green")
    text = got["text"].replace("Merge after green", "Merge after every check")
    out = await call(
        world, "knowledge.put", name="merge-after-green", text=text, stamp=got["stamp"]
    )
    assert out["status"] == "approved" and len(out["sha"]) == 40
    it = app.knowledge.store.get(app.user, "merge-after-green")
    assert it.status == "approved" and it.description == "Merge after every check"
    root = app.knowledge.store.dir(app.user)
    top = history.log(root, "memories/merge-after-green.md")[0]
    assert top["message"] == "edit memory merge-after-green" and top["by"] == app.user
    # The stamp it answers is the next edit's.
    again = await call(world, "knowledge.get", name="merge-after-green")
    assert out["stamp"] == again["stamp"]
    # The index sees it at once.
    rows = await call(world, "knowledge.list", query="every check")
    assert [r["name"] for r in rows["items"]] == ["merge-after-green"]


async def test_put_refuses_a_stale_stamp(world):
    app = world.app
    await seed(world, mem("deploy-host", "Where the bot runs"))
    got = await call(world, "knowledge.get", name="deploy-host")
    await seed(world, mem("deploy-host", "The bot runs on the small host"))
    now = text_of(world, "deploy-host")
    err = await refused(
        world, "knowledge.put", name="deploy-host", text=got["text"], stamp=got["stamp"]
    )
    assert err.code == "stale" and text_of(world, "deploy-host") == now
    # A hand edit of the file is a change too.
    fresh = await call(world, "knowledge.get", name="deploy-host")
    path = app.knowledge.store.get(app.user, "deploy-host").path
    path.write_text(now + "edited by hand\n")
    err = await refused(
        world,
        "knowledge.put",
        name="deploy-host",
        text=fresh["text"],
        stamp=fresh["stamp"],
    )
    assert err.code == "stale" and path.read_text().endswith("edited by hand\n")


async def test_put_refuses_a_text_that_does_not_parse(world):
    await seed(world, mem("deploy-host"))
    got = await call(world, "knowledge.get", name="deploy-host")
    err = await refused(
        world, "knowledge.put", name="deploy-host", text="no front", stamp=got["stamp"]
    )
    assert err.code == "bad_params" and "frontmatter" in err.message
    assert text_of(world, "deploy-host") == got["text"]


async def test_put_keeps_a_skills_other_files(world):
    app = world.app
    await seed(world, skill("release"), extra={"go.py": b"print(1)"})
    got = await call(world, "knowledge.get", name="release")
    text = got["text"].replace("1. do release", "1. tag\n2. push")
    await call(world, "knowledge.put", name="release", text=text, stamp=got["stamp"])
    assert app.knowledge.store.files(app.user, "release") == ["SKILL.md", "go.py"]
    assert "2. push" in app.knowledge.store.get(app.user, "release").body


async def test_history_lists_each_commit_and_restore_brings_back_an_old_one(world):
    app = world.app
    await seed(world, mem("deploy-host", "first"))
    first = text_of(world, "deploy-host")
    await seed(world, mem("deploy-host", "second"))
    rows = (await call(world, "knowledge.history", name="deploy-host"))["history"]
    assert [r["message"] for r in rows] == [
        "update memory deploy-host",
        "save memory deploy-host",
    ]
    assert rows[0]["kind"] == "memory" and rows[0]["by"] == "test"
    out = await call(world, "knowledge.restore", name="deploy-host", sha=rows[1]["sha"])
    assert text_of(world, "deploy-host") == first
    rows = (await call(world, "knowledge.history", name="deploy-host"))["history"]
    assert rows[0]["sha"] == out["sha"]
    assert rows[0]["message"].startswith("restore memory deploy-host ")
    hits = (await app.knowledge.index(app.user)).rank("first", "memory")
    assert [h.name for h in hits] == ["deploy-host"]


async def test_remove_deletes_and_its_history_restores_it(world):
    app = world.app
    await seed(world, skill("release", "Cut a release"), extra={"go.py": b"print(1)"})
    out = await call(world, "knowledge.remove", name="release")
    assert (
        len(out["sha"]) == 40 and app.knowledge.store.get(app.user, "release") is None
    )
    assert (await call(world, "knowledge.list"))["items"] == []
    rows = (await call(world, "knowledge.history", name="release"))["history"]
    assert [r["message"] for r in rows] == [
        "delete skill release",
        "save skill release",
    ]
    # The deletion's own version holds nothing to bring back.
    err = await refused(world, "knowledge.restore", name="release", sha=rows[0]["sha"])
    assert err.code == "bad_params"
    await call(world, "knowledge.restore", name="release", sha=rows[1]["sha"][:12])
    assert app.knowledge.store.files(app.user, "release") == ["SKILL.md", "go.py"]
    assert [r["name"] for r in (await call(world, "knowledge.list"))["items"]] == [
        "release"
    ]


async def test_restore_refuses_a_sha_of_another_item(world):
    await seed(world, mem("deploy-host"), mem("other-fact"))
    other = (await call(world, "knowledge.history", name="other-fact"))["history"]
    err = await refused(
        world, "knowledge.restore", name="deploy-host", sha=other[0]["sha"]
    )
    assert err.code == "not_found"


async def test_the_view_ops_are_not_agent_tools(world):
    s = await world.spawn()
    agent = Caller("agent", log_id=s.log_id)
    for op in ("knowledge.list", "knowledge.remove"):
        params = {} if op == "knowledge.list" else {"name": "x"}
        with pytest.raises(OpError) as e:
            await world.app.registry.call(op, params, agent)
        assert e.value.code == "not_for_agents"


async def test_knowledge_list_is_per_principal_for_an_admin_too(members):
    app = members.app
    await seed(members, mem("admin-fact", "The admin's own"))
    await seed(members, mem("ben-fact", "The bot runs on the small host"), owner="ben")
    mine = await call(members, "knowledge.list")
    assert [r["name"] for r in mine["items"]] == ["admin-fact"]
    bens = await call(members, "knowledge.list", BEN)
    assert [r["name"] for r in bens["items"]] == ["ben-fact"]
    for op in ("knowledge.get", "knowledge.remove"):
        assert (await refused(members, op, name="ben-fact")).code == "not_found"
    got = await call(members, "knowledge.history", name="ben-fact")
    assert got["history"] == []
    assert app.knowledge.store.get("ben", "ben-fact") is not None
    # An admin still reads a member's transcript, the hint's row included.
    lid = await spawn(members, BEN)
    await turn(app.sessions.sessions[lid], "where does the bot run on the host")
    rows = app.sessions.sessions[lid].entries()
    assert any("ben-fact" in (e.get("md") or "") for e in rows)
    assert app.sees(app.who(ADMIN), "ben")


async def test_an_edit_and_a_remove_work_in_a_store_filled_by_hand(world):
    app = world.app
    d = app.knowledge.store.dir(app.user) / "memories"
    d.mkdir(parents=True)
    (d / "deploy-host.md").write_text(
        "---\nname: deploy-host\ndescription: x\n---\nA\n"
    )
    (d / "other-fact.md").write_text("---\nname: other-fact\ndescription: y\n---\nB\n")
    got = await call(world, "knowledge.get", name="deploy-host")
    text = got["text"].replace("A\n", "B\n")
    await call(
        world, "knowledge.put", name="deploy-host", text=text, stamp=got["stamp"]
    )
    assert text_of(world, "deploy-host").endswith("B\n")
    await call(world, "knowledge.remove", name="other-fact")
    assert app.knowledge.store.get(app.user, "other-fact") is None
