"""Live e2e for the JSON DSL dynamic workflow. Skips when `claude` is
not on PATH. Runs a tiny 2-item map fan-out + reduction against real
spawned agents."""
from __future__ import annotations

import asyncio
import shutil

import pytest

from aegis.config import Agent
from aegis.core.manager import SessionManager
from aegis.drivers import get_driver
from aegis.mcp import AegisMCP
from aegis.queue import InboxRouter, Queue, QueueManager
from aegis.workflow import run_workflow
from aegis.workflows import dynamic as _dyn_mod  # noqa: F401

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(shutil.which("claude") is None,
                       reason="claude CLI not on PATH; live test skipped"),
]


async def test_live_dynamic_map_fanout_and_reduce(tmp_path):
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
                       max_parallel=2)},
        mgr, inbox, state_dir=tmp_path)
    mgr.attach_queue_manager(qm)
    mcp.bind(mgr)
    await mcp.start()
    await qm.start()
    try:
        spec = {"meta": {"name": "dsl-live-fanout"},
                "root": {"type": "sequence", "children": [
                    {"type": "agent", "id": "seed",
                     "prompt": "Reply ONLY with JSON: "
                               '{"words": ["alpha", "beta"]}',
                     "target": {"kind": "spawn", "profile": "default"},
                     "schema": {"type": "object", "required": ["words"],
                                "properties": {"words": {"type": "array"}}}},
                    {"type": "map", "id": "acks", "over": "seed.words",
                     "concurrency": 2,
                     "body": {"type": "agent", "prompt":
                              "Reply with a single word acknowledging: "
                              "{{item}}",
                              "target": {"kind": "spawn",
                                         "profile": "default"}}},
                    {"type": "agent", "id": "sum",
                     "prompt": "Summarize these acknowledgements in one "
                               "short sentence: {{a}}",
                     "inputs": {"a": "acks"},
                     "target": {"kind": "spawn", "profile": "default"}}]}}

        out = await asyncio.wait_for(
            run_workflow(
                "dynamic",
                {"spec": spec, "default_profile": "default"},
                bridge=mgr, queue_manager=qm, inbox_router=inbox,
                state_dir=tmp_path),
            timeout=300)
        assert out["status"] == "ok", out
        assert out["result"]["sum"], out
    finally:
        await qm.stop()
        await mgr.close_all()
        await mcp.stop()
