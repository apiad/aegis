"""Live e2e for the workflow scaffold using the TDD workflow against
a tiny target. Skips when `claude` not on PATH."""
from __future__ import annotations

import asyncio
import shutil
import subprocess

import pytest

from aegis.config import Agent
from aegis.core.manager import SessionManager
from aegis.drivers import get_driver
from aegis.mcp import AegisMCP
from aegis.queue import InboxRouter, Queue, QueueManager
from aegis.workflow import run_workflow

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(shutil.which("claude") is None,
                       reason="claude CLI not on PATH; live test skipped"),
]


async def test_live_tdd_workflow_writes_and_passes_trivial_test(tmp_path):
    """Tiny TDD loop: ask the worker to write a test asserting 1+1==2,
    verify it fails on a stub module, implement, verify green."""
    # Register the workflow.
    from examples.tdd_step import tdd_step                    # noqa: F401

    agent = Agent(harness="claude-code", model="sonnet",
                  effort="low", permission="full")
    agents = {"default": agent, "worker-sonnet": agent}

    inbox = InboxRouter(state_dir=tmp_path)
    mcp = AegisMCP()

    def make_session(profile, mcp_url, handle):
        return get_driver(profile.harness).session(
            profile, str(tmp_path), mcp_url, handle)

    mgr = SessionManager(agents, "default", make_session, mcp, inbox=inbox)
    qm = QueueManager(
        {"impl": Queue(name="impl", agent_profile="worker-sonnet",
                       max_parallel=1)},
        mgr, inbox, state_dir=tmp_path)
    mgr.attach_queue_manager(qm)
    mcp.bind(mgr)
    await mcp.start()
    await qm.start()
    try:
        out = await asyncio.wait_for(
            run_workflow(
                "tdd_step",
                {"plan_step": "trivial: assert one plus one equals two",
                 "test_command": "python -m pytest",
                 "test_path": str(tmp_path / "test_one_plus_one.py")},
                bridge=mgr, queue_manager=qm, inbox_router=inbox,
                state_dir=tmp_path),
            timeout=180)
        assert out["status"] == "ok", out
        assert "green" in (out["result"] or "")
        # Sanity: the test file exists and passes.
        assert (tmp_path / "test_one_plus_one.py").exists()
        proc = subprocess.run(
            ["python", "-m", "pytest", str(tmp_path / "test_one_plus_one.py")],
            capture_output=True, text=True)
        assert proc.returncode == 0
    finally:
        await qm.stop()
        await mgr.close_all()
        await mcp.stop()
