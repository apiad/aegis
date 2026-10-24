# ruff: noqa: F811  (pytest fixtures imported from other test modules)
"""Expiry and staleness flags (knowledge/checks.py): computed on save and in
the daily pass, read as the member, carried by search and knowledge_stats."""

from __future__ import annotations

import json
import threading
from datetime import date

from aegis.knowledge import checks

from .conftest import until
from .knowledge_samples import mem
from .test_acts_for import BEN, recorded  # noqa: F401
from .test_agents import mcp, turn, world  # noqa: F401  (fixture)
from .test_member_view import homes, spawn  # noqa: F401
from .test_member_view import world as members  # noqa: F401

TODAY = date(2026, 10, 24)


def ok(said: str) -> dict:
    assert said.startswith("mcp ok: "), said
    return json.loads(said.removeprefix("mcp ok: "))


def body_item(body: str, **kw):
    it = mem("m", "d", **kw)
    return type(it)(it.kind, it.name, it.description, body, it.meta, None)


def test_quoted_paths_are_paths_only():
    body = (
        "See `repos/bot/run.py` and `AGENTS.md`, not `git -C x`, `<state>/x`,"
        " `https://a.b/c`, `make test` or `True`.\n"
        "```bash\ncat `repos/in/fence.py`\n```\n"
    )
    assert checks.quoted_paths(body) == ["repos/bot/run.py", "AGENTS.md"]


def test_paths_resolve_against_paths_then_the_roots():
    got = checks.candidates("src/x.py", ["repos/bot/*", "/abs/dir"], ["/w", "/h"])
    assert got == [
        "/w/repos/bot/src/x.py",
        "/h/repos/bot/src/x.py",
        "/abs/dir/src/x.py",
        "/w/src/x.py",
        "/h/src/x.py",
    ]
    assert checks.candidates("~/notes.md", [], ["/w", "/h"]) == ["/h/notes.md"]
    assert checks.candidates("/etc/hosts", ["x"], ["/w"]) == ["/etc/hosts"]


def test_a_temporary_memory_past_its_date_is_expired():
    it = mem("m", "d", expires="2026-10-01")
    assert checks.flags(it, TODAY, lambda p: True, ["/w"]) == ["expired"]
    assert checks.flags(it, date(2026, 9, 1), lambda p: True, ["/w"]) == []


def test_flags_report_each_missing_path():
    it = body_item("`a/here.py` and `a/gone.py`")
    seen = []

    def exists(p):
        seen.append(p)
        return p == "/w/a/here.py"

    assert checks.flags(it, TODAY, exists, ["/w"]) == ["stale:a/gone.py"]
    assert seen == ["/w/a/here.py", "/w/a/gone.py"]


async def test_a_temporary_memory_past_its_date_is_expired_and_leaves_the_hint(
    world,
):
    app = world.app
    s = await world.spawn()
    await app.knowledge.store.save(
        app.user, mem("freeze", "release freeze this week", expires="2000-01-01"), "t"
    )
    await app.knowledge_check(app.user)
    assert "freeze" not in app.knowledge.hint_for(app.user, s.log_id, "freeze", None)
    hit = ok(await turn(s, mcp("memory_search", query="release freeze")))["hits"][0]
    assert hit["name"] == "freeze" and hit["flags"] == ["expired"]
    rows = app.knowledge.usage.rows(app.user, "freeze")
    assert [r["detail"] for r in rows if r["event"] == "flagged"] == ["expired"]


async def test_a_missing_backticked_path_flags_stale_but_stays_hinted(world):
    app = world.app
    s = await world.spawn()
    got = ok(
        await turn(
            s,
            mcp(
                "memory_save",
                name="bot-runner",
                description="The bot starts from its run script",
                body="Start the bot with `repos/bot/run.py`.",
                asked=True,
            ),
        )
    )
    assert got["flags"] == ["stale:repos/bot/run.py"]
    hint = app.knowledge.hint_for(app.user, s.log_id, "start the bot", None)
    assert "bot-runner" in hint
    stats = ok(await turn(s, mcp("knowledge_stats")))["items"]
    assert stats[0]["flags"] == ["stale:repos/bot/run.py"]
    # The file appears: the daily pass clears the flag.
    (world.root / "repos" / "bot").mkdir(parents=True)
    (world.root / "repos" / "bot" / "run.py").write_text("")
    await app.knowledge_check(app.user)
    assert ok(await turn(s, mcp("knowledge_stats")))["items"][0]["flags"] == []


async def test_a_present_path_raises_no_flag(world):
    app = world.app
    s = await world.spawn()
    (world.root / "deploy.md").write_text("where it runs")
    got = ok(
        await turn(
            s,
            mcp(
                "memory_save",
                name="deploy-note",
                description="Where the bot is deployed",
                body="Read `deploy.md` first.",
                asked=True,
            ),
        )
    )
    assert "flags" not in got
    rows = app.knowledge.usage.rows(app.user, "deploy-note")
    assert not [r for r in rows if r["event"] == "flagged"]


async def test_stats_filters_by_flag(world):
    app = world.app
    s = await world.spawn()
    st = app.knowledge.store
    await st.save(app.user, mem("old", "past", expires="2000-01-01"), "t")
    await st.save(app.user, body_item("`gone/x.py`"), "t")
    await st.save(app.user, mem("fine", "nothing wrong"), "t")
    await app.knowledge_check(app.user)

    async def names(**kw):
        items = ok(await turn(s, mcp("knowledge_stats", **kw)))["items"]
        return sorted(i["name"] for i in items)

    assert await names(flag="expired") == ["old"]
    assert await names(flag="stale") == ["m"]
    assert await names() == ["fine", "m", "old"]


async def test_a_members_paths_are_checked_as_the_member_in_their_home(members, homes):
    app = members.app
    ben = app.sessions.sessions[await spawn(members, BEN)]
    home = homes["ben"].pw_dir
    (__import__("pathlib").Path(home) / "plan.md").write_text("x")
    threads = []
    real = app.knowledge.exists

    def exists(p):
        threads.append(threading.current_thread().name)
        return real(p)

    app.knowledge.exists = exists
    got = ok(
        await turn(
            ben,
            mcp(
                "memory_save",
                name="ben-plan",
                description="Ben keeps his plan in one file",
                body="It is `plan.md`; the old one was `old/plan.md`.",
                asked=True,
            ),
        )
    )
    assert got["flags"] == ["stale:old/plan.md"]
    assert threads and all(t.startswith("as-ben") for t in threads)


async def test_the_daily_pass_does_not_run_on_the_event_loop(world):
    app = world.app
    await app.knowledge.store.save(app.user, body_item("`gone/x.py`"), "t")
    loop_thread = threading.current_thread()
    threads = []
    real = app.knowledge.exists

    def exists(p):
        threads.append(threading.current_thread())
        return real(p)

    app.knowledge.exists = exists
    await app.knowledge_check(app.user)
    assert threads and loop_thread not in threads


async def test_the_boot_pass_flags_what_a_restart_forgot(world):
    app = world.app
    await app.knowledge.store.save(app.user, body_item("`gone/x.py`"), "t")
    await world.restart()
    kn = world.app.knowledge
    await until(lambda: kn.flags_of(world.app.user, "m") == ["stale:gone/x.py"])
