from __future__ import annotations

import asyncio
import dataclasses
import json

from fastmcp import FastMCP

from aegis.mcp.bridge import AppBridge

BRIEFING = (
    "You are running inside aegis — a meta-harness for coding agents. "
    "aegis drives this Claude Code process via stream-json and re-renders "
    "it in a multi-agent terminal UI; you are one agent inside it.\n\n"
    "You are connected to the aegis MCP server. Because aegis runs with "
    "strict MCP config, this is your ONLY MCP server — other MCP servers "
    "from the user's config are not loaded in aegis sessions.\n\n"
    "aegis tools available to you now:\n"
    "  - aegis_meta() : this briefing.\n"
    "  - aegis_list_sessions() : the live aegis sessions (your peers). "
    "Each entry has handle, agent_slug, state, active, unseen. Use this "
    "to see who you can hand off to and whether they are idle.\n"
    "  - aegis_list_agents() : the configured agent-profile slugs that "
    "could be spawned (spawn itself is a future tool, not in this "
    "release).\n"
    "  - aegis_handoff(from_handle, target_handle, context) : one-way "
    "(fire-and-forget) context transfer to a live peer session. You pass "
    "your own aegis handle as from_handle — it is in your system prompt. "
    "The target receives a tagged user turn and starts working; you do "
    "not wait for its reply. Returns 'delivered to <handle>' on success, "
    "or a 'handoff rejected: …' reason (self / unknown / busy).\n"
    "  - aegis_enqueue(queue, payload, from_handle, callback=true) : "
    "delegate a task onto a named queue; the substrate spawns a worker "
    "of the queue's configured agent profile, runs the payload as its "
    "opening prompt, and (if callback=true) delivers the worker's final "
    "result into your inbox as a normal incoming message. Returns "
    "{task_id, queued_position}; keep working between enqueue and "
    "callback arrival.\n"
    "  - aegis_task_status(task_id) : inspect a previously-enqueued "
    "task. Use when callback was false or you want to poll mid-flight.\n"
    "  - aegis_run_workflow(name, kwargs, from_handle, callback=true) : "
    "invoke a registered workflow (a deterministic Python procedure "
    "that drives a sequence of agent interactions with predicate-"
    "verified steps and retry-with-feedback). Non-blocking: returns "
    "{workflow_run_id, status: 'running'} immediately. If you pass "
    "your own handle as from_handle, the workflow runs ON YOU — it "
    "will send you follow-up messages (engine.send → your inbox) "
    "between its bash predicates, and you must be free to process "
    "them, which is why this tool can't block. With callback=true the "
    "final result lands in your inbox tagged sender=workflow:<name> "
    "with task_id matching workflow_run_id.\n\n"
    "INBOX — how messages reach you. Anything other people or the "
    "substrate send you arrives as a normal user-message turn, but "
    "begins with a one-line substrate header so you can tell where it "
    "came from:\n"
    "  > from queue:<name> · task#<id> · ok|error · <timestamp>\n"
    "      A task you previously enqueued has completed (or failed). "
    "The task_id matches what aegis_enqueue returned; the body that "
    "follows the header is the worker's final assistant text (or the "
    "error reason on status=error).\n"
    "  > from agent:<handle> · <timestamp>\n"
    "      A peer agent handed you context via aegis_handoff. Treat "
    "the body as a fresh user instruction from that peer.\n"
    "  > from telegram · <timestamp>\n"
    "      A user message from Alex (or whoever owns the Telegram "
    "chat) — same as anything else they would type.\n"
    "  > from workflow:<name> · task#<id> · ok|error · <timestamp>\n"
    "      Either (a) the final result of a workflow you invoked via "
    "aegis_run_workflow, tagged with the workflow_run_id returned to "
    "you, or (b) an intermediate instruction from a workflow that is "
    "currently running ON YOU (no task_id in that case — the workflow "
    "is mid-flight). Read the body and proceed.\n"
    "Multiple messages that arrive while you are mid-turn batch into a "
    "single user-message at your next turn boundary; each entry keeps "
    "its own header. If you were idle, an arrival wakes you into a new "
    "turn automatically.\n\n"
    "DELEGATION PATTERN. When a unit of work is independent and you do "
    "not need to think while it runs, enqueue it: "
    "aegis_enqueue(queue=<name>, payload=<full prompt for the worker>, "
    "from_handle=<your handle>, callback=true) returns "
    "{task_id, queued_position} immediately — you continue working. "
    "When the worker finishes, the substrate writes the result into "
    "your inbox as the next user-turn, tagged queue:<name>. The worker "
    "is a fresh agent with no context — write the payload as a "
    "self-contained prompt with everything it needs (goal, "
    "constraints, files to read, success criteria). The worker's last "
    "assistant text becomes the task result, so phrase the payload so "
    "the worker's natural final answer is the thing you want back.\n\n"
    "Use aegis_handoff (not enqueue) when you want a SPECIFIC live "
    "peer to take over — handoff targets a handle, runs in an "
    "existing session, and is fire-and-forget. Use aegis_enqueue when "
    "you want a FRESH worker spawned for one task and the result "
    "returned to you.\n\n"
    "More aegis tools (vault/file/web/workflow) are planned. Built-in "
    "Claude tools (Read, Edit, Bash, WebFetch, …) are unchanged and "
    "available. Call aegis_meta once at the start to orient, then proceed "
    "with the user's request. When the user asks what you can do, "
    "summarise this briefing."
)

PRIMING = (
    "You are running inside aegis, a meta-harness. An MCP server named "
    "'aegis' is attached and (strict config) is your only MCP server. "
    "Your aegis handle is '{handle}'. Call its aegis_meta tool first to "
    "learn this environment and the aegis tools available to you, then "
    "proceed. When handing off to a peer (aegis_handoff) or delegating "
    "via a queue (aegis_enqueue), pass your handle '{handle}' as "
    "from_handle — that is how queue callbacks find their way back to "
    "you. Messages from queue callbacks, peer handoffs, Telegram, and "
    "the substrate all arrive as user-message turns prefixed with a "
    "'> from <sender> · …' header line — recognise the sender to know "
    "what kind of message it is."
)


def aegis_meta() -> str:
    """Orientation briefing: where you are and what aegis offers."""
    return BRIEFING


def build_server(bridge: AppBridge) -> FastMCP:
    server = FastMCP("aegis")
    server.tool(aegis_meta)

    @server.tool
    async def aegis_list_sessions() -> list[dict]:
        """Live aegis sessions (peers you can hand off to)."""
        return [dataclasses.asdict(s) for s in bridge.list_sessions()]

    @server.tool
    async def aegis_list_agents() -> list[str]:
        """Configured agent profiles that could be spawned."""
        return list(bridge.list_agents())

    @server.tool
    async def aegis_handoff(from_handle: str, target_handle: str,
                            context: str) -> str:
        """One-way context transfer to a live peer aegis session.

        Delivered via the universal inbox channel: the target receives a
        normal user-message whose substrate header carries
        ``sender=agent:<from_handle>`` and an ISO timestamp — same
        universal-tagging shape that queue callbacks use, so the target
        agent reads handoffs and callbacks through one consistent surface.

        from_handle is your own aegis handle (read it from your system
        prompt). Returns 'delivered to <target>' on success, or a
        'handoff rejected: …' reason (self / unknown / busy).
        """
        from aegis.queue import InboxMessage, now_iso, sender_agent

        if from_handle == target_handle:
            return "handoff rejected: cannot hand off to yourself"
        sessions = list(bridge.list_sessions())
        target_info = next(
            (s for s in sessions if s.handle == target_handle), None)
        if target_info is None:
            return (f"handoff rejected: no session {target_handle!r} "
                    f"(use aegis_list_sessions)")
        if target_info.state == "working":
            return (f"handoff rejected: {target_handle!r} is busy, "
                    f"retry shortly")
        await bridge.inbox_router.deliver(
            target_handle,
            InboxMessage(
                sender=sender_agent(from_handle),
                timestamp=now_iso(),
                body=context))
        return f"delivered to {target_handle}"

    @server.tool
    async def aegis_enqueue(queue: str, payload: str, from_handle: str,
                            callback: bool = True) -> dict:
        """Enqueue a task on a named queue. Returns task_id + queued_position.

        If callback=true (default), the worker's final result lands in your
        inbox as a normal user message when it completes; you can keep
        working between enqueue and the callback arrival. If callback=false,
        the result is dropped — use aegis_task_status to poll instead.

        from_handle is your own aegis handle (read it from your system
        prompt). Unknown queue returns {"error": "enqueue rejected: …"}.
        """
        from aegis.queue import sender_agent
        try:
            tid, pos = bridge.queue_manager.enqueue(
                queue, payload,
                enqueued_by=sender_agent(from_handle),
                callback=callback)
        except KeyError as e:
            return {"error": f"enqueue rejected: unknown queue {e.args[0]!r}"}
        return {"task_id": tid, "queued_position": pos}

    @server.tool
    async def aegis_run_workflow(
        name: str, kwargs: dict | None = None,
        from_handle: str = "", callback: bool = True,
    ) -> dict:
        """Invoke a registered workflow. Non-blocking: returns
        ``{workflow_run_id, status: "running"}`` immediately; the
        workflow runs in the background.

        **Non-blocking is load-bearing.** The canonical case is an agent
        invoking a workflow on itself (passing its own handle as
        ``from_handle``) — the workflow then drives the caller via
        ``engine.send`` / ``engine.drain``, and the caller must be free
        to process those sends. Sync-block here would deadlock the
        caller's MCP turn against its own pending inbox messages.

        ``kwargs`` is forwarded to the workflow. ``from_handle`` is your
        aegis handle (read from your system prompt) — surfaced to the
        workflow as ``engine.caller_handle``.

        ``callback=true`` (default): when the workflow finishes, the
        result lands in your inbox as a normal user-message turn tagged
        ``sender="workflow:<name>"``, with ``task_id`` matching the
        ``workflow_run_id`` returned here. ``callback=false`` drops the
        result (use only when you don't need recovery).
        """
        from aegis.queue import InboxMessage
        from aegis.queue.schema import new_ulid, now_iso
        from aegis.workflow import get_workflow, list_workflows
        from aegis.workflow.runner import run_workflow

        if get_workflow(name) is None:
            return {
                "error": (f"unknown workflow: {name!r}. "
                          f"Available: {list_workflows()}")}

        run_id = new_ulid()
        qm = bridge.queue_manager
        state_dir = (getattr(qm, "_state_dir", None)
                     if qm is not None else None)
        kw = kwargs or {}

        async def _run_and_callback() -> None:
            out = await run_workflow(
                name, kw,
                bridge=bridge,
                queue_manager=qm,
                inbox_router=bridge.inbox_router,
                caller_handle=from_handle or None,
                state_dir=state_dir,
                workflow_run_id=run_id)
            if not callback or not from_handle:
                return
            ok = out["status"] == "ok"
            body = out.get("result") if ok else out.get("error", "")
            msg = InboxMessage(
                sender=f"workflow:{name}",
                timestamp=now_iso(),
                body=str(body) if body is not None else "",
                task_id=run_id,
                status=("ok" if ok else "error"))
            await bridge.inbox_router.deliver(from_handle, msg)

        # Schedule via Textual's App.run_worker when the bridge is the
        # interactive TUI (so the workflow's downstream session.deliver
        # chain inherits active_app context and pane renderer hooks
        # don't trip NoActiveAppError — same lesson as the queue v1
        # adapter's mount path). Otherwise asyncio.create_task is fine.
        scheduler = getattr(bridge, "run_worker", None)
        if scheduler is not None:
            scheduler(_run_and_callback(),
                      name=f"workflow:{name}:{run_id}", exclusive=False)
        else:
            asyncio.create_task(_run_and_callback())
        return {"workflow_run_id": run_id, "status": "running"}

    @server.tool
    async def aegis_task_status(task_id: str) -> dict:
        """Inspect a previously-enqueued task by its task_id.

        Returns {"status": "pending"|"dispatched"|"completed"|"failed", …}
        with result/error/completed_at/queued_position fields when set.
        Returns {"status": "unknown"} if the task_id is not known to this
        aegis instance.
        """
        st = bridge.queue_manager.status(task_id)
        if st is None:
            return {"status": "unknown"}
        return st

    return server


def mcp_config_json(url: str) -> str:
    return json.dumps(
        {"mcpServers": {"aegis": {"type": "http", "url": url}}})
