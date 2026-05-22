"""Workflow test fixtures — minimal fake bridges/runners.

The fakes expose just enough of the ``workflow_runner`` surface
(``send_and_await_reply``, ``spawn_subagent``, ``close_session``,
``register_human_question``, ``append_ledger``/``read_ledger``,
``run_bash``, ``narrate``) for the catalog and engine tests to exercise
the engine without booting a real ``SessionManager``/``InboxRouter``.

Imported by ``tests/conftest.py``.
"""
from __future__ import annotations

import asyncio
import json
from collections import deque
from pathlib import Path
from typing import Any

import pytest


class FakeBridge:
    """A bridge + workflow_runner all in one. Tests reach for the
    methods they need; absent methods raise AttributeError, surfacing
    coverage gaps."""

    def __init__(self) -> None:
        self.queue_manager = None
        self.inbox_router = None
        self.workflow_runner = self  # self-as-runner
        # send/reply bookkeeping ------------------------------------
        self._replies: dict[str, str | list[str]] = {}
        self._sends_to: dict[str, list[str]] = {}
        # human-question bookkeeping --------------------------------
        self._human_queue: dict[str, deque[str]] = {}
        self._last_options: dict[str, list[str] | None] = {}
        # spawn bookkeeping -----------------------------------------
        self._spawn_counter = 0
        self.live_handles: set[str] = set()
        self.spawned_profiles: list[str] = []
        self.spawned_handles: list[str] = []
        self.closed_handles: list[str] = []
        # bash bookkeeping ------------------------------------------
        self._bash_sequence: list[dict] = []
        self.bash_calls: list[str] = []
        # narration / state-dir -------------------------------------
        self._state_dir: Path | None = None
        self.narrations: list[tuple[str, str, str]] = []

    # AppBridge bits the engine occasionally reads ------------------
    def list_sessions(self) -> list:
        return []

    def list_agents(self) -> list[str]:
        return []

    async def handoff(self, a: str, b: str, c: str) -> str:
        return "ok"

    async def spawn(self, profile: str, *,
                    handle: str | None = None) -> str:
        # Legacy AppBridge.spawn — unused once workflow_runner.spawn_subagent
        # is preferred, but kept for fallback paths.
        return await self.spawn_subagent(profile, alias=handle)

    async def close(self, handle: str) -> None:
        await self.close_session(handle)

    # workflow_runner surface -----------------------------------------
    def set_reply(self, handle: str, reply: str) -> None:
        self._replies[handle] = reply

    def set_reply_sequence(self, handle: str, replies: list[str]) -> None:
        self._replies[handle] = list(replies)

    def sends_to(self, handle: str) -> list[str]:
        return list(self._sends_to.get(handle, []))

    async def send_and_await_reply(self, *, handle: str, prompt: str,
                                   workflow_id: str, workflow_name: str,
                                   timeout: float | None = None) -> str:
        self._sends_to.setdefault(handle, []).append(prompt)
        canned = self._replies.get(handle, "")
        if isinstance(canned, list):
            if canned:
                return canned.pop(0)
            return ""
        return canned

    def enqueue_reply(self, handle: str, reply: str) -> None:
        self._human_queue.setdefault(handle, deque()).append(reply)

    def last_options(self, handle: str) -> list[str] | None:
        return self._last_options.get(handle)

    async def register_human_question(self, *, host: str, workflow_id: str,
                                      question: str,
                                      options: list[str] | None,
                                      fut: asyncio.Future) -> None:
        self._last_options[host] = options
        q = self._human_queue.get(host)
        if q:
            reply = q.popleft()
            if not fut.done():
                fut.set_result(reply)
        # else: leave pending (test must enqueue or rely on timeout)

    async def spawn_subagent(self, profile: str, *,
                             alias: str | None = None) -> str:
        self._spawn_counter += 1
        h = alias or f"{profile}-{self._spawn_counter}"
        self.live_handles.add(h)
        self.spawned_profiles.append(profile)
        self.spawned_handles.append(h)
        return h

    async def close_session(self, handle: str) -> None:
        self.live_handles.discard(handle)
        self.closed_handles.append(handle)

    def set_bash_sequence(self, seq: list[dict]) -> None:
        self._bash_sequence = list(seq)

    async def run_bash(self, cmd: str, *,
                       cwd: str | None = None,
                       timeout: float | None = None) -> dict:
        self.bash_calls.append(cmd)
        if self._bash_sequence:
            return self._bash_sequence.pop(0)
        return {"exit": 0, "stdout": "", "stderr": ""}

    def set_state_dir(self, path: Path) -> None:
        self._state_dir = Path(path)

    def _ledger_path(self, workflow_id: str) -> Path:
        if self._state_dir is None:
            raise RuntimeError(
                "FakeBridge: state_dir not set (call set_state_dir first)")
        return self._state_dir / workflow_id / "ledger.jsonl"

    def append_ledger(self, workflow_id: str, record: dict) -> None:
        path = self._ledger_path(workflow_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")

    def read_ledger(self, workflow_id: str) -> list[dict]:
        path = self._ledger_path(workflow_id)
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines()
                if line.strip()]

    async def narrate(self, workflow_id: str, host: str | None,
                      msg: str) -> None:
        self.narrations.append((workflow_id, host or "", msg))


# ── fixtures ────────────────────────────────────────────────────────


@pytest.fixture
def fake_bridge() -> FakeBridge:
    return FakeBridge()


@pytest.fixture
def fake_bridge_with_canned_reply() -> FakeBridge:
    return FakeBridge()


@pytest.fixture
def fake_bridge_with_spawner() -> FakeBridge:
    return FakeBridge()


@pytest.fixture
def fake_bridge_with_human_queue() -> FakeBridge:
    return FakeBridge()


@pytest.fixture
def fake_bridge_with_state() -> FakeBridge:
    return FakeBridge()


@pytest.fixture
def fake_bridge_with_runner() -> FakeBridge:
    """Bridge with a real ``WorkflowRunner`` attached (used by MCP and
    resume tests). The runner reads/writes a ledger via the bridge's
    ``state_dir`` plumbing — call ``set_state_dir`` before ``start``."""
    from aegis.workflow.runner import WorkflowRunner
    br = FakeBridge()
    br.workflow_runner = WorkflowRunner(br)
    return br


class _Harness:
    """Test harness for end-to-end workflow tests (slices 6–9).

    Holds the FakeBridge and pre-built engine. Tracks
    spawned_profiles / spawned_handles / closed_handles for assertions.
    """

    def __init__(self, *, host: str = "h",
                 human_replies: list[str] | None = None,
                 subagent_replies: dict[str, str] | None = None,
                 bash_sequence: list[dict] | None = None,
                 initial_state: dict | None = None,
                 cwd: Path | None = None,
                 config: dict | None = None,
                 workflow_id: str = "wf_test",
                 state_dir: Path | None = None) -> None:
        from aegis.workflow.engine import WorkflowEngine
        self.bridge = FakeBridge()
        if state_dir is not None:
            self.bridge.set_state_dir(state_dir)
        elif cwd is not None:
            self.bridge.set_state_dir(Path(cwd) / ".aegis-state")
        # human reply queue
        for r in (human_replies or []):
            self.bridge.enqueue_reply(host, r)
        # subagent replies — keyed by profile (the harness maps the
        # spawned handle back to the profile and dispenses replies).
        self._subagent_replies = dict(subagent_replies or {})
        self.bridge.send_and_await_reply = self._send_and_await_reply  # type: ignore[assignment]
        if bash_sequence is not None:
            self.bridge.set_bash_sequence(bash_sequence)
        if initial_state is not None:
            # Seed a single checkpoint for resume_state to find.
            self.bridge.append_ledger(workflow_id, {
                "kind": "checkpoint", "at": "1970-01-01T00:00:00",
                "name": "seed", "payload": initial_state,
            })
        self.cwd = cwd
        cfg = dict(config) if config else {}
        if cwd is not None and "cwd" not in cfg:
            cfg["cwd"] = str(cwd)
        self.engine = WorkflowEngine(
            bridge=self.bridge, workflow_id=workflow_id,
            name="harness", host=host, config=cfg)

    async def _send_and_await_reply(self, *, handle: str, prompt: str,
                                    workflow_id: str, workflow_name: str,
                                    timeout: float | None = None) -> str:
        self.bridge._sends_to.setdefault(handle, []).append(prompt)
        # Reply by handle if explicitly set; else by profile (the
        # subagent_handles list records the profile for each spawn).
        if handle in self.bridge._replies:
            r = self.bridge._replies[handle]
            if isinstance(r, list):
                return r.pop(0) if r else ""
            return r
        # Map handle -> profile via spawn order.
        if handle in self.bridge.spawned_handles:
            idx = self.bridge.spawned_handles.index(handle)
            profile = self.bridge.spawned_profiles[idx]
            return self._subagent_replies.get(profile, "")
        return self._subagent_replies.get(handle, "")

    # convenience proxies
    @property
    def spawned_profiles(self) -> list[str]:
        return self.bridge.spawned_profiles

    @property
    def spawned_handles(self) -> list[str]:
        return self.bridge.spawned_handles

    @property
    def closed_handles(self) -> list[str]:
        return self.bridge.closed_handles


@pytest.fixture
def workflow_test_harness(tmp_path: Path):
    """Factory fixture: callers pass workflow-specific knobs.

    Usage::

        harness = workflow_test_harness(
            host="h",
            subagent_replies={"implementer": "ok"},
            cwd=tmp_path,
        )
        await my_workflow(harness.engine, ...)
        assert "implementer" in harness.spawned_profiles
    """
    def _make(**kw: Any) -> _Harness:
        kw.setdefault("cwd", tmp_path)
        return _Harness(**kw)
    return _make
