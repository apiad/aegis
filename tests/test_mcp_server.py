import asyncio
import json

import pytest

from aegis.mcp.bridge import SessionInfo
from aegis.mcp.server import (
    BRIEFING,
    PRIMING,
    aegis_meta,
    build_server,
    mcp_config_json,
)


class FakeBridge:
    # AppBridge surface. queue_manager stays None for the handoff tests
    # (none of them exercise queue tools). inbox_router is the real one —
    # aegis_handoff now delivers through it (T4.2), and the tests assert
    # against its pending buffer rather than a side-channel attribute.
    queue_manager = None

    def __init__(self):
        from aegis.queue import InboxRouter

        self.inbox_router = InboxRouter()
        self._sessions = [
            SessionInfo(handle="lucid-knuth", agent_slug="default",
                        state="ready", active=True, unseen=False)]

    def list_sessions(self):
        return list(self._sessions)

    def list_agents(self):
        return ["default", "fast"]

    async def handoff(self, a, b, c):
        # Legacy AppBridge method — no longer called by aegis_handoff after
        # T4.2 (the MCP tool talks to inbox_router directly). Kept on the
        # protocol surface for back-compat with any external caller.
        return f"ignored: {a}->{b}"

    async def spawn(self, profile, *, handle=None):
        return handle or "stub-handle"

    async def close(self, handle):
        return None


async def _call(server, name, **kwargs):
    tools = await server.list_tools()
    tool = next(t for t in tools if t.name == name)
    result = await tool.run(kwargs)
    # fastmcp 3.x wraps results in ToolResult; unwrap structured_content.
    sc = getattr(result, "structured_content", None)
    if isinstance(sc, dict) and "result" in sc:
        return sc["result"]
    if sc is not None:
        return sc
    # Fallback: parse the text content (e.g. plain-string returns).
    return result.content[0].text


def test_briefing_has_orientation_phrases():
    b = BRIEFING.lower()
    for phrase in ("aegis", "meta-harness", "aegis_meta",
                   "only mcp server"):
        assert phrase in b, phrase
    assert aegis_meta() == BRIEFING


def test_priming_points_at_aegis_meta():
    p = PRIMING.lower()
    assert "aegis" in p and "aegis_meta" in p and "first" in p


def test_mcp_config_json_shape():
    cfg = json.loads(mcp_config_json("http://127.0.0.1:9/mcp/"))
    s = cfg["mcpServers"]["aegis"]
    assert s["type"] == "http"
    assert s["url"] == "http://127.0.0.1:9/mcp/"


def test_build_server_registers_all_aegis_tools():
    srv = build_server(FakeBridge())
    tools = asyncio.run(srv.list_tools())
    assert {t.name for t in tools} == {
        "aegis_meta", "aegis_list_sessions",
        "aegis_list_agents", "aegis_handoff",
        "aegis_enqueue", "aegis_task_status",
        "aegis_run_workflow",
        "aegis_workflow_status", "aegis_workflow_cancel",
        "aegis_canvas_open", "aegis_canvas_read",
        "aegis_canvas_write_section",
        "aegis_canvas_append_to_section",
        "aegis_canvas_subscribe", "aegis_canvas_unsubscribe",
        "aegis_canvas_list",
        "aegis_term_spawn", "aegis_term_list", "aegis_term_run",
        "aegis_term_keys", "aegis_term_read",
        "aegis_term_subscribe", "aegis_term_unsubscribe",
        "aegis_term_close",
        "aegis_group_spawn", "aegis_group_broadcast",
        "aegis_group_wait_all", "aegis_group_wait_any",
        "aegis_group_spawn_mixed"}


@pytest.mark.asyncio
async def test_list_tools_serialise():
    br = FakeBridge()
    srv = build_server(br)
    sess = await _call(srv, "aegis_list_sessions")
    assert isinstance(sess, list)
    assert sess[0]["handle"] == "lucid-knuth"
    assert sess[0]["state"] == "ready"
    assert sess[0]["active"] is True
    agents = await _call(srv, "aegis_list_agents")
    assert agents == ["default", "fast"]


@pytest.mark.asyncio
async def test_handoff_delivers_via_inbox_router():
    from aegis.queue import sender_agent

    br = FakeBridge()
    srv = build_server(br)
    out = await _call(srv, "aegis_handoff",
                      from_handle="wry-hopper",
                      target_handle="lucid-knuth",
                      context="please continue the spec")
    assert "delivered to lucid-knuth" in out
    pending = br.inbox_router.pending("lucid-knuth")
    assert len(pending) == 1
    msg = pending[0]
    assert msg.sender == sender_agent("wry-hopper")
    assert "please continue the spec" in msg.body
    # No task_id — handoffs aren't queue results.
    assert msg.task_id is None


@pytest.mark.asyncio
async def test_handoff_self_and_unknown_still_reject():
    br = FakeBridge()
    srv = build_server(br)
    assert "yourself" in await _call(
        srv, "aegis_handoff", from_handle="x", target_handle="x",
        context="c")
    assert "no session" in await _call(
        srv, "aegis_handoff", from_handle="x", target_handle="ghost",
        context="c")
    # No inbox delivery on rejection.
    assert br.inbox_router.pending("ghost") == []


@pytest.mark.asyncio
async def test_handoff_rejects_busy_target():
    br = FakeBridge()
    # Mutate the session into 'working' state for this test.
    br._sessions = [
        SessionInfo(handle="busy-one", agent_slug="default",
                    state="working", active=True, unseen=False)]
    srv = build_server(br)
    out = await _call(srv, "aegis_handoff",
                      from_handle="me", target_handle="busy-one",
                      context="now")
    assert "busy" in out and "busy-one" in out
    assert br.inbox_router.pending("busy-one") == []


@pytest.mark.asyncio
async def test_run_workflow_tool_returns_run_id_immediately_and_callbacks():
    """Non-blocking: returns {workflow_run_id, status: 'running'} sync,
    workflow runs in the background, result lands in producer's inbox
    tagged sender=workflow:<name> with task_id matching the run_id."""
    from aegis.workflow import workflow
    from aegis.workflow.decorator import _REGISTRY
    _REGISTRY.clear()

    @workflow
    async def greet(engine, *, who):
        return f"hello {who}, caller={engine.caller_handle}"

    br = FakeBridge()
    srv = build_server(br)
    tools = await srv.list_tools()
    tool = next(t for t in tools if t.name == "aegis_run_workflow")
    res = await tool.run({"name": "greet", "kwargs": {"who": "alex"},
                          "from_handle": "lucid-knuth"})
    out = res.structured_content
    # Immediate ack.
    assert out["status"] == "running"
    assert "workflow_run_id" in out
    run_id = out["workflow_run_id"]
    # Let the scheduled task run + deliver the callback.
    for _ in range(20):
        await asyncio.sleep(0.01)
        if br.inbox_router.pending("lucid-knuth"):
            break
    pending = br.inbox_router.pending("lucid-knuth")
    assert len(pending) == 1
    msg = pending[0]
    assert msg.sender == "workflow:greet"
    assert msg.task_id == run_id
    assert msg.status == "ok"
    assert msg.body == "hello alex, caller=lucid-knuth"
    _REGISTRY.clear()


@pytest.mark.asyncio
async def test_run_workflow_tool_callback_false_skips_inbox():
    """callback=False fires the workflow but drops the result."""
    from aegis.workflow import workflow
    from aegis.workflow.decorator import _REGISTRY
    _REGISTRY.clear()

    @workflow
    async def silent(engine):
        return "no one will hear this"

    br = FakeBridge()
    srv = build_server(br)
    tools = await srv.list_tools()
    tool = next(t for t in tools if t.name == "aegis_run_workflow")
    res = await tool.run({"name": "silent", "kwargs": {},
                          "from_handle": "lucid-knuth",
                          "callback": False})
    assert res.structured_content["status"] == "running"
    for _ in range(10):
        await asyncio.sleep(0.01)
    assert br.inbox_router.pending("lucid-knuth") == []
    _REGISTRY.clear()


@pytest.mark.asyncio
async def test_run_workflow_tool_unknown_workflow():
    """Unknown name returns error synchronously (no callback)."""
    from aegis.workflow.decorator import _REGISTRY
    _REGISTRY.clear()
    br = FakeBridge()
    srv = build_server(br)
    tools = await srv.list_tools()
    tool = next(t for t in tools if t.name == "aegis_run_workflow")
    res = await tool.run({"name": "ghost", "kwargs": {}, "from_handle": "x"})
    out = res.structured_content
    assert "error" in out
    assert "ghost" in out["error"]


@pytest.mark.asyncio
async def test_run_workflow_tool_workflow_error_callbacks_as_error_status():
    """A WorkflowError-raising workflow produces an error-tagged callback."""
    from aegis.workflow import workflow, WorkflowError
    from aegis.workflow.decorator import _REGISTRY
    _REGISTRY.clear()

    @workflow
    async def predicate_fails(engine):
        raise WorkflowError("predicate violated")

    br = FakeBridge()
    srv = build_server(br)
    tools = await srv.list_tools()
    tool = next(t for t in tools if t.name == "aegis_run_workflow")
    res = await tool.run({"name": "predicate_fails", "kwargs": {},
                          "from_handle": "lucid-knuth"})
    assert res.structured_content["status"] == "running"
    for _ in range(20):
        await asyncio.sleep(0.01)
        if br.inbox_router.pending("lucid-knuth"):
            break
    pending = br.inbox_router.pending("lucid-knuth")
    assert len(pending) == 1
    msg = pending[0]
    assert msg.status == "error"
    assert "predicate violated" in msg.body
    _REGISTRY.clear()


def test_meta_and_priming_updated():
    b = BRIEFING.lower()
    for t in ("aegis_list_sessions", "aegis_list_agents",
              "aegis_handoff", "aegis_enqueue", "aegis_task_status",
              "aegis_run_workflow"):
        assert t in b
    assert "{handle}" in PRIMING
    assert "aegis_meta" in PRIMING
    assert "your aegis handle" in PRIMING.lower()


def test_briefing_explains_workflow_callback_header():
    """Agents need to recognize workflow callbacks landing in their
    inbox — distinct from queue callbacks (queue:<name>) and peer
    handoffs (agent:<handle>)."""
    b = BRIEFING
    assert "workflow:<name>" in b
    # The 'why non-blocking' rationale must be present so callers don't
    # try to await the tool call.
    assert "non-blocking" in b.lower() or "Non-blocking" in b


def test_briefing_explains_inbox_header_and_delegation():
    """Spawned agents must know (a) what arrives in their inbox and how
    to recognise it, and (b) when to enqueue vs. hand off."""
    b = BRIEFING
    # Inbox header shapes — the three sender prefixes a v1 agent meets.
    assert "queue:<name>" in b and "task_id" in b
    assert "agent:<handle>" in b
    assert "telegram" in b.lower()
    # Wake-on-idle / batched-at-turn-boundary semantics named explicitly.
    assert "turn boundary" in b.lower()
    assert "wakes you" in b.lower() or "wake" in b.lower()
    # Delegation pattern + handoff-vs-enqueue guidance present.
    assert "delegation pattern" in b.lower()
    assert "aegis_handoff (not enqueue)" in b or (
        "aegis_handoff" in b and "aegis_enqueue" in b
        and "specific" in b.lower())
