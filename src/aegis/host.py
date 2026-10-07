"""Host meters for the Fleet band: CPU, RAM, and the disk that holds aegis.

Read from /proc and ``os.statvfs``, stdlib only. Sampled every ``INTERVAL_S``
and only while someone subscribes to the ``host`` channel, so a server nobody
watches reads nothing. Published when a whole-percent value changes. Where
/proc is missing (macOS) the snapshot is None and the client hides the meters.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from pathlib import Path

from .quota.core import cancel_and_wait

INTERVAL_S = 5.0
GB = 1024**3
Publish = Callable[[str, list[dict]], None]


def cpu_times(text: str) -> tuple[int, int] | None:
    """(busy, total) jiffies from the aggregate ``cpu`` line of /proc/stat.
    Idle is idle plus iowait; guest time is already inside user and nice, so
    only the first eight columns count."""
    for line in text.splitlines():
        if line.startswith("cpu "):
            v = [int(x) for x in line.split()[1:9]]
            idle = v[3] + (v[4] if len(v) > 4 else 0)
            total = sum(v)
            return total - idle, total
    return None


def memory(text: str) -> dict | None:
    kb: dict[str, int] = {}
    for line in text.splitlines():
        name, _, rest = line.partition(":")
        parts = rest.split()
        if parts and parts[0].isdigit():
            kb[name] = int(parts[0])
    total, avail = kb.get("MemTotal"), kb.get("MemAvailable")
    if not total or avail is None:
        return None
    used = (total - avail) * 1024
    return {
        "pct": round(100 * used / (total * 1024)),
        "used_gb": round(used / GB, 1),
        "total_gb": round(total * 1024 / GB, 1),
    }


def disk(path: Path) -> dict | None:
    """Used and total for the filesystem holding ``path``, the way ``df``
    counts them: the root-reserved blocks are neither used nor available."""
    try:
        st = os.statvfs(path)
    except OSError:
        return None
    used = (st.f_blocks - st.f_bfree) * st.f_frsize
    avail = st.f_bavail * st.f_frsize
    if used + avail <= 0:
        return None
    return {
        "pct": round(100 * used / (used + avail)),
        "used_gb": round(used / GB),
        "total_gb": round(st.f_blocks * st.f_frsize / GB),
    }


def _key(snap: dict | None) -> tuple | None:
    if snap is None:
        return None
    d = snap["disk"]
    return snap["cpu"], snap["ram"]["pct"], None if d is None else d["pct"]


class HostSampler:
    def __init__(
        self,
        publish: Publish,
        subscribers: Callable[[str], int],
        disk_path: Path,
        *,
        proc: Path = Path("/proc"),
        interval: float = INTERVAL_S,
    ) -> None:
        self._publish = publish
        self._subscribers = subscribers
        self._disk_path = disk_path
        self._proc = proc
        self._interval = interval
        self._prev_cpu: tuple[int, int] | None = None
        self._last: dict | None = None
        self._task: asyncio.Task | None = None

    def sample(self) -> dict | None:
        try:
            stat = (self._proc / "stat").read_text()
            meminfo = (self._proc / "meminfo").read_text()
        except OSError:
            return None
        times, mem = cpu_times(stat), memory(meminfo)
        if times is None or mem is None:
            return None
        prev, self._prev_cpu = self._prev_cpu, times
        busy, total = (
            times if prev is None else (times[0] - prev[0], times[1] - prev[1])
        )
        return {
            "cpu": round(100 * busy / total) if total > 0 else 0,
            "ram": mem,
            "disk": disk(self._disk_path),
        }

    def snapshot(self) -> dict | None:
        if self._last is None:
            self._last = self.sample()
        return self._last

    def check(self) -> None:
        if not self._subscribers("host"):
            return
        snap = self.sample()
        if _key(snap) != _key(self._last):
            self._last = snap
            self._publish("host", [{"set": snap}])

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop())

    async def _loop(self) -> None:
        while True:
            self.check()
            await asyncio.sleep(self._interval)

    async def stop(self) -> None:
        task, self._task = self._task, None
        await cancel_and_wait(task)
