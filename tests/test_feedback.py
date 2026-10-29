"""/feedback: the turn it delivers, and the facts the server adds to it."""

from importlib.metadata import PackageNotFoundError
from types import SimpleNamespace

from aegis import feedback

from .conftest import until
from .test_commands import app, send, spawn  # noqa: F401  (the app fixture)


def test_the_turn_carries_the_words_the_page_and_the_facts():
    header, body = feedback.turn(
        "the queue card should show the worker's model",
        "aegis 2.11.0 · Claude Code",
        "https://github.com/syalia-srl/aegis/issues",
        "2026-10-19T16:40:02Z",
    )
    assert header == "> from feedback · 2026-10-19T16:40:02Z"
    assert (
        "The person typed: /feedback the queue card should show the worker's model"
        in body
    )
    assert "help_read feedback" in body
    assert "file nothing\nuntil they say yes" in body
    assert body.rstrip().endswith("issues: https://github.com/syalia-srl/aegis/issues")


def test_a_bare_command_says_no_text_was_given():
    _, body = feedback.turn("", "aegis 2.11.0", feedback.ISSUES_FALLBACK, "now")
    assert "The person typed /feedback with no text" in body
    assert "from this conversation" in body


def test_facts_leave_out_an_unknown_harness_version():
    assert "Claude Code 2.1.301" in feedback.facts("Claude Code", "2.1.301")
    for unknown in (None, "?"):
        line = feedback.facts("Claude Code", unknown)
        assert "Claude Code ·" in line and "?" not in line
    assert line.startswith("aegis ") and "Python " in line


def test_the_issues_url_comes_from_the_package_and_falls_back(monkeypatch):
    assert feedback.issues_url() == "https://github.com/syalia-srl/aegis/issues"

    def missing(name):
        raise PackageNotFoundError(name)

    monkeypatch.setattr(feedback, "metadata", missing)
    assert feedback.issues_url() == feedback.ISSUES_FALLBACK


def inbox(s) -> list[str]:
    return [
        e["md"]
        for e in s.entries()
        if e["kind"] == "inbox" and "> from feedback" in (e.get("md") or "")
    ]


async def test_slash_feedback_delivers_one_turn_to_the_agent(app):  # noqa: F811
    lid = await spawn(app)
    s = app.sessions.sessions[lid]
    r = await send(app, lid, "/feedback the queue card should show the worker's model")
    assert r == {"status": "delivered"}
    await until(lambda: inbox(s), timeout=5)
    (md,) = inbox(s)
    assert md.startswith("> from feedback · ")
    assert "help_read feedback" in md and "queue card" in md


async def test_slash_feedback_mid_turn_is_held_until_the_turn_ends(app):  # noqa: F811
    lid = await spawn(app)
    s = app.sessions.sessions[lid]
    await send(app, lid, "/busy 1")
    await until(lambda: s.status == "working", timeout=5)
    await send(app, lid, "/feedback something")
    assert any(m["header"].startswith("> from feedback") for m in s.held)
    await until(lambda: inbox(s), timeout=10)


async def test_the_turn_names_the_harness_version_the_last_check_found(app):  # noqa: F811
    lid = await spawn(app)
    s = app.sessions.sessions[lid]
    # checked_version keeps the last version outside the range; the last
    # check's verdict is what runs now.
    s.checked_version = "1.0.0"
    s.last_verdict = SimpleNamespace(version="9.9.9")
    await send(app, lid, "/feedback something")
    await until(lambda: inbox(s), timeout=5)
    (md,) = inbox(s)
    assert f"{s.harness.label} 9.9.9" in md and "1.0.0" not in md


async def test_an_agents_spawn_prompt_of_slash_feedback_runs_the_command(app):  # noqa: F811
    """#545: an agent spawned a session with prompt "/feedback <words>", and
    Claude Code answered with its own /feedback builtin. The line runs as if
    typed in the new session; the harness never gets it as text."""
    from aegis.ops import Caller

    parent = await spawn(app)
    r = await app.registry.call(
        "session.spawn",
        {"agent": "opus", "prompt": "/feedback the queue card needs a model"},
        Caller("agent", log_id=parent),
    )
    assert r["command"] == {"status": "delivered"}
    s = app.sessions.sessions[r["log_id"]]
    await until(lambda: inbox(s), timeout=5)
    assert "queue card needs a model" in inbox(s)[0]
    assert not [e for e in s.entries() if e["kind"] == "user"], "no plain prompt"


async def test_a_spawn_prompt_with_a_bad_command_says_the_session_started(app):  # noqa: F811
    import pytest

    from aegis.ops import OpError

    with pytest.raises(OpError) as e:
        await app.registry.call("session.spawn", {"agent": "opus", "prompt": "/model"})
    assert e.value.code == "missing_argument"
    assert "started, but its first line failed: usage: /model" in e.value.message
