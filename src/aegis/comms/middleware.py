"""One envelope per call into the aegis MCP surface.

``on_call_tool`` is the single point every tool invocation passes through,
including plugin ``@tool``s. Wrapping the sixty-odd tools individually would
work today and silently miss the next one added.
"""
from __future__ import annotations

import logging
import time

from fastmcp.server.middleware import Middleware

from aegis.comms.descriptors import (aegis_family, aegis_target,
                                     descriptor_for)
from aegis.comms.models import Envelope
from aegis.comms.persistence import CommsLedger
from aegis.queue.schema import new_ulid, now_iso

log = logging.getLogger(__name__)

#: Substrate ids, in the order they are looked for. The result wins over the
#: arguments: ``enqueue`` mints its task id inside the call, while ``cancel``
#: and ``release`` are handed one.
_THREAD_KEYS = ("task_id", "monitor_id", "reminder_id", "claim_id",
                "workflow_run_id", "broadcast_id")


def _thread(result_data: object, args: dict, call_id: str) -> str:
    for source in (result_data if isinstance(result_data, dict) else {},
                   args):
        for key in _THREAD_KEYS:
            val = source.get(key)
            if isinstance(val, str) and val:
                return val
    return call_id


class CommsMiddleware(Middleware):
    def __init__(self, ledger: CommsLedger, tokens=None) -> None:
        self._ledger = ledger
        self._tokens = tokens

    async def on_call_tool(self, context, call_next):  # noqa: ANN001
        # ``context.message.name`` is the bare registered name
        # (``aegis_enqueue``); the ``mcp__aegis__`` prefix a transcript shows
        # is added by the harness, not by the server.
        name = context.message.name
        if descriptor_for(name) is None:
            return await call_next(context)

        args = context.message.arguments or {}
        call_id = new_ulid()
        ts = now_iso()
        started = time.monotonic()
        outcome = "ok"
        payload: object = None
        try:
            result = await call_next(context)
            payload = getattr(result, "structured_content", None)
            return result
        except Exception:
            outcome = "error"
            raise
        finally:
            self._record(name, args, call_id, ts, started, outcome, payload)

    def _from(self, args: dict) -> str:
        """Who called. The token wins when it resolves; the argument is
        the fallback for one release; unattributed stays honest.

        Resolution is inside the ledger's try/except by construction —
        `_record` already swallows-and-logs — so a broken identity path
        cannot fail a tool call.
        """
        from aegis.mcp.identity import resolve_caller
        return (resolve_caller(self._tokens)
                or str(args.get("from_handle") or ""))

    def _record(self, name: str, args: dict, call_id: str, ts: str,
                started: float, outcome: str, payload: object) -> None:
        try:
            self._ledger.write(Envelope(
                call_id=call_id,
                ts=ts,
                from_handle=self._from(args),
                to=aegis_target(name, args),
                family=aegis_family(name) or "",
                verb=name.removeprefix("aegis_"),
                thread=_thread(payload, args, call_id),
                outcome=outcome,
                duration_ms=int((time.monotonic() - started) * 1000),
            ))
        except Exception:
            # The ledger is observability. It never gets to fail a call — but
            # it is logged rather than swallowed, because a silent except here
            # would make a broken writer look like a working one.
            log.exception("comms ledger write failed for %s", name)
