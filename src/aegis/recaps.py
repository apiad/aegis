"""Runs recaps: one call per session at a time, never twice for the same point
in the transcript unless asked, and the result appended as an aegis record so
every browser gets the same entry. Rules of one recap are in recap.py."""

from __future__ import annotations

import asyncio
import contextlib
import os
import time

from . import recap


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
        """Cancel every running call; each kills its claude on the way out."""
        tasks = list(self._running.values())
        for t in tasks:
            t.cancel()
        for t in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await t

    async def _make(self, s, cfg: dict, upto: int) -> dict:
        prompt = recap.window(s.fold().entries(), s.unread, s.standing)
        cwd = self.app.roots.state_root / "oneshot"
        cwd.mkdir(parents=True, exist_ok=True)
        try:
            proc = await asyncio.create_subprocess_exec(
                *recap.argv(self.app.claude_bin, cfg["model"], prompt),
                cwd=cwd,
                env={**os.environ, "MAX_THINKING_TOKENS": "0"},
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except OSError as e:
            return {"status": "failed", "why": str(e)}
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), recap.TIMEOUT_S)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return {"status": "failed", "why": f"no answer in {recap.TIMEOUT_S}s"}
        except BaseException:
            # Cancelled (server shutdown): a claude left running would keep billing.
            if proc.returncode is None:
                proc.kill()
                await proc.wait()
            raise
        if proc.returncode != 0:
            return {
                "status": "failed",
                "why": f"claude exited with code {proc.returncode}",
            }
        value, cost, ms = recap.parse(out.decode(errors="replace"))
        if value is None:
            return {"status": "failed", "why": "the model returned nothing usable"}
        if s.archived:
            return {"status": "failed", "why": "the session was closed"}
        if s.in_turn or s.fold().content_index != upto:
            # The person wrote while the call ran: the recap answers a point the
            # session has left. The call was paid all the same.
            s.add_recap_cost(cost)
            return {"status": "stale"}
        s.report(
            {
                "kind": "recap",
                "upto": upto,
                "context": value.context,
                "ask": value.ask,
                "model": cfg["model"],
                "cost_usd": cost,
                "duration_ms": ms,
            }
        )
        s.add_recap_cost(cost)
        return {"status": "made", "context": value.context, "ask": value.ask}
