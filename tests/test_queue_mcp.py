from __future__ import annotations

import asyncio

from aegis.mcp.server import build_server
from aegis.queue import InboxRouter, Queue, QueueManager, sender_agent


class FakeBridge:
    def __init__(self, qm, inbox):
        self.queue_manager = qm
        self.inbox_router = inbox

    def list_sessions(self): return []
    def list_agents(self): return []
    async def handoff(self, a, b, c): return "ok"


class NopSM:
    """Session-manager stub for tools tests — spawns a pseudo-session that
    never finishes a turn (events generator is empty + immediately exhausted
    is fine; we never actually let it run in these tests because cap=0)."""
    _sessions = []

    def spawn(self, slug, *, opening_prompt=None, handle=None):
        from aegis.core.session import AgentSession

        class _H:
            async def start(self): pass
            async def send(self, t): pass
            async def close(self): pass

            async def events(self):
                if False:
                    yield

        return AgentSession(_H(), None, slug, handle or "w1")

    async def close(self, h): ...


async def _call(server, name, **kwargs):
    tools = await server.list_tools()
    tool = next(t for t in tools if t.name == name)
    result = await tool.run(kwargs)
    sc = getattr(result, "structured_content", None)
    # fastmcp wraps non-model returns as {"result": <value>} (single key).
    # Only unwrap when the envelope is *exactly* that shape — otherwise the
    # tool's own dict might contain a "result" key (e.g. aegis_task_status
    # returns {"status": …, "result": …, …} where "result" is the worker
    # output, not the fastmcp envelope).
    if isinstance(sc, dict) and set(sc.keys()) == {"result"}:
        return sc["result"]
    if sc is not None:
        return sc
    return result.content[0].text


def _build(cap=0):
    inbox = InboxRouter()
    qm = QueueManager({"impl": Queue(name="impl",
                                     agent_profile="claude-impl",
                                     max_parallel=cap)},
                      NopSM(), inbox)
    return build_server(FakeBridge(qm, inbox)), qm


async def test_aegis_enqueue_returns_shape():
    srv, qm = _build(cap=0)
    out = await _call(srv, "aegis_enqueue",
                      queue="impl", payload="do",
                      from_handle="lucid-knuth", callback=True)
    assert set(out) == {"task_id", "queued_position"}
    assert isinstance(out["task_id"], str) and len(out["task_id"]) == 26
    assert out["queued_position"] == 1
    # task is in the manager, enqueued_by reflects from_handle
    st = qm.status(out["task_id"])
    assert st["status"] == "pending"


async def test_aegis_enqueue_unknown_queue_returns_error_string():
    srv, _qm = _build()
    out = await _call(srv, "aegis_enqueue",
                      queue="ghost", payload="x",
                      from_handle="h", callback=False)
    # fastmcp surfaces our raise as the structured error or string;
    # contract: dict with single "error" key OR plain string containing
    # "enqueue rejected:" and the offending queue name.
    if isinstance(out, dict):
        assert "error" in out and "ghost" in out["error"]
    else:
        assert isinstance(out, str) and "ghost" in out


async def test_aegis_task_status_known_and_unknown():
    srv, qm = _build(cap=0)
    tid, _ = qm.enqueue("impl", "x",
                        enqueued_by=sender_agent("p"), callback=False)
    out = await _call(srv, "aegis_task_status", task_id=tid)
    assert out["status"] == "pending"
    miss = await _call(srv, "aegis_task_status", task_id="nope")
    assert miss == {"status": "unknown"}


async def test_aegis_enqueue_default_callback_is_true():
    srv, qm = _build(cap=0)
    out = await _call(srv, "aegis_enqueue", queue="impl", payload="x",
                      from_handle="h")    # no callback kwarg
    t = next(t for t in qm._all.values() if t.id == out["task_id"])
    assert t.callback is True


async def test_aegis_cancel_pending_and_unknown():
    srv, qm = _build(cap=0)   # cap=0 → task stays pending
    tid, _ = qm.enqueue("impl", "x",
                        enqueued_by=sender_agent("p"), callback=False)
    out = await _call(srv, "aegis_cancel", task_id=tid)
    assert out["ok"] is True and out["status"] == "cancelled"
    assert qm.status(tid)["status"] == "cancelled"
    miss = await _call(srv, "aegis_cancel", task_id="nope")
    assert miss["ok"] is False and "unknown" in miss["error"]


async def test_aegis_delegate_unknown_queue():
    srv, _qm = _build(cap=0)
    out = await _call(srv, "aegis_delegate",
                      queue="ghost", payload="x", from_handle="h")
    assert "error" in out and "ghost" in out["error"]
