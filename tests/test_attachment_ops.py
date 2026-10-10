import base64
import json
from pathlib import Path

import pytest

from aegis import attachments
from aegis.app import App
from aegis.ops import Caller, OpError
from aegis.roots import make_roots
from aegis.transcript.entries import fold_records
from aegis.transcript.store import read_store
from aegis.web import _loggable

from .conftest import argv_of, until

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


async def upload(app, lid, name, body) -> str:
    call = app.registry.call
    uid = (
        await call("attachment.begin", {"log_id": lid, "name": name, "size": len(body)})
    )["upload_id"]
    for off in range(0, len(body), attachments.CHUNK_BYTES):
        chunk = body[off : off + attachments.CHUNK_BYTES]
        data = base64.b64encode(chunk).decode()
        got = await call(
            "attachment.put",
            {"log_id": lid, "upload_id": uid, "offset": off, "data": data},
        )
        assert got["size"] == off + len(chunk)
    return uid


def prompts(tmp_path) -> list[str]:
    (f,) = (tmp_path / "fake-home").glob("*.prompts")
    return [json.loads(line) for line in f.read_text().splitlines()]


async def test_a_message_with_files_reaches_the_agent_as_paths_after_the_text(
    app, tmp_path
):
    lid = await spawn(app)
    s = app.sessions.sessions[lid]
    png = b"\x89PNG" + bytes(300_000)  # two chunks
    ids = [
        await upload(app, lid, "shot.png", png),
        await upload(app, lid, "note.txt", b"hello"),
    ]
    await app.registry.call(
        "session.send", {"log_id": lid, "text": "what is this", "attachments": ids}
    )
    await until(lambda: s.status == "idle", timeout=8, what="the turn")
    (said,) = prompts(tmp_path)
    head, block = said.split("\n\nAttached files:\n")
    assert head == "what is this"
    lines = block.splitlines()
    paths = [Path(line[2:].rsplit(" (", 1)[0]) for line in lines]
    assert [p.name.split("-", 2)[2] for p in paths] == ["shot.png", "note.txt"]
    assert paths[0].read_bytes() == png and paths[1].read_bytes() == b"hello"
    assert all(p.parent == s.inbox for p in paths)
    assert lines[1].endswith("(text/plain, 5 B)")
    (user,) = [e for e in s.entries() if e["kind"] == "user"]
    assert user["md"] == "what is this"
    assert [f["name"] for f in user["detail"]["files"]] == ["shot.png", "note.txt"]
    records, damaged = read_store(s.store.path)
    assert damaged == 0 and fold_records(records).entries() == s.entries()


async def test_a_message_of_files_alone_sends_the_block(app, tmp_path):
    lid = await spawn(app)
    s = app.sessions.sessions[lid]
    uid = await upload(app, lid, "a.txt", b"x")
    await app.registry.call("session.send", {"log_id": lid, "attachments": [uid]})
    await until(lambda: s.status == "idle", timeout=8, what="the turn")
    (said,) = prompts(tmp_path)
    assert said.startswith("Attached files:\n- ")
    assert s.title  # named from the block, not left empty


async def test_a_command_line_takes_no_attachments_and_moves_nothing(app):
    lid = await spawn(app)
    uid = await upload(app, lid, "a.txt", b"x")
    with pytest.raises(OpError) as e:
        await app.registry.call(
            "session.send", {"log_id": lid, "text": "/compact", "attachments": [uid]}
        )
    assert e.value.code == "attachments_with_command"
    assert (
        attachments.inbox(app.roots.state_root, lid) / ".staged" / uid / "a.txt"
    ).exists()


async def test_an_unfinished_upload_refuses_the_send(app):
    lid = await spawn(app)
    done = await upload(app, lid, "a.txt", b"x")
    half = (
        await app.registry.call(
            "attachment.begin", {"log_id": lid, "name": "b.txt", "size": 10}
        )
    )["upload_id"]
    with pytest.raises(OpError) as e:
        await app.registry.call(
            "session.send", {"log_id": lid, "text": "hi", "attachments": [done, half]}
        )
    assert e.value.code == "upload_incomplete"
    assert not list(attachments.inbox(app.roots.state_root, lid).glob("*-a.txt"))


@pytest.mark.parametrize(
    "params", [{"text": ""}, {}, {"text": "hi", "attachments": ["x"] * 21}]
)
async def test_an_empty_or_overfull_send_is_refused(app, params):
    lid = await spawn(app)
    with pytest.raises(OpError) as e:
        await app.registry.call("session.send", {"log_id": lid, **params})
    assert e.value.code == "bad_params"


async def test_a_chunk_that_is_not_base64_is_refused(app):
    lid = await spawn(app)
    uid = (
        await app.registry.call(
            "attachment.begin", {"log_id": lid, "name": "a", "size": 3}
        )
    )["upload_id"]
    with pytest.raises(OpError) as e:
        await app.registry.call(
            "attachment.put",
            {"log_id": lid, "upload_id": uid, "offset": 0, "data": "@@@"},
        )
    assert e.value.code == "bad_chunk"


async def test_no_agent_may_attach(app):
    lid = await spawn(app)
    agent = Caller("agent", log_id=lid)
    for op, params in [
        ("attachment.begin", {"name": "a", "size": 1}),
        ("attachment.put", {"upload_id": "x" * 22, "offset": 0, "data": ""}),
        ("attachment.drop", {"upload_id": "x" * 22}),
    ]:
        with pytest.raises(OpError) as e:
            await app.registry.call(op, {"log_id": lid, **params}, agent)
        assert e.value.code == "not_for_agents"


async def test_a_restart_clears_staged_uploads(tmp_path, fake_claude):
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    a = make_app(tmp_path, fake_claude)
    await a.boot()
    lid = await spawn(a)
    uid = (
        await a.registry.call(
            "attachment.begin", {"log_id": lid, "name": "a.txt", "size": 3}
        )
    )["upload_id"]
    await a.shutdown()
    b = make_app(tmp_path, fake_claude)
    await b.boot()
    try:
        assert not (attachments.inbox(b.roots.state_root, lid) / ".staged").exists()
        with pytest.raises(OpError) as e:
            await b.registry.call(
                "attachment.put",
                {"log_id": lid, "upload_id": uid, "offset": 0, "data": "eHl6"},
            )
        assert e.value.code == "no_upload"
    finally:
        await b.shutdown()


def test_a_chunk_never_reaches_the_log():
    got = _loggable(
        {"log_id": "l", "upload_id": "u", "offset": 0, "data": "QUJD" * 100}
    )
    assert got == {"log_id": "l", "upload_id": "u", "offset": 0, "data": "<400 chars>"}
    assert _loggable({"token": "s"}) == {"token": "***"}


async def test_claude_starts_with_its_inbox_as_an_added_dir(app):
    lid = await spawn(app)
    s = app.sessions.sessions[lid]
    argv = await argv_of(s)
    assert argv[argv.index("--add-dir") + 1] == str(s.inbox)
    assert s.inbox.is_dir() and s.inbox.stat().st_mode & 0o777 == 0o700


async def test_a_send_that_cannot_start_the_harness_leaves_the_upload_staged(
    app, tmp_path
):
    lid = await spawn(app)
    s = app.sessions.sessions[lid]
    uid = await upload(app, lid, "a.txt", b"x")
    await s.stop()
    claude = tmp_path / "bin" / "claude"
    claude.rename(claude.with_name("claude.away"))
    with pytest.raises(OpError) as e:
        await app.registry.call(
            "session.send", {"log_id": lid, "text": "hi", "attachments": [uid]}
        )
    assert e.value.code == "harness_not_found"
    assert (
        attachments.inbox(app.roots.state_root, lid) / ".staged" / uid / "a.txt"
    ).exists()
    claude.with_name("claude.away").rename(claude)
    await app.registry.call(
        "session.send", {"log_id": lid, "text": "hi", "attachments": [uid]}
    )
    await until(lambda: s.status == "idle", timeout=8, what="the resent turn")
    assert [
        f["name"]
        for f in [e for e in s.entries() if e["kind"] == "user"][-1]["detail"]["files"]
    ] == ["a.txt"]
