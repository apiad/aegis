# ruff: noqa: F811  (fixtures imported from test_commands)
"""Store skills as slash commands, for every harness."""

import pytest

from aegis.app import App
from aegis.ops import OpError
from aegis.roots import make_roots

from .conftest import until
from .knowledge_samples import skill
from .test_commands import app, send, spawn  # noqa: F401


def inbox(s, header: str) -> list[str]:
    return [
        e["md"]
        for e in s.entries()
        if e["kind"] == "inbox" and header in (e.get("md") or "")
    ]


async def names(app, lid) -> list[dict]:
    r = await app.registry.call("commands.list", {"log_id": lid})
    return r["commands"]


async def test_the_catalog_lists_store_skills_after_aegis_commands(app):
    store = app.knowledge.store
    await store.save(app.user, skill("release", "Use when cutting a release"), "t")
    await store.save(app.user, skill("draft", "Not yet", status="proposed"), "t")
    await store.save(
        app.user, skill("quiet-one", "Hidden", aegis={"command": False}), "t"
    )
    # The fake claude lists a skill "busy" of its own: the store's hides it.
    await store.save(app.user, skill("busy", "The store's busy"), "t")
    lid = await spawn(app)
    rows = await names(app, lid)
    src = [c["source"] for c in rows]
    first = src.index("aegis skill")
    assert all(x == "aegis" for x in src[:first])
    mine = [c for c in rows if c["source"] == "aegis skill"]
    assert [(c["name"], c["doc"]) for c in mine] == [
        ("busy", "The store's busy"),
        ("release", "Use when cutting a release"),
    ]
    assert [c["name"] for c in rows].count("busy") == 1
    assert {"draft", "quiet-one"}.isdisjoint(c["name"] for c in rows)
    assert all(c["source"] not in ("aegis", "aegis skill") for c in rows[first + 2 :])
    preview = await app.registry.call("commands.preview", {"harness": "claude-code"})
    assert "release" in {c["name"] for c in preview["commands"]}


async def test_a_skill_named_like_an_aegis_command_is_offered_as_skill_colon(app):
    await app.knowledge.store.save(app.user, skill("model", "Model the data"), "t")
    lid = await spawn(app)
    s = app.sessions.sessions[lid]
    rows = await names(app, lid)
    assert [c["name"] for c in rows if c["source"] == "aegis skill"] == ["skill:model"]
    assert await send(app, lid, "/skill:model the orders table") == {
        "status": "delivered"
    }
    await until(lambda: inbox(s, "> from skill:model"), what="the skill's turn")
    with pytest.raises(OpError) as e:
        await send(app, lid, "/skill:nothing")
    assert e.value.code == "unknown_command"


async def test_running_a_skill_delivers_its_body_and_args(app):
    await app.knowledge.store.save(
        app.user,
        skill("release", "Cut a release", "1. bump the version\n2. tag it\n"),
        "t",
    )
    lid = await spawn(app)
    s = app.sessions.sessions[lid]
    assert await send(app, lid, "/release v2.1 now") == {"status": "delivered"}
    await until(lambda: inbox(s, "> from skill:release"), what="the skill's turn")
    (md,) = inbox(s, "> from skill:release")
    assert 'Run the skill "release". Its text follows;' in md
    assert "the person's arguments: v2.1 now." in md
    assert "1. bump the version" in md and "may be relevant" not in md
    assert s.skills == ["release"]
    rows = app.knowledge.usage.rows(app.user, "release")
    assert [r["event"] for r in rows] == ["invoked"]


CODEX = (
    "default_agent: cx\nagents:\n"
    "  cx: {harness: codex, model: openai/fake-pro, effort: high, permission: full}\n"
)


@pytest.fixture
async def codex_app(tmp_path, fake_claude, fake_codex):
    (tmp_path / ".aegis.yaml").write_text(CODEX)
    a = App(
        make_roots(tmp_path, None),
        claude_bin=fake_claude,
        codex_bin=fake_codex,
        interrupt_timeout=0.5,
    )
    await a.boot()
    yield a
    await a.shutdown()


async def test_it_works_in_a_codex_session(codex_app):
    a = codex_app
    await a.knowledge.store.save(
        a.user, skill("release", "Cut a release", "1. tag\n"), "t"
    )
    lid = (await a.registry.call("session.spawn", {"agent": "cx"}))["log_id"]
    s = a.sessions.sessions[lid]
    assert s.spec.harness == "codex"
    assert "release" in {c["name"] for c in await names(a, lid)}
    assert await send(a, lid, "/release") == {"status": "delivered"}
    await until(lambda: inbox(s, "> from skill:release"), what="the skill's turn")
    (md,) = inbox(s, "> from skill:release")
    assert "no arguments were given" in md and "1. tag" in md
    assert s.skills == ["release"]
