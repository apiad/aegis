import asyncio

import pytest

from aegis.app import App
from aegis.commands import escape, split
from aegis.ops import OpError
from aegis.roots import make_roots
from aegis.transcript.store import read_store

from .conftest import cmdline, until

CONFIG = "default_agent: opus\nagents:\n  opus: {harness: claude-code, model: opus, effort: high, permission: full}\n"


def make_app(root, fake) -> App:
    return App(make_roots(root, None), claude_bin=fake, interrupt_timeout=0.5)


@pytest.fixture
async def app(tmp_path, fake_claude):
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    a = make_app(tmp_path, fake_claude)
    await a.boot()
    yield a
    await a.shutdown()


async def spawn(app) -> str:
    return (await app.registry.call("session.spawn", {"agent": "opus"}))["log_id"]


async def send(app, log_id, text):
    return await app.registry.call("session.send", {"log_id": log_id, "text": text})


def test_split_and_escape():
    assert split("/model sonnet") == ("model", "sonnet")
    assert split("/compact") == ("compact", "")
    assert split("//compact") is None and split("hello") is None and split("/") is None
    assert escape("//compact now") == " /compact now" and escape("/x") == "/x"


async def test_model_switches_live_and_survives_a_stop_and_resume(app):
    lid = await spawn(app)
    s = app.sessions.sessions[lid]
    await send(app, lid, "hi")
    await until(lambda: s.status == "idle", what="the first turn")
    wire = await send(app, lid, "/model sonnet")
    assert wire["model"] == "sonnet"
    await s.stop()
    await send(app, lid, "back")
    argv = await cmdline(s.pid)
    assert argv[argv.index(b"--model") + 1] == b"sonnet"


async def test_bad_models_and_efforts_are_refused_before_anything_is_sent(app):
    lid = await spawn(app)
    for text, code in (
        ("/model gpt-5", "unknown_model"),
        ("/model retired", "unknown_model"),
        ("/effort extreme", "bad_effort"),
        ("/permission god", "bad_permission"),
        ("/model", "missing_argument"),
    ):
        with pytest.raises(OpError) as e:
            await send(app, lid, text)
        assert e.value.code == code, text
    await send(app, lid, "/model haiku")
    with pytest.raises(OpError, match="Haiku takes no effort level"):
        await send(app, lid, "/effort high")


async def test_an_unknown_command_is_refused_and_nothing_is_stored(app):
    lid = await spawn(app)
    with pytest.raises(OpError, match="// to send it as text") as e:
        await send(app, lid, "/etc/hosts is broken")
    assert e.value.code == "unknown_command"
    records, _ = read_store(app.sessions.store_path(lid))
    assert not [r for r in records if r.get("kind") == "send"]


async def test_the_escape_sends_a_prompt_that_claude_does_not_run(app):
    lid = await spawn(app)
    s = app.sessions.sessions[lid]
    await send(app, lid, "//compact now")
    await until(lambda: s.status == "idle", what="the turn")
    es = s.entries()
    assert [e["md"] for e in es if e["kind"] == "user"] == ["/compact now"]
    assert not [e for e in es if "compacted" in e["summary"]]


async def test_claudes_commands_pass_through_and_fold_cleanly(app):
    lid = await spawn(app)
    s = app.sessions.sessions[lid]
    for text in ("/context", "/hello world", "/compact"):
        await send(app, lid, text)
        await until(lambda: s.status == "idle", what=text)
    await until(lambda: s.claude_session_id, what="a session id")
    first_id = s.claude_session_id
    await send(app, lid, "/clear")
    await until(lambda: s.status == "idle", what="/clear")
    await send(app, lid, "after")
    await until(
        lambda: s.status == "idle" and s.claude_session_id != first_id,
        what="the new conversation",
    )
    es = s.entries()
    assert not [e for e in es if e["status"] == "pending"]
    assert [e["title"] for e in es if e["kind"] == "command"] == [
        "/context",
        "/compact",
    ]
    assert [e["md"] for e in es if e["kind"] == "user"] == ["/hello world", "after"]
    assert any(e["summary"].startswith("context cleared") for e in es)


async def test_effort_mid_turn_says_from_the_next_turn(app):
    lid = await spawn(app)
    s = app.sessions.sessions[lid]
    await send(app, lid, "/sleep 1")
    await until(lambda: s.status == "working", what="the turn")
    await send(app, lid, "/effort low")
    assert s.status == "working" and s.spec.effort == "low"
    assert any(e["summary"] == "effort → low (from the next turn)" for e in s.entries())


async def test_aegis_commands_rename_title_stop_and_close(app):
    lid = await spawn(app)
    s = app.sessions.sessions[lid]
    await send(app, lid, "/rename brave-otter")
    await send(app, lid, "/title Fix the parser")
    assert (s.handle, s.title) == ("brave-otter", "Fix the parser")
    await send(app, lid, "/stop")
    assert s.pid is None
    await send(app, lid, "/close")
    assert lid in app.sessions.archived


async def test_commands_list_puts_aegis_first_and_hides_what_it_shadows(app):
    lid = await spawn(app)
    r = await app.registry.call("commands.list", {"log_id": lid})
    names = [c["name"] for c in r["commands"]]
    assert names[:8] == [
        "model",
        "effort",
        "permission",
        "rename",
        "title",
        "stop",
        "close",
        "help",
    ]
    assert names.count("model") == 1 and names.count("rename") == 1
    src = {c["name"]: c["source"] for c in r["commands"]}
    assert (src["model"], src["compact"], src["hello"], src["sleep"]) == (
        "aegis",
        "claude",
        "project",
        "skill",
    )
    assert [m["value"] for m in r["models"]] == [
        "opus",
        "sonnet",
        "haiku",
        "claude-fake-old",
    ]
    assert r["permissions"] == ["read", "write", "full", "auto"]


@pytest.mark.slow
async def test_a_stopped_session_after_a_restart_gets_its_catalog_from_a_probe(
    tmp_path, fake_claude
):
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    a = make_app(tmp_path, fake_claude)
    await a.boot()
    lid = await spawn(a)
    await a.shutdown()
    b = make_app(tmp_path, fake_claude)
    await b.boot()
    try:
        r = await b.registry.call("commands.list", {"log_id": lid})
        assert "compact" in {c["name"] for c in r["commands"]}
        assert b.sessions.sessions[lid].pid is None
    finally:
        await b.shutdown()


def inits(path) -> int:
    return len(path.read_text().splitlines()) if path.exists() else 0


async def test_without_a_catalog_claudes_commands_still_pass_through(
    tmp_path, fake_claude, monkeypatch
):
    log = tmp_path / "init.log"
    monkeypatch.setenv("FAKE_CLAUDE_NO_INIT", "1")
    monkeypatch.setenv("FAKE_CLAUDE_INIT_LOG", str(log))
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    a = make_app(tmp_path, fake_claude)
    await a.boot()
    try:
        lid = await spawn(a)
        s = a.sessions.sessions[lid]
        await send(a, lid, "/compact")
        await until(lambda: s.status == "idle", what="/compact")
        assert [e["title"] for e in s.entries() if e["kind"] == "command"] == [
            "/compact"
        ]
        await s.stop()
        for _ in range(2):
            r = await a.registry.call("commands.list", {"log_id": lid})
            assert r["complete"] is False and r["models"] == []
            assert {c["source"] for c in r["commands"]} == {"aegis"}
        assert inits(log) == 1, "a failed lookup is remembered, not probed again"
    finally:
        await a.shutdown()


@pytest.mark.slow
async def test_concurrent_lookups_after_a_restart_start_one_probe(
    tmp_path, fake_claude, monkeypatch
):
    log = tmp_path / "init.log"
    monkeypatch.setenv("FAKE_CLAUDE_INIT_LOG", str(log))
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    a = make_app(tmp_path, fake_claude)
    await a.boot()
    lid = await spawn(a)
    await a.shutdown()
    before = inits(log)
    b = make_app(tmp_path, fake_claude)
    await b.boot()
    try:
        rs = await asyncio.gather(
            *(b.registry.call("commands.list", {"log_id": lid}) for _ in range(5))
        )
        assert all(r["complete"] for r in rs)
        assert inits(log) - before == 1
    finally:
        await b.shutdown()


async def test_models_list_answers_before_any_session_with_one_probe(
    tmp_path, fake_claude, monkeypatch
):
    """The new-tab composer asks for a cwd's models before a session runs
    there (#172)."""
    log = tmp_path / "init.log"
    monkeypatch.setenv("FAKE_CLAUDE_INIT_LOG", str(log))
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    a = make_app(tmp_path, fake_claude)
    await a.boot()
    try:
        for _ in range(2):
            r = await a.registry.call("models.list", {"agent": "opus"})
            assert [m["value"] for m in r["models"]][:3] == ["opus", "sonnet", "haiku"]
            assert (
                next(m for m in r["models"] if m["value"] == "haiku")["efforts"] == []
            )
        assert inits(log) == 1, "the cwd's catalog is kept"
        assert list(a.sessions.open_sessions()) == []
        with pytest.raises(OpError) as e:
            await a.registry.call("models.list", {"agent": "opus", "cwd": "/"})
        assert e.value.code == "bad_cwd"
    finally:
        await a.shutdown()


async def test_models_list_is_empty_when_claude_gives_no_catalog(
    tmp_path, fake_claude, monkeypatch
):
    monkeypatch.setenv("FAKE_CLAUDE_NO_INIT", "1")
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    a = make_app(tmp_path, fake_claude)
    await a.boot()
    try:
        r = await a.registry.call("models.list", {})
        assert r == {"models": []}
    finally:
        await a.shutdown()


async def test_help_reaching_the_server_sends_nothing(app):
    lid = await spawn(app)
    assert await send(app, lid, "/help") is None
    records, _ = read_store(app.sessions.store_path(lid))
    assert not [r for r in records if r.get("kind") == "send"]
