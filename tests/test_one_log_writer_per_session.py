"""One session, one writer to its transcript.

Two places attach a session-log observer: `SessionManager` when it spawns
a session, and `ConversationPane.__init__` for the pane's own log. While a
pane built its own `AgentSession` those were the same thing counted once.

They stopped being the same thing when the brain started owning sessions.
`_mount_brain_pane` hands the pane a core the manager already spawned and
already logs, so the pane added a second writer, and it did so once per
pane. Every attach added another: a view attached twice wrote every
subsequent event twice, three times wrote it three times.

It shows up as a transcript with each line repeated after a reattach,
which reads as a rendering bug and is not one. The replay is faithful; the
file on disk really does hold the event N times. `release_core_observers`
does not save it either, because the log observer is an anonymous closure
the pane keeps no reference to.

Asserted on the file, which is the thing that was actually corrupted.
"""
from __future__ import annotations

from pathlib import Path

from aegis.config import Agent
from aegis.config.roots import AegisRoots
from aegis.core.manager import SessionManager
from aegis.events import AssistantText
from aegis.state.session_log import replay_events
from aegis.tui.pane import ConversationPane


class _FakeHarness:
    async def start(self): ...
    async def send(self, t): ...
    async def close(self): ...

    async def events(self):
        if False:
            yield


def _agent():
    return Agent(harness="claude-code", model="opus",
                 effort="high", permission="auto")


def _manager(tmp_path) -> SessionManager:
    roots = AegisRoots.for_project(tmp_path)
    mgr = SessionManager({"opus": _agent()}, "opus",
                         make_session=lambda p, u, h, **kw: _FakeHarness(),
                         mcp=None, roots=roots)
    mgr.attach_persistence(roots.state_dir)
    return mgr


def _texts(state_dir, log_id) -> list[str]:
    return [e.text for e in replay_events(state_dir, log_id).events
            if isinstance(e, AssistantText)]


async def test_a_pane_over_a_brain_session_adds_no_second_writer(tmp_path):
    mgr = _manager(tmp_path)
    roots = AegisRoots.for_project(tmp_path)
    handle = await mgr.spawn("opus")
    session = mgr.get(handle)

    ConversationPane(None, _agent(), "opus", handle, palette=None,
                     core=session, state_dir_path=roots.state_dir,
                     project_root=tmp_path)
    session._fire_event(AssistantText("once", usage=None))

    assert _texts(roots.state_dir, session.log_id) == ["once"], (
        "the pane added a second writer to a session the manager already "
        "logs")


async def test_reattaching_does_not_multiply_the_transcript(tmp_path):
    """The user-visible shape: attach, detach, attach, and every later line
    is written once, not twice."""
    mgr = _manager(tmp_path)
    roots = AegisRoots.for_project(tmp_path)
    handle = await mgr.spawn("opus")
    session = mgr.get(handle)

    for _ in range(3):          # three attaches over one live session
        ConversationPane(None, _agent(), "opus", handle, palette=None,
                         core=session, state_dir_path=roots.state_dir,
                         project_root=tmp_path)
    session._fire_event(AssistantText("after three attaches", usage=None))

    got = _texts(roots.state_dir, session.log_id)
    assert got == ["after three attaches"], \
        f"one event per attach reached the log: {got}"


async def test_a_pane_that_owns_its_session_still_writes(tmp_path):
    """The guard must not take logging away from the case that has no
    manager behind it: a pane given no core builds its own AgentSession,
    and nothing else is writing that transcript."""
    roots = AegisRoots.for_project(tmp_path)
    roots.state_dir.mkdir(parents=True, exist_ok=True)
    pane = ConversationPane(_FakeHarness(), _agent(), "opus", "solo-session",
                            palette=None, state_dir_path=roots.state_dir,
                            project_root=tmp_path)
    pane._core._fire_event(AssistantText("mine", usage=None))

    assert _texts(roots.state_dir, pane.log_id) == ["mine"], \
        "a pane that owns its session stopped logging"
