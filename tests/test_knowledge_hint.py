import pytest

from aegis.knowledge import hint
from aegis.knowledge.search import Hit
from aegis.transcript.entries import split_notices
from aegis.transcript.store import read_store

from .conftest import until
from .knowledge_samples import mem
from .test_acts_for import BEN, ben_home, mu_world, recorded  # noqa: F401
from .test_commands import app, send, spawn  # noqa: F401


def hit(name, description):
    return Hit("skill", name, description, 1.0)


def sends(a, lid):
    records, _ = read_store(a.sessions.store_path(lid))
    return [r["text"] for r in records if r.get("kind") == "send"]


def test_the_block_names_three_of_each_and_cuts_descriptions():
    b = hint.block([hit("s1", "x" * 300)] * 3, [hit("m1", "fact")])
    assert b.startswith(hint.HEADER) and "x" * 121 not in b and "x" * 120 in b
    assert b.splitlines()[2] == "memories: m1: fact"
    assert split_notices("hello\n\n" + b) == ("hello", [b])


def test_an_empty_store_adds_nothing():
    assert hint.block([], []) == ""


async def test_a_send_carries_the_hint_and_a_slash_command_does_not(app):  # noqa: F811
    await app.knowledge.store.save(
        app.user, mem("bot-host", "the bot runs on host b"), "t"
    )
    lid = await spawn(app)
    s = app.sessions.sessions[lid]
    await send(app, lid, "deploy the bot")
    await until(
        lambda: any(
            "knowledge · may be relevant" in (e.get("md") or "") for e in s.entries()
        ),
        what="the hint row",
    )
    assert "bot-host" in sends(app, lid)[-1]
    rows = app.knowledge.usage.rows(app.user, "bot-host")
    assert [(r["event"], r["detail"], r["session"]) for r in rows] == [
        ("hinted", "rank=1", lid)
    ]
    assert app.knowledge.read_rank(lid, "bot-host") == 1
    await until(lambda: s.status == "idle", what="the first turn")
    await send(app, lid, "/compact")
    await until(lambda: len(sends(app, lid)) == 2, what="the command's send")
    assert "may be relevant" not in sends(app, lid)[-1]


async def test_a_send_with_no_match_carries_no_hint(app):  # noqa: F811
    await app.knowledge.store.save(
        app.user, mem("bot-host", "the bot runs on host b"), "t"
    )
    lid = await spawn(app)
    await send(app, lid, "hello there")
    await until(lambda: sends(app, lid), what="the send")
    assert "may be relevant" not in sends(app, lid)[-1]


@pytest.fixture
async def world(tmp_path, fake_claude, monkeypatch, ben_home, recorded):  # noqa: F811
    w = await mu_world(tmp_path, fake_claude, monkeypatch)
    yield w
    await w.stop()


async def test_a_member_is_never_hinted_another_principals_memory(world):
    w = world.app
    await w.knowledge.store.save(w.user, mem("secret-fact", "the bot host"), "t")
    await w.knowledge.store.save("ben", mem("ben-fact", "the bot host"), "t")
    r = await w.registry.call("session.spawn", {"agent": "opus"}, BEN)
    lid = r["log_id"]
    await w.registry.call("session.send", {"log_id": lid, "text": "the bot host"}, BEN)
    await until(lambda: sends(w, lid), what="ben's send")
    assert "ben-fact" in sends(w, lid)[-1]
    assert "secret-fact" not in sends(w, lid)[-1]
