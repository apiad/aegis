"""Slice 4 — bash returns structured dict; bash_predicate retry loop;
parallel branches."""
from __future__ import annotations

import pytest

from aegis.workflow.engine import PredicateFailed, WorkflowEngine


async def test_bash_returns_structured_result(fake_bridge):
    eng = WorkflowEngine(
        bridge=fake_bridge, workflow_id="w", name="t",
        host="h", config={})
    res = await eng.bash("echo hello")
    assert res["exit"] == 0
    assert res["stdout"] == ""  # FakeBridge.run_bash default


async def test_bash_predicate_succeeds_first_try(fake_bridge):
    eng = WorkflowEngine(
        bridge=fake_bridge, workflow_id="w", name="t",
        host="h", config={})
    res = await eng.bash_predicate("true", retry_with="never used")
    assert res["exit"] == 0


async def test_bash_predicate_retries_with_feedback(
        fake_bridge_with_canned_reply):
    fake_bridge_with_canned_reply.set_reply("h", "ok I'll try")
    eng = WorkflowEngine(
        bridge=fake_bridge_with_canned_reply, workflow_id="w",
        name="t", host="h", config={})
    fake_bridge_with_canned_reply.set_bash_sequence([
        {"exit": 1, "stdout": "fail1", "stderr": ""},
        {"exit": 1, "stdout": "fail2", "stderr": ""},
        {"exit": 0, "stdout": "ok", "stderr": ""},
    ])
    res = await eng.bash_predicate(
        "pytest", retry_with="fix it", max_retries=3)
    assert res["exit"] == 0
    assert fake_bridge_with_canned_reply.sends_to("h") == [
        "fix it", "fix it"]


async def test_bash_predicate_raises_when_max_retries_exhausted(
        fake_bridge_with_canned_reply):
    fake_bridge_with_canned_reply.set_reply("h", "trying")
    eng = WorkflowEngine(
        bridge=fake_bridge_with_canned_reply, workflow_id="w",
        name="t", host="h", config={})
    fake_bridge_with_canned_reply.set_bash_sequence([
        {"exit": 1, "stdout": "", "stderr": ""},
        {"exit": 1, "stdout": "", "stderr": ""},
    ])
    with pytest.raises(PredicateFailed):
        await eng.bash_predicate("pytest", retry_with="x", max_retries=1)


async def test_bash_predicate_retry_with_template_substitutes(
        fake_bridge_with_canned_reply):
    fake_bridge_with_canned_reply.set_reply("h", "ok")
    eng = WorkflowEngine(
        bridge=fake_bridge_with_canned_reply, workflow_id="w",
        name="t", host="h", config={})
    fake_bridge_with_canned_reply.set_bash_sequence([
        {"exit": 2, "stdout": "boom", "stderr": "err"},
        {"exit": 0, "stdout": "", "stderr": ""},
    ])
    await eng.bash_predicate(
        "pytest",
        retry_with="exit={exit} stdout={stdout} stderr={stderr}")
    sends = fake_bridge_with_canned_reply.sends_to("h")
    assert sends == ["exit=2 stdout=boom stderr=err"]


async def test_bash_predicate_retry_with_callable(
        fake_bridge_with_canned_reply):
    fake_bridge_with_canned_reply.set_reply("h", "ok")
    eng = WorkflowEngine(
        bridge=fake_bridge_with_canned_reply, workflow_id="w",
        name="t", host="h", config={})
    fake_bridge_with_canned_reply.set_bash_sequence([
        {"exit": 1, "stdout": "x", "stderr": ""},
        {"exit": 0, "stdout": "", "stderr": ""},
    ])
    await eng.bash_predicate(
        "pytest",
        retry_with=lambda r: f"got exit {r['exit']}")
    assert fake_bridge_with_canned_reply.sends_to("h") == ["got exit 1"]


async def test_parallel_runs_branches(fake_bridge):
    eng = WorkflowEngine(
        bridge=fake_bridge, workflow_id="w", name="t",
        host="h", config={})

    async def one():
        return "a"

    async def two():
        return "b"

    results = await eng.parallel([one(), two()])
    assert set(results) == {"a", "b"}


async def test_config_passthrough_via_engine_kwarg():
    eng = WorkflowEngine(
        bridge=None, workflow_id="w", name="t",
        host="h", config={"foo": 42, "bar": "x"})
    assert eng.config == {"foo": 42, "bar": "x"}
