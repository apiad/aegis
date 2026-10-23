import pytest

from aegis.knowledge import hint
from aegis.knowledge.service import Knowledge
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
    lines = [ln for ln in b.splitlines() if ln]
    assert lines[2] == "memories: m1: fact"
    assert lines[3] == (
        "read with skill_read / memory_read · search with skill_search / memory_search"
    )
    only = hint.block([], [hit("m1", "fact")])
    assert only.splitlines()[-1] == "read with memory_read · search with memory_search"
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


async def test_a_datetime_expiry_does_not_break_the_send(app):  # noqa: F811
    d = app.knowledge.store.dir(app.user) / "memories"
    d.mkdir(parents=True)
    (d / "bot-host.md").write_text(
        "---\nname: bot-host\ndescription: the bot runs on host b\n"
        "lifetime: temporary\nexpires: 2026-12-01T00:00:00Z\n---\nHost b.\n"
    )
    lid = await spawn(app)
    await send(app, lid, "deploy the bot")
    await until(lambda: sends(app, lid), what="the send")
    assert sends(app, lid)[-1].startswith("deploy the bot")


async def test_a_failing_hint_never_breaks_a_send(app, monkeypatch, caplog):  # noqa: F811
    def boom(*a, **k):
        raise RuntimeError("index broke")

    monkeypatch.setattr(app.knowledge, "hint_for", boom)
    lid = await spawn(app)
    s = app.sessions.sessions[lid]
    await send(app, lid, "first")
    await until(lambda: s.status == "idle" and sends(app, lid), what="turn one")
    await send(app, lid, "second")
    await until(lambda: len(sends(app, lid)) == 2, what="turn two")
    assert sends(app, lid) == ["first", "second"]
    assert sum("index broke" in r.getMessage() for r in caplog.records) == 1


async def test_the_index_is_incremental_and_sees_edits_after_the_scan(tmp_path):
    kn = Knowledge(tmp_path)
    now = [1000.0]
    kn._clock = lambda: now[0]
    await kn.store.save("ana", mem("bot-host", "the bot runs on host b"), "t")
    await kn.store.save("ana", mem("tea", "green tea in the morning"), "t")
    assert "bot-host" in await kn.hint_for("ana", "s1", "deploy the bot", None)
    parsed = kn.store.parses
    path = kn.store.dir("ana") / "memories" / "bot-host.md"
    path.write_text("---\ndescription: the kettle sits in room c\n---\nKettle.\n")
    # Inside the scan interval the edit is not seen yet, and nothing is parsed.
    assert "bot-host" not in await kn.hint_for("ana", "s1", "where is the kettle", None)
    assert kn.store.parses == parsed
    now[0] += 6
    assert "bot-host" in await kn.hint_for("ana", "s1", "where is the kettle", None)
    assert kn.store.parses == parsed + 1  # only the edited file
    now[0] += 6
    await kn.hint_for("ana", "s1", "where is the kettle", None)
    assert kn.store.parses == parsed + 1  # a scan with no change parses nothing
    kn.usage.stop()


def test_the_docs_promise_only_what_this_release_has():
    from pathlib import Path

    import aegis

    page = (Path(aegis.__file__).parent / "help" / "knowledge.md").read_text()
    assert "can be restored" not in page and "one line" not in page
    assert "skills" not in page.split("## The hint", 1)[1].split("##", 1)[0]
    stub = (Path(aegis.__file__).parent / "api" / "ops.pyi").read_text()
    delete = (
        stub.split("class _memory:", 1)[1]
        .split("async def delete", 1)[1]
        .split('"""', 2)[1]
    )
    assert "can be restored" not in delete and "Knowledge view" in delete
    assert not hasattr(hint, "FOOTER")


async def test_aegis_wakes_carry_no_hint_and_messages_from_people_do(app):  # noqa: F811
    await app.knowledge.store.save(
        app.user, mem("bot-host", "the bot runs on host b"), "t"
    )
    lid = await spawn(app)
    s = app.sessions.sessions[lid]
    texts = [
        ("> from monitor:m1 · ok · deploy the bot finished", False),
        ("> from aegis:notice · disk · warn\ndeploy the bot", False),
        ("> from artifact:a1 · submit\ndeploy the bot", False),
        ("> from agent:ana · handoff\ndeploy the bot", True),
        ("> from schedule:nightly · run#3\ndeploy the bot", True),
        ("> from workflow:w1 · step\ndeploy the bot", True),
        ("deploy the bot", True),
    ]
    for i, (text, hinted) in enumerate(texts, 1):
        await until(lambda: s.status != "working", what="the last turn")
        await send(app, lid, text)
        await until(lambda: len(sends(app, lid)) == i, what="the send")
        assert ("may be relevant" in sends(app, lid)[-1]) == hinted, text
