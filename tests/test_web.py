import json
import stat
import struct
import time
from pathlib import Path

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from aegis.app import App
from aegis.roots import make_roots
from aegis.web import PROTO, cookie_name, load_or_create_token, build_web

TOKEN = "t0ken"
ORIGIN = {"origin": "http://testserver"}


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / ".aegis.yaml").write_text(
        "default_agent: opus\n"
        "agents:\n"
        "  opus: {harness: claude-code, model: opus, effort: high, permission: full}\n"
        "  reviewer: {harness: claude-code, model: claude-sonnet-5, effort: max, permission: read, priming: You review.}\n"
        "  deepseek: {harness: opencode, model: opencode-go/fake-pro, effort: high, permission: full}\n"
        "  broken: {harness: claude-code, model: opus, permission: full}\n"
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
        self.ws.send_json({"t": "hello", "token": TOKEN, "proto": PROTO})
        msg = self.ws.receive_json()
        assert msg["t"] == "welcome" and msg["proto"] == PROTO
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


def public_client(project: Path, claude_bin: str, origins: list[str]) -> TestClient:
    app = App(make_roots(project, None), claude_bin=claude_bin, interrupt_timeout=0.5)
    return TestClient(build_web(app, TOKEN, {"testserver"}, origins))


PUBLIC = {"host": "dev.example", "origin": "https://dev.example"}


@pytest.mark.parametrize("headers", [PUBLIC, ORIGIN])
def test_a_public_origin_is_accepted_and_loopback_still_is(
    project, fake_claude, headers
):
    with (
        public_client(project, fake_claude, ["https://dev.example"]) as c,
        c.websocket_connect("/ws", headers=headers) as ws,
    ):
        Conn(ws).hello()


@pytest.mark.parametrize(
    "headers",
    [
        {"host": "dev.example", "origin": "http://dev.example"},
        {"host": "evil.example", "origin": "https://dev.example"},
        {"host": "dev.example", "origin": "https://evil.example"},
        {"host": "dev.example"},
        {"host": "dev.example:8742", "origin": "https://dev.example"},
    ],
    ids=["other-scheme", "other-host", "other-origin", "no-origin", "host-with-port"],
)
def test_only_the_exact_public_origin_is_accepted(project, fake_claude, headers):
    with public_client(project, fake_claude, ["https://dev.example"]) as c:
        with (
            pytest.raises(WebSocketDisconnect) as e,
            c.websocket_connect("/ws", headers=headers) as ws,
        ):
            ws.receive_json()
        assert e.value.code == 4403


def test_without_origins_a_public_name_is_refused(project, fake_claude):
    with client_for(project, fake_claude) as c:
        with (
            pytest.raises(WebSocketDisconnect) as e,
            c.websocket_connect("/ws", headers=PUBLIC) as ws,
        ):
            ws.receive_json()
        assert e.value.code == 4403


@pytest.mark.parametrize(
    "value, wanted",
    [
        ("https://dev.example", "https://dev.example"),
        ("https://dev.example/", "https://dev.example"),
        ("http://box.lan:8080", "http://box.lan:8080"),
        ("dev.example", None),
        ("ftp://dev.example", None),
        ("https://dev.example/aegis", None),
        ("https://dev.example/?x=1", None),
        ("https://", None),
    ],
)
def test_an_origin_is_a_scheme_and_a_host_only(value, wanted):
    from aegis.web import public_origin

    if wanted is None:
        with pytest.raises(ValueError):
            public_origin(value)
    else:
        assert public_origin(value) == wanted


def test_a_wrong_token_is_refused(project, fake_claude):
    with (
        client_for(project, fake_claude) as c,
        c.websocket_connect("/ws", headers=ORIGIN) as ws,
    ):
        ws.send_json({"t": "hello", "token": "nope", "proto": PROTO})
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
        ws.send_json({"t": "hello", "token": TOKEN, "proto": PROTO + 1})
        assert f"protocol {PROTO}" in ws.receive_json()["message"]
        with pytest.raises(WebSocketDisconnect) as e:
            ws.receive_json()
        assert e.value.code == 4400


def test_agents_list(project, fake_claude):
    with (
        client_for(project, fake_claude) as c,
        c.websocket_connect("/ws", headers=ORIGIN) as ws,
    ):
        r = Conn(ws).hello().call("agents.list")["result"]
    assert r["default"] == "opus"
    assert [(a["name"], a["enabled"], a["error"]) for a in r["agents"]] == [
        ("opus", True, None),
        ("reviewer", True, None),
        ("deepseek", True, None),
        ("broken", False, "effort is missing"),
    ]
    reviewer = r["agents"][1]
    assert reviewer["has_priming"] is True and "priming" not in reviewer
    assert r["harnesses"] == [
        {"name": "claude-code", "supported": True},
        {"name": "opencode", "supported": True},
    ]
    assert r["models"] == {
        "claude-code": ["opus", "sonnet", "haiku", "fable", "claude-sonnet-5"],
        "opencode": ["opencode-go/fake-pro"],
    }
    assert r["cwd"] == str(project)


def test_spawn_send_and_watch_a_turn(project, fake_claude):
    with (
        client_for(project, fake_claude) as c,
        c.websocket_connect("/ws", headers=ORIGIN) as ws,
    ):
        conn = Conn(ws).hello()
        ws.send_json({"t": "sub", "channel": "sessions"})
        assert conn.until(lambda m: m["t"] == "snapshot")["data"] == []
        r = conn.call("session.spawn", agent="opus", cwd="repo")["result"]
        log_id = r["log_id"]
        assert r["handle"]
        ws.send_json({"t": "sub", "channel": f"transcript:{log_id}"})
        snap = conn.until(
            lambda m: m["t"] == "snapshot" and m["channel"].startswith("transcript")
        )
        assert snap["data"]["entries"][0]["summary"].startswith("spawned opus")
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
        second = conn.call("session.spawn", agent="opus")["result"]
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
            log_id = ca.call("session.spawn", agent="opus")["result"]["log_id"]
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
        ({"agent": "nope"}, "unknown_agent"),
        ({"agent": "broken"}, "bad_agent"),
        ({"agent": "opus", "harness": "opencode"}, "bad_model"),
        ({"agent": "opus", "cwd": "/"}, "bad_cwd"),
        ({"agent": "opus", "cwd": "missing"}, "bad_cwd"),
        ({"agent": "opus", "effort": "huge"}, "bad_params"),
        ({"agent": "opus", "prompt": ""}, "bad_params"),
        ({"profile": "opus"}, "bad_params"),
    ],
)
def test_spawn_errors(project, fake_claude, params, code):
    with (
        client_for(project, fake_claude) as c,
        c.websocket_connect("/ws", headers=ORIGIN) as ws,
    ):
        assert Conn(ws).hello().call("session.spawn", **params)["error"]["code"] == code


def test_spawn_without_an_agent_or_a_default_is_refused(project, fake_claude):
    (project / ".aegis.yaml").write_text(
        "agents:\n  opus: {harness: claude-code, model: opus, effort: high, permission: full}\n"
    )
    with (
        client_for(project, fake_claude) as c,
        c.websocket_connect("/ws", headers=ORIGIN) as ws,
    ):
        assert Conn(ws).hello().call("session.spawn")["error"]["code"] == "no_agent"


def test_spawn_sends_the_prompt_and_marks_the_override(project, fake_claude):
    with (
        client_for(project, fake_claude) as c,
        c.websocket_connect("/ws", headers=ORIGIN) as ws,
    ):
        conn = Conn(ws).hello()
        r = conn.call("session.spawn", model="sonnet", prompt="hello there")["result"]
        ws.send_json({"t": "sub", "channel": "sessions"})
        (meta,) = conn.until(lambda m: m["t"] == "snapshot")["data"]
        assert (meta["agent"], meta["model"], meta["overridden"]) == (
            "opus",
            "sonnet",
            ["model"],
        )
        ws.send_json({"t": "sub", "channel": f"transcript:{r['log_id']}"})
        snap = conn.until(
            lambda m: m["t"] == "snapshot" and m["channel"].startswith("transcript")
        )
        assert snap["data"]["entries"][0]["summary"].startswith(
            "spawned opus* · sonnet"
        )

        def has_prompt(rows):
            return any(
                e.get("kind") == "user" and e.get("md") == "hello there" for e in rows
            )

        if not has_prompt(snap["data"]["entries"]):
            conn.until(
                lambda m: (
                    m["t"] == "patch"
                    and m["channel"] == f"transcript:{r['log_id']}"
                    and has_prompt([op["upsert"] for op in m["ops"] if "upsert" in op])
                )
            )
        conn.call("session.close", log_id=r["log_id"])


def test_a_missing_claude_leaves_no_session_behind(project, tmp_path):
    with (
        client_for(project, str(tmp_path / "no-claude")) as c,
        c.websocket_connect("/ws", headers=ORIGIN) as ws,
    ):
        conn = Conn(ws).hello()
        assert (
            conn.call("session.spawn", agent="opus")["error"]["code"]
            == "harness_not_found"
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


def test_server_version_reports_the_running_build_and_the_latest_release(
    project, fake_claude, monkeypatch, tmp_path
):
    feed = tmp_path / "pypi.json"
    feed.write_text('{"info": {"version": "99.0.0"}}')
    monkeypatch.setenv("AEGIS_RELEASES_URL", feed.as_uri())
    with (
        client_for(project, fake_claude) as c,
        c.websocket_connect("/ws", headers=ORIGIN) as ws,
    ):
        r = Conn(ws).hello().call("server.version")["result"]
    assert r["latest"] == "99.0.0"
    assert set(r["running"]) == {"version", "commit", "ref", "dev"}
    assert r["status"] == ("dev" if r["running"]["dev"] else "behind")


def test_sent_files_are_served_at_their_capability_url(project, fake_claude):
    from aegis import files

    app = App(make_roots(project, None), claude_bin=fake_claude)
    state = app.roots.state_root

    def sent(name: str, data: bytes = b"x") -> str:
        src = project / name
        src.write_bytes(data)
        rec = files.store(state, src)
        return files.url(rec["file_id"], rec["name"])

    png, html, svg, pdf = (
        sent("a.png", b"png"),
        sent("r.html"),
        sent("d.svg"),
        sent("p.pdf"),
    )
    md, zipf, odd = sent("n.md"), sent("z.zip"), sent("informe año #2.pdf", b"pdf")
    with TestClient(build_web(app, TOKEN, {"testserver"})) as c:
        r = c.get(png)
        assert r.status_code == 200 and r.content == b"png"
        assert r.headers["content-type"] == "image/png"
        assert r.headers["x-content-type-options"] == "nosniff"
        assert r.headers["referrer-policy"] == "no-referrer"
        assert "immutable" in r.headers["cache-control"]
        assert c.get(html).headers["content-security-policy"] == "sandbox allow-scripts"
        assert c.get(svg).headers["content-security-policy"] == "sandbox"
        assert "content-security-policy" not in c.get(pdf).headers
        assert c.get(md).headers["content-type"] == "text/plain; charset=utf-8"
        assert c.get(zipf).headers["content-disposition"].startswith("attachment")
        r = c.get(odd + "?download=1")
        assert r.content == b"pdf"
        assert r.headers["content-disposition"] == (
            "attachment; filename*=UTF-8''informe%20a%C3%B1o%20%232.pdf"
        )
        file_id = png.split("/")[2]
        assert c.get(f"/files/{file_id}/b.png").status_code == 404
        assert c.get("/files/AAAAAAAAAAAAAAAAAAAAAA/a.png").status_code == 404
        assert c.get(png, headers={"host": "evil.example"}).status_code == 404

    # Behind a reverse proxy the browser sends the public name as Host.
    again = App(make_roots(project, None), claude_bin=fake_claude)  # same state
    proxied = build_web(again, TOKEN, {"testserver"}, ["https://dev.example"])
    with TestClient(proxied, base_url="https://dev.example") as c:
        assert c.get(png).content == b"png"
        assert c.get(png, headers={"host": "evil.example"}).status_code == 404


def test_serve_refuses_a_bad_origin_before_starting(tmp_path):
    from typer.testing import CliRunner

    from aegis.cli import app as cli_app

    r = CliRunner().invoke(
        cli_app,
        ["serve", "--root", str(tmp_path), "--origin", "https://dev.example/aegis"],
        env={"COLUMNS": "200"},  # Rich wraps the error box at the default width
    )
    assert r.exit_code == 2
    assert "path or query" in r.output
    assert not (tmp_path / ".aegis").exists(), "nothing was created"


def test_open_natively_only_for_a_browser_on_the_servers_desktop(
    project, fake_claude, monkeypatch, tmp_path
):
    from aegis import files

    marker = tmp_path / "opened"
    script = tmp_path / "opener.sh"
    script.write_text(f'#!/bin/sh\nprintf "%s" "$1" > "{marker}"\n')
    script.chmod(0o755)
    monkeypatch.setenv("AEGIS_OPENER", str(script))
    src = project / "chart.png"
    src.write_bytes(b"png")

    # A browser elsewhere: told so, and refused even if it asks.
    app = App(make_roots(project, None), claude_bin=fake_claude)
    rec = files.store(app.roots.state_root, src)
    ref = {"file_id": rec["file_id"], "name": rec["name"]}
    with (
        TestClient(build_web(app, TOKEN, {"testserver"})) as c,
        c.websocket_connect("/ws", headers=ORIGIN) as ws,
    ):
        ws.send_json({"t": "hello", "token": TOKEN, "proto": PROTO})
        assert ws.receive_json()["native"] is False
        r = Conn(ws).call("file.open", **ref)
        assert r["error"]["code"] == "not_local"
    assert not marker.exists()

    # A browser on loopback, on a server with a desktop.
    app = App(make_roots(project, None), claude_bin=fake_claude)
    local = "127.0.0.1:8742"
    with (
        TestClient(build_web(app, TOKEN, {local}), base_url=f"http://{local}") as c,
        c.websocket_connect(
            "/ws", headers={"host": local, "origin": f"http://{local}"}
        ) as ws,
    ):
        ws.send_json({"t": "hello", "token": TOKEN, "proto": PROTO})
        assert ws.receive_json()["native"] is True
        conn = Conn(ws)
        assert "error" not in conn.call("file.open", **ref)
        assert (
            conn.call("file.open", file_id=rec["file_id"], name="nope.png")["error"][
                "code"
            ]
            == "no_file"
        )
    for _ in range(100):
        if marker.exists():
            break
        time.sleep(0.02)
    assert marker.read_text().endswith("/chart.png")

    # The same loopback browser on a headless server: no button, and refused.
    monkeypatch.delenv("AEGIS_OPENER")
    monkeypatch.setattr(files, "opener", lambda: None)
    app = App(make_roots(project, None), claude_bin=fake_claude)
    with (
        TestClient(build_web(app, TOKEN, {local}), base_url=f"http://{local}") as c,
        c.websocket_connect(
            "/ws", headers={"host": local, "origin": f"http://{local}"}
        ) as ws,
    ):
        ws.send_json({"t": "hello", "token": TOKEN, "proto": PROTO})
        assert ws.receive_json()["native"] is False
        assert Conn(ws).call("file.open", **ref)["error"]["code"] == "not_local"


def test_a_resubscribe_with_since_gets_only_what_changed(project, fake_claude):
    with (
        client_for(project, fake_claude) as c,
        c.websocket_connect("/ws", headers=ORIGIN) as ws,
    ):
        conn = Conn(ws).hello()
        log_id = conn.call("session.spawn", agent="opus")["result"]["log_id"]
        ch = f"transcript:{log_id}"

        def snapshot(**extra):
            ws.send_json({"t": "sub", "channel": ch, **extra})
            return conn.until(lambda m: m["t"] == "snapshot" and m["channel"] == ch)[
                "data"
            ]

        def turn(text):
            conn.call("session.send", log_id=log_id, text=text)
            conn.until(
                lambda m: (
                    m["t"] == "patch"
                    and m["channel"] == ch
                    and any(
                        op.get("upsert", {}).get("summary", "").startswith("done in")
                        for op in m["ops"]
                    )
                )
            )

        snapshot()  # subscribed, so the turns' patches reach us
        turn("first")
        full = snapshot()
        assert "since" not in full and full["rev"] >= 0
        turn("second")
        delta = snapshot(since=full["rev"])
        assert delta["since"] == full["rev"] and delta["rev"] > full["rev"]
        assert delta["entries"] and all(
            e["rev"] > full["rev"] for e in delta["entries"]
        )
        assert len(delta["entries"]) < len(full["entries"]) + 4
        assert "since" not in snapshot(since=full["rev"] + 10_000)
        conn.call("session.close", log_id=log_id)


def test_a_read_reaches_a_tab_that_returns_with_since(project, fake_claude):
    # A read makes no store record: the delta carries the flag anyway.
    with client_for(project, fake_claude) as c:
        with (
            c.websocket_connect("/ws", headers=ORIGIN) as a,
            c.websocket_connect("/ws", headers=ORIGIN) as b,
        ):
            ca, cb = Conn(a).hello(), Conn(b).hello()
            log_id = ca.call("session.spawn", agent="opus")["result"]["log_id"]
            ch = f"transcript:{log_id}"

            def snapshot(conn, **extra):
                conn.ws.send_json({"t": "sub", "channel": ch, **extra})
                return conn.until(
                    lambda m: m["t"] == "snapshot" and m["channel"] == ch
                )["data"]

            snapshot(ca)
            ca.call("session.send", log_id=log_id, text="hello both")
            ca.until(
                lambda m: (
                    m["t"] == "patch"
                    and any(
                        op.get("upsert", {}).get("summary", "").startswith("done in")
                        for op in m["ops"]
                    )
                )
            )
            held = snapshot(cb)
            (pid,) = [e["id"] for e in held["entries"] if e["kind"] == "prose"]
            assert [e["unread"] for e in held["entries"] if e["id"] == pid] == [True]
            cb.ws.send_json({"t": "unsub", "channel": ch})  # b leaves the tab
            assert ca.call("session.read", log_id=log_id, ids=[pid])["result"] == {
                "read": 1,
                "unread": 0,
            }
            delta = snapshot(cb, since=held["rev"])  # and comes back
            assert delta["since"] == held["rev"] == delta["rev"]
            assert [(e["id"], e["unread"]) for e in delta["entries"]] == [(pid, False)]
            ca.call("session.close", log_id=log_id)


def test_transcript_detail_returns_what_the_wire_left_out(project, fake_claude):
    with (
        client_for(project, fake_claude) as c,
        c.websocket_connect("/ws", headers=ORIGIN) as ws,
    ):
        conn = Conn(ws).hello()
        log_id = conn.call("session.spawn", agent="opus")["result"]["log_id"]
        ch = f"transcript:{log_id}"
        ws.send_json({"t": "sub", "channel": ch})
        conn.until(lambda m: m["t"] == "snapshot" and m["channel"] == ch)
        conn.call("session.send", log_id=log_id, text="/bash list it => SECRET-OUT")
        patch = conn.until(
            lambda m: (
                m["t"] == "patch"
                and m["channel"] == ch
                and any(
                    op.get("upsert", {}).get("kind") == "tool"
                    and op["upsert"]["status"] == "ok"
                    for op in m["ops"]
                )
            )
        )
        tool = next(
            op["upsert"]
            for op in patch["ops"]
            if op.get("upsert", {}).get("kind") == "tool"
        )
        # The closed row keeps its one-line result; the tail stays behind.
        assert "tail" not in tool["detail"] and tool["detail"]["more"] is True
        (full,) = conn.call("transcript.detail", log_id=log_id, ids=[tool["id"]])[
            "result"
        ]
        assert "SECRET-OUT" in full["detail"]["tail"] and full["rev"] == tool["rev"]
        assert (
            conn.call("transcript.detail", log_id=log_id, ids=["nope"])["result"] == []
        )
        assert (
            conn.call("transcript.detail", log_id="nope", ids=["x"])["error"]["code"]
            == "no_session"
        )
        assert (
            conn.call("transcript.detail", log_id=log_id, ids=[])["error"]["code"]
            == "bad_params"
        )
        assert (
            conn.call("transcript.detail", log_id=log_id, ids=["x"] * 101)["error"][
                "code"
            ]
            == "bad_params"
        )
        conn.call("session.close", log_id=log_id)


def test_the_token_url_sets_a_cookie_and_moves_the_token_out(project, fake_claude):
    with client_for(project, fake_claude) as c:
        r = c.get(f"/?token={TOKEN}", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/"
        cookie = r.headers["set-cookie"]
        name = cookie_name(project / ".aegis" / "state")
        assert cookie.startswith(f"{name}={TOKEN};")
        for attr in ("HttpOnly", "SameSite=strict", "Path=/", "Max-Age=31536000"):
            assert attr.lower() in cookie.lower(), attr
        assert "secure" not in cookie.lower(), "plain http on loopback"
        bad = c.get("/?token=wrong", follow_redirects=False)
        assert (
            bad.headers["location"] == "/?refused=1" and "set-cookie" not in bad.headers
        )


def test_a_socket_signs_in_with_the_cookie_alone(project, fake_claude):
    name = cookie_name(project / ".aegis" / "state")
    with client_for(project, fake_claude) as c:
        with c.websocket_connect(
            "/ws", headers={**ORIGIN, "cookie": f"{name}={TOKEN}"}
        ) as ws:
            ws.send_json({"t": "hello", "proto": PROTO})
            assert ws.receive_json()["t"] == "welcome"
        with c.websocket_connect(
            "/ws", headers={**ORIGIN, "cookie": f"{name}=wrong"}
        ) as ws:
            ws.send_json({"t": "hello", "proto": PROTO})
            with pytest.raises(WebSocketDisconnect) as e:
                ws.receive_json()
            assert e.value.code == 4401
        with c.websocket_connect(
            "/ws", headers={**ORIGIN, "cookie": f"aegis_00000000={TOKEN}"}
        ) as ws:
            ws.send_json({"t": "hello", "proto": PROTO})
            with pytest.raises(WebSocketDisconnect) as e:
                ws.receive_json()
            assert e.value.code == 4401, "another server's cookie name"


def test_login_sets_the_cookie_for_a_pasted_token(project, fake_claude):
    with client_for(project, fake_claude) as c:
        ok = c.post("/login", json={"token": TOKEN})
        assert ok.status_code == 204 and "httponly" in ok.headers["set-cookie"].lower()
        for body in ({"token": "wrong"}, {"token": 3}, {}):
            r = c.post("/login", json=body)
            assert r.status_code == 401 and "set-cookie" not in r.headers


def test_two_state_roots_have_two_cookie_names(tmp_path):
    a, b = cookie_name(tmp_path / "a"), cookie_name(tmp_path / "b")
    assert a != b and a.startswith("aegis_") and len(a) == len("aegis_") + 8


def test_the_cookie_is_secure_only_behind_an_https_origin(project, fake_claude):
    app = App(make_roots(project, None), claude_bin=fake_claude)
    web = build_web(
        app, TOKEN, {"testserver"}, ["https://dev.example", "http://box.lan:8742"]
    )
    with TestClient(web) as c:
        r = c.get(f"https://dev.example/?token={TOKEN}", follow_redirects=False)
        assert "secure" in r.headers["set-cookie"].lower()
        r = c.get(f"http://box.lan:8742/?token={TOKEN}", follow_redirects=False)
        assert "secure" not in r.headers["set-cookie"].lower()


def png_size(data: bytes) -> tuple[int, int]:
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", data[16:24])


def test_the_manifest_names_the_server_and_its_icons_load(project, fake_claude):
    with client_for(project, fake_claude) as c:
        r = c.get("/manifest.webmanifest")
        assert r.headers["content-type"].startswith("application/manifest+json")
        m = json.loads(r.text)
        assert m["display"] == "standalone" and m["start_url"] == "/"
        assert m["name"].startswith("aegis · ") and m["short_name"] == "aegis"
        got_icons = set()
        for icon in m["icons"]:
            got = c.get(icon["src"])
            assert got.status_code == 200 and got.headers["content-type"] == "image/png"
            w, h = png_size(got.content)
            assert f"{w}x{h}" == icon["sizes"]
            got_icons.add((icon["sizes"], icon.get("purpose", "any")))
        assert got_icons == {
            ("192x192", "any"),
            ("512x512", "any"),
            ("512x512", "maskable"),
        }
        page = c.get("/").text
        assert '<link rel="manifest" href="/manifest.webmanifest">' in page
