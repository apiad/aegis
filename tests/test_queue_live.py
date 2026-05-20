"""Live end-to-end smoke test for the aegis task-queue v1.

Spins up a real ``claude -p`` subprocess as both producer and worker.
Producer is prompted to call ``aegis_enqueue`` via MCP; the substrate
spawns the worker; the worker's final assistant text becomes the task
result; the callback lands in the producer's inbox. We assert the
task transitions to ``completed`` with "ECHO" in the result.

Skipped automatically when ``claude`` is not on PATH (the marker also
excludes this from the hermetic suite — run with ``-m live`` to
include).
"""
from __future__ import annotations

import asyncio
import shutil

import pytest

from aegis.config import Agent
from aegis.core.manager import SessionManager
from aegis.drivers import get_driver
from aegis.mcp import AegisMCP
from aegis.queue import InboxRouter, Queue, QueueManager

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(shutil.which("claude") is None,
                       reason="claude CLI not on PATH; live test skipped"),
]


async def test_live_enqueue_to_callback_roundtrip(tmp_path):
    """Producer agent calls aegis_enqueue; worker runs; callback lands
    in producer's inbox; the task transitions to completed with ECHO."""
    # permission="full" (bypassPermissions) so the producer can call
    # aegis_enqueue via MCP without an interactive approval prompt — the
    # headless test has no TTY. Workspace memory: sonnet+auto-permission
    # in claude -p headless is "lobotomized"; opus would work too but
    # sonnet is the cheap-yet-reliable choice for a CI smoke.
    agent = Agent(harness="claude-code", model="sonnet",
                  effort="low", permission="full")
    agents = {"default": agent}

    inbox = InboxRouter(state_dir=tmp_path)
    mcp = AegisMCP()

    def make_session(profile, mcp_url, handle):
        return get_driver(profile.harness).session(
            profile, str(tmp_path), mcp_url, handle)

    mgr = SessionManager(agents, "default", make_session, mcp, inbox=inbox)
    qm = QueueManager(
        {"default": Queue(name="default", agent_profile="default",
                          max_parallel=1)},
        mgr, inbox, state_dir=tmp_path)
    mgr.attach_queue_manager(qm)
    mcp.bind(mgr)
    await mcp.start()
    await qm.start()
    try:
        producer = mgr.spawn("default")
        await producer.send(
            "Call aegis_enqueue(queue='default', "
            "payload='Reply with the single word ECHO and stop.', "
            f"from_handle='{producer.handle}', callback=true). "
            "After the tool returns, reply 'enqueued' and stop.")
        # Wait for: producer turn finishes → worker spawns → worker runs →
        # finalize captures result → callback delivered. ~60s budget.
        for _ in range(60):
            await asyncio.sleep(1)
            ids = list(qm._all)
            if ids and qm.status(ids[0])["status"] == "completed":
                break
        ids = list(qm._all)
        assert ids, "producer never enqueued — check tool-call invocation"
        st = qm.status(ids[0])
        assert st["status"] == "completed", (
            f"task did not complete in 60s: {st}")
        assert "ECHO" in (st["result"] or ""), (
            f"worker result missing ECHO: {st['result']!r}")
    finally:
        await qm.stop()
        await mgr.close_all()
        await mcp.stop()
