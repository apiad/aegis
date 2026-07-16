"""Tests for the --remote ws://... CLI flag and AegisApp manager injection.

RED phase: these tests define the expected interface; all will fail until
the implementation is added in cli.py / app.py.
"""
from __future__ import annotations

import asyncio

import pytest
from typer.testing import CliRunner

from aegis.cli import app


# ---------------------------------------------------------------------------
# Fake helpers
# ---------------------------------------------------------------------------

class _FakeWsClient:
    """Minimal stub satisfying the WsClient surface used by _build_remote_manager."""

    def __init__(self, url: str, token: str, *, default_tail: int = 10) -> None:
        self.url = url
        self.token = token
        self.default_tail = default_tail
        self._handlers: dict = {}
        self.rpc_calls: list = []

    async def connect(self) -> dict:
        return {"type": "hello", "protocol_version": 2, "constants": {}}

    async def rpc(self, method: str, params: dict | None = None) -> dict:
        self.rpc_calls.append((method, params or {}))
        if method == "list_sessions":
            return {"sessions": []}
        return {}

    def on(self, kind: str, fn) -> None:
        self._handlers.setdefault(kind, []).append(fn)

    async def subscribe_global(self, stream: str) -> None:
        pass


class _FakeManager:
    """Minimal stub for RemoteSessionManager."""

    def list_agents(self) -> list:
        return []

    def list_sessions(self) -> list:
        return []

    def inline_schedule_names(self) -> set:
        return set()

    scheduler = None
    remotes: dict = {}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_remote_ws_url_parses_and_builds_remote_manager(monkeypatch):
    """--remote ws://... should construct RemoteSessionManager, not build
    a local SessionManager. AegisApp must never be constructed with a
    positional agents dict here — the remote branch supplies manager."""
    from aegis.cli import _build_remote_manager  # to be added
    monkeypatch.setattr("aegis.tui.ws_client.WsClient", _FakeWsClient)
    mgr = asyncio.run(_build_remote_manager(
        url="ws://localhost:8080", token="t", tail=10))
    assert mgr.__class__.__name__ == "RemoteSessionManager"


def test_remote_rejects_non_ws_scheme(monkeypatch):
    """--remote wss://... or http://... should raise BadParameter."""
    from aegis.cli import _build_remote_manager
    monkeypatch.setattr("aegis.tui.ws_client.WsClient", _FakeWsClient)
    import typer
    with pytest.raises(typer.BadParameter) as exc_info:
        asyncio.run(_build_remote_manager(
            url="wss://localhost:8080", token="t", tail=10))
    assert "unsupported scheme" in str(exc_info.value)
    assert "ws://" in str(exc_info.value)


def test_remote_localhost_autolaunches_serve_if_port_free(monkeypatch,
                                                           tmp_path):
    """When --remote targets localhost and nothing is listening, spawn
    a background `aegis serve` subprocess before opening the WS."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".aegis.yaml").write_text("agents: {}\n")
    called: dict = {}
    monkeypatch.setattr("aegis.cli._maybe_autolaunch_serve",
                        lambda url: called.setdefault("url", url))

    async def _fake_build(**kw):
        return _FakeManager()

    monkeypatch.setattr("aegis.cli._build_remote_manager", _fake_build)
    monkeypatch.setattr("aegis.tui.app.AegisApp.run", lambda self: None)
    r = CliRunner().invoke(app, ["--remote", "ws://localhost:8080",
                                 "--token", "tok"])
    assert r.exit_code == 0, r.output
    assert called.get("url") == "ws://localhost:8080"


def test_remote_ws_remote_host_does_not_autolaunch(monkeypatch, tmp_path):
    """Non-localhost host must still call _maybe_autolaunch_serve (which
    no-ops for non-localhost), and the CLI path must route through it."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".aegis.yaml").write_text("agents: {}\n")
    called: dict = {}
    monkeypatch.setattr("aegis.cli._maybe_autolaunch_serve",
                        lambda url: called.setdefault("url", url))

    async def _fake_build(**kw):
        return _FakeManager()

    monkeypatch.setattr("aegis.cli._build_remote_manager", _fake_build)
    monkeypatch.setattr("aegis.tui.app.AegisApp.run", lambda self: None)
    r = CliRunner().invoke(app, ["--remote", "ws://otherhost:8080",
                                 "--token", "t"])
    assert r.exit_code == 0, r.output
    # _maybe_autolaunch_serve is called with the URL (but is a no-op for
    # non-localhost addresses — that logic lives inside the helper itself).
    assert called.get("url") == "ws://otherhost:8080"


def test_remote_ssh_fetches_token_and_opens_tunnel(monkeypatch):
    fetched: dict = {}

    class FakeTunnel:
        local_port = 41234
        async def __aenter__(self):
            fetched["opened"] = True
            return self
        async def __aexit__(self, *a): fetched["closed"] = True

    def fake_ssh_token(host):
        fetched["host"] = host
        return "server-token"

    monkeypatch.setattr("aegis.cli._ssh_fetch_token", fake_ssh_token)
    monkeypatch.setattr("aegis.remote.ssh_tunnel.SSHTunnel",
                        lambda host, port: FakeTunnel())

    async def fake_build(*, url, token, tail):
        fetched["url"] = url
        fetched["token"] = token
        return _FakeManager()

    monkeypatch.setattr("aegis.cli._build_remote_manager", fake_build)
    monkeypatch.setattr("aegis.tui.app.AegisApp.run", lambda self: None)

    from typer.testing import CliRunner
    r = CliRunner().invoke(app, ["--remote", "ssh://vps:8080"])
    assert r.exit_code == 0, r.output
    assert fetched["host"] == "vps"
    assert fetched["token"] == "server-token"
    assert fetched["url"] == "ws://localhost:41234"
    assert fetched["opened"] is True


def test_app_launched_with_manager_kwarg(monkeypatch, tmp_path):
    """When --remote is given, AegisApp must receive a manager= kwarg."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".aegis.yaml").write_text("agents: {}\n")
    launched_with: dict = {}
    monkeypatch.setattr("aegis.cli._maybe_autolaunch_serve", lambda url: None)

    async def _fake_build(**kw):
        return _FakeManager()

    monkeypatch.setattr("aegis.cli._build_remote_manager", _fake_build)

    original_init = None

    def _capturing_init(self, *args, manager=None, **kwargs):
        launched_with["manager"] = manager
        # Prevent actual TUI startup — just call App.__init__ via super path
        # Avoid full init by raising immediately after capturing.
        raise SystemExit(0)

    import aegis.tui.app as _app_mod
    monkeypatch.setattr(_app_mod.AegisApp, "__init__", _capturing_init)

    r = CliRunner().invoke(app, ["--remote", "ws://host:8080", "--token", "t"])
    assert launched_with.get("manager") is not None
    assert launched_with["manager"].__class__.__name__ == "_FakeManager"
