"""Origin answers two questions spawned_by cannot: who made this agent,
and will it outlive the work it was made for."""

import pytest

from aegis.fleet.models import Origin


def test_default_origin_is_the_operator():
    assert Origin().kind == "operator"


@pytest.mark.parametrize("kind", ["queue", "workflow", "group"])
def test_substrate_born_agents_are_ephemeral(kind):
    assert Origin(kind=kind).ephemeral is True


@pytest.mark.parametrize("kind", ["operator", "agent", "fork", "schedule"])
def test_agents_that_outlive_their_task_are_not(kind):
    assert Origin(kind=kind).ephemeral is False


def test_an_unknown_kind_is_not_ephemeral():
    """Ephemerality means the substrate closes it. An unrecognised kind
    has nobody to do that, so guessing 'yes' would ghost a live agent."""
    assert Origin(kind="something-new").ephemeral is False


def test_a_queue_worker_carries_where_its_answer_goes():
    o = Origin(kind="queue", by="general", detail="a3f2", returns_to="rosy-rivest")
    assert (o.by, o.detail, o.returns_to) == ("general", "a3f2", "rosy-rivest")


class _FakeHarness:
    async def start(self): ...
    async def send(self, t): ...
    async def close(self): ...

    async def events(self):
        if False:
            yield


@pytest.fixture
def session_manager(tmp_path):
    """A brain wired the way `cli.py::_serve` wires one.

    Built here rather than in `tests/conftest.py`: only this file needs it,
    and the shared conftest's `live_session_manager` drives real harnesses.
    """
    from aegis.config import Agent
    from aegis.config.roots import AegisRoots

    from tests.brain import make_brain

    roster = {
        "opus": Agent(
            harness="claude-code", model="opus", effort="high", permission="auto"
        )
    }
    return make_brain(
        roster,
        "opus",
        make_session=lambda p, u, h, **kw: _FakeHarness(),
        mcp=None,
        roots=AegisRoots.for_project(tmp_path),
    )


def test_every_session_has_an_origin(session_manager):
    """A tab nobody explicitly attributed is the operator's."""
    s = session_manager._sync_spawn("opus")
    assert s.origin == Origin()


def test_spawn_records_the_origin_it_is_given(session_manager):
    o = Origin(kind="queue", by="general", detail="a3f2")
    s = session_manager._sync_spawn("opus", origin=o)
    assert s.origin == o


def test_a_fork_is_marked_as_one(session_manager):
    parent = session_manager._sync_spawn("opus")
    child = session_manager._sync_spawn(
        "opus", origin=Origin(kind="fork", by=parent.handle)
    )
    assert child.origin.kind == "fork"
    assert child.origin.by == parent.handle


async def test_the_real_fork_marks_its_child_as_a_fork(tmp_path):
    """`SessionManager.fork()` itself, not an origin handed to `_sync_spawn`:
    the line that sets a fork's origin is inside `fork()`, so only a call
    through it proves that line runs."""
    from aegis.config import Agent
    from aegis.config.roots import AegisRoots

    from tests.brain import make_brain

    class _Resumable(_FakeHarness):
        session_id = "parent-sid"

    roster = {
        "opus": Agent(
            harness="claude-code", model="opus", effort="high", permission="auto"
        )
    }
    mgr = make_brain(
        roster,
        "opus",
        make_session=lambda p, u, h, **kw: _Resumable(),
        mcp=None,
        roots=AegisRoots.for_project(tmp_path),
    )
    mgr._fork_capability = lambda harness: True
    parent = await mgr.spawn("opus")
    child = await mgr.fork(parent, prompt="diverge", forked_by="rosy-rivest")
    assert mgr.get(child).origin.kind == "fork"
    assert mgr.get(child).origin.by == "rosy-rivest"
    assert mgr.get(child).origin.ephemeral is False


async def test_the_queue_spawns_its_worker_with_a_queue_origin():
    """Drive the real dispatch loop, not a synthetic call: the queue is the
    site that records nothing today, so what matters is whether THAT line
    changed. The brief specified an `inspect.getsource` seam here; the real
    substrate is reachable from a test, so this asserts on the session the
    queue actually built."""
    from aegis.queue import InboxRouter, QueueManager, sender_agent

    from tests.test_queue_manager import StubSessionManager, _q

    sm, inbox = StubSessionManager(), InboxRouter()
    qm = QueueManager(
        {"general": _q(name="general", profile="claude", cap=1)},
        sm,
        inbox,
        handle_factory=lambda used: "worker-1",
    )
    task_id, _ = qm.enqueue(
        "general",
        "audit the ledger",
        enqueued_by=sender_agent("rosy-rivest"),
        callback=True,
        callback_to="rosy-rivest",
    )

    ((_slug, _handle, _prompt, session),) = sm.spawns
    assert session.origin.kind == "queue"
    assert session.origin.by == "general"
    assert session.origin.detail == task_id[-4:]
    assert session.origin.returns_to == "rosy-rivest"
    assert session.origin.ephemeral is True


async def test_the_mcp_spawn_tool_marks_an_agent_origin():
    """`aegis_spawn` is an agent making a peer, which `spawned_by` records
    identically to an operator typing `/spawn` in that agent's tab."""
    from aegis.mcp.server import build_server

    from tests.test_mcp_server import FakeBridge, _call

    br = FakeBridge()
    seen: dict = {}

    async def _spawn(agent, *, origin=None, **kw):
        seen["origin"] = origin
        return "new-agent"

    br.spawn = _spawn
    out = await _call(
        build_server(br),
        "aegis_spawn",
        agent="default",
        prompt="go",
        from_handle="rosy-rivest",
    )
    assert out == {"handle": "new-agent"}
    assert seen["origin"] == Origin(kind="agent", by="rosy-rivest")


async def test_the_slash_command_marks_an_operator_origin():
    """The other half of the pair: the operator typed this while standing
    in `ctx.handle`, which is a different event from that agent spawning."""
    from aegis.commands import CommandContext, dispatch

    from tests.test_slash_commands import FakeBridge

    bridge = FakeBridge()
    res = await dispatch(
        "/spawn opus go analyze the logs", CommandContext(bridge=bridge, handle="me")
    )
    assert res.ok
    assert bridge.spawn_origin == Origin(kind="operator", by="me")
