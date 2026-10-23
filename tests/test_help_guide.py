"""help.state, help.dismiss and help.guide on the real app, the fake claude
behind it, called as the admin and as a member."""

import pytest

from aegis.app import App
from aegis.config import Finding
from aegis.help_ops import tutorial_prompt
from aegis.helppages import load
from aegis.helpstate import installed
from aegis.names import TITLE_MAX
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


async def offers(app, caller=USER) -> dict:
    """help.state's two offers, without the hints and the doctor row."""
    s = await call(app, "help.state", caller=caller)
    return {"tutorial": s["tutorial"], "whats_new": s["whats_new"]}


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
    for words in (
        "help_read",
        "turn_end",
        "spends quota",
        "next tutorial",
        "Welcome to the aegis tutorial",
        "what the page covers",
        "bring me back to the step",
        "under Next",
    ):
        assert words in text


async def test_the_help_ops_are_the_persons_not_agent_tools(app):
    tools = {op.name for op in app.registry.agent_ops()}
    assert not {"help.state", "help.dismiss", "help.guide"} & tools


async def test_a_new_person_is_offered_the_tutorial_and_no_release_notes(app):
    assert await offers(app) == {"tutorial": False, "whats_new": None}


async def test_a_person_who_owned_sessions_before_is_offered_both(app):
    await call(app, "session.spawn", {"agent": "opus"})
    assert await offers(app) == {
        "tutorial": False,
        "whats_new": installed(),
    }


async def test_a_person_whose_sessions_are_all_archived_was_here_before(app):
    r = await call(app, "session.spawn", {"agent": "opus"})
    await app.sessions.close(r["log_id"])
    assert app.sessions.sessions == {} and r["log_id"] in app.sessions.archived
    assert (await call(app, "help.state"))["whats_new"] == installed()


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
    assert await offers(app) == {"tutorial": True, "whats_new": None}


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
    assert s.title == f"Tour: {PAGES['monitors'].title}"


async def test_a_dismiss_is_recorded_for_that_person_only(app):
    assert await call(app, "help.dismiss", {"what": "tutorial"}) == "noted"
    assert (await call(app, "help.state"))["tutorial"] is True
    await call(app, "session.spawn", {"agent": "opus"}, BEN)
    assert await offers(app, BEN) == {
        "tutorial": False,
        "whats_new": installed(),
    }
    await call(app, "help.dismiss", {"what": "whats_new"}, BEN)
    assert (await call(app, "help.state", caller=BEN))["whats_new"] is None


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


async def test_a_how_to_page_is_toured_and_leaves_the_tutorial_on_offer(app):
    r = await call(app, "help.guide", {"kind": "tutorial", "page": "monitors"})
    s = app.sessions.sessions[r["log_id"]]
    assert s.title == f"Tour: {PAGES['monitors'].title}"
    md = await replied(app, r["log_id"])
    assert "`monitors`" in md and "guided tour" in md
    assert (await call(app, "help.state"))["tutorial"] is False


async def test_an_ask_carries_the_question_and_the_page_it_came_from(app):
    q = "Can two queues share a worker?"
    r = await call(app, "help.guide", {"kind": "ask", "question": q, "page": "queues"})
    s = app.sessions.sessions[r["log_id"]]
    assert s.title == f"Help: {q}"
    md = await replied(app, r["log_id"])
    assert q in md and "`queues`" in md and "help_search" in md
    assert (await call(app, "help.state"))["tutorial"] is False


async def test_an_ask_from_the_list_names_no_page(app):
    r = await call(app, "help.guide", {"kind": "ask", "question": "what is a mark"})
    md = await replied(app, r["log_id"])
    assert "the list of help pages" in md


async def test_an_ask_without_a_question_or_with_a_wrong_page_starts_nothing(app):
    with pytest.raises(OpError) as e:
        await call(app, "help.guide", {"kind": "ask"})
    assert e.value.code == "bad_params"
    with pytest.raises(OpError) as e:
        await call(app, "help.guide", {"kind": "ask", "question": "x", "page": "nope"})
    assert e.value.code == "unknown_page"
    assert app.sessions.sessions == {}


async def test_a_long_question_with_line_breaks_gets_a_one_line_title(app):
    q = "why\ndoes   this\n" + "x" * 500
    r = await call(app, "help.guide", {"kind": "ask", "question": q})
    title = app.sessions.sessions[r["log_id"]].title
    assert "\n" not in title and len(title) <= TITLE_MAX
    assert title.startswith("Help: why does this x")


# -- the new tab's hints, doctor row, /feedback and /doctor (#580) ------------

WARN = Finding("warn", "harness.claude-code", "Claude Code 2.4.1 is older than 2.6.0")
ERR = Finding("error", "agents.ds.harness", "harness 'deepsek' does not exist")


def doctor_finds(app, *found):
    async def run():
        return [Finding("ok", "file", "fine"), *found]

    app.doctor_watch._run = run
    app.doctor_watch.findings = [Finding("ok", "file", "fine"), *found]


async def test_every_page_s_hints_reach_the_new_tab(app):
    hints = (await call(app, "help.state"))["hints"]
    assert len(hints) == sum(len(p.hints) for p in PAGES.values())
    assert {"text", "ref"} == set(hints[0])


async def test_the_doctor_row_is_an_admin_s_and_only_with_a_problem(app):
    doctor_finds(app)
    assert (await call(app, "help.state"))["doctor"] is None
    doctor_finds(app, WARN, ERR)
    row = (await call(app, "help.state"))["doctor"]
    assert (row["errors"], row["warns"]) == (1, 1)
    assert [f["level"] for f in row["findings"]] == ["error", "warn"]
    assert (await call(app, "help.state", caller=BEN))["doctor"] is None


async def test_doctor_is_listed_only_for_an_admin_with_a_problem(app):
    s = await call(app, "session.spawn", {"agent": "opus"})

    async def names(caller=USER):
        r = await call(app, "commands.list", {"log_id": s["log_id"]}, caller)
        return {c["name"] for c in r["commands"]}

    doctor_finds(app)
    assert "doctor" not in await names()
    doctor_finds(app, WARN)
    assert "doctor" in await names()


async def test_doctor_refuses_when_a_fresh_run_finds_nothing(app):
    doctor_finds(app, ERR)

    async def clean():
        return [Finding("ok", "file", "fine")]

    app.doctor_watch._run = clean
    with pytest.raises(OpError) as e:
        await call(app, "help.guide", {"kind": "doctor"})
    assert e.value.code == "nothing_to_fix"


async def test_doctor_is_not_for_a_member(app):
    doctor_finds(app, ERR)
    with pytest.raises(OpError) as e:
        await call(app, "help.guide", {"kind": "doctor"}, caller=BEN)
    assert e.value.code == "not_for_members"


async def test_doctor_starts_a_session_with_the_report(app):
    doctor_finds(app, WARN, ERR)
    r = await call(app, "help.guide", {"kind": "doctor"})
    s = app.sessions.sessions[r["log_id"]]
    assert s.title == "Doctor: 1 error, 1 warning"
    assert s.spec.cwd == app.roots.config_root
    text = await replied(app, r["log_id"])
    assert "harness 'deepsek' does not exist" in text
    assert "1 errors, 1 warnings" in text and "fine" not in text


async def test_feedback_from_the_new_tab_starts_a_session_that_asks(app):
    r = await call(app, "help.guide", {"kind": "feedback", "agent": "quick"})
    assert app.sessions.sessions[r["log_id"]].title == "Feedback"
    text = await replied(app, r["log_id"])
    assert "from /feedback on the new tab" in text and "help_read feedback" in text
