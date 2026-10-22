# ruff: noqa: F811  (pytest fixtures imported from other test modules)
"""secret_send and secret_request (secret_store.py, secret_ops.py): a secret
the person reads once and the agent never sees, and one the person hands the
agent into a file, neither of which may reach a transcript, a log or a reply."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import secrets
import stat
from pathlib import Path

import httpx
import pytest

from aegis import secret_store as ss
from aegis.ops import Caller, OpError, Principal
from aegis.web import _loggable

from .conftest import until
from .test_agents import inbox, mcp, turn, world  # noqa: F401  (fixture)
from .test_links import Browser, pair  # noqa: F401  (fixture)


def ok(said: str) -> dict:
    assert said.startswith("mcp ok: "), said
    return json.loads(said.removeprefix("mcp ok: "))


def card(s, id: str) -> dict:
    return next(e for e in s.entries() if e["id"] == id)


def leaks(root: Path, needle: bytes, skip: set[Path] = frozenset()) -> list[Path]:
    """Every file under root holding needle, but those in skip."""
    return [
        p
        for p in root.rglob("*")
        if p.is_file() and p not in skip and needle in p.read_bytes()
    ]


@pytest.fixture
def quick_ttl(monkeypatch):
    monkeypatch.setattr(ss, "TTL_MIN_S", 1.0)


# -- the store -----------------------------------------------------------------------
async def test_a_pad_is_taken_once_and_undoes_the_xor():
    gone = []
    store = ss.SecretStore(lambda *a: gone.append(a))
    id, ct = store.put("L", b"hunter2", 60, 0)
    assert ct != b"hunter2" and len(ct) == 7
    pad = store.take("L", id)
    assert ss.xor(ct, pad) == b"hunter2"
    assert store.take("L", id) is None and gone == []


async def test_a_pad_is_not_another_sessions_and_expires():
    gone = []
    store = ss.SecretStore(lambda *a: gone.append(a))
    id, _ = store.put("L", b"x", 0.05, 0)
    assert store.take("other", id) is None
    await until(lambda: gone, timeout=2, what="the expiry")
    assert gone == [("L", id, "send")] and store.take("L", id) is None


async def test_a_fresh_store_holds_no_pad():
    id, _ = ss.SecretStore(lambda *a: None).put("L", b"x", 60, 0)
    assert ss.SecretStore(lambda *a: None).take("L", id) is None


def test_ttl_outside_30s_to_24h_is_refused():
    assert ss.ttl_seconds("10m") == 600
    for bad in ("5s", "25h", "soon", ""):
        with pytest.raises(ss.SecretError):
            ss.ttl_seconds(bad)


def test_errors_never_quote_the_content(tmp_path):
    big = tmp_path / "big"
    big.write_bytes(b"S3CRET" * 3000)
    binary = tmp_path / "bin"
    binary.write_bytes(b"S3CRET\xff\xfe")
    for path, code in (
        (big, "too_large"),
        (binary, "not_text"),
        (tmp_path / "no", "not_found"),
    ):
        with pytest.raises(ss.SecretError) as e:
            ss.read_secret(path)
        assert e.value.code == code and "S3CRET" not in e.value.message


def test_one_trailing_newline_is_stripped(tmp_path):
    f = tmp_path / "pw"
    f.write_bytes(b"abc\n")
    assert bytes(ss.read_secret(f)) == b"abc"


def test_the_log_masks_a_secret_param():
    assert _loggable({"secret": "hunter2", "id": "r-1"}) == {
        "secret": "***",
        "id": "r-1",
    }


# -- secret_send -----------------------------------------------------------------------
async def test_a_sent_secret_reaches_no_transcript_log_or_reply(
    world, tmp_path, caplog
):
    caplog.set_level(logging.DEBUG)
    a = await world.spawn()
    value = secrets.token_urlsafe(32).encode()
    src = tmp_path / "pw"
    src.write_bytes(value + b"\n")
    said = await turn(a, mcp("secret_send", path="pw", caption="db admin"))
    got = ok(said)
    assert got["bytes"] == len(value) and value.decode() not in said
    e = card(a, got["id"])
    assert (
        e["kind"] == "secret" and e["status"] == "pending" and e["title"] == "db admin"
    )
    assert leaks(tmp_path, value, {src}) == []
    pad = await world.app.registry.call(
        "secret.reveal", {"log_id": a.log_id, "id": got["id"]}
    )
    ct = base64.b64decode(e["detail"]["ct"])
    assert ss.xor(ct, base64.b64decode(pad["pad"])) == value
    assert card(a, got["id"])["status"] == "revealed"
    again = await world.app.registry.call(
        "secret.reveal", {"log_id": a.log_id, "id": got["id"]}
    )
    assert again == {"gone": "revealed"}
    assert leaks(tmp_path, value, {src}) == [] and value.decode() not in caplog.text
    assert src.exists()


async def test_delete_source_removes_the_file(world, tmp_path):
    a = await world.spawn()
    (tmp_path / "pw").write_text("x")
    ok(await turn(a, mcp("secret_send", path="pw", delete_source=True)))
    assert not (tmp_path / "pw").exists()


async def test_a_secret_expires_and_after_a_restart_is_gone(world, tmp_path, quick_ttl):
    a = await world.spawn()
    (tmp_path / "pw").write_text("x")
    id = ok(await turn(a, mcp("secret_send", path="pw", ttl="1s")))["id"]
    await until(lambda: card(a, id)["status"] == "expired", timeout=4, what="expiry")
    assert await world.app.registry.call(
        "secret.reveal", {"log_id": a.log_id, "id": id}
    ) == {"gone": "expired"}
    id = ok(await turn(a, mcp("secret_send", path="pw")))["id"]
    world.app.secrets.drop(a.log_id)  # what a restart does to every pad
    assert await world.app.registry.call(
        "secret.reveal", {"log_id": a.log_id, "id": id}
    ) == {"gone": "restarted"}


async def test_reveal_is_for_the_owner_and_not_an_agent_tool(world, tmp_path):
    a = await world.spawn()
    (tmp_path / "pw").write_text("x")
    id = ok(await turn(a, mcp("secret_send", path="pw")))["id"]
    ben = Caller("user", principal=Principal("ben", "member"))
    with pytest.raises(OpError):
        await world.app.registry.call(
            "secret.reveal", {"log_id": a.log_id, "id": id}, ben
        )
    async with httpx.AsyncClient() as c:
        r = await c.post(
            world.base + "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={"accept": "application/json, text/event-stream"},
        )
    tools = {t["name"] for t in r.json()["result"]["tools"]}
    assert {"secret_send", "secret_request"} <= tools
    assert not {"secret_reveal", "secret_provide"} & tools


# -- secret_request ----------------------------------------------------------------------
async def test_a_provided_secret_lands_0600_and_wakes_the_agent(
    world, tmp_path, caplog
):
    caplog.set_level(logging.DEBUG)
    a = await world.spawn()
    got = ok(await turn(a, mcp("secret_request", path="tok", prompt="A GitHub token")))
    e = card(a, got["id"])
    assert e["kind"] == "secret_request" and e["status"] == "waiting"
    assert e["detail"]["prompt"] == "A GitHub token"
    value = secrets.token_urlsafe(24)
    await world.app.registry.call(
        "secret.provide", {"log_id": a.log_id, "id": got["id"], "secret": value}
    )
    target = tmp_path / "tok"
    assert target.read_text() == value
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert card(a, got["id"])["status"] == "provided"
    await until(lambda: inbox(a), timeout=8, what="the wake")
    assert f"secret:{got['id']} · provided" in json.dumps(inbox(a), ensure_ascii=False)
    assert value not in json.dumps(a.entries()) and value not in caplog.text
    assert leaks(tmp_path, value.encode(), {target}) == []
    with pytest.raises(OpError) as err:
        await world.app.registry.call(
            "secret.provide", {"log_id": a.log_id, "id": got["id"], "secret": "again"}
        )
    assert err.value.code == "gone" and target.read_text() == value


async def test_a_request_refuses_an_existing_path_or_a_missing_directory(
    world, tmp_path
):
    a = await world.spawn()
    (tmp_path / "there").write_text("keep")
    said = await turn(a, mcp("secret_request", path="there", prompt="p"))
    assert said.startswith("mcp error: exists"), said
    said = await turn(a, mcp("secret_request", path="no/dir/f", prompt="p"))
    assert said.startswith("mcp error: not_found"), said
    assert (tmp_path / "there").read_text() == "keep"


async def test_a_request_that_expires_wakes_the_agent_and_writes_nothing(
    world, tmp_path, quick_ttl
):
    a = await world.spawn()
    id = ok(await turn(a, mcp("secret_request", path="tok", prompt="p", ttl="1s")))[
        "id"
    ]
    await until(lambda: card(a, id)["status"] == "expired", timeout=4, what="expiry")
    await until(lambda: inbox(a), timeout=8, what="the wake")
    assert f"secret:{id} · expired" in json.dumps(inbox(a), ensure_ascii=False)
    assert not (tmp_path / "tok").exists()


async def test_closing_the_session_removes_the_file_unless_it_moved(world, tmp_path):
    a = await world.spawn()
    b = await world.spawn()
    ids = []
    for s, name in ((a, "a-tok"), (b, "b-tok")):
        id = ok(await turn(s, mcp("secret_request", path=name, prompt="p")))["id"]
        await world.app.registry.call(
            "secret.provide", {"log_id": s.log_id, "id": id, "secret": "v"}
        )
        ids.append(id)
    os.rename(tmp_path / "b-tok", tmp_path / "b-moved")
    (tmp_path / "b-tok").write_text("someone else's")
    await world.app.sessions.close(a.log_id)
    await world.app.sessions.close(b.log_id)
    assert not (tmp_path / "a-tok").exists()
    assert (tmp_path / "b-moved").read_text() == "v"
    assert (tmp_path / "b-tok").read_text() == "someone else's"


# -- through a link -----------------------------------------------------------------------
async def test_reveal_and_provide_work_through_a_link(pair, caplog):
    caplog.set_level(logging.DEBUG)
    alpha, beta = pair
    s = await beta.spawn()
    value = secrets.token_urlsafe(32)
    (s.spec.cwd / "pw").write_text(value)
    id = ok(await turn(s, mcp("secret_send", path=str(s.spec.cwd / "pw"))))["id"]
    req = ok(
        await turn(s, mcp("secret_request", path=str(s.spec.cwd / "tok"), prompt="p"))
    )["id"]
    async with Browser(alpha) as b:
        r = await b.call("secret.reveal", server="beta", log_id=s.log_id, id=id)
        pad = base64.b64decode(r["result"]["pad"])
        ct = base64.b64decode(card(s, id)["detail"]["ct"])
        assert ss.xor(ct, pad).decode() == value
        r = await b.call("secret.reveal", server="beta", log_id=s.log_id, id=id)
        assert r["result"] == {"gone": "revealed"}
        typed = secrets.token_urlsafe(24)
        r = await b.call(
            "secret.provide", server="beta", log_id=s.log_id, id=req, secret=typed
        )
        assert r["result"] == "ok" and (s.spec.cwd / "tok").read_text() == typed
    pad_b64 = base64.b64encode(pad).decode()
    for needle in (value, typed, pad_b64):
        assert needle not in caplog.text
    await asyncio.sleep(0)
