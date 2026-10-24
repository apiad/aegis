# ruff: noqa: F811  (pytest fixtures imported from other test modules)
"""What /consolidate gathers (duplicates and never-recalled items in
knowledge_stats) and the turn it delivers."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from aegis.knowledge import checks
from aegis.knowledge.search import Index
from aegis.knowledge_ops import consolidate_turn
from aegis.ops import OpError

from .conftest import until
from .knowledge_samples import fixture_items, mem, skill
from .test_acts_for import BEN, recorded  # noqa: F401
from .test_agents import mcp, turn, world  # noqa: F401  (fixture)
from .test_commands import send
from .test_member_view import ADMIN, homes, spawn  # noqa: F401
from .test_member_view import world as members  # noqa: F401


def ok(said: str) -> dict:
    assert said.startswith("mcp ok: "), said
    return json.loads(said.removeprefix("mcp ok: "))


def test_planted_duplicates_are_found_and_nothing_else():
    items = fixture_items()
    got = checks.duplicates(Index(items), items, checks.DUPLICATE_SCORE)
    assert [(a, b) for a, b, _ in got] == [
        ("no-force-push", "shared-branch-history"),
        ("database-backup", "restore-database"),
    ]
    assert all(score >= checks.DUPLICATE_SCORE for *_, score in got)


def test_a_duplicate_is_never_across_kinds():
    items = [mem("deploy-the-bot", "deploy the chat bot with docker compose")]
    items.append(
        type(items[0])("skill", "deploy-bot", items[0].description, "b", {}, None)
    )
    assert checks.duplicates(Index(items), items, 0.1) == []


async def test_stats_lists_duplicates_off_the_loop_per_principal(
    members, homes, monkeypatch
):
    app = members.app
    st = app.knowledge.store
    for it in fixture_items():
        if it.kind == "memory":
            await st.save(app.user, it, "t")
    await st.save("ben", mem("ben-fact", "ben's only fact"), "t")
    threads = []
    real = checks.duplicates

    def watched(*a):
        threads.append(threading.current_thread())
        return real(*a)

    monkeypatch.setattr(checks, "duplicates", watched)
    admin = app.sessions.sessions[await spawn(members, ADMIN)]
    got = ok(await turn(admin, mcp("knowledge_stats", duplicates=True)))
    assert [[d["a"], d["b"]] for d in got["duplicates"]] == [
        ["no-force-push", "shared-branch-history"]
    ]
    assert threads and threading.main_thread() not in threads
    ben = app.sessions.sessions[await spawn(members, BEN)]
    assert (
        ok(await turn(ben, mcp("knowledge_stats", duplicates=True)))["duplicates"] == []
    )
    assert "duplicates" not in ok(await turn(admin, mcp("knowledge_stats")))


async def test_never_recalled_needs_ten_hints_and_no_read(world):
    app = world.app
    st, usage = app.knowledge.store, app.knowledge.usage
    for name in ("nine", "ten", "ten-read", "ten-invoked"):
        await st.save(app.user, mem(name), "t")
        for _ in range(9 if name == "nine" else 10):
            usage.add(app.user, "s", "memory", name, "hinted", "rank=1")
    usage.add(app.user, "s", "memory", "ten-read", "read")
    usage.add(app.user, "s", "memory", "ten-invoked", "invoked")
    s = await world.spawn()
    items = ok(await turn(s, mcp("knowledge_stats")))["items"]
    assert {i["name"]: i["never_recalled"] for i in items} == {
        "nine": False,
        "ten": True,
        "ten-read": False,
        "ten-invoked": False,
    }
    got = ok(await turn(s, mcp("knowledge_stats", flag="never_recalled")))
    assert [i["name"] for i in got["items"]] == ["ten"]


KEYS = ("merge:<a>+<b>", "renew:<name>", "drop:<name>", "fix:<name>")
KEYS += ("retire:<name>", "link:<name>")
PAGE = Path(__file__).parent.parent / "src" / "aegis" / "help" / "consolidate.md"


async def test_consolidate_delivers_one_turn_pointing_at_the_page(world):
    app = world.app
    s = await world.spawn()
    said = await send(app, s.log_id, "/consolidate the deploy notes")
    assert said == {"status": "delivered"}
    assert s.person_turn  # what the turn applies after a submit is approved

    def turns():
        return [
            e["md"]
            for e in s.entries()
            if e["kind"] == "inbox" and "> from consolidate" in (e.get("md") or "")
        ]

    await until(turns, timeout=5)
    (md,) = turns()
    assert "/consolidate the deploy notes" in md
    assert "help_read consolidate" in md and "knowledge_stats" in md
    assert all(k in md for k in KEYS)


async def test_consolidate_refuses_a_read_permission_session(world):
    r = await world.app.registry.call("session.spawn", {"agent": "reviewer"})
    with pytest.raises(OpError) as e:
        await send(world.app, r["log_id"], "/consolidate")
    assert e.value.code == "read_only"


def test_the_page_names_every_key():
    page = PAGE.read_text()
    for needle in (*KEYS, "knowledge_stats", "duplicates=true", "checkbox"):
        assert needle in page, needle
    for tool in ("memory_save", "memory_delete", "skill_save", "skill_delete"):
        assert f"`{tool}`" in page, tool
    assert "schedule_create" in page and '"cron": "0 9 1 * *"' in page
    head, body = consolidate_turn("", "now")
    assert head == "> from consolidate · now" and "with no text" in body


async def test_skill_delete_leaves_an_undo_card_that_restores_the_folder(world):
    app = world.app
    st = app.knowledge.store
    await st.save(
        app.user,
        skill("bump-version", "Bump the version"),
        "t",
        extra={"bump.py": b"print('bumped')\n"},
    )
    s = await world.spawn()
    folder = st.get(app.user, "bump-version").path.parent
    gone = ok(await turn(s, mcp("skill_delete", name="bump-version")))
    assert st.get(app.user, "bump-version") is None and not folder.exists()
    (e,) = [x for x in s.entries() if x["id"] == gone["card"]]
    assert (e["detail"]["op"], e["detail"]["item_kind"]) == ("deleted", "skill")
    said = await turn(s, mcp("skill_delete", name="bump-version"))
    assert said.startswith("mcp error: not_found"), said
    await app.registry.call(
        "knowledge.card.undo", {"log_id": s.log_id, "id": gone["card"]}
    )
    assert st.get(app.user, "bump-version") is not None
    assert (folder / "bump.py").read_bytes() == b"print('bumped')\n"


async def test_a_member_cannot_delete_the_admins_skill(members, homes):
    app = members.app
    await app.knowledge.store.save(app.user, skill("admin-skill"), "t")
    ben = app.sessions.sessions[await spawn(members, BEN)]
    said = await turn(ben, mcp("skill_delete", name="admin-skill"))
    assert said.startswith("mcp error: not_found"), said
    assert app.knowledge.store.get(app.user, "admin-skill") is not None
