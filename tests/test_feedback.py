"""/feedback: the turn it delivers, and the facts the server adds to it."""

from importlib.metadata import PackageNotFoundError

from aegis import feedback

from .conftest import until
from .test_commands import app, send, spawn  # noqa: F401  (the app fixture)


def test_the_turn_carries_the_words_the_page_and_the_facts():
    header, body = feedback.turn(
        "the queue card should show the worker's model",
        "aegis 2.11.0 · Claude Code",
        "https://github.com/apiad/aegis/issues",
        "2026-10-19T16:40:02Z",
    )
    assert header == "> from feedback · 2026-10-19T16:40:02Z"
    assert (
        "The person typed: /feedback the queue card should show the worker's model"
        in body
    )
    assert "help_read feedback" in body
    assert "file nothing\nuntil they say yes" in body
    assert body.rstrip().endswith("issues: https://github.com/apiad/aegis/issues")


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
    assert feedback.issues_url() == "https://github.com/apiad/aegis/issues"

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
