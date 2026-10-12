"""help.state, help.dismiss and help.guide on the real app, the fake claude
behind it, called as the admin and as a member."""

import pytest

from aegis.app import App
from aegis.help_ops import tutorial_prompt
from aegis.helppages import load
from aegis.helpstate import installed
from aegis.ops import USER, Caller, OpError, Principal
from aegis.roots import make_roots
from aegis.transcript.store import read_store

from .conftest import until

CONFIG = (
    "default_agent: opus\nagents:\n"
    "  opus: {harness: claude-code, model: opus, effort: high, permission: full}\n"
    "  quick: {harness: claude-code, model: sonnet, effort: low, permission: full}\n"
)
BEN = Caller("user", principal=Principal("ben", "member"))
PAGES = {p.id: p for p in load()}


@pytest.fixture
async def app(tmp_path, fake_claude):
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    a = App(make_roots(tmp_path, None), claude_bin=fake_claude, interrupt_timeout=0.5)
    await a.boot()
    a.principals.add("ben", "member")
    # A member with no policy starts nothing (quota_gate.py): open the pool.
    a.principals.set_default_policy({"claude": {"mode": "open"}})
    yield a
    await a.shutdown()


async def call(app, op, params=None, caller=USER):
    return await app.registry.call(op, params or {}, caller)


async def replied(app, log_id) -> str:
    """The fake claude's answer to the guide's first prompt, which quotes it."""
    s = app.sessions.sessions[log_id]

    def answered():
        return s.status == "idle" and any(e["kind"] == "prose" for e in s.entries())

    await until(answered, timeout=8, what="the guide's first turn")
    return next(e["md"] for e in s.entries() if e["kind"] == "prose")


def test_the_prompt_names_the_page_and_what_the_guide_must_do():
    text = tutorial_prompt("monitors")
    assert "`monitors`" in text
    for words in ("help_read", "turn_end", "spends quota", "next tutorial"):
        assert words in text


async def test_the_help_ops_are_the_persons_not_agent_tools(app):
    tools = {op.name for op in app.registry.agent_ops()}
    assert not {"help.state", "help.dismiss", "help.guide"} & tools


async def test_a_new_person_is_offered_the_tutorial_and_no_release_notes(app):
    assert await call(app, "help.state") == {"tutorial": False, "whats_new": None}


async def test_a_person_who_owned_sessions_before_is_offered_both(app):
    await call(app, "session.spawn", {"agent": "opus"})
    assert await call(app, "help.state") == {
        "tutorial": False,
        "whats_new": installed(),
    }


async def test_the_guide_spawns_from_the_chips_and_the_agent_reads_the_page(app):
    r = await call(
        app, "help.guide", {"kind": "tutorial", "agent": "quick", "effort": "max"}
    )
    s = app.sessions.sessions[r["log_id"]]
    assert r["handle"] == s.handle
    assert (s.spec.agent, s.spec.model, s.spec.effort) == ("quick", "sonnet", "max")
    assert s.title == f"Tutorial: {PAGES['getting-started'].title}"
    md = await replied(app, r["log_id"])
    assert "`getting-started`" in md and "help_read" in md
    # Decided before the spawn: the guide's own session leaves a new person new.
    assert await call(app, "help.state") == {"tutorial": True, "whats_new": None}


async def test_a_spawn_that_fails_leaves_the_tutorial_on_offer(app):
    with pytest.raises(OpError):
        await call(app, "help.guide", {"kind": "tutorial", "agent": "nosuch"})
    assert app.sessions.sessions == {}
    assert (await call(app, "help.state"))["tutorial"] is False


async def test_a_guide_whose_title_cannot_be_set_still_counts_and_opens(
    app, monkeypatch
):
    def refuse(*_):
        raise OpError("bad_title", "refused")

    monkeypatch.setattr(app.sessions, "rename", refuse)
    r = await call(app, "help.guide", {"kind": "tutorial"})
    assert r["log_id"] in app.sessions.sessions
    assert (await call(app, "help.state"))["tutorial"] is True


async def test_a_mistyped_page_starts_nothing_and_any_real_page_can_be_toured(app):
    with pytest.raises(OpError) as e:
        await call(app, "help.guide", {"kind": "tutorial", "page": "getting-startd"})
    assert e.value.code == "unknown_page"
    assert "getting-started" in e.value.message
    assert app.sessions.sessions == {}
    assert (await call(app, "help.state"))["tutorial"] is False
    r = await call(app, "help.guide", {"kind": "tutorial", "page": "monitors"})
    s = app.sessions.sessions[r["log_id"]]
    assert s.title == f"Tutorial: {PAGES['monitors'].title}"


async def test_a_dismiss_is_recorded_for_that_person_only(app):
    assert await call(app, "help.dismiss", {"what": "tutorial"}) == "noted"
    assert (await call(app, "help.state"))["tutorial"] is True
    await call(app, "session.spawn", {"agent": "opus"}, BEN)
    assert await call(app, "help.state", caller=BEN) == {
        "tutorial": False,
        "whats_new": installed(),
    }
    await call(app, "help.dismiss", {"what": "whats_new"}, BEN)
    assert (await call(app, "help.state", caller=BEN))["whats_new"] is None


async def test_whats_new_guides_wait_for_slice_4(app):
    # Slice 4 replaces this test with one that tours the release notes.
    with pytest.raises(OpError) as e:
        await call(app, "help.guide", {"kind": "whats_new"})
    assert e.value.code == "not_yet"
    assert app.sessions.sessions == {}


async def test_slash_tutorial_runs_on_default_agent_in_the_sessions_directory(
    app, tmp_path
):
    work = tmp_path / "work"
    work.mkdir()
    src = await call(app, "session.spawn", {"agent": "quick", "cwd": str(work)})
    r = await call(
        app, "session.send", {"log_id": src["log_id"], "text": "/tutorial monitors"}
    )
    assert r["server"] == app.server_name
    s = app.sessions.sessions[r["log_id"]]
    assert (s.spec.agent, s.spec.cwd) == ("opus", work.resolve())
    assert "`monitors`" in await replied(app, r["log_id"])


async def test_slash_tutorial_alone_runs_getting_started_and_completes_tutorials(app):
    src = await call(app, "session.spawn", {"agent": "opus"})
    listed = await call(app, "commands.list", {"log_id": src["log_id"]})
    tut = next(c for c in listed["commands"] if c["name"] == "tutorial")
    assert (tut["hint"], tut["args"], tut["source"]) == ("[page]", "tutorials", "aegis")
    r = await call(app, "session.send", {"log_id": src["log_id"], "text": "/tutorial"})
    assert "`getting-started`" in await replied(app, r["log_id"])
    # Nothing went to the session it was typed in.
    records, _ = read_store(app.sessions.store_path(src["log_id"]))
    assert not [x for x in records if x.get("kind") == "send"]
