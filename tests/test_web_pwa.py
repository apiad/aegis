from __future__ import annotations

import json
from pathlib import Path

from starlette.testclient import TestClient

from aegis.config import WebConfig
from aegis.web.server import build_web_app


class FakeManager:
    def list_agents(self): return []
    def list_sessions(self): return []
    def get(self, h): return None


def _client(tmp_path: Path) -> TestClient:
    app = build_web_app(FakeManager(), WebConfig(token="secret"),
                        tmp_path / "state", files_root=tmp_path,
                        server_version="9.9.9")
    return TestClient(app)


def test_manifest_served(tmp_path):
    r = _client(tmp_path).get("/manifest.webmanifest")
    assert r.status_code == 200
    data = json.loads(r.text)
    assert data["start_url"] == "/"
    assert data["display"] == "standalone"
    assert data["icons"]


def test_index_links_manifest_and_registers_sw(tmp_path):
    html = _client(tmp_path).get("/").text
    assert 'rel="manifest"' in html
    assert "serviceWorker" in html
    assert 'name="theme-color"' in html


def test_icon_served(tmp_path):
    r = _client(tmp_path).get("/static/icons/icon.svg")
    assert r.status_code == 200
    assert "svg" in r.text.lower()


def test_service_worker_served_root_scope_versioned(tmp_path):
    r = _client(tmp_path).get("/service-worker.js")
    assert r.status_code == 200
    assert r.headers["service-worker-allowed"] == "/"
    assert "9.9.9" in r.text                 # version substituted
    assert "__SW_VERSION__" not in r.text
    assert 'addEventListener("fetch"' in r.text   # has a fetch handler (installable)
    assert "aegis-shell-" in r.text               # versioned runtime cache
