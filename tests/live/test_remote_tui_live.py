"""Live smoke tests for WsClient against a co-resident aegis serve.

These tests are gated behind the ``live`` marker and auto-skip unless
``aegis serve`` is already listening on localhost:8080.

Run manually after starting the server:

    # Terminal A
    uv run aegis serve

    # Terminal B
    uv run pytest -m live tests/live/test_remote_tui_live.py -v
"""
from __future__ import annotations

import socket

import pytest


_PORT = 8080
_HOST = "127.0.0.1"


def _serve_is_up() -> bool:
    """Return True when localhost:8080 accepts a TCP connection."""
    with socket.socket() as s:
        s.settimeout(0.1)
        try:
            s.connect((_HOST, _PORT))
            return True
        except OSError:
            return False


def _read_local_token() -> str:
    """Return the web token from the project's .aegis.yaml, or 'test'."""
    try:
        from aegis.config.yaml_loader import load_config
        from pathlib import Path
        cfg = load_config(Path.cwd())
        if cfg.web and cfg.web.token:
            return cfg.web.token
    except Exception:
        pass
    return "test"


pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def serve_url() -> str:
    if not _serve_is_up():
        pytest.skip(f"no aegis serve on {_HOST}:{_PORT}")
    return f"ws://{_HOST}:{_PORT}"


async def test_ws_client_list_sessions(serve_url: str) -> None:
    """Connect WsClient to a running serve, run list_sessions, verify dict."""
    from aegis.tui.ws_client import WsClient

    token = _read_local_token()
    ws = WsClient(serve_url, token, default_tail=5)
    await ws.connect()
    try:
        result = await ws.rpc("list_sessions", {})
        assert isinstance(result, dict), f"expected dict, got {type(result)}"
        assert "sessions" in result, f"missing 'sessions' key: {result!r}"
        assert isinstance(result["sessions"], list)
    finally:
        await ws.close()
