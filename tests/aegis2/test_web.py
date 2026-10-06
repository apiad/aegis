import stat
from pathlib import Path

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from aegis2.app import App
from aegis2.roots import make_roots
from aegis2.web import load_or_create_token, build_web

TOKEN = "t0ken"
ORIGIN = {"origin": "http://testserver"}


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / ".aegis.yaml").write_text(
        "default_agent: opus\n"
        "agents:\n"
        "  opus: {model: opus, effort: high, permission: full}\n"
        "  deepseek: {harness: opencode, model: x}\n"
    )
    (tmp_path / "repo").mkdir()
    return tmp_path


def client_for(project: Path, claude_bin: str) -> TestClient:
    app = App(make_roots(project, None), claude_bin=claude_bin, interrupt_timeout=0.5)
    return TestClient(build_web(app, TOKEN, {"testserver"}))


class Conn:
    def __init__(self, ws):
        self.ws = ws
        self.n = 0
        self.seen: list[dict] = []

    def hello(self):
        self.ws.send_json({"t": "hello", "token": TOKEN, "proto": 1})
        msg = self.ws.receive_json()
        assert msg["t"] == "welcome" and msg["proto"] == 1
        return self

    def call(self, op, **params):
        self.n += 1
        self.ws.send_json({"t": "call", "id": self.n, "op": op, "params": params})
        return self.until(lambda m: m["t"] == "reply" and m["id"] == self.n)

    def until(self, pred, limit=400):
        for _ in range(limit):
            m = self.ws.receive_json()
            self.seen.append(m)
            if pred(m):
                return m
        raise AssertionError(f"no matching message in {limit}")


def test_index_and_static_are_public_and_always_revalidated(project, fake_claude):
    with client_for(project, fake_claude) as c:
        assert c.get("/").status_code == 200
        css = c.get("/static/css/base.css")
        assert css.status_code == 200 and css.headers["cache-control"] == "no-cache"
        assert (
            c.get(
                "/static/css/base.css", headers={"if-none-match": css.headers["etag"]}
            ).status_code
            == 304
        )


def test_a_wrong_token_is_refused(project, fake_claude):
    with (
        client_for(project, fake_claude) as c,
        c.websocket_connect("/ws", headers=ORIGIN) as ws,
    ):
        ws.send_json({"t": "hello", "token": "nope", "proto": 1})
        with pytest.raises(WebSocketDisconnect) as e:
            ws.receive_json()
        assert e.value.code == 4401


@pytest.mark.parametrize(
    "headers",
    [
        {"origin": "http://evil.example"},
        {},
        {"origin": "http://evil.example:8742", "host": "evil.example:8742"},
    ],
)
def test_foreign_origins_and_hosts_are_refused(project, fake_claude, headers):
    with client_for(project, fake_claude) as c:
        with (
            pytest.raises(WebSocketDisconnect) as e,
            c.websocket_connect("/ws", headers=headers) as ws,
        ):
            ws.receive_json()
        assert e.value.code == 4403


def test_another_protocol_version_is_refused(project, fake_claude):
    with (
        client_for(project, fake_claude) as c,
        c.websocket_connect("/ws", headers=ORIGIN) as ws,
    ):
        ws.send_json({"t": "hello", "token": TOKEN, "proto": 2})
        assert "protocol 1" in ws.receive_json()["message"]
        with pytest.raises(WebSocketDisconnect) as e:
            ws.receive_json()
        assert e.value.code == 4400


def test_profiles_list(project, fake_claude):
    with (
        client_for(project, fake_claude) as c,
        c.websocket_connect("/ws", headers=ORIGIN) as ws,
    ):
        r = Conn(ws).hello().call("profiles.list")["result"]
        assert r["default"] == "opus"
        assert [(p["name"], p["enabled"]) for p in r["profiles"]] == [
            ("opus", True),
            ("deepseek", False),
        ]
        assert r["cwd"] == str(project)


def test_spawn_send_and_watch_a_turn(project, fake_claude):
    with (
        client_for(project, fake_claude) as c,
        c.websocket_connect("/ws", headers=ORIGIN) as ws,
    ):
        conn = Conn(ws).hello()
        ws.send_json({"t": "sub", "channel": "sessions"})
        assert conn.until(lambda m: m["t"] == "snapshot")["data"] == []
        r = conn.call("session.spawn", profile="opus", cwd="repo")["result"]
        log_id = r["log_id"]
        assert r["handle"]
        ws.send_json({"t": "sub", "channel": f"transcript:{log_id}"})
        snap = conn.until(
            lambda m: m["t"] == "snapshot" and m["channel"].startswith("transcript")
        )
        assert snap["data"][0]["summary"].startswith("spawned opus")
        assert conn.call("session.send", log_id=log_id, text="hi")["result"] is None
        conn.until(
            lambda m: (
                m["t"] == "patch"
                and m["channel"] == "sessions"
                and any(
                    op.get("upsert", {}).get("state") == "idle"
                    and op["upsert"].get("cost_usd")
                    for op in m["ops"]
                )
            )
        )
        patches = [
            m
            for m in conn.seen
            if m["t"] == "patch" and m["channel"] == f"transcript:{log_id}"
        ]
        assert [m["seq"] for m in patches] == list(range(1, len(patches) + 1))
        upserts = [op["upsert"] for m in patches for op in m["ops"] if "upsert" in op]
        assert {e["kind"] for e in upserts} >= {"user", "prose", "system"}
        second = conn.call("session.spawn", profile="opus")["result"]
        assert second["handle"] != r["handle"]
        assert conn.call("session.close", log_id=log_id).get("error") is None
        assert any(
            m["t"] == "patch" and {"remove": log_id} in m["ops"] for m in conn.seen
        )
        assert (
            conn.call("session.send", log_id=log_id, text="x")["error"]["code"]
            == "archived"
        )
        assert [m["log_id"] for m in conn.call("archive.list")["result"]] == [log_id]
        reopened = conn.call("session.reopen", log_id=log_id)["result"]
        assert reopened["state"] == "stopped"
        assert (
            conn.call("session.reopen", log_id=log_id)["error"]["code"]
            == "not_archived"
        )
        assert (
            conn.call("session.send", log_id=log_id, text="/recall").get("error")
            is None
        )
        conn.until(
            lambda m: (
                m["t"] == "patch"
                and m["channel"] == f"transcript:{log_id}"
                and any(
                    op.get("upsert", {}).get("md") == "earlier: hi" for op in m["ops"]
                )
            )
        )
        renamed = conn.call(
            "session.rename", log_id=log_id, handle="my-session", title="Hello world"
        )["result"]
        assert (renamed["handle"], renamed["title"]) == ("my-session", "Hello world")
        taken = conn.call(
            "session.rename", log_id=second["log_id"], handle="my-session"
        )
        assert taken["error"]["code"] == "handle_taken"
        assert (
            conn.call("session.rename", log_id=log_id, handle="Bad Handle")["error"][
                "code"
            ]
            == "bad_handle"
        )
        assert (
            conn.call("session.send", log_id="nope", text="x")["error"]["code"]
            == "no_session"
        )


def test_two_clients_see_the_same_patches(project, fake_claude):
    with client_for(project, fake_claude) as c:
        with (
            c.websocket_connect("/ws", headers=ORIGIN) as a,
            c.websocket_connect("/ws", headers=ORIGIN) as b,
        ):
            ca, cb = Conn(a).hello(), Conn(b).hello()
            log_id = ca.call("session.spawn", profile="opus")["result"]["log_id"]
            for conn in (ca, cb):
                conn.ws.send_json({"t": "sub", "channel": f"transcript:{log_id}"})
                conn.until(lambda m: m["t"] == "snapshot")
            ca.call("session.send", log_id=log_id, text="hello both")
            for conn in (ca, cb):
                conn.until(
                    lambda m: (
                        m["t"] == "patch"
                        and any(
                            op.get("upsert", {}).get("kind") == "prose"
                            for op in m["ops"]
                        )
                    )
                )
            ca.call("session.close", log_id=log_id)


@pytest.mark.parametrize(
    ("params", "code"),
    [
        ({"profile": "nope"}, "unknown_profile"),
        ({"profile": "deepseek"}, "harness_unsupported"),
        ({"profile": "opus", "cwd": "/"}, "bad_cwd"),
        ({"profile": "opus", "cwd": "missing"}, "bad_cwd"),
        ({"profile": "opus", "effort": "huge"}, "bad_params"),
    ],
)
def test_spawn_errors(project, fake_claude, params, code):
    with (
        client_for(project, fake_claude) as c,
        c.websocket_connect("/ws", headers=ORIGIN) as ws,
    ):
        assert Conn(ws).hello().call("session.spawn", **params)["error"]["code"] == code


def test_a_missing_claude_leaves_no_session_behind(project, tmp_path):
    with (
        client_for(project, str(tmp_path / "no-claude")) as c,
        c.websocket_connect("/ws", headers=ORIGIN) as ws,
    ):
        conn = Conn(ws).hello()
        assert (
            conn.call("session.spawn", profile="opus")["error"]["code"]
            == "claude_not_found"
        )
        ws.send_json({"t": "sub", "channel": "sessions"})
        assert conn.until(lambda m: m["t"] == "snapshot")["data"] == []


def test_unknown_channel_and_op(project, fake_claude):
    with (
        client_for(project, fake_claude) as c,
        c.websocket_connect("/ws", headers=ORIGIN) as ws,
    ):
        conn = Conn(ws).hello()
        ws.send_json({"t": "sub", "channel": "transcript:nope"})
        assert (
            conn.until(lambda m: m["t"] == "error")["error"]["code"]
            == "unknown_channel"
        )
        assert conn.call("nope.nope")["error"]["code"] == "unknown_op"


def test_the_token_file_is_private_and_reused(tmp_path):
    a = load_or_create_token(tmp_path / "state")
    assert load_or_create_token(tmp_path / "state") == a
    mode = (tmp_path / "state" / "token").stat().st_mode
    assert stat.S_IMODE(mode) == 0o600
