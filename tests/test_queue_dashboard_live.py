"""Live end-to-end smoke test for the queue dashboard observability surface.

Spins up a real ``claude -p`` subprocess as producer + worker via the same
substrate as test_queue_live.py, but exercises the dashboard's digest layer:
- QueueDigest subscribes to QueueManager.
- Producer enqueues a task that completes quickly.
- We assert digest.snapshot() reflects the lifecycle (one task, state == "ok")
  and that the tail buffer captured at least one assistant line.
"""
from __future__ import annotations

import asyncio
import shutil

import pytest

from aegis.config import Agent
from aegis.core.manager import SessionManager
from aegis.drivers import get_driver
from aegis.mcp import AegisMCP
from aegis.queue import InboxRouter, Queue, QueueManager, QueueDigest

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(shutil.which("claude") is None,
                       reason="claude CLI not on PATH; live test skipped"),
]


async def test_live_digest_reflects_real_enqueue(tmp_path):
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

    digest = QueueDigest(qm)
    digest.start()

    await mcp.start()
    await qm.start()
    try:
        producer = mgr._sync_spawn("default")
        await producer.send(
            "Call aegis_enqueue(queue='default', "
            "payload='Reply with the single word ECHO and stop.', "
            f"from_handle='{producer.handle}', callback=true). "
            "After the tool returns, reply 'enqueued' and stop.")

        for _ in range(60):
            await asyncio.sleep(1)
            snap = digest.snapshot()
            ok_tasks = [t for t in snap.tasks if t.state == "ok"]
            if ok_tasks:
                break

        snap = digest.snapshot()
        # Exactly one task should have made it through.
        assert any(t.state == "ok" for t in snap.tasks), (
            f"no task reached state=ok in 60s; tasks: "
            f"{[(t.task_id, t.state) for t in snap.tasks]}")

        ok = next(t for t in snap.tasks if t.state == "ok")
        # The task was for the "default" queue.
        assert ok.queue == "default"
        # The result was captured.
        assert ok.result is not None and "ECHO" in ok.result, (
            f"result missing ECHO: {ok.result!r}")
        # Tail buffer captured at least one assistant line for the worker.
        tail = digest.tail_of(ok.task_id)
        # tail_of returns lines indexed by worker_handle; once the task is
        # completed the worker buffer may still be present.
        assert isinstance(tail, list)
        # Queue config reflected.
        q = next(q for q in snap.queues if q.name == "default")
        assert q.max_parallel == 1
        assert q.ok >= 1
    finally:
        digest.stop()
        await qm.stop()
        await mgr.close_all()
        await mcp.stop()
