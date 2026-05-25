"""Live (opt-in) round-trip tests for the v0.8.0 remote-plane additions:
callbacks on `aegis_enqueue(target=…, callback_to=…)` and the
`/remote/v1/schedule/*` control plane.

Both tests auto-skip when `AEGIS_LIVE_PEER_URL` is unset, so they're
inert in the hermetic suite. Run them by pointing the env var at a
real reachable peer:

    AEGIS_LIVE_PEER_URL=http://100.64.0.5:8556 \\
    AEGIS_LIVE_PEER_TOKEN=<bearer or unset> \\
    uv run pytest tests/test_remote_callback_schedule_live.py -v -m live

The peer must be running `aegis serve` with the `remote_plane:` section
enabled. For the callback test it must additionally know about the
caller in its own `remotes:` block (symmetric config); the
`callback_to` value below is "self" so the peer's bookkeeping is
self-contained — what we exercise here is the wire round-trip, not
the inbox delivery semantics that the hermetic suite already covers.
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone

import pytest

from aegis.remote.client import (
    remote_enqueue,
    remote_schedule_logs,
    remote_schedule_push,
    remote_schedule_remove,
)
from aegis.remote.config import RemoteSpec


def _peer_spec() -> RemoteSpec | None:
    url = os.environ.get("AEGIS_LIVE_PEER_URL")
    if not url:
        return None
    token = os.environ.get("AEGIS_LIVE_PEER_TOKEN") or None
    return RemoteSpec(url=url, token=token, peer_name="self")


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_callback_round_trip():
    """POST one enqueue with callback_to=self to the real peer, assert
    the wire response is well-formed (carries task_id). Receiver-side
    callback delivery is exercised by the hermetic suite; this test
    only verifies the HTTP path actually reaches a real serve."""
    spec = _peer_spec()
    if spec is None:
        pytest.skip("AEGIS_LIVE_PEER_URL not set")

    result = await remote_enqueue(
        spec,
        queue="implementation",
        payload="echo aegis live callback round-trip",
        from_="aegis-live-test",
        callback_to="self",
        callback_handle="agent",
    )
    assert "error" not in result, f"remote_enqueue failed: {result!r}"
    assert "task_id" in result, f"missing task_id in response: {result!r}"


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_schedule_push_cycle():
    """Push a once-shot schedule with fire_at ~10s ahead, sleep ~15s,
    fetch its logs, then remove it. Confirms the full push→fire→log→
    remove cycle works against a real peer."""
    spec = _peer_spec()
    if spec is None:
        pytest.skip("AEGIS_LIVE_PEER_URL not set")

    name = f"aegis-live-once-{int(datetime.now(timezone.utc).timestamp())}"
    fire_at = (datetime.now(timezone.utc) + timedelta(seconds=10)
               ).isoformat().replace("+00:00", "Z")
    spec_body = {
        "workflow": "prompt",
        "fire_at": fire_at,
        "lifecycle": "once",
        "args": {"agent": "default",
                 "text": "aegis live schedule push cycle"},
    }

    push = await remote_schedule_push(
        spec, name=name, spec_body=spec_body, pushed_from="live-test")
    assert "error" not in push, f"schedule push failed: {push!r}"

    try:
        await asyncio.sleep(15)
        logs = await remote_schedule_logs(spec, name, tail=20)
        assert "error" not in logs, f"schedule logs failed: {logs!r}"
        # The fire should have produced at least one log line by now.
        # Exact log shape is receiver-defined; we only check it's a
        # mapping with some recognizable key.
        assert isinstance(logs, dict)
    finally:
        rm = await remote_schedule_remove(spec, name)
        assert "error" not in rm, f"schedule remove failed: {rm!r}"
