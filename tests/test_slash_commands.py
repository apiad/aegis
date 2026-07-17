"""Unit tests for the slash-command dispatcher with a fake AppBridge."""
from __future__ import annotations

from dataclasses import dataclass

from aegis.commands import CommandContext, dispatch


@dataclass
class FakeSession:
    handle: str
    agent_slug: str
    state: str = "ready"
    active: bool = False


class FakeQueueManager:
    def __init__(self):
        self.enqueued = []
        self._queues = {}

    def list_queues(self):
        return sorted(self._queues)

    def enqueue(self, queue, payload, *, enqueued_by, callback):
        if queue not in ("build",):
            raise KeyError(queue)
        self.enqueued.append((queue, payload))
        return ("task-1", 0)


class FakeBridge:
    def __init__(self):
        self.spawned = []
        self.registered = []
        self._sessions = [FakeSession("alpha", "opus", active=True)]
        self.queue_manager = FakeQueueManager()

    def list_agents(self):
        return ["default", "opus"]

    def list_sessions(self):
        return self._sessions

    async def spawn(self, profile, *, handle=None, opening_prompt=None,
                    spawned_by=None):
        self.spawned.append((profile, opening_prompt, spawned_by))
        return "beta"

    def register_queue(self, queue):
        if any(q.name == queue.name for q in self.registered):
            raise ValueError(f"queue {queue.name!r} already exists")
        self.registered.append(queue)


def _ctx():
    return CommandContext(bridge=FakeBridge(), handle="me")


async def test_help_lists_commands():
    res = await dispatch("/help", _ctx())
    assert res.ok
    assert "/spawn" in res.body and "/enqueue" in res.body


async def test_bare_slash_is_help():
    res = await dispatch("/", _ctx())
    assert res.ok and "/help" in res.body


async def test_unknown_command_errors():
    res = await dispatch("/nope", _ctx())
    assert not res.ok
    assert "unknown command" in res.title


async def test_sessions_lists_live():
    res = await dispatch("/sessions", _ctx())
    assert res.ok
    assert "alpha" in res.body and "opus" in res.body
    assert res.body.startswith("*")          # active session marked


@dataclass
class FakeAgent:
    harness: str
    model: str
    permission: str


async def test_agents_lists_names_when_no_detail():
    res = await dispatch("/agents", _ctx())      # FakeBridge has no _agents
    assert res.ok
    assert "default" in res.body and "opus" in res.body


async def test_agents_enriches_with_config_detail():
    bridge = FakeBridge()
    bridge._agents = {
        "default": FakeAgent("claude-code", "sonnet", "auto"),
        "opus": FakeAgent("claude-code", "opus", "full"),
    }
    res = await dispatch("/agents", CommandContext(bridge=bridge, handle="me"))
    assert res.ok and "2 agents" in res.title
    assert "claude-code · opus · full" in res.body


async def test_spawn_unknown_agent_errors():
    ctx = _ctx()
    res = await dispatch("/spawn ghost do stuff", ctx)
    assert not res.ok
    assert "unknown agent" in res.title
    assert not ctx.bridge.spawned


async def test_spawn_passes_agent_prompt_and_spawned_by():
    ctx = _ctx()
    res = await dispatch("/spawn opus go analyze the logs", ctx)
    assert res.ok and "beta" in res.title
    assert ctx.bridge.spawned == [("opus", "go analyze the logs", "me")]


async def test_spawn_without_prompt():
    ctx = _ctx()
    res = await dispatch("/spawn opus", ctx)
    assert res.ok
    assert ctx.bridge.spawned == [("opus", None, "me")]


async def test_queue_new_ephemeral_registers():
    ctx = _ctx()
    res = await dispatch("/queues new build opus --ephemeral", ctx)
    assert res.ok and "build" in res.title
    assert [q.name for q in ctx.bridge.registered] == ["build"]
    assert ctx.bridge.registered[0].agent_profile == "opus"


async def test_queue_new_defaults_to_first_agent():
    ctx = _ctx()
    await dispatch("/queues new build --ephemeral", ctx)
    assert ctx.bridge.registered[0].agent_profile == "default"


async def test_queues_new_usage_on_missing_name():
    res = await dispatch("/queues new", _ctx())
    assert not res.ok and "usage" in res.title


async def test_queues_bare_lists():
    from aegis.queue import Queue
    bridge = FakeBridge()
    bridge.queue_manager._queues = {
        "build": Queue(name="build", agent_profile="opus", max_parallel=2)}
    res = await dispatch("/queues", CommandContext(bridge=bridge, handle="me"))
    assert res.ok is True
    assert "build" in res.body
    assert "opus" in res.body


async def test_queue_old_name_is_gone():
    res = await dispatch("/queue", _ctx())
    assert res.ok is False
    assert "unknown command" in res.title


async def test_enqueue_drops_task():
    ctx = _ctx()
    res = await dispatch("/enqueue build deploy the thing", ctx)
    assert res.ok and "task-1" in res.title
    assert ctx.bridge.queue_manager.enqueued == [("build", "deploy the thing")]


async def test_enqueue_unknown_queue_errors():
    res = await dispatch("/enqueue ghost payload", _ctx())
    assert not res.ok and "unknown queue" in res.title


async def test_handler_exception_becomes_error_result():
    class Boom(FakeBridge):
        def list_sessions(self):
            raise RuntimeError("kaboom")

    ctx = CommandContext(bridge=Boom(), handle="me")
    res = await dispatch("/sessions", ctx)
    assert not res.ok
    assert "failed" in res.title and "kaboom" in res.body


async def test_argerror_returns_usage_and_skips_handler():
    # /spawn with no agent → parse fails on the required positional
    ctx = _ctx()
    res = await dispatch("/spawn", ctx)
    assert res.ok is False
    assert res.title.startswith("usage:")
    assert "/spawn" in res.title
    # dispatch-level parse populates the body with the parse error; the old
    # hand-rolled handler left it empty.
    assert "missing required argument" in res.body
    assert not ctx.bridge.spawned


async def test_typed_handler_receives_parsed_args():
    ctx = _ctx()
    res = await dispatch("/spawn opus write the report", ctx)
    assert res.ok is True
    assert ctx.bridge.spawned[-1] == ("opus", "write the report", "me")


async def test_queue_new_persists_by_default(monkeypatch):
    import pathlib
    import aegis.config as cfg
    import aegis.config.edit as cfg_edit
    calls = {}
    monkeypatch.setattr(cfg, "find_project_root",
                        lambda: pathlib.Path("/tmp/proj"))
    monkeypatch.setattr(
        cfg_edit, "add_queue",
        lambda root, name, **kw: calls.__setitem__("add", (str(root), name, kw)))

    class _Q:
        name = "build"
        agent_profile = "opus"
    monkeypatch.setattr(cfg, "load_queues", lambda root: {"build": _Q()})

    ctx = _ctx()
    res = await dispatch("/queues new build opus", ctx)
    assert res.ok is True
    assert calls["add"][1] == "build"
    assert calls["add"][2] == {"agent": "opus", "max_parallel": 1}
    assert [q.name for q in ctx.bridge.registered] == ["build"]  # hot-registered


async def test_queue_new_ephemeral_skips_persistence(monkeypatch):
    import aegis.config.edit as cfg_edit

    def _boom(*a, **k):
        raise AssertionError("should not persist for --ephemeral")
    monkeypatch.setattr(cfg_edit, "add_queue", _boom)

    ctx = _ctx()
    res = await dispatch("/queues new build opus --ephemeral", ctx)
    assert res.ok is True
    assert "ephemeral" in res.title
    assert [q.name for q in ctx.bridge.registered] == ["build"]
