"""ACP-based harness driver.

Built on the official `agent-client-protocol` Python SDK. Replaces the
v1 one-shot Gemini + OpenCode drivers with a single generic
`AcpSession` + `AcpDriver`. Two thin shims (`GeminiDriver`,
`OpenCodeDriver`) only set ``BASE_CMD`` — all the protocol heavy
lifting lives here.

Spec: ``vault/Atlas/Architecture/2026-05-20-aegis-acp-drivers-design.md``
Playtest evidence: ``.playground/acp-probe/FINDINGS.md``

The session is multi-turn by design: each ``send()`` issues a new
``conn.prompt()`` against the same ``session_id``, so conversation
state survives across sends. Per-session MCP injection is wired
through ``new_session(mcp_servers=[...])``; the agent connects to the
aegis MCP server for that session only — no global config side-effects.

OAuth pass-through is automatic: the agent subprocess reads its own
cached creds (``~/.gemini/oauth_creds.json`` etc.) regardless of how
it's invoked. ACP is just protocol on top.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import acp


class _RingHandler(logging.Handler):
    """In-memory log handler that keeps the last N records. Attached to
    the ``acp`` logger so the SDK's internal logging.exception calls
    (e.g. 'Receive loop failed') are captured and surfaceable in our
    error reports."""

    def __init__(self, max_records: int = 64) -> None:
        super().__init__(level=logging.DEBUG)
        self._records: list[str] = []
        self._max = max_records

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
        except Exception:  # noqa: BLE001
            msg = record.getMessage()
        self._records.append(msg)
        if len(self._records) > self._max:
            self._records.pop(0)

    def snapshot(self) -> str:
        return "\n".join(self._records)

    def clear(self) -> None:
        self._records.clear()

from aegis.config import Agent
from aegis.drivers.base import HarnessDriver, HarnessSession
from aegis.events import (
    AssistantText,
    AssistantThinking,
    Event,
    Result,
    ToolResult,
    ToolUse,
)

_AEGIS_VERSION = "0.2.0"
_STREAM_LIMIT = 16 * 1024 * 1024


class _AegisAcpClient(acp.Client):
    """Translates ACP ``session_update`` notifications into aegis Events
    on a queue the surrounding session drains. Implements the
    client-side ACP methods agents may call back (fs read/write,
    permission requests, etc.)."""

    def __init__(self, event_queue: asyncio.Queue) -> None:
        self._queue = event_queue
        # Track tool-call ids → name so ToolCallProgress(completed) can
        # carry a useful renderable.
        self._tool_calls: dict[str, str] = {}

    # The SDK invokes on_connect as a regular function, NOT as a
    # coroutine — declaring this async produces a "coroutine was never
    # awaited" warning. See playtest FINDINGS.md gotcha #1.
    def on_connect(self, conn) -> None:  # noqa: ARG002 — unused
        return None

    async def session_update(self, session_id, update, **kw) -> None:
        kind = update.__class__.__name__
        if kind == "AgentMessageChunk":
            text = getattr(update.content, "text", None)
            if text:
                self._queue.put_nowait(AssistantText(text=text))
        elif kind == "AgentThoughtChunk":
            text = getattr(update.content, "text", None)
            if text:
                self._queue.put_nowait(AssistantThinking(text=text))
        elif kind == "ToolCallStart":
            tcid = getattr(update, "tool_call_id", "")
            title = getattr(update, "title", "?") or "?"
            self._tool_calls[tcid] = title
            self._queue.put_nowait(ToolUse(name=title, summary=""))
        elif kind == "ToolCallProgress":
            status = getattr(update, "status", "")
            if status == "completed":
                text = ""
                for block in (update.content or []):
                    inner = getattr(block, "content", None)
                    if inner is not None:
                        candidate = getattr(inner, "text", "")
                        if candidate:
                            text = candidate
                self._queue.put_nowait(
                    ToolResult(text=text, is_error=False))
        # Other update classes (AvailableCommandsUpdate, UsageUpdate,
        # CurrentModeUpdate, etc.) are provider telemetry — drop.

    async def request_permission(self, options, session_id, tool_call,
                                 **kw):
        # Queue workers use Permission.full anyway. Auto-allow the first
        # option. (A future enhancement could route via TUI / Telegram.)
        return acp.RequestPermissionResponse(
            outcome={"outcome": "selected",
                     "optionId": options[0].option_id})

    async def read_text_file(self, path, session_id,
                             limit=None, line=None, **kw):
        try:
            content = Path(path).read_text(
                encoding="utf-8", errors="replace")
        except Exception as e:  # noqa: BLE001
            raise acp.RequestError(code=-32000, message=str(e))
        return acp.ReadTextFileResponse(content=content)

    async def write_text_file(self, content, path, session_id, **kw):
        Path(path).write_text(content, encoding="utf-8")
        return None

    # We declare terminal: False in client capabilities, so the agent
    # shouldn't call these. Implement as no-ops for protocol compliance.
    async def create_terminal(self, *a, **kw): return None
    async def terminal_output(self, *a, **kw): return None
    async def wait_for_terminal_exit(self, *a, **kw): return None
    async def kill_terminal(self, *a, **kw): return None
    async def release_terminal(self, *a, **kw): return None

    async def ext_method(self, method, params): return {}
    async def ext_notification(self, method, params): return None


class AcpSession(HarnessSession):
    """ACP-backed harness session. Multi-turn; per-session MCP injection."""

    BASE_CMD: list[str] = []  # set by the subclass driver

    def __init__(self, agent: Agent, cwd: str,
                 mcp_url: str, handle: str) -> None:
        self._agent = agent
        self._cwd = cwd
        self._mcp_url = mcp_url
        self._handle = handle
        self._proc: asyncio.subprocess.Process | None = None
        self._conn: Any = None
        self._session_id: str | None = None
        self._queue: asyncio.Queue = asyncio.Queue()
        self._client = _AegisAcpClient(self._queue)

    def _argv(self) -> list[str]:
        # Default: just the BASE_CMD. Subclasses override to inject
        # provider-specific flags like model selection.
        return list(self.BASE_CMD)

    async def _drain_stderr(self) -> None:
        """Continuously read subprocess stderr into a ring buffer.
        Cap at ~64KB so a runaway log can't OOM us. The contents are
        attached to the exception in start()/send() when the SDK
        bubbles up a ConnectionError so the operator sees the real
        reason the subprocess died."""
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        max_bytes = 64 * 1024
        total = 0
        try:
            while True:
                chunk = await proc.stderr.read(4096)
                if not chunk:
                    return
                self._stderr_tail.append(chunk)
                total += len(chunk)
                while total > max_bytes and len(self._stderr_tail) > 1:
                    total -= len(self._stderr_tail.pop(0))
        except Exception:  # noqa: BLE001
            return

    def _stderr_snapshot(self) -> str:
        if not getattr(self, "_stderr_tail", None):
            return ""
        return b"".join(self._stderr_tail).decode("utf-8", "replace")

    async def _wrap_error(self, exc: BaseException) -> BaseException:
        """Annotate a ConnectionError/EOF-ish failure with subprocess
        stderr + exit code so the surfaced error is actually actionable.

        Always wraps — even when stderr is empty — so the operator can
        see that the diagnostic ran and confirm the subprocess actually
        produced nothing. Gives the subprocess up to 1s to finish dying
        and flush its stderr before reading the snapshot."""
        # Give the subprocess a moment to actually exit + flush.
        if self._proc is not None:
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=1.0)
            except (asyncio.TimeoutError, ProcessLookupError):
                pass
        # Drain task should be near-done now that the pipe closed; give
        # it a tick to finish appending.
        await asyncio.sleep(0)
        rc = self._proc.returncode if self._proc else None
        tail = self._stderr_snapshot().rstrip()
        acp_log = getattr(self, "_log_ring", None)
        acp_log_text = acp_log.snapshot() if acp_log else ""
        argv = " ".join(self._argv())
        msg = (f"{type(exc).__name__}: {exc}\n"
               f"  subprocess: {argv}\n"
               f"  exit_code:  {rc if rc is not None else '(still running)'}\n"
               f"  stderr:\n{tail or '(empty — subprocess produced no stderr before failing)'}\n"
               f"  acp logger:\n{acp_log_text or '(empty)'}")
        wrapped = RuntimeError(msg)
        wrapped.__cause__ = exc
        return wrapped

    async def start(self) -> None:
        argv = self._argv()
        self._proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=self._cwd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=_STREAM_LIMIT,
        )
        # Drain subprocess stderr into a ring buffer in the background.
        # When a ConnectionError/EOF bubbles up from the SDK ("Connection
        # closed") it usually means the subprocess died with a real error
        # message to stderr — surfacing those bytes is how we debug.
        self._stderr_tail: list[bytes] = []
        self._stderr_task = asyncio.create_task(self._drain_stderr())
        # The SDK logs internal failures (e.g. "Receive loop failed",
        # "Error parsing JSON-RPC message") via the bare top-level
        # logging.exception(...) call — which routes to the ROOT logger,
        # NOT the "acp" logger. Attach the ring handler to root with a
        # filter so we only capture records that originated inside the
        # acp.* package (avoids slurping unrelated app noise).
        class _AcpFilter(logging.Filter):
            def filter(self, record: logging.LogRecord) -> bool:
                # SDK calls are 'logging.exception(...)' from inside
                # acp/* modules — pathname/module identifies them.
                module = (record.module or "")
                pathname = (record.pathname or "")
                return ("/acp/" in pathname or pathname.endswith("/acp")
                        or module.startswith("acp"))

        self._log_ring = _RingHandler(max_records=64)
        self._log_ring.setFormatter(logging.Formatter(
            "%(levelname)s %(name)s [%(module)s]: %(message)s"))
        self._log_ring.addFilter(_AcpFilter())
        self._root_logger = logging.getLogger()
        self._prev_root_level = self._root_logger.level
        self._root_logger.addHandler(self._log_ring)
        if self._root_logger.level == logging.NOTSET \
                or self._root_logger.level > logging.DEBUG:
            self._root_logger.setLevel(logging.DEBUG)
        # ACP SDK arg order: (client, in_stream, out_stream) where
        # in_stream is where the CLIENT writes (= agent's stdin) and
        # out_stream is where the CLIENT reads (= agent's stdout).
        self._conn = acp.connect_to_agent(
            self._client, self._proc.stdin, self._proc.stdout)
        try:
            await self._conn.initialize(
                protocol_version=1,
                client_capabilities={
                    "fs": {"readTextFile": True, "writeTextFile": True},
                    "terminal": False,
                },
                client_info={"name": "aegis", "version": _AEGIS_VERSION},
            )
            mcp_servers = ([{
                "type": "http",
                "name": "aegis",
                "url": self._mcp_url,
                "headers": [],
            }] if self._mcp_url else [])
            sess = await self._conn.new_session(
                cwd=self._cwd, mcp_servers=mcp_servers)
            self._session_id = sess.session_id
        except BaseException as e:
            raise (await self._wrap_error(e)) from None

    async def send(self, text: str) -> None:
        if not self._conn or not self._session_id:
            raise RuntimeError(
                "AcpSession.send() called before start()")
        try:
            resp = await self._conn.prompt(
                session_id=self._session_id,
                prompt=[{"type": "text", "text": text}],
            )
        except BaseException as e:
            raise (await self._wrap_error(e)) from None
        # The ACP SDK dispatches incoming notifications as separate
        # supervised tasks (see acp.task.dispatcher._dispatch_notification),
        # which means session_update handlers can still be pending when
        # prompt() resolves. Yield a few times so they run to completion
        # before we put the terminal Result on the queue.
        for _ in range(3):
            await asyncio.sleep(0)
        # Synthesize the terminal Result from PromptResponse. Token
        # counts arrive (when available) under
        # resp.field_meta["quota"]["token_count"].
        is_error = resp.stop_reason not in ("end_turn", None)
        in_tok = out_tok = None
        try:
            tok = resp.field_meta["quota"]["token_count"]  # type: ignore[index]
            in_tok = tok.get("input_tokens")
            out_tok = tok.get("output_tokens")
        except (KeyError, TypeError, AttributeError):
            pass
        self._queue.put_nowait(Result(
            duration_ms=None, is_error=is_error,
            input_tokens=in_tok, output_tokens=out_tok))
        # End-of-turn sentinel so events() returns.
        self._queue.put_nowait(None)

    async def events(self) -> AsyncIterator[Event]:
        while True:
            ev = await self._queue.get()
            if ev is None:
                return
            yield ev

    async def close(self) -> None:
        if self._conn and self._session_id:
            with contextlib.suppress(Exception):
                await self._conn.cancel(session_id=self._session_id)
        if self._proc and self._proc.returncode is None:
            self._proc.terminate()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._proc.wait(), timeout=3.0)
        # Detach the root-logger ring handler so we don't leak handlers
        # across repeated session lifetimes within the same process.
        ring = getattr(self, "_log_ring", None)
        logger = getattr(self, "_root_logger", None)
        if ring is not None and logger is not None:
            with contextlib.suppress(Exception):
                logger.removeHandler(ring)
            prev_level = getattr(self, "_prev_root_level", logging.NOTSET)
            with contextlib.suppress(Exception):
                logger.setLevel(prev_level)


class AcpDriver(HarnessDriver):
    """Generic ACP driver. Per-provider subclasses set ``BASE_CMD``."""

    BASE_CMD: list[str] = []
    SESSION_CLS: type[AcpSession] = AcpSession

    def build_argv(self, agent: Agent, cwd: str,
                   mcp_url: str, handle: str) -> list[str]:
        # Default: BASE_CMD verbatim. Provider drivers override to add
        # CLI-specific flags (e.g. Gemini's -m model selector). Models
        # that the CLI doesn't accept stay in agent.model for logging
        # / queue routing; the CLI uses its own default config.
        return list(self.BASE_CMD)

    def session(self, agent: Agent, cwd: str,
                mcp_url: str, handle: str) -> AcpSession:
        s = self.SESSION_CLS(agent, cwd, mcp_url, handle)
        # The session reads BASE_CMD from itself; provider sessions
        # override _argv if they need per-call argv tweaks.
        s.BASE_CMD = self.build_argv(agent, cwd, mcp_url, handle)
        return s
