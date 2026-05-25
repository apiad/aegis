"""Live e2e: scheduler dispatches a real ``prompt`` workflow to the
``claude`` CLI and records the result in JSONL. Skipped when ``claude``
isn't on PATH."""
from __future__ import annotations

import asyncio
import json
import shutil
from datetime import datetime, timezone

import pytest

from aegis.config import Agent
from aegis.core.manager import SessionManager
from aegis.drivers import get_driver
from aegis.mcp import AegisMCP
from aegis.queue import InboxRouter, QueueManager
from aegis.scheduler import FakeClock, Scheduler
from aegis.workflow.runner import run_workflow

# Importing the builtin registers ``prompt`` via @workflow.
from aegis.workflows.builtins import prompt as _prompt  # noqa: F401

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(shutil.which("claude") is None,
                       reason="claude CLI not on PATH; live test skipped"),
]


async def test_live_scheduler_dispatches_prompt(tmp_path):
    agent = Agent(harness="claude-code", model="haiku",
                  effort="low", permission="full")
    agents = {"default": agent}

    inbox = InboxRouter(state_dir=tmp_path)
    mcp = AegisMCP()

    def make_session(profile, mcp_url, handle):
        return get_driver(profile.harness).session(
            profile, str(tmp_path), mcp_url, handle)

    mgr = SessionManager(agents, "default", make_session, mcp, inbox=inbox)
    qm = QueueManager({}, mgr, inbox, state_dir=tmp_path)
    mgr.attach_queue_manager(qm)
    mcp.bind(mgr)
    await mcp.start()
    await qm.start()
    try:
        async def _run_wf(name: str, args: dict):
            result = await run_workflow(
                name, args, bridge=mgr, queue_manager=qm,
                inbox_router=inbox, state_dir=tmp_path)
            if result.get("status") == "ok":
                return result.get("result")
            raise RuntimeError(result.get("error", "workflow failed"))

        clock = FakeClock(datetime(2026, 5, 25, 1, 59, tzinfo=timezone.utc))
        schedules = {
            "say_hi": {
                "workflow": "prompt",
                "args": {"agent": "default",
                         "text": "Reply with exactly the word PONG."},
                "cron": "0 2 * * *",
                "timezone": "UTC",
                "lifecycle": "forever",
                "on_overlap": "skip",
                "enabled": True,
            }
        }
        sched = Scheduler(
            schedules=schedules, state_dir=tmp_path,
            run_workflow=_run_wf, clock=clock)
        clock.advance(minutes=2)  # past fire time
        await sched.tick()
        # Live claude turn — give it time.
        for _ in range(60):
            log = tmp_path / "schedules" / "say_hi.jsonl"
            if log.exists():
                events = [json.loads(line)["event"]
                          for line in log.read_text().splitlines()]
                if "fire_completed" in events or "fire_failed" in events:
                    break
            await asyncio.sleep(1)
        log = tmp_path / "schedules" / "say_hi.jsonl"
        assert log.exists(), "scheduler did not write any JSONL"
        lines = [json.loads(l) for l in log.read_text().splitlines()]
        events = [r["event"] for r in lines]
        assert "fire_requested" in events
        assert "fire_completed" in events, lines
        completed = next(r for r in lines if r["event"] == "fire_completed")
        assert completed["status"] == "ok"
    finally:
        await qm.stop()
        await mgr.close_all()
        await mcp.stop()
