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


async def test_live_tdd_workflow_writes_and_passes_factorial(tmp_path):
    """Tiny but non-trivial TDD loop: write tests for a `factorial(n)`
    function that doesn't yet exist, verify they fail (ImportError or
    NameError), have the worker implement factorial, verify they pass.

    Earlier draft used `assert 1+1==2`, which Python validates without
    any implementation — TDD's 'tests should fail before impl' predicate
    correctly rejected that case. Factorial needs a real function body
    so the loop actually runs through to green.
    """
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
        plan = (
            "Implement a function `factorial(n: int) -> int` in a new "
            f"file {tmp_path / 'factorial.py'}. It must return n! "
            "(0! = 1, 1! = 1, 5! = 120). The function does not exist "
            "yet — tests written first must import it and will fail "
            "with ImportError until the implementation lands.")
        out = await asyncio.wait_for(
            run_workflow(
                "tdd_step",
                {"plan_step": plan,
                 "test_command": "python -m pytest",
                 "test_path": str(tmp_path / "test_factorial.py")},
                bridge=mgr, queue_manager=qm, inbox_router=inbox,
                state_dir=tmp_path),
            timeout=240)
        assert out["status"] == "ok", out
        assert "green" in (out["result"] or "")
        # Sanity: the implementation file exists; tests pass.
        assert (tmp_path / "factorial.py").exists()
        proc = subprocess.run(
            ["python", "-m", "pytest", str(tmp_path / "test_factorial.py")],
            capture_output=True, text=True)
        assert proc.returncode == 0
    finally:
        await qm.stop()
        await mgr.close_all()
        await mcp.stop()
