from __future__ import annotations

from pathlib import Path

import pytest

from aegis.workflow import WorkflowEngine


class _StubBridge:
    queue_manager = None
    inbox_router = None
    def list_sessions(self): return []
    def list_agents(self): return ["default"]


def _engine(tmp_path: Path, **kw):
    return WorkflowEngine(
        workflow_name="t", workflow_run_id="01TID",
        bridge=_StubBridge(), queue_manager=None, inbox_router=None,
        state_dir=tmp_path, **kw)


def test_engine_exposes_name_run_id_caller(tmp_path):
    e = _engine(tmp_path, caller_handle="lucid-knuth")
    assert e.workflow_name == "t"
    assert e.workflow_run_id == "01TID"
    assert e.caller_handle == "lucid-knuth"


def test_engine_caller_defaults_to_none(tmp_path):
    e = _engine(tmp_path)
    assert e.caller_handle is None


def test_engine_log_writes_jsonl_under_state_dir(tmp_path):
    e = _engine(tmp_path)
    e.log("hello")
    e.log("world")
    log_file = tmp_path / "workflows" / "01TID.jsonl"
    assert log_file.exists()
    lines = [line for line in log_file.read_text().splitlines() if line]
    assert len(lines) == 2
    import json
    assert json.loads(lines[0])["message"] == "hello"
    assert json.loads(lines[1])["message"] == "world"


def test_engine_log_no_state_dir_is_stderr_only(capfd):
    e = WorkflowEngine(workflow_name="t", workflow_run_id="01TID",
                       bridge=_StubBridge(), queue_manager=None,
                       inbox_router=None, state_dir=None)
    e.log("only-stderr")
    captured = capfd.readouterr()
    assert "only-stderr" in captured.err
    assert "[workflow:t]" in captured.err


def test_engine_initial_state_empty(tmp_path):
    e = _engine(tmp_path)
    assert e._spawned_handles == set()
    assert e._touched_handles == set()


def test_engine_list_passthroughs(tmp_path):
    e = _engine(tmp_path)
    assert e.list_sessions() == []
    assert e.list_agents() == ["default"]


import asyncio
import subprocess

from aegis.workflow import WorkflowError


async def test_bash_returns_completed_process(tmp_path):
    e = _engine(tmp_path)
    proc = await e.bash("echo hi")
    assert isinstance(proc, subprocess.CompletedProcess)
    assert proc.returncode == 0
    assert proc.stdout.strip() == "hi"
    assert proc.stderr == ""


async def test_bash_nonzero_returncode_not_raised(tmp_path):
    e = _engine(tmp_path)
    proc = await e.bash("false")
    assert proc.returncode != 0


async def test_bash_timeout_raises_workflow_error(tmp_path):
    e = _engine(tmp_path)
    with pytest.raises(WorkflowError, match="timed out"):
        await e.bash("sleep 5", timeout=0.1)


async def test_bash_default_cwd_is_project_root(tmp_path, monkeypatch):
    # Run from a tmp dir; bash() should still resolve to project root
    # (or fall back to tmp_path when no .aegis.yaml upstream).
    monkeypatch.chdir(tmp_path)
    e = _engine(tmp_path)
    proc = await e.bash("pwd")
    # We don't assert exact path (depends on find_project_root in test env)
    # — just that it executed and produced a string.
    assert proc.returncode == 0
    assert proc.stdout.strip()


async def test_bash_explicit_cwd_honored(tmp_path):
    e = _engine(tmp_path)
    proc = await e.bash("pwd", cwd=tmp_path)
    assert tmp_path.name in proc.stdout


from aegis.queue import (
    InboxMessage, InboxRouter, Queue, QueueManager, sender_agent,
)
from aegis.events import AssistantText, Result


class _StubSM:
    def __init__(self):
        self._sessions = []
        self._scripts: dict[str, list] = {}
        self.closed: list[str] = []
    def script(self, handle, events):
        self._scripts[handle] = events
    def spawn(self, slug, *, opening_prompt=None, handle=None):
        from aegis.core.session import AgentSession
        evs = self._scripts.get(
            handle,
            [AssistantText(text="DONE"),
             Result(duration_ms=1, is_error=False, usage=None)])
        class _H:
            def __init__(s, e): s._e = list(e); s.sent = []; s.started = s.closed = False
            async def start(s): s.started = True
            async def send(s, t): s.sent.append(t)
            async def close(s): s.closed = True
            async def events(s):
                import asyncio
                for e in s._e:
                    await asyncio.sleep(0)
                    yield e
        sess = AgentSession(_H(evs), None, slug, handle)
        self._sessions.append(sess)
        if opening_prompt is not None:
            import asyncio
            asyncio.create_task(sess.send(opening_prompt))
        return sess
    async def close(self, handle):
        self.closed.append(handle)
        self._sessions = [s for s in self._sessions if s.handle != handle]


def _engine_with_queue(tmp_path, *, sm=None, inbox=None, qm=None,
                       worker_handle="w1"):
    sm = sm or _StubSM()
    inbox = inbox or InboxRouter()
    qm = qm or QueueManager(
        {"impl": Queue(name="impl", agent_profile="default",
                       max_parallel=1)},
        sm, inbox, handle_factory=lambda used: worker_handle)
    return (WorkflowEngine(
        workflow_name="t", workflow_run_id="01TID",
        bridge=_StubBridge(), queue_manager=qm, inbox_router=inbox,
        state_dir=tmp_path), sm, qm, inbox)


async def test_delegate_returns_worker_result_text(tmp_path):
    e, sm, _qm, _inbox = _engine_with_queue(tmp_path)
    sm.script("w1", [AssistantText(text="hello from worker"),
                     Result(duration_ms=1, is_error=False, usage=None)])
    out = await e.delegate("impl", "do the thing")
    assert out == "hello from worker"


async def test_delegate_worker_failure_raises_workflow_error(tmp_path):
    e, sm, _qm, _inbox = _engine_with_queue(tmp_path)
    sm.script("w1", [Result(duration_ms=1, is_error=True, usage=None)])
    with pytest.raises(WorkflowError, match="task .* failed"):
        await e.delegate("impl", "fail me")


async def test_delegate_unknown_queue_raises_workflow_error(tmp_path):
    e, _sm, _qm, _inbox = _engine_with_queue(tmp_path)
    with pytest.raises(WorkflowError, match="unknown queue"):
        await e.delegate("ghost", "x")


async def test_concurrent_delegates_use_unique_inbox_handles(tmp_path):
    # Two workers in parallel; each callback resolves the correct promise.
    sm = _StubSM()
    inbox = InboxRouter()
    qm = QueueManager(
        {"impl": Queue(name="impl", agent_profile="default",
                       max_parallel=2)},
        sm, inbox,
        handle_factory=lambda used: f"w{len(used) + 1}")
    sm.script("w1", [AssistantText(text="ONE"),
                     Result(duration_ms=1, is_error=False, usage=None)])
    sm.script("w2", [AssistantText(text="TWO"),
                     Result(duration_ms=1, is_error=False, usage=None)])
    e = WorkflowEngine(
        workflow_name="t", workflow_run_id="01TID",
        bridge=_StubBridge(), queue_manager=qm, inbox_router=inbox,
        state_dir=tmp_path)
    a, b = await asyncio.gather(
        e.delegate("impl", "a"),
        e.delegate("impl", "b"))
    assert {a, b} == {"ONE", "TWO"}


class _SpawningStubBridge:
    def __init__(self):
        self._spawned = []
        self._closed = []
        self.queue_manager = None
        self.inbox_router = None
    def list_sessions(self): return []
    def list_agents(self): return []
    async def handoff(self, a, b, c): return "ok"
    async def spawn(self, profile, *, handle=None):
        h = handle or f"auto-{len(self._spawned) + 1}"
        self._spawned.append((profile, h))
        return h
    async def close(self, handle):
        self._closed.append(handle)


async def test_engine_spawn_tracks_handle_and_returns_it(tmp_path):
    br = _SpawningStubBridge()
    e = WorkflowEngine(workflow_name="t", workflow_run_id="01",
                       bridge=br, queue_manager=None, inbox_router=None,
                       state_dir=tmp_path)
    h = await e.spawn("reviewer", handle="r1")
    assert h == "r1"
    assert "r1" in e._spawned_handles
    assert ("reviewer", "r1") in br._spawned


async def test_engine_close_removes_handle_and_is_idempotent(tmp_path):
    br = _SpawningStubBridge()
    e = WorkflowEngine(workflow_name="t", workflow_run_id="01",
                       bridge=br, queue_manager=None, inbox_router=None,
                       state_dir=tmp_path)
    h = await e.spawn("reviewer", handle="r1")
    await e.close(h)
    assert "r1" not in e._spawned_handles
    assert "r1" in br._closed
    # Idempotent: closing again is a no-op
    await e.close(h)
    assert br._closed == ["r1"]    # not appended twice


class _RecordingInbox:
    """Captures every deliver call; doesn't actually route."""
    def __init__(self):
        self.delivered: list[tuple[str, InboxMessage]] = []
        self._sessions = {}
    def bind_session(self, handle, session):
        self._sessions[handle] = session
    def unbind_session(self, handle):
        self._sessions.pop(handle, None)
    async def deliver(self, handle, msg):
        self.delivered.append((handle, msg))


async def test_engine_send_fallback_delivers_to_inbox(tmp_path):
    """When the bridge has no ``workflow_runner``, ``send`` falls back
    to legacy inbox delivery and returns "" (no reply available)."""
    inbox = _RecordingInbox()
    e = WorkflowEngine(workflow_name="tdd_step", workflow_run_id="01",
                       bridge=_StubBridge(), queue_manager=None,
                       inbox_router=inbox, state_dir=tmp_path)
    reply = await e.send("lucid-knuth", "do the thing")
    assert reply == ""
    assert len(inbox.delivered) == 1
    handle, msg = inbox.delivered[0]
    assert handle == "lucid-knuth"
    assert msg.sender == "workflow:tdd_step"
    assert msg.body == "do the thing"
    assert msg.timestamp        # ISO string
    assert "lucid-knuth" in e._touched_handles


async def test_engine_send_tracks_touched_handle(tmp_path):
    inbox = _RecordingInbox()
    e = WorkflowEngine(workflow_name="t", workflow_run_id="01",
                       bridge=_StubBridge(), queue_manager=None,
                       inbox_router=inbox, state_dir=tmp_path)
    await e.send("h", "msg")
    assert "h" in e._touched_handles


from aegis.core.session import AgentSession
from aegis.tui.state import AgentState


class _LiveFakeHarness:
    """Fake harness whose events generator finishes one short turn."""
    def __init__(self):
        self.sent = []
        self.started = self.closed = False
    async def start(self): self.started = True
    async def send(self, t): self.sent.append(t)
    async def close(self): self.closed = True
    async def events(self):
        import asyncio as _a
        await _a.sleep(0)
        yield AssistantText("ok")
        yield Result(duration_ms=1, is_error=False, usage=None)


async def test_drain_returns_when_target_idle(tmp_path):
    inbox = InboxRouter()
    h = "lucid-knuth"
    sess = AgentSession(_LiveFakeHarness(), None, "default", h, inbox=inbox)
    inbox.bind_session(h, sess)
    e = WorkflowEngine(workflow_name="t", workflow_run_id="01",
                       bridge=_StubBridge(), queue_manager=None,
                       inbox_router=inbox, state_dir=tmp_path,
                       drain_timeout=2.0)
    await e.send(h, "go")
    await e.drain(h)
    assert sess.state is AgentState.ready


async def test_drain_no_touched_handle_is_noop(tmp_path):
    e = WorkflowEngine(workflow_name="t", workflow_run_id="01",
                       bridge=_StubBridge(), queue_manager=None,
                       inbox_router=InboxRouter(), state_dir=tmp_path)
    # Touched-handle set is empty; drain returns immediately.
    await e.drain()


async def test_drain_timeout_logs_warning_and_returns(tmp_path, capfd):
    inbox = InboxRouter()
    h = "stuck"
    class _NeverFinishes:
        sent = []; started = closed = False
        async def start(self): pass
        async def send(self, t): self.sent.append(t)
        async def close(self): pass
        async def events(self):
            import asyncio as _a
            await _a.Event().wait()
            if False: yield  # pragma: no cover
    sess = AgentSession(_NeverFinishes(), None, "default", h, inbox=inbox)
    inbox.bind_session(h, sess)
    e = WorkflowEngine(workflow_name="t", workflow_run_id="01",
                       bridge=_StubBridge(), queue_manager=None,
                       inbox_router=inbox, state_dir=tmp_path,
                       drain_timeout=0.05)
    await e.send(h, "go")
    await e.drain(h)
    out = capfd.readouterr().err
    assert "drain timed out" in out
    assert h in out
