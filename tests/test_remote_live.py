"""Live cross-host enqueue test.

Skipped by default (gated by the ``live`` marker). Run with:

    AEGIS_REMOTE_LIVE_URL=http://vps.tail-net.ts.net:8556 \\
    AEGIS_REMOTE_LIVE_QUEUE=implementation \\
    uv run pytest -m live tests/test_remote_live.py -v

Both env vars are required. The remote queue must exist on the VPS.
"""
from __future__ import annotations

import os

import pytest

from aegis.remote.client import remote_enqueue
from aegis.remote.config import RemoteSpec


@pytest.mark.live
@pytest.mark.asyncio
async def test_remote_live_roundtrip() -> None:
    url = os.environ.get("AEGIS_REMOTE_LIVE_URL")
    queue = os.environ.get("AEGIS_REMOTE_LIVE_QUEUE")
    if not (url and queue):
        pytest.skip(
            "AEGIS_REMOTE_LIVE_URL + AEGIS_REMOTE_LIVE_QUEUE required")
    token = os.environ.get("AEGIS_REMOTE_LIVE_TOKEN")

    spec = RemoteSpec(url=url, token=token)
    result = await remote_enqueue(
        spec, queue, "live-test payload — ignore", "live-test")

    assert "error" not in result, f"remote returned error: {result}"
    assert "task_id" in result
    assert result["target_url"] == url
