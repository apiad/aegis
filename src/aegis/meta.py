"""Session meta files: one small JSON per session, next to its store.

Boot reads only these, never the stores, so a hundred archived sessions cost a
hundred small reads. Writes go through write-then-rename, so a crash leaves the
old file or the new one, never half of one. During a turn writes are throttled to
one per second per session; ``flush`` writes whatever is pending. A missing or
damaged file is rebuilt from its store and marked archived.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections.abc import Callable
from pathlib import Path

from .claude.stream import Init, parse
from .names import default_title
from .transcript.store import read_store

THROTTLE_S = 1.0


class MetaStore:
    def __init__(self, dir: Path) -> None:
        self.dir = dir
        self._pending: dict[str, Callable[[], dict]] = {}
        self._last: dict[str, float] = {}
        self._timers: dict[str, asyncio.TimerHandle] = {}

    def path(self, log_id: str) -> Path:
        return self.dir / f"{log_id}.json"

    def write(self, meta: dict) -> None:
        log_id = meta["log_id"]
        self._pending.pop(log_id, None)
        timer = self._timers.pop(log_id, None)
        if timer:
            timer.cancel()
        self.dir.mkdir(parents=True, exist_ok=True)
        target = self.path(log_id)
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=1))
        os.replace(tmp, target)
        self._last[log_id] = time.monotonic()

    def write_soon(self, log_id: str, meta: Callable[[], dict]) -> None:
        """Write now if the last write was over a second ago, else within a
        second. ``meta`` is called only when the file is written, so a session
        changing on every line builds its meta once a second, not per line."""
        if log_id in self._pending:
            self._pending[log_id] = meta
            return
        wait = THROTTLE_S - (time.monotonic() - self._last.get(log_id, 0.0))
        if wait <= 0:
            self.write(meta())
            return
        self._pending[log_id] = meta
        if log_id not in self._timers:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                return  # no loop: flush() writes it
            self._timers[log_id] = loop.call_later(wait, self._flush_one, log_id)

    def _flush_one(self, log_id: str) -> None:
        self._timers.pop(log_id, None)
        meta = self._pending.pop(log_id, None)
        if meta is not None:
            self.write(meta())

    def flush(self) -> None:
        for log_id in list(self._pending):
            self._flush_one(log_id)

    def read_all(self) -> tuple[list[dict], list[Path]]:
        """Every readable meta, and the paths of the ones that are not."""
        metas, broken = [], []
        if not self.dir.is_dir():
            return metas, broken
        for p in sorted(self.dir.glob("*.json")):
            try:
                m = json.loads(p.read_text())
                if not isinstance(m, dict) or m.get("log_id") != p.stem:
                    raise ValueError("not a meta for this file")
                metas.append(m)
            except (OSError, ValueError):
                broken.append(p)
        return metas, broken


def rebuild(store_path: Path) -> dict | None:
    """A meta recovered from a store, marked archived; None if nothing usable."""
    try:
        records, _ = read_store(store_path)
    except OSError:
        return None
    spawn = next((r for r in records if r.get("kind") == "spawn"), None)
    if spawn is None:
        return None
    claude_id = None
    for r in records:
        if r.get("src") == "claude":
            init = next(
                (e for e in parse(r.get("line", "")) if isinstance(e, Init)), None
            )
            if init and init.session_id:
                claude_id = init.session_id
                break
    first_send = next((r for r in records if r.get("kind") == "send"), None)
    return {
        "log_id": store_path.stem,
        "handle": None,
        "title": default_title(first_send["text"]) if first_send else "",
        "agent": spawn.get("agent") or spawn.get("profile"),
        "harness": spawn.get("harness"),
        "priming": spawn.get("priming"),
        "overridden": spawn.get("overridden"),
        "spawned_by": spawn.get("spawned_by"),
        "model": spawn.get("model"),
        "effort": spawn.get("effort"),
        "permission": spawn.get("permission"),
        "cwd": spawn.get("cwd"),
        "claude_session_id": claude_id,
        "archived": True,
        "created_at": records[0].get("ts"),
        "last_activity": records[-1].get("ts"),
        "last_status": "stopped",
        "cost_usd": None,
        "context_tokens": None,
        "context_window": None,
    }
