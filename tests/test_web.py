import stat
import time
from pathlib import Path

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from aegis.app import App
from aegis.roots import make_roots
from aegis.web import load_or_create_token, build_web

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
        assert snap["data"][0]["summary"].startswith("spawned opus* · sonnet")

        def has_prompt(rows):
            return any(
                e.get("kind") == "user" and e.get("md") == "hello there" for e in rows
            )

        if not has_prompt(snap["data"]):
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
        ws.send_json({"t": "hello", "token": TOKEN, "proto": 1})
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
        ws.send_json({"t": "hello", "token": TOKEN, "proto": 1})
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
        ws.send_json({"t": "hello", "token": TOKEN, "proto": 1})
        assert ws.receive_json()["native"] is False
        assert Conn(ws).call("file.open", **ref)["error"]["code"] == "not_local"
