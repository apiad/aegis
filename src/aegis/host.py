"""Host meters for the Fleet band: CPU, RAM, and every disk the host mounts.

Read from /proc and ``os.statvfs``, stdlib only. The disks are the mount
table's block devices, less loop devices (snaps), squashfs and the boot and EFI
partitions: what is left is where work can live. The disk that holds aegis is
always among them, so a container whose root is an overlay still shows one.
The ``host:`` block of ``.aegis.yaml`` hides mount points or adds ones the
filter drops (a ZFS dataset, an NFS share). ``disk`` is the fullest of them,
the one gauge a row has room for; the ``net`` field is the
last network reading (net/service.py), which calls ``check`` when it changes. Sampled every ``INTERVAL_S``
and only while someone subscribes to the ``host`` channel, so a server nobody
watches reads nothing. Published when a whole-percent value changes. Where
/proc is missing (macOS) the snapshot is None and the client hides the meters.
"""

from __future__ import annotations

import asyncio
import os
import re
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


# Filesystems on a /dev node that are never where work lives: snap images,
# live-CD images, and the FAT of an EFI partition.
SKIP_FS = {"squashfs", "iso9660", "vfat"}
SKIP_UNDER = ("/boot", "/efi", "/snap")


def _unescape(field: str) -> str:
    """The mount table writes a space in a path as \\040."""
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m[1], 8)), field)


def real_mounts(table: str) -> list[str]:
    """The mount points of /proc/self/mounts that are disks: one per block
    device, at its shortest mount point (a bind mount or a btrfs subvolume
    lists the same device again)."""
    by_dev: dict[str, str] = {}
    for line in table.splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        dev, mount, fs = parts[0], _unescape(parts[1]), parts[2]
        if not dev.startswith("/dev/") or dev.startswith("/dev/loop"):
            continue
        if fs in SKIP_FS or any(
            mount == u or mount.startswith(u + "/") for u in SKIP_UNDER
        ):
            continue
        if dev not in by_dev or len(mount) < len(by_dev[dev]):
            by_dev[dev] = mount
    return sorted(set(by_dev.values()))


def mount_of(path: Path) -> str:
    """The mount point of the filesystem holding ``path``."""
    p = os.path.realpath(path)
    while not os.path.ismount(p):
        p = os.path.dirname(p)
    return p


def host_from(raw: object) -> tuple[dict, str | None]:
    """The ``host:`` block: ``hide_mounts`` and ``add_mounts``, two lists of
    mount points. A wrong block keeps the defaults and says why."""
    default: dict = {"hide_mounts": [], "add_mounts": []}
    if raw is None:
        return default, None
    if not isinstance(raw, dict):
        return default, "host must be a mapping"
    out = dict(default)
    for k, v in raw.items():
        if k not in default:
            return default, f"host has no key {k!r}; it takes hide_mounts, add_mounts"
        bad = f"host.{k} must be a list of absolute mount points"
        if not isinstance(v, list):
            return default, bad
        mounts = [m for m in v if isinstance(m, str) and m.startswith("/")]
        if len(mounts) != len(v):
            return default, bad
        out[k] = [m.rstrip("/") or "/" for m in mounts]
    return out, None


def _key(snap: dict | None) -> tuple | None:
    if snap is None:
        return None
    net = snap.get("net")
    return (
        snap["cpu"],
        snap["ram"]["pct"],
        tuple((d["mount"], d["pct"]) for d in snap.get("disks", [])),
        None if net is None else repr(sorted(net.items())),
    )


class HostSampler:
    def __init__(
        self,
        publish: Publish,
        subscribers: Callable[[str], int],
        disk_path: Path,
        *,
        proc: Path = Path("/proc"),
        interval: float = INTERVAL_S,
        net: Callable[[], dict | None] = lambda: None,
        mounts: Callable[[], dict] = lambda: host_from(None)[0],
    ) -> None:
        self._publish = publish
        self._subscribers = subscribers
        self._disk_path = disk_path
        self._proc = proc
        self._interval = interval
        self._net = net
        self._mounts = mounts
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
        disks = self.disks()
        return {
            "cpu": round(100 * busy / total) if total > 0 else 0,
            "ram": mem,
            "disk": max(disks, key=lambda d: d["pct"], default=None),
            "disks": disks,
            "net": self._net(),
        }

    def disks(self) -> list[dict]:
        """Each disk's mount point, used and total, by mount point."""
        try:
            table = (self._proc / "self" / "mounts").read_text()
        except OSError:
            table = ""
        conf = self._mounts()
        mounts = set(real_mounts(table)) | set(conf["add_mounts"])
        mounts.add(mount_of(self._disk_path))
        mounts -= set(conf["hide_mounts"])
        out, seen = [], set()
        for m in sorted(mounts):
            try:
                dev = os.stat(m).st_dev  # one filesystem reached two ways
            except OSError:
                continue
            d = disk(Path(m))
            if d is not None and dev not in seen:
                seen.add(dev)
                out.append({"mount": m} | d)
        return out

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
