"""Hook invocation: timeout, log-and-skip vs strict, JSONL logging."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from aegis.hooks.contexts import (
    PreTurnContext, PreTurnResult, SessionHandle,
)
from aegis.hooks.decorator import HookEntry, _reset_registry_for_tests
from aegis.hooks.runner import run_pre_turn_hooks


@pytest.fixture(autouse=True)
def _clean():
    _reset_registry_for_tests()
    yield
    _reset_registry_for_tests()


def _ctx(tmp_path: Path) -> PreTurnContext:
    return PreTurnContext(
        session=SessionHandle(handle="x", agent_profile="p", harness="claude"),
        user_message="hi",
        history=(),
        project_root=tmp_path,
        prior_results=(),
    )


@pytest.mark.asyncio
async def test_runs_a_hook_and_returns_result(tmp_path: Path) -> None:
    async def my_hook(ctx):
        return PreTurnResult(prepend_system="hello")
    entries = [HookEntry(event="pre_turn", func=my_hook, strict=False,
                         qualname="t.my_hook")]
    composed = await run_pre_turn_hooks(_ctx(tmp_path), entries, state_dir=tmp_path / "state")
    assert composed.prepend_system == "hello"


@pytest.mark.asyncio
async def test_hook_exception_is_logged_and_skipped(tmp_path: Path) -> None:
    async def bad(ctx):
        raise RuntimeError("boom")
    async def good(ctx):
        return PreTurnResult(prepend_system="ok")
    entries = [
        HookEntry(event="pre_turn", func=bad, strict=False, qualname="t.bad"),
        HookEntry(event="pre_turn", func=good, strict=False, qualname="t.good"),
    ]
    state = tmp_path / "state"
    composed = await run_pre_turn_hooks(_ctx(tmp_path), entries, state_dir=state)
    assert composed.prepend_system == "ok"
    log = state / "hooks" / "t.bad.jsonl"
    assert log.exists()
    rec = json.loads(log.read_text().strip().splitlines()[-1])
    assert rec["status"] == "exception"
    assert "boom" in rec["error"]


@pytest.mark.asyncio
async def test_strict_hook_exception_blocks_turn(tmp_path: Path) -> None:
    async def bad(ctx):
        raise RuntimeError("boom")
    entries = [HookEntry(event="pre_turn", func=bad, strict=True,
                         qualname="t.bad")]
    composed = await run_pre_turn_hooks(_ctx(tmp_path), entries, state_dir=tmp_path / "state")
    assert composed.block is not None
    assert "boom" in composed.block


@pytest.mark.asyncio
async def test_hook_timeout_logs_and_skips(tmp_path: Path) -> None:
    async def slow(ctx):
        await asyncio.sleep(10)
        return PreTurnResult(prepend_system="never")
    entries = [HookEntry(event="pre_turn", func=slow, strict=False,
                         qualname="t.slow")]
    state = tmp_path / "state"
    composed = await run_pre_turn_hooks(
        _ctx(tmp_path), entries, state_dir=state, timeout=0.05
    )
    assert composed.prepend_system is None
    log = state / "hooks" / "t.slow.jsonl"
    rec = json.loads(log.read_text().strip().splitlines()[-1])
    assert rec["status"] == "timeout"


@pytest.mark.asyncio
async def test_success_logged(tmp_path: Path) -> None:
    async def ok(ctx):
        return PreTurnResult(prepend_system="x")
    entries = [HookEntry(event="pre_turn", func=ok, strict=False,
                         qualname="t.ok")]
    state = tmp_path / "state"
    await run_pre_turn_hooks(_ctx(tmp_path), entries, state_dir=state)
    log = state / "hooks" / "t.ok.jsonl"
    rec = json.loads(log.read_text().strip().splitlines()[-1])
    assert rec["status"] == "ok"
