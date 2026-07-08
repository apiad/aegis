from __future__ import annotations

from pathlib import Path

from starlette.testclient import TestClient

from aegis.config import WebConfig
from aegis.web.server import build_web_app


class FakeManager:
    def list_agents(self): return []
    def list_sessions(self): return []
    def get(self, h): return None


def _client(tmp_path: Path) -> TestClient:
    (tmp_path / "hello.txt").write_text("hi there", encoding="utf-8")
    app = build_web_app(FakeManager(), WebConfig(token="secret"),
                        tmp_path / "state", files_root=tmp_path,
                        server_version="9")
    return TestClient(app)


def test_download_serves_in_tree_file(tmp_path):
    r = _client(tmp_path).get("/download?path=hello.txt&t=secret")
    assert r.status_code == 200
    assert r.text == "hi there"
    assert "attachment" in r.headers["content-disposition"]
    assert "hello.txt" in r.headers["content-disposition"]


def test_download_bad_token_401(tmp_path):
    r = _client(tmp_path).get("/download?path=hello.txt&t=WRONG")
    assert r.status_code == 401


def test_download_traversal_403(tmp_path):
    r = _client(tmp_path).get("/download?path=../secret&t=secret")
    assert r.status_code == 403


def test_download_missing_404(tmp_path):
    r = _client(tmp_path).get("/download?path=nope.txt&t=secret")
    assert r.status_code == 404
