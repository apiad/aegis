"""Monitors: bash conditions polled for an agent until one decides it.

An agent arms a monitor and ends its turn; that is how it waits (the primer
says so). ``done`` exiting 0 completes it, ``fail`` exiting 0 fails it, the
timeout ends it, and ``progress`` prints 0 to 100 for the bar on its card. The
owner is woken through its inbox with ``> from monitor:<id> · <outcome> · …``,
and the wake lists the owner's other live monitors so it can cancel stale ones.

Monitors are kept in ``<state>/monitors.json`` and re-armed at boot; a wake to
a stopped owner resumes it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .registry import Registry

log = logging.getLogger("aegis.monitors")
CONDITION_LIMIT_S = 30.0


def iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _elapsed(seconds: float) -> str:
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m}m {s}s"
    h, m = divmod(m, 60)
    return f"{h}h {m}m"


@dataclass
class Monitor:
    id: str
    owner: str  # log id
    description: str
    done: str
    cwd: str
    fail: str | None = None
    progress: str | None = None
    interval_s: float = 10.0
    timeout_s: float = 3600.0
    started_at: float = field(default_factory=time.time)
    last_progress: int | None = None

    def card(self) -> dict:
        return {
            "id": self.id,
            "description": self.description,
            "progress": self.last_progress,
        }


class Monitors:
    def __init__(self, registry: Registry, path: Path) -> None:
        self._registry = registry
        self._path = path
        self.items: dict[str, Monitor] = {}
        self._tasks: dict[str, asyncio.Task] = {}

    # -- persistence ---------------------------------------------------------
    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps([asdict(m) for m in self.items.values()], indent=1))
        os.replace(tmp, self._path)

    def boot(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text())
        except (OSError, ValueError):
            log.warning("aegis: %s is damaged; starting with no monitors", self._path)
            return
        for d in raw if isinstance(raw, list) else []:
            try:
                m = Monitor(**d)
            except TypeError:
                continue
            if m.owner in self._registry.sessions:
                self.items[m.id] = m
        self._save()

    def arm_all(self) -> None:
        for m in self.items.values():
            self._arm(m)

    # -- operations ----------------------------------------------------------
    def start(self, owner: str, cwd: str, **kw) -> Monitor:
        m = Monitor(id=f"mon-{secrets.token_hex(4)}", owner=owner, cwd=cwd, **kw)
        self.items[m.id] = m
        self._save()
        self._arm(m)
        self._registry.refresh_card(owner)
        return m

    def cancel(self, monitor_id: str) -> Monitor | None:
        m = self.items.pop(monitor_id, None)
        if m is None:
            return None
        t = self._tasks.pop(monitor_id, None)
        if t:
            t.cancel()
        self._save()
        self._registry.refresh_card(m.owner)
        return m

    def of(self, owner: str) -> list[Monitor]:
        return [m for m in self.items.values() if m.owner == owner]

    def card(self, owner: str) -> list[dict]:
        return [m.card() for m in self.of(owner)]

    def drop_owner(self, owner: str) -> None:
        for m in self.of(owner):
            self.cancel(m.id)

    async def shutdown(self) -> None:
        for t in self._tasks.values():
            t.cancel()
        self._tasks.clear()

    # -- watching ------------------------------------------------------------
    def _arm(self, m: Monitor) -> None:
        self._tasks[m.id] = asyncio.get_running_loop().create_task(self._watch(m))

    async def _run(self, cmd: str, cwd: str) -> tuple[int | None, str]:
        try:
            proc = await asyncio.create_subprocess_exec(
                "bash",
                "-c",
                cmd,
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except OSError as e:
            return None, str(e)
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), CONDITION_LIMIT_S)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return None, f"took over {CONDITION_LIMIT_S:g}s"
        return proc.returncode, out.decode("utf-8", "replace")

    async def _watch(self, m: Monitor) -> None:
        try:
            if not Path(m.cwd).is_dir():
                await self._end(m, "fail", f"its cwd {m.cwd} does not exist")
                return
            while True:
                if time.time() - m.started_at > m.timeout_s:
                    await self._end(
                        m,
                        "timeout",
                        f"it ran out of time after {_elapsed(m.timeout_s)}",
                    )
                    return
                rc, _ = await self._run(m.done, m.cwd)
                if rc == 0:
                    await self._end(m, "ok", "its done condition passed")
                    return
                if m.fail:
                    rc, _ = await self._run(m.fail, m.cwd)
                    if rc == 0:
                        await self._end(m, "fail", "its fail condition passed")
                        return
                if m.progress:
                    rc, out = await self._run(m.progress, m.cwd)
                    value = _percent(out) if rc == 0 else None
                    if value is not None and value != m.last_progress:
                        m.last_progress = value
                        self._registry.refresh_card(m.owner)
                await asyncio.sleep(m.interval_s)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # a monitor bug must still wake its owner
            log.exception("aegis: monitor %s crashed", m.id)
            await self._end(
                m, "fail", f"the monitor itself failed: {type(e).__name__}: {e}"
            )

    async def _end(self, m: Monitor, outcome: str, why: str) -> None:
        self.items.pop(m.id, None)
        self._tasks.pop(m.id, None)
        self._save()
        self._registry.refresh_card(m.owner)
        others = self.of(m.owner)
        lines = [
            f"{m.description}: {outcome}, {why}, after {_elapsed(time.time() - m.started_at)}."
        ]
        if others:
            lines.append(
                "Your other live monitors: "
                + "; ".join(f"{o.id} ({o.description})" for o in others)
                + "."
            )
        else:
            lines.append("You have no other live monitors.")
        session = self._registry.sessions.get(m.owner)
        if session is None:
            return
        try:
            await session.deliver(
                f"> from monitor:{m.id} · {outcome} · {iso_now()}", "\n".join(lines)
            )
        except Exception:
            log.exception("aegis: could not deliver the wake of %s", m.id)


def _percent(out: str) -> int | None:
    try:
        v = float(out.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return None
    return max(0, min(100, int(v)))
