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


async def test_loop_round1_replays_on_resume(fake_bridge_with_runner, tmp_path):
    """Round-1 body completes + its until decision (shell=exit 1) records.
    Round-2 body crashes on spawn. Resume: round-1 must NOT re-run body
    NOR re-evaluate its shell predicate; only round-2 runs afresh."""
    br = fake_bridge_with_runner
    br.set_state_dir(tmp_path / "wf")
    br.session_send_and_await = br.send_and_await_reply
    runner = br.workflow_runner
    # Route engine.bash through the FakeBridge's bash sequence (engine's
    # runner check looks for run_bash on workflow_runner).
    runner.run_bash = br.run_bash

    br.set_reply_sequence("fixer-1", ["r1"])
    br.set_reply_sequence("fixer-2", ["r2"])
    # Only ONE bash entry — round 1 returns exit 1 (loop again).
    # If round 1's predicate is re-evaluated after resume, this list will
    # empty and run_bash will fall through to default {"exit":0} (which
    # would stop the loop early — a wrong outcome we also assert against
    # via bash_calls count).
    br.set_bash_sequence([{"exit": 1, "stdout": "", "stderr": ""},
                          {"exit": 0, "stdout": "", "stderr": ""}])

    calls: list[str] = []
    real_spawn = br.spawn_subagent

    async def _counting_spawn(profile, *, alias=None):
        calls.append(profile)
        if profile == "fixer" and calls.count("fixer") == 2:
            raise RuntimeError("simulated crash on round-2 body spawn")
        return await real_spawn(profile, alias=alias)

    br.spawn_subagent = _counting_spawn  # type: ignore[assignment]

    spec = {"meta": {"name": "s"},
            "root": {"type": "loop", "id": "rounds", "max_rounds": 3,
                     "until": {"kind": "shell", "cmd": "tsc"},
                     "body": {"type": "agent", "prompt": "fix",
                              "target": {"kind": "spawn", "profile": "fixer"}}}}

    wid = await runner.start("dynamic", {"spec": spec}, host="h")
    for _ in range(100):
        await asyncio.sleep(0.01)
        if runner.status(wid).get("status") != "running":
            break
    assert runner.status(wid)["status"] == "error"
    assert calls == ["fixer", "fixer"]  # round 1 done, round 2 spawn tried
    bash_before = list(br.bash_calls)
    assert bash_before == ["tsc"]  # round-1 predicate evaluated once

    await runner.resume(wid)
    for _ in range(100):
        await asyncio.sleep(0.01)
        if runner.status(wid).get("status") in {"ok", "error"}:
            break
    assert runner.status(wid)["status"] == "ok"
    # Round-1 body must NOT respawn. Round-2 body runs; round-2 predicate
    # evaluates (exit 0 → stop). Round-1 predicate NOT re-evaluated.
    assert calls == ["fixer", "fixer", "fixer"]  # only round-2 re-spawned
    assert br.bash_calls == ["tsc", "tsc"]  # round-1 shell NOT re-run
