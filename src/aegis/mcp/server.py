from __future__ import annotations

import asyncio
import dataclasses
import json
from dataclasses import asdict

from fastmcp import FastMCP

from aegis.mcp.bridge import AppBridge


async def _aegis_group_spawn_impl(bridge, *, profile: str, group: str,
                                  handle: str | None = None) -> dict:
    h = await bridge.groups.spawn(profile=profile, group=group,
                                   handle=handle)
    return {"handle": h, "group": group}


async def _aegis_group_broadcast_impl(bridge, *, group: str, sender: str,
                                      objective: str, output_format: str,
                                      tool_guidance: str,
                                      boundaries: str) -> dict:
    bid = await bridge.groups.broadcast(
        group, sender=sender, objective=objective,
        output_format=output_format, tool_guidance=tool_guidance,
        boundaries=boundaries,
    )
    return {"broadcast_id": bid}


def _group_result_to_dict(result) -> dict:
    return {
        "broadcast_id": result.broadcast_id,
        "by_member": {h: asdict(mr) for h, mr in result.by_member.items()},
        "combined":  result.combined,
        "errors":    dict(result.errors),
        "timeouts":  list(result.timeouts),
    }


async def _aegis_group_wait_all_impl(bridge, *, group: str,
                                     timeout: float = 600.0,
                                     reducer: str = "concat") -> dict:
    result = await bridge.groups.wait_all(group, timeout=timeout,
                                           reducer=reducer)
    return _group_result_to_dict(result)


async def _aegis_group_wait_any_impl(bridge, *, group: str,
                                     timeout: float = 600.0,
                                     cancel_losers: bool = True) -> dict:
    result = await bridge.groups.wait_any(
        group, timeout=timeout, cancel_losers=cancel_losers)
    return _group_result_to_dict(result)


async def _aegis_group_spawn_mixed_impl(bridge, *, group: str,
                                        profiles: list[str]) -> dict:
    handles = await bridge.groups.spawn_mixed(
        group=group, profiles=profiles)
    return {"handles": list(handles), "group": group}


async def _aegis_group_status_impl(bridge, *, group: str) -> dict:
    return await bridge.groups.status(group)


async def _aegis_group_dissolve_impl(bridge, *, group: str) -> dict:
    return await bridge.groups.dissolve(group)


async def _aegis_group_rename_impl(bridge, *, old: str, new: str) -> dict:
    return await bridge.groups.rename(old, new)


async def _aegis_group_move_member_impl(bridge, *, handle: str,
                                        from_group: str,
                                        to_group: str) -> dict:
    return await bridge.groups.move_member(
        handle, from_group=from_group, to_group=to_group)

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
    "  - aegis_canvas_open(name, file?, from_handle) : open or create a "
    "shared canvas — a markdown file multiple agents collaboratively "
    "write to. First open of a name requires ``file`` (the on-disk "
    "path); subsequent opens just return metadata. Returns "
    "{name, file, sections, created_at}.\n"
    "  - aegis_canvas_read(name, section?, from_handle) : read the full "
    "canvas or one section's body. Returns {content}.\n"
    "  - aegis_canvas_write_section(name, section, content, from_handle) "
    ": replace one ## section with content (creates the section if "
    "missing). Pass your own handle so other subscribers see who wrote "
    "it (and so you don't get an inbox echo of your own write).\n"
    "  - aegis_canvas_append_to_section(name, section, text, from_handle) "
    ": append text to a section (joined with newline). Cheaper than "
    "write_section for log-style growth.\n"
    "  - aegis_canvas_subscribe(name, from_handle, sections?) : opt in "
    "to wake-on-change. When any other agent writes to a watched "
    "section, you receive a user-message turn tagged "
    "sender=canvas:<name>. sections=None watches everything; passing a "
    "list filters to those sections only.\n"
    "  - aegis_canvas_unsubscribe(name, from_handle) : stop receiving "
    "notifications.\n"
    "  - aegis_canvas_list() : see all canvases open in this aegis. Use "
    "before opening a new one to check whether the work already has a "
    "home.\n"
    "  - aegis_term_spawn(name, shell?, cwd?, env?, from_handle) : "
    "spawn a live shared PTY terminal — a real shell process that you, "
    "Alex, and peer agents can run commands on, send raw keystrokes "
    "to, read history from, and subscribe to. Names are unique within "
    "this aegis. Returns {name, pid, shell, cwd, started_at, …}.\n"
    "  - aegis_term_list() : list all live terminals.\n"
    "  - aegis_term_run(name, cmd, timeout?, from_handle) : run a "
    "command in the terminal — BLOCKS until the shell finishes it "
    "(detected via OSC 133 markers). Holds a per-terminal lock so "
    "concurrent runs serialize. Returns the full command record "
    "(stdout, stderr, exit, duration_s, seq, writer).\n"
    "  - aegis_term_keys(name, keys, from_handle) : send raw bytes — "
    "fire-and-forget, bypasses the run-lock. Use for interactive "
    "prompts ('y\\n'), Ctrl-C ('\\x03'), or driving REPLs while a "
    "long-running command is in flight.\n"
    "  - aegis_term_read(name, last_n?, since_seq?, from_handle) : "
    "read recent command records from the ledger.\n"
    "  - aegis_term_subscribe(name, from_handle) / "
    "aegis_term_unsubscribe(name, from_handle) : opt in/out of "
    "command-finish wakes. Every command that finishes (yours or a "
    "peer's or Alex's typed input) wakes subscribers with a normal "
    "user-message tagged sender=term:<name>; your own commands are "
    "suppressed from your own wakes.\n"
    "  - aegis_term_close(name, purge?, from_handle) : terminate the "
    "PTY. ``purge=true`` also wipes the on-disk state.\n"
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
    "  > from canvas:<name> · <timestamp>\n"
    "      A subscriber notification: another agent wrote to a section "
    "of a canvas you subscribed to. The body carries the section name, "
    "the writer, line-count diff, and a short preview of the new "
    "content. Re-read the canvas (aegis_canvas_read) before reacting if "
    "you need the full state.\n"
    "  > from term:<name> · <timestamp>\n"
    "      A subscriber wake from a shared terminal: a command finished "
    "in a terminal you subscribed to. The body shows the command, "
    "writer, exit code, duration, and a tail of stdout (plus stderr "
    "block when non-empty). Call aegis_term_read for more history if "
    "you need it.\n"
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
    "SHARED CANVAS PATTERN. When multiple agents need to shape one "
    "artifact (a report, a plan, a shared notes file), open a canvas "
    "with aegis_canvas_open(name, file, from_handle=<your handle>). "
    "Sections (## headings) are the unit of write and notification — "
    "decide who owns which sections, hand off accordingly via "
    "aegis_handoff, and subscribe (aegis_canvas_subscribe) if you want "
    "to react when collaborators change content. Section ownership is "
    "by convention only in v1 (any subscriber can write any section); "
    "the ledger records who wrote what and the inbox notification names "
    "the writer.\n\n"
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


def _terminal_info_to_dict(info) -> dict:
    return {
        "name": info.name,
        "pid": info.pid,
        "shell": info.shell,
        "cwd": info.cwd,
        "started_at": info.started_at,
        "last_cmd_at": info.last_cmd_at,
        "last_exit": info.last_exit,
    }


def _command_record_to_dict(rec) -> dict:
    return {
        "seq": rec.seq,
        "cmd": rec.cmd,
        "writer": rec.writer,
        "started_at": rec.started_at,
        "finished_at": rec.finished_at,
        "duration_s": rec.duration_s,
        "exit": rec.exit,
        "stdout": rec.stdout,
        "stderr": rec.stderr,
        "killed_by_restart": rec.killed_by_restart,
        "timed_out": rec.timed_out,
    }


def _canvas_info_to_dict(info) -> dict:
    return {
        "name": info.name,
        "file": info.file,
        "created_at": info.created_at,
        "sections": [
            {"name": s.name, "lines": s.lines,
             "last_writer": s.last_writer,
             "updated_at": s.updated_at}
            for s in info.sections
        ],
    }


def _write_result_to_dict(res) -> dict:
    return {
        "ok": True,
        "canvas": res.canvas,
        "section": res.section,
        "op": res.op,
        "writer": res.writer,
        "added": res.added,
        "removed": res.removed,
        "timestamp": res.timestamp,
    }


def build_server(bridge: AppBridge) -> FastMCP:
    server = FastMCP("aegis")
    server.tool(aegis_meta)

    # Lazily attach a WorkflowRunner so the MCP workflow tools have a
    # canonical place to track running tasks + pending human questions.
    if getattr(bridge, "workflow_runner", None) is None:
        from aegis.workflow.runner import WorkflowRunner
        try:
            bridge.workflow_runner = WorkflowRunner(bridge)  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001 — frozen dataclass etc.
            pass

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
        from aegis.workflow.runner import WorkflowRunner

        if get_workflow(name) is None:
            return {
                "error": (f"unknown workflow: {name!r}. "
                          f"Available: {list_workflows()}")}

        runner: WorkflowRunner = getattr(bridge, "workflow_runner", None)
        if runner is None:
            runner = WorkflowRunner(bridge)
            try:
                bridge.workflow_runner = runner  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                pass

        run_id = new_ulid()
        qm = bridge.queue_manager
        state_dir = (getattr(qm, "_state_dir", None)
                     if qm is not None else None)
        kw = kwargs or {}

        async def _deliver_callback() -> None:
            if not callback or not from_handle:
                return
            st = runner.status(run_id)
            ok = st.get("status") == "ok"
            body = st.get("result") if ok else st.get("error", "")
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
        rw = getattr(bridge, "run_worker", None)
        if rw is not None:
            def _sched(coro, *, name):  # noqa: ANN001
                return rw(coro, name=name, exclusive=False)
        else:
            _sched = None
        await runner.start(
            name, kw,
            host=from_handle or None,
            state_dir=state_dir,
            workflow_id=run_id,
            scheduler=_sched,
            done_callback=_deliver_callback)
        return {
            "workflow_id": run_id,
            "workflow_run_id": run_id,
            "host": from_handle or None,
            "status": "running",
        }

    @server.tool
    async def aegis_workflow_status(workflow_id: str) -> dict:
        """Inspect a running or completed workflow.

        Returns ``{workflow_id, name, host, status, result?, error?}``;
        ``status`` is one of ``running``, ``ok``, ``error``, ``cancelled``,
        or ``unknown``."""
        runner = getattr(bridge, "workflow_runner", None)
        if runner is None:
            return {"workflow_id": workflow_id, "status": "unknown"}
        return runner.status(workflow_id)

    @server.tool
    async def aegis_workflow_cancel(workflow_id: str) -> dict:
        """Cancel a running workflow. Returns ``{ok, status?, error?}``.
        Idempotent for already-completed runs."""
        runner = getattr(bridge, "workflow_runner", None)
        if runner is None:
            return {"ok": False, "error": "no workflow_runner on bridge"}
        return await runner.cancel(workflow_id)

    @server.tool
    async def aegis_canvas_open(name: str, file: str | None = None,
                                from_handle: str = "") -> dict:
        """Open or create a shared canvas — a markdown file multiple
        agents can collaboratively write to.

        First open of a name requires ``file`` (the on-disk path); the
        file is created empty if missing. Subsequent opens of the same
        name (from any agent) just return the metadata; passing a
        different ``file`` raises an error.

        Returns ``{name, file, sections: [{name, lines, last_writer,
        updated_at}], created_at}``.
        """
        from aegis.canvas.manager import CanvasError
        cm = getattr(bridge, "canvas_manager", None)
        if cm is None:
            return {"error": "canvas plane not available"}
        try:
            info = await cm.open(name, file)
        except CanvasError as e:
            return {"error": f"canvas_open rejected: {e}"}
        return _canvas_info_to_dict(info)

    @server.tool
    async def aegis_canvas_read(name: str,
                                section: str | None = None,
                                from_handle: str = "") -> dict:
        """Read a canvas — full file when ``section`` is omitted, or
        just that section's body. Returns ``{content: <str>}`` on
        success, ``{error: ...}`` if the canvas/section is missing.
        """
        from aegis.canvas.manager import CanvasError
        cm = getattr(bridge, "canvas_manager", None)
        if cm is None:
            return {"error": "canvas plane not available"}
        try:
            content = await cm.read(name, section)
        except CanvasError as e:
            return {"error": f"canvas_read rejected: {e}"}
        return {"content": content}

    @server.tool
    async def aegis_canvas_write_section(
            name: str, section: str, content: str,
            from_handle: str) -> dict:
        """Replace one section of the canvas with ``content``. If the
        section doesn't exist, it's appended to the end of the file.

        ``from_handle`` is your aegis handle (read it from your system
        prompt) — recorded as the writer in the ledger and used to
        suppress your own write from your own inbox notifications.
        """
        from aegis.canvas.manager import CanvasError
        cm = getattr(bridge, "canvas_manager", None)
        if cm is None:
            return {"error": "canvas plane not available"}
        try:
            res = await cm.write_section(name, section, content,
                                         writer=from_handle)
        except CanvasError as e:
            return {"error": f"canvas_write_section rejected: {e}"}
        return _write_result_to_dict(res)

    @server.tool
    async def aegis_canvas_append_to_section(
            name: str, section: str, text: str,
            from_handle: str) -> dict:
        """Append ``text`` to an existing section (joined with newline);
        create the section if missing. Cheaper than ``write_section`` for
        log-style growth.
        """
        from aegis.canvas.manager import CanvasError
        cm = getattr(bridge, "canvas_manager", None)
        if cm is None:
            return {"error": "canvas plane not available"}
        try:
            res = await cm.append_to_section(name, section, text,
                                             writer=from_handle)
        except CanvasError as e:
            return {"error": f"canvas_append_to_section rejected: {e}"}
        return _write_result_to_dict(res)

    @server.tool
    async def aegis_canvas_subscribe(name: str, from_handle: str,
                                     sections: list[str] | None = None
                                     ) -> dict:
        """Subscribe to canvas changes. When another agent writes to a
        watched section, you receive a normal user-message turn with
        sender ``canvas:<name>`` — same delivery channel as queue
        callbacks and handoffs.

        ``sections=None`` (default) means all sections. Provide a list
        to filter — only writes to those sections wake you.

        Subscription lives for your session's lifetime. Re-subscribe
        after an aegis restart.
        """
        from aegis.canvas.manager import CanvasError
        cm = getattr(bridge, "canvas_manager", None)
        if cm is None:
            return {"error": "canvas plane not available"}
        try:
            subs = cm.subscribe(name, from_handle, sections=sections)
        except CanvasError as e:
            return {"error": f"canvas_subscribe rejected: {e}"}
        return {"ok": True, "subscribers": subs}

    @server.tool
    async def aegis_canvas_unsubscribe(name: str,
                                       from_handle: str) -> dict:
        """Stop receiving notifications for a canvas."""
        from aegis.canvas.manager import CanvasError
        cm = getattr(bridge, "canvas_manager", None)
        if cm is None:
            return {"error": "canvas plane not available"}
        try:
            cm.unsubscribe(name, from_handle)
        except CanvasError as e:
            return {"error": f"canvas_unsubscribe rejected: {e}"}
        return {"ok": True}

    @server.tool
    async def aegis_canvas_list() -> list[dict]:
        """List all canvases open in this aegis instance.

        Each entry has ``{name, file, sections, created_at}`` — use
        ``aegis_canvas_open(name)`` to subscribe / write without
        rebinding the file.
        """
        cm = getattr(bridge, "canvas_manager", None)
        if cm is None:
            return []
        return [_canvas_info_to_dict(i) for i in cm.list_canvases()]

    @server.tool
    async def aegis_term_spawn(
        name: str, shell: str | None = None, cwd: str | None = None,
        env: dict | None = None, from_handle: str = "",
    ) -> dict:
        """Spawn a live shared PTY terminal.

        Errors if ``name`` already exists. ``shell`` defaults to $SHELL
        (then /bin/bash); ``cwd`` defaults to aegis's launch dir.
        Returns ``{name, pid, shell, cwd, started_at, last_cmd_at,
        last_exit}``.
        """
        from aegis.terminal.manager import TerminalAlreadyExists
        tm = getattr(bridge, "terminal_manager", None)
        if tm is None:
            return {"error": "terminal plane not available"}
        try:
            info = await tm.spawn(name=name, shell=shell, cwd=cwd, env=env)
        except TerminalAlreadyExists:
            return {"error": f"term_spawn rejected: {name!r} already exists"}
        return _terminal_info_to_dict(info)

    @server.tool
    async def aegis_term_list() -> list[dict]:
        """List all live terminals."""
        tm = getattr(bridge, "terminal_manager", None)
        if tm is None:
            return []
        return [_terminal_info_to_dict(i) for i in tm.list()]

    @server.tool
    async def aegis_term_run(
        name: str, cmd: str, timeout: float | None = None,
        from_handle: str = "",
    ) -> dict:
        """Run a command in a terminal. Blocks until the shell's OSC 133
        D marker arrives (or ``timeout`` elapses). Holds a per-terminal
        lock so concurrent ``run`` calls serialize FIFO.

        Pass your aegis handle as ``from_handle`` — recorded as the
        writer, and used to suppress your own command's wake from your
        inbox. Returns the full command record.
        """
        from aegis.terminal.manager import TerminalNotFound
        tm = getattr(bridge, "terminal_manager", None)
        if tm is None:
            return {"error": "terminal plane not available"}
        writer = f"agent:{from_handle}" if from_handle else "agent:unknown"
        try:
            rec = await tm.run(name, cmd, writer=writer, timeout=timeout)
        except TerminalNotFound:
            return {"error": f"term_run rejected: unknown terminal {name!r}"}
        return _command_record_to_dict(rec)

    @server.tool
    async def aegis_term_keys(
        name: str, keys: str, from_handle: str = "",
    ) -> dict:
        """Send raw bytes to a terminal — fire-and-forget, bypasses the
        per-terminal lock. Use for interactive prompts (``y\\n``),
        Ctrl-C (``\\x03``), or driving REPLs. UTF-8 string accepted.
        """
        from aegis.terminal.manager import TerminalNotFound
        tm = getattr(bridge, "terminal_manager", None)
        if tm is None:
            return {"error": "terminal plane not available"}
        writer = f"agent:{from_handle}" if from_handle else "agent:unknown"
        try:
            await tm.send_keys(name, keys, writer=writer)
        except TerminalNotFound:
            return {"error": f"term_keys rejected: unknown terminal {name!r}"}
        return {"ok": True}

    @server.tool
    async def aegis_term_read(
        name: str, last_n: int = 5, since_seq: int | None = None,
        from_handle: str = "",
    ) -> list[dict]:
        """Read command records from a terminal's ledger. ``since_seq``
        overrides ``last_n`` when set (returns records with seq > value).
        """
        from aegis.terminal.manager import TerminalNotFound
        tm = getattr(bridge, "terminal_manager", None)
        if tm is None:
            return []
        try:
            recs = tm.read(name, last_n=last_n, since_seq=since_seq)
        except TerminalNotFound:
            return []
        return [_command_record_to_dict(r) for r in recs]

    @server.tool
    async def aegis_term_subscribe(
        name: str, from_handle: str,
    ) -> dict:
        """Subscribe to a terminal's command-finish events. Every command
        the terminal finishes wakes you with a normal user-message turn
        tagged ``sender=term:<name>`` (except your own commands).
        Idempotent.
        """
        from aegis.terminal.manager import TerminalNotFound
        tm = getattr(bridge, "terminal_manager", None)
        if tm is None:
            return {"error": "terminal plane not available"}
        handle = f"agent:{from_handle}" if from_handle else "agent:unknown"
        try:
            subs = tm.subscribe(name, handle)
        except TerminalNotFound:
            return {"error": f"term_subscribe rejected: unknown terminal {name!r}"}
        return {"ok": True, "subscribers": subs}

    @server.tool
    async def aegis_term_unsubscribe(
        name: str, from_handle: str,
    ) -> dict:
        """Stop receiving command-finish wakes for a terminal."""
        from aegis.terminal.manager import TerminalNotFound
        tm = getattr(bridge, "terminal_manager", None)
        if tm is None:
            return {"error": "terminal plane not available"}
        handle = f"agent:{from_handle}" if from_handle else "agent:unknown"
        try:
            tm.unsubscribe(name, handle)
        except TerminalNotFound:
            return {"error": f"term_unsubscribe rejected: unknown terminal {name!r}"}
        return {"ok": True}

    @server.tool
    async def aegis_term_close(
        name: str, purge: bool = False, from_handle: str = "",
    ) -> dict:
        """Close a terminal. SIGTERM then SIGKILL after 2s. ``purge=true``
        also wipes the terminal's state directory; the default keeps the
        ledger on disk.
        """
        from aegis.terminal.manager import TerminalNotFound
        tm = getattr(bridge, "terminal_manager", None)
        if tm is None:
            return {"error": "terminal plane not available"}
        try:
            await tm.close(name, purge=purge)
        except TerminalNotFound:
            return {"error": f"term_close rejected: unknown terminal {name!r}"}
        return {"ok": True}

    @server.tool
    async def aegis_group_spawn(profile: str, group: str,
                                 handle: str | None = None) -> dict:
        """Spawn a new agent into a group. Creates the group implicitly
        if it doesn't exist. Returns ``{handle, group}``.

        Profile must resolve in the loaded ``.aegis.yaml`` agents list.
        """
        return await _aegis_group_spawn_impl(bridge, profile=profile,
                                              group=group, handle=handle)

    @server.tool
    async def aegis_group_broadcast(from_handle: str, group: str,
                                    objective: str, output_format: str,
                                    tool_guidance: str,
                                    boundaries: str) -> dict:
        """Broadcast a four-field message to every member of a group.

        Required fields: ``objective``, ``output_format``, ``tool_guidance``,
        ``boundaries``. The four are composed into the next user-message
        turn for every group member. ``from_handle`` is your own aegis
        handle. Returns ``{broadcast_id}`` — pass it to
        ``aegis_group_wait_all`` / ``aegis_group_wait_any`` to collect.

        Only one in-flight broadcast per group; a second call before the
        first completes raises BroadcastInFlight.
        """
        from aegis.queue import sender_agent
        return await _aegis_group_broadcast_impl(
            bridge, group=group, sender=sender_agent(from_handle),
            objective=objective, output_format=output_format,
            tool_guidance=tool_guidance, boundaries=boundaries,
        )

    @server.tool
    async def aegis_group_wait_all(group: str, timeout: float = 600.0,
                                    reducer: str = "concat") -> dict:
        """Block until every member of ``group`` posts one
        post-broadcast turn, or until ``timeout`` seconds elapse.
        Returns the ``GroupResult`` bundle as a JSON-serialisable dict.
        """
        return await _aegis_group_wait_all_impl(
            bridge, group=group, timeout=timeout, reducer=reducer)

    @server.tool
    async def aegis_group_wait_any(group: str, timeout: float = 600.0,
                                    cancel_losers: bool = True) -> dict:
        """Block until the first member of ``group`` posts one
        post-broadcast turn. Surviving members receive an inbox
        cancel signal unless ``cancel_losers=False``.
        """
        return await _aegis_group_wait_any_impl(
            bridge, group=group, timeout=timeout,
            cancel_losers=cancel_losers)

    @server.tool
    async def aegis_group_spawn_mixed(group: str,
                                       profiles: list[str]) -> dict:
        """Spawn one member per profile string into ``group``. Profiles
        may repeat; each entry gets its own session. Returns the list of
        handles in the same order as ``profiles``.
        """
        return await _aegis_group_spawn_mixed_impl(
            bridge, group=group, profiles=profiles)

    @server.tool
    async def aegis_group_status(group: str) -> dict:
        """Snapshot of ``group``: name, members (handle+profile), and the
        current in-flight broadcast if any.
        """
        return await _aegis_group_status_impl(bridge, group=group)

    @server.tool
    async def aegis_group_dissolve(group: str) -> dict:
        """Tear down ``group`` (does not close member sessions)."""
        return await _aegis_group_dissolve_impl(bridge, group=group)

    @server.tool
    async def aegis_group_rename(old: str, new: str) -> dict:
        """Rename a group from ``old`` to ``new``."""
        return await _aegis_group_rename_impl(
            bridge, old=old, new=new)

    @server.tool
    async def aegis_group_move_member(handle: str, from_group: str,
                                       to_group: str) -> dict:
        """Move a member from ``from_group`` to ``to_group``."""
        return await _aegis_group_move_member_impl(
            bridge, handle=handle, from_group=from_group,
            to_group=to_group)

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
