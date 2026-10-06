"""pre_spawn hook event: contexts, decorator registration, runner composition.

The pre_spawn event fires once per harness session, between argv/env
construction and ``asyncio.create_subprocess_exec``. Hooks can rewrite
argv (proxy wrappers, sandboxers, resource limiters) or env (per-agent
credentials, regional routing) before the child process is launched.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from aegis.hooks import SessionHandle
from aegis.hooks.contexts import PreSpawnContext, PreSpawnResult
from aegis.hooks.decorator import (
    _REGISTRY, HookEntry, _reset_registry_for_tests, hook,
)
from aegis.hooks.runner import run_pre_spawn_hooks


@pytest.fixture(autouse=True)
def _clean():
    _reset_registry_for_tests()
    yield
    _reset_registry_for_tests()


# ---------- context / result dataclasses --------------------------------


def test_prespawn_context_is_frozen() -> None:
    ctx = PreSpawnContext(
        session=SessionHandle(handle="x", agent_profile="p", harness="claude"),
        argv=("claude", "-p"),
        env={"PATH": "/usr/bin"},
        cwd="/tmp",
    )
    with pytest.raises((AttributeError, Exception)):
        ctx.argv = ("nope",)


def test_prespawn_result_defaults_are_none() -> None:
    r = PreSpawnResult()
    assert r.argv is None
    assert r.env is None
    assert r.block is None


# ---------- decorator registration --------------------------------------


def test_pre_spawn_is_a_valid_event() -> None:
    @hook("pre_spawn")
    async def my_hook(ctx):
        return None
    assert len(_REGISTRY["pre_spawn"]) == 1
    assert _REGISTRY["pre_spawn"][0].func is my_hook


# ---------- runner composition ------------------------------------------


def _handle() -> SessionHandle:
    return SessionHandle(handle="x", agent_profile="p", harness="claude")


@pytest.mark.asyncio
async def test_no_hooks_returns_initial_argv_and_env(tmp_path: Path) -> None:
    composed = await run_pre_spawn_hooks(
        argv=("claude", "-p"), env={"A": "1"},
        session=_handle(), cwd="/tmp",
        entries=[], state_dir=tmp_path / "state",
    )
    assert composed.argv == ("claude", "-p")
    assert composed.env == {"A": "1"}
    assert composed.block is None


@pytest.mark.asyncio
async def test_single_hook_rewrites_argv(tmp_path: Path) -> None:
    async def wrap(ctx):
        return PreSpawnResult(argv=("proxychains4", "-q", *ctx.argv))
    entries = [HookEntry(event="pre_spawn", func=wrap, strict=False,
                         qualname="t.wrap")]
    composed = await run_pre_spawn_hooks(
        argv=("claude", "-p"), env={},
        session=_handle(), cwd="/tmp",
        entries=entries, state_dir=tmp_path / "state",
    )
    assert composed.argv == ("proxychains4", "-q", "claude", "-p")


@pytest.mark.asyncio
async def test_hooks_chain_with_accumulated_argv(tmp_path: Path) -> None:
    """Second hook sees the first hook's transformed argv in ctx.argv."""
    async def outer(ctx):
        return PreSpawnResult(argv=("OUTER", *ctx.argv))
    async def inner(ctx):
        return PreSpawnResult(argv=("INNER", *ctx.argv))
    entries = [
        HookEntry(event="pre_spawn", func=outer, strict=False,
                  qualname="t.outer"),
        HookEntry(event="pre_spawn", func=inner, strict=False,
                  qualname="t.inner"),
    ]
    composed = await run_pre_spawn_hooks(
        argv=("claude",), env={},
        session=_handle(), cwd="/tmp",
        entries=entries, state_dir=tmp_path / "state",
    )
    # declaration order: outer first, then inner — final argv stacks both
    assert composed.argv == ("INNER", "OUTER", "claude")


@pytest.mark.asyncio
async def test_hook_can_set_env(tmp_path: Path) -> None:
    async def setenv(ctx):
        return PreSpawnResult(env={**ctx.env, "ALL_PROXY": "socks5://1:2"})
    entries = [HookEntry(event="pre_spawn", func=setenv, strict=False,
                         qualname="t.setenv")]
    composed = await run_pre_spawn_hooks(
        argv=("x",), env={"PATH": "/usr/bin"},
        session=_handle(), cwd="/tmp",
        entries=entries, state_dir=tmp_path / "state",
    )
    assert composed.env == {"PATH": "/usr/bin", "ALL_PROXY": "socks5://1:2"}


@pytest.mark.asyncio
async def test_hook_returning_none_is_a_noop(tmp_path: Path) -> None:
    async def noop(ctx):
        return None
    entries = [HookEntry(event="pre_spawn", func=noop, strict=False,
                         qualname="t.noop")]
    composed = await run_pre_spawn_hooks(
        argv=("claude",), env={"A": "1"},
        session=_handle(), cwd="/tmp",
        entries=entries, state_dir=tmp_path / "state",
    )
    assert composed.argv == ("claude",)
    assert composed.env == {"A": "1"}


@pytest.mark.asyncio
async def test_hook_exception_is_logged_and_skipped(tmp_path: Path) -> None:
    async def bad(ctx):
        raise RuntimeError("boom")
    async def good(ctx):
        return PreSpawnResult(argv=("WRAP", *ctx.argv))
    entries = [
        HookEntry(event="pre_spawn", func=bad, strict=False, qualname="t.bad"),
        HookEntry(event="pre_spawn", func=good, strict=False,
                  qualname="t.good"),
    ]
    state = tmp_path / "state"
    composed = await run_pre_spawn_hooks(
        argv=("claude",), env={},
        session=_handle(), cwd="/tmp",
        entries=entries, state_dir=state,
    )
    assert composed.argv == ("WRAP", "claude")
    log = state / "hooks" / "t.bad.jsonl"
    assert log.exists()
    rec = json.loads(log.read_text().strip().splitlines()[-1])
    assert rec["status"] == "exception"
    assert "boom" in rec["error"]


@pytest.mark.asyncio
async def test_strict_hook_exception_blocks_spawn(tmp_path: Path) -> None:
    async def bad(ctx):
        raise RuntimeError("boom")
    entries = [HookEntry(event="pre_spawn", func=bad, strict=True,
                         qualname="t.bad")]
    composed = await run_pre_spawn_hooks(
        argv=("claude",), env={},
        session=_handle(), cwd="/tmp",
        entries=entries, state_dir=tmp_path / "state",
    )
    assert composed.block is not None
    assert "boom" in composed.block


@pytest.mark.asyncio
async def test_hook_returning_block_short_circuits(tmp_path: Path) -> None:
    """A hook can refuse to spawn — chain stops; block surfaces."""
    async def deny(ctx):
        return PreSpawnResult(block="socks endpoint unreachable")
    async def downstream(ctx):
        return PreSpawnResult(argv=("never",))
    entries = [
        HookEntry(event="pre_spawn", func=deny, strict=False,
                  qualname="t.deny"),
        HookEntry(event="pre_spawn", func=downstream, strict=False,
                  qualname="t.downstream"),
    ]
    composed = await run_pre_spawn_hooks(
        argv=("claude",), env={},
        session=_handle(), cwd="/tmp",
        entries=entries, state_dir=tmp_path / "state",
    )
    assert composed.block == "socks endpoint unreachable"
    # downstream did not run
    assert composed.argv == ("claude",)


@pytest.mark.asyncio
async def test_hook_timeout_logs_and_skips(tmp_path: Path) -> None:
    async def slow(ctx):
        await asyncio.sleep(10)
        return PreSpawnResult(argv=("never",))
    entries = [HookEntry(event="pre_spawn", func=slow, strict=False,
                         qualname="t.slow")]
    state = tmp_path / "state"
    composed = await run_pre_spawn_hooks(
        argv=("claude",), env={},
        session=_handle(), cwd="/tmp",
        entries=entries, state_dir=state, timeout=0.05,
    )
    assert composed.argv == ("claude",)
    rec = json.loads(
        (state / "hooks" / "t.slow.jsonl").read_text().strip().splitlines()[-1]
    )
    assert rec["status"] == "timeout"
