"""Runs recaps: one call per session at a time, never twice for the same point
in the transcript unless asked, and the result appended as an aegis record so
every browser gets the same entry. Rules of one recap are in recap.py."""

from __future__ import annotations

import asyncio
import contextlib
import time

from . import oneshot, recap


class Recaps:
    def __init__(self, app) -> None:
        self.app = app
        self._running: dict[str, asyncio.Task] = {}

    async def request(self, s, force: bool) -> dict:
        cfg = recap.load_recap(self.app.roots.config_root)
        if cfg is None:
            return {"status": "off", "why": "add recap: {agent: <name>} to .aegis.yaml"}
        if "error" in cfg:
            return {"status": "off", "why": cfg["error"]}
        if s.in_turn:
            return {"status": "busy"}
        if s.log_id in self._running:
            return await asyncio.shield(self._running[s.log_id])
        fold = s.fold()
        upto = fold.content_index
        if not force:
            if fold.last_recap_upto == upto:
                return {"status": "exists"}
            if not recap.needed(fold.entries(), s.unread, s.last_read_at, time.time()):
                return {"status": "skip"}
        task = asyncio.get_running_loop().create_task(self._make(s, cfg, upto))
        self._running[s.log_id] = task
        # The call frees its own slot: a requester that goes away leaves the call
        # running, and the next request must join it, not pay for a second one.
        task.add_done_callback(
            lambda t, k=s.log_id: (
                self._running.pop(k, None) if self._running.get(k) is t else None
            )
        )
        return await asyncio.shield(task)

    async def shutdown(self) -> None:
        """Cancel every running call; each kills its child on the way out."""
        tasks = list(self._running.values())
        for t in tasks:
            t.cancel()
        for t in tasks:
            # Nothing a recap does may stop the sessions shutting down after it.
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t

    async def _make(self, s, cfg: dict, upto: int) -> dict:
        prompt = recap.window(s.fold().entries(), s.unread, s.standing)
        try:
            out = await oneshot.run(
                recap.argv(
                    self.app._bin(cfg["harness"]), cfg["model"], harness=cfg["harness"]
                ),
                self.app.roots.state_root / "oneshot",
                recap.TIMEOUT_S,
                self.app.sessions.user_for(s.spec),
                oneshot.message(recap.SYSTEM, prompt, cfg["harness"]),
                harness=cfg["harness"],
            )
        except (oneshot.OneshotError, PermissionError) as e:
            return {"status": "failed", "why": str(e)}
        value, cost, ms = recap.parse(out)
        if value is None:
            s.add_recap_cost(cost)  # paid, even with nothing to show
            return {"status": "failed", "why": "the model returned nothing usable"}
        if s.archived:
            return {"status": "failed", "why": "the session was closed"}
        if s.in_turn or s.fold().content_index != upto:
            # The person wrote while the call ran: the recap answers a point the
            # session has left. The call was paid all the same.
            s.add_recap_cost(cost)
            return {"status": "stale"}
        # An ask only while the session waits on its person: waiting on a
        # monitor or CI, the model turned the agent's own next step into an
        # order from the person ("Merge #636 when CI passes").
        ask = value.ask if self.app.sessions.attention_of(s) == "needs_you" else ""
        s.report(
            {
                "kind": "recap",
                "upto": upto,
                "context": value.context,
                "ask": ask,
                "model": cfg["model"],
                "cost_usd": cost,
                "duration_ms": ms,
            }
        )
        s.add_recap_cost(cost)
        return {"status": "made", "context": value.context, "ask": ask}
