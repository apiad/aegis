"""Runs side notes: one call per session at a time, the question recorded at
once and the answer as a second record, so every browser draws the same block
and the send op never waits on a model. Rules of one side note are in btw.py."""

from __future__ import annotations

import asyncio
import contextlib
import uuid

from . import btw, oneshot
from .ops import OpError


class SideNotes:
    def __init__(self, app) -> None:
        self.app = app
        self._running: dict[str, tuple[str, asyncio.Task]] = {}  # log_id -> (id, task)

    def ask(self, s, question: str) -> dict:
        cfg = oneshot.load_generator(self.app.roots.config_root, "btw")
        if cfg is None:
            raise OpError("btw_off", "add btw: {agent: <name>} to .aegis.yaml")
        if "error" in cfg:
            raise OpError("btw_off", cfg["error"])
        if s.log_id in self._running:
            raise OpError(
                "btw_busy",
                "a side note is already running in this session; wait for it or cancel it",
            )
        entries = s.fold().entries()
        w = btw.window(entries)
        text = btw.prompt(question, w, s.standing, btw.earlier(entries))
        bid = uuid.uuid4().hex[:12]
        s.report({"kind": "btw", "id": bid, "question": question})
        task = asyncio.get_running_loop().create_task(
            self._make(s, cfg, bid, text, w.header)
        )
        self._running[s.log_id] = (bid, task)
        task.add_done_callback(
            lambda t, k=s.log_id: (
                self._running.pop(k, None)
                if self._running.get(k, ("", None))[1] is t
                else None
            )
        )
        return {"status": "asked", "id": bid}

    async def _make(self, s, cfg: dict, bid: str, text: str, header: str) -> None:
        base = {
            "kind": "btw_answer",
            "id": bid,
            "model": cfg["model"],
            "header": header,
        }
        argv = oneshot.argv(
            self.app._bin(cfg["harness"]),
            cfg["model"],
            btw.BtwOut,
            btw.SYSTEM,
            harness=cfg["harness"],
        )
        try:
            out = await oneshot.run(
                argv,
                self.app.roots.state_root / "oneshot",
                btw.TIMEOUT_S,
                self.app.sessions.user_for(s.spec),
                oneshot.message(btw.SYSTEM, text, cfg["harness"]),
                harness=cfg["harness"],
            )
        except (oneshot.OneshotError, PermissionError) as e:
            self._answer(s, {**base, "status": "failed", "why": str(e)})
            return
        except asyncio.CancelledError:
            self._answer(s, {**base, "status": "cancelled"})
            raise
        value, cost, ms = oneshot.parse(out, btw.BtwOut)
        s.add_btw_cost(cost)  # paid, even with nothing to show
        paid = {"cost_usd": cost, "duration_ms": ms}
        if value is None:
            why = "the model returned nothing usable"
            self._answer(s, {**base, **paid, "status": "failed", "why": why})
            return
        self._answer(
            s,
            {
                **base,
                **paid,
                "status": "ok",
                "answer": value.answer,
                "needs_more": value.needs_more,
            },
        )

    def _answer(self, s, record: dict) -> None:
        # Free the slot first: whoever reads the settled block may ask again at
        # once, before this task's done callback runs (#826).
        self._running.pop(s.log_id, None)
        if not s.archived:  # a closed session's store is shut
            s.report(record)

    def cancel(self, s, bid: str) -> dict:
        cur = self._running.get(s.log_id)
        if cur is not None and cur[0] == bid:
            cur[1].cancel()
            return {"status": "cancelled"}
        if any(
            e["kind"] == "btw"
            and e["detail"].get("id") == bid
            and e["status"] == "running"
            for e in s.fold().entries()
        ):
            # Running in the transcript with no call behind it: the server that
            # ran it stopped hard. Settle it, or its cancel link does nothing.
            self._answer(s, {"kind": "btw_answer", "id": bid, "status": "cancelled"})
            return {"status": "cancelled"}
        return {"status": "none"}

    def drop(self, log_id: str) -> None:
        """A session closing: its call dies with it."""
        cur = self._running.get(log_id)
        if cur is not None:
            cur[1].cancel()

    async def shutdown(self) -> None:
        """Cancel every running call; each kills its claude on the way out."""
        tasks = [t for _, t in self._running.values()]
        for t in tasks:
            t.cancel()
        for t in tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t
