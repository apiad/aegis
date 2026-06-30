from __future__ import annotations

from pathlib import Path

from starlette.testclient import TestClient

from aegis.config import WebConfig
from aegis.web.server import build_web_app


class FakeManager:
    def list_agents(self):
        return ["claude"]

    def list_sessions(self):
        return []

    def get(self, handle):
        return None


def _client(tmp_path: Path) -> TestClient:
    app = build_web_app(FakeManager(), WebConfig(token="secret"),
                        tmp_path / "state")
    return TestClient(app)


def test_index_served(tmp_path: Path):
    r = _client(tmp_path).get("/")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    body = r.text
    assert 'id="tabbar"' in body
    assert 'id="panes"' in body
    assert "/theme.css" in body
    assert "/static/js/app.js" in body


def test_theme_css_has_ink_vars_and_base_rules(tmp_path: Path):
    r = _client(tmp_path).get("/theme.css")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/css")
    assert "--aegis-bg: #0e0e0d" in r.text     # S1 ink value
    assert ".tool-use" in r.text               # a base.css rule


def test_referenced_assets_are_served(tmp_path: Path):
    client = _client(tmp_path)
    for path in ("/static/js/app.js", "/static/js/ws.js",
                 "/static/js/coalesce.js", "/static/css/base.css"):
        r = client.get(path)
        assert r.status_code == 200, path
        assert r.text.strip() != ""


def test_healthz_still_ok(tmp_path: Path):
    r = _client(tmp_path).get("/healthz")
    assert r.status_code == 200 and r.json() == {"ok": True}
