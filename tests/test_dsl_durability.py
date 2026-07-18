from __future__ import annotations

import asyncio
import json

import pytest

from aegis.workflows import dynamic as _dyn_mod  # noqa: F401  registers "dynamic"


async def test_completed_nodes_replay_only_inflight_reruns(
        fake_bridge_with_runner, tmp_path):
    """First agent completes + checkpoints; run 'crashes' in the second
    agent. Resume must NOT re-spawn the first agent, only the second."""
    br = fake_bridge_with_runner
    br.set_state_dir(tmp_path / "wf")
    # Route the real runner's send_and_await_reply through the fake bridge's
    # canned-reply store.
    br.session_send_and_await = br.send_and_await_reply
    runner = br.workflow_runner

    br.set_reply_sequence("lister-1", [json.dumps({"files": ["a.ts"]})])
    br.set_reply_sequence("merger-2", ["done"])
    br.set_reply_sequence("merger-3", ["done"])

    calls: list[str] = []
    real_spawn = br.spawn_subagent

    async def _counting_spawn(profile, *, alias=None):
        calls.append(profile)
        if profile == "merger" and calls.count("merger") == 1:
            raise RuntimeError("simulated crash on first merger spawn")
        return await real_spawn(profile, alias=alias)

    br.spawn_subagent = _counting_spawn  # type: ignore[assignment]

    spec = {"meta": {"name": "s"}, "root": {"type": "sequence", "children": [
        {"type": "agent", "id": "list", "prompt": "p",
         "target": {"kind": "spawn", "profile": "lister"},
         "schema": {"type": "object", "properties": {"files": {"type": "array"}}}},
        {"type": "agent", "id": "rep", "prompt": "merge {{a}}",
         "inputs": {"a": "list"},
         "target": {"kind": "spawn", "profile": "merger"}}]}}

    wid = await runner.start("dynamic", {"spec": spec}, host="h")
    for _ in range(100):
        await asyncio.sleep(0.01)
        if runner.status(wid).get("status") != "running":
            break
    assert runner.status(wid)["status"] == "error"
    assert calls == ["lister", "merger"]  # lister ran once, merger tried once

    await runner.resume(wid)
    for _ in range(100):
        await asyncio.sleep(0.01)
        if runner.status(wid).get("status") in {"ok", "error"}:
            break
    assert runner.status(wid)["status"] == "ok"
    # lister must NOT have re-spawned; only merger re-ran.
    assert calls == ["lister", "merger", "merger"]
