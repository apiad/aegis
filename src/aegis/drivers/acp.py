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
import acp.connection as _acp_connection


# ---------------------------------------------------------------------
# Workaround for an upstream ACP SDK race (observed against
# agent-client-protocol 0.10.0, real-terminal Textual loop):
#
#   Connection.__init__ schedules self._receive_loop() as a task BEFORE
#   it assigns ``self._receive_timeout``. Normally __init__ runs to
#   completion before the loop ticks, so the receive loop sees the
#   attribute. Under aggressive task scheduling (Textual + Python 3.13
#   here) the receive task can run first → AttributeError in the
#   readline call → 'Receive loop failed' → connection closed →
#   ConnectionError on the next initialize().
#
# Setting ``_receive_timeout = None`` as a CLASS-LEVEL default makes
# the attribute lookup safe even when the instance attribute hasn't
# been assigned yet. Idempotent: if upstream lands a fix, this still
# does the right thing.
# ---------------------------------------------------------------------
if not hasattr(_acp_connection.Connection, "_receive_timeout") \
        or isinstance(
            _acp_connection.Connection.__dict__.get("_receive_timeout"),
            type(None)) is False:
    # Add as a class attribute so per-instance reads have a fallback
    # before __init__ writes the instance attribute.
    _acp_connection.Connection._receive_timeout = None  # type: ignore[attr-defined]


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

try:
    from importlib.metadata import PackageNotFoundError as _PNFE
    from importlib.metadata import version as _pkg_version
    _AEGIS_VERSION = _pkg_version("aegis-harness")
except _PNFE:                    # not installed (e.g. running from source)
    _AEGIS_VERSION = "0.0.0+unknown"
_STREAM_LIMIT = 16 * 1024 * 1024


def _summarize_acp_input(raw_input: dict) -> str:
    """One-line summary from an ACP tool's raw_input. Tries common
    keys (command / file_path / filePath / pattern) first, then any
    string value. Empty string when nothing fits."""
    for key in ("command", "file_path", "filePath", "pattern"):
        v = raw_input.get(key)
        if isinstance(v, str) and v:
            return v
    for v in raw_input.values():
        if isinstance(v, str) and v:
            return v
    return ""


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
        # Tool-call ids → kind (read/edit/execute/…). ToolCallProgress
        # doesn't restate the kind from the matching ToolCallStart, so
        # we cache it here for the ToolResult correlation.
        self._tool_kinds: dict[str, str] = {}

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
            tcid = getattr(update, "tool_call_id", "") or ""
            title = getattr(update, "title", "?") or "?"
            tool_kind = getattr(update, "kind", None)
            raw_input = getattr(update, "raw_input", None)
            status = getattr(update, "status", None)
            locations_raw = getattr(update, "locations", None) or []
            locations = tuple(
                (getattr(loc, "path", ""), getattr(loc, "line", None))
                for loc in locations_raw
            )
            self._tool_calls[tcid] = title
            if tool_kind:
                self._tool_kinds[tcid] = tool_kind
            summary = _summarize_acp_input(raw_input) \
                if isinstance(raw_input, dict) else ""
            self._queue.put_nowait(ToolUse(
                name=title, summary=summary,
                kind=tool_kind,
                raw_input=raw_input if isinstance(raw_input, dict) else None,
                tool_call_id=tcid or None,
                locations=locations,
                status=status,
            ))
        elif kind == "ToolCallProgress":
            status = getattr(update, "status", "")
            if status in ("completed", "failed"):
                is_error = status == "failed"
                tcid = getattr(update, "tool_call_id", "") or ""
                text = ""
                for block in (update.content or []):
                    inner = getattr(block, "content", None)
                    if inner is not None:
                        candidate = getattr(inner, "text", "")
                        if candidate:
                            text = candidate
                self._queue.put_nowait(ToolResult(
                    text=text, is_error=is_error,
                    tool_call_id=tcid or None,
                    kind=self._tool_kinds.get(tcid),
                ))
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
                 mcp_url: str, handle: str,
                 *, resume_session_id: str | None = None) -> None:
        self._agent = agent
        self._cwd = cwd
        self._mcp_url = mcp_url
        self._handle = handle
        # When set, start() calls load_session(session_id=...) instead of
        # new_session(...) so the agent re-attaches to an existing
        # conversation rather than starting fresh.
        self._resume_session_id: str | None = resume_session_id
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
            if self._resume_session_id:
                sess = await self._conn.load_session(
                    cwd=self._cwd,
                    session_id=self._resume_session_id,
                    mcp_servers=mcp_servers)
            else:
                sess = await self._conn.new_session(
                    cwd=self._cwd, mcp_servers=mcp_servers)
            # load_session's response may not echo session_id; fall back
            # to the requested id so subsequent prompt() calls hit the
            # right conversation.
            self._session_id = (getattr(sess, "session_id", None)
                                or self._resume_session_id)
        except BaseException as e:
            raise (await self._wrap_error(e)) from None

    async def send(self, text: str) -> None:
        import time as _time
        if not self._conn or not self._session_id:
            raise RuntimeError(
                "AcpSession.send() called before start()")
        started = _time.monotonic()
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
        # Synthesize the terminal Result from PromptResponse.
        # ACP PromptResponse carries a top-level ``usage`` (acp.schema.Usage)
        # with input_tokens / output_tokens / cached_read_tokens /
        # cached_write_tokens / thought_tokens / total_tokens. Map it into
        # our TokenUsage so SessionMetrics.commit picks the same fields
        # the claude-code driver populates — otherwise the status line
        # would show 0/0/0 for ACP-backed sessions even though the agent
        # genuinely consumed tokens. duration_ms is measured locally;
        # PromptResponse doesn't carry it.
        from aegis.events import TokenUsage as _TU
        duration_ms = int((_time.monotonic() - started) * 1000)
        is_error = resp.stop_reason not in ("end_turn", None)
        usage = None
        in_tok = out_tok = None
        u = getattr(resp, "usage", None)
        if u is not None:
            in_tok = int(getattr(u, "input_tokens", 0) or 0)
            out_tok = int(getattr(u, "output_tokens", 0) or 0)
            cr_tok = int(getattr(u, "cached_read_tokens", 0) or 0)
            cw_tok = int(getattr(u, "cached_write_tokens", 0) or 0)
            th_tok = int(getattr(u, "thought_tokens", 0) or 0)
            # Thought tokens are billed at the output rate (true for
            # Anthropic + Gemini + Moonshot) — fold them into output so
            # the cost segment is accurate without a separate thinking
            # tally.
            usage = _TU(
                input=in_tok,
                cache_creation=cw_tok,
                cache_read=cr_tok,
                output=out_tok + th_tok,
            )
        else:
            # Gemini doesn't populate PromptResponse.usage — it puts
            # token counts in field_meta.quota.token_count instead.
            # Without this fallback every Gemini turn reports 0/0.
            fm = getattr(resp, "field_meta", None) or {}
            tc = ((fm.get("quota") or {}).get("token_count")) or {}
            if tc:
                in_tok = int(tc.get("input_tokens") or 0)
                out_tok = int(tc.get("output_tokens") or 0)
                usage = _TU(input=in_tok, cache_creation=0,
                            cache_read=0, output=out_tok)
        self._queue.put_nowait(Result(
            duration_ms=duration_ms, is_error=is_error,
            input_tokens=in_tok, output_tokens=out_tok,
            usage=usage))
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
    # ACP defines `loadSession` (and the SDK exposes it), so we advertise
    # support and the per-provider start() probes whether the spawned
    # agent actually implements it. If the agent doesn't, load_session
    # raises and the resumed tab surfaces a clear failure banner.
    supports_resume = True

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

    def resume(self, agent: Agent, cwd: str,
               mcp_url: str, handle: str,
               session_id: str) -> AcpSession:
        s = self.SESSION_CLS(agent, cwd, mcp_url, handle,
                             resume_session_id=session_id)
        s.BASE_CMD = self.build_argv(agent, cwd, mcp_url, handle)
        return s
