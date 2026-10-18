"""Notices: what a session is told about the host's disk, memory, CPU and quota.

A notice never starts a turn and never goes into the system prompt: it rides
on the session's next send or its next aegis tool result. Reading a signal
never raises; a missing /proc or PSI file reads as None, and None means no
notice of that kind.
"""

from __future__ import annotations

import os
from pathlib import Path

from . import host


def psi(text: str) -> dict | None:
    """``{"some": {"avg10": .., "avg60": .., ...}, "full": {...}}`` from a
    /proc/pressure file, or None when it holds no PSI line."""
    out: dict[str, dict[str, float]] = {}
    for line in text.splitlines():
        name, *fields = line.split()
        if name not in ("some", "full"):
            continue
        vals: dict[str, float] = {}
        for f in fields:
            k, _, v = f.partition("=")
            try:
                vals[k] = float(v)
            except ValueError:
                return None
        out[name] = vals
    return out or None


def cpu_pct(psi_cpu: dict | None, loadavg: str | None, ncpu: int) -> float | None:
    """PSI CPU some avg60. Without PSI, the 1-minute load over the CPU count
    times 25, so a load of twice the CPUs reads as 50."""
    if psi_cpu and "avg60" in psi_cpu.get("some", {}):
        return psi_cpu["some"]["avg60"]
    if loadavg is None or ncpu < 1:
        return None
    try:
        return round(float(loadavg.split()[0]) / ncpu * 25, 1)
    except (IndexError, ValueError):
        return None


def next_level(prev: str | None, value: float, levels: dict) -> str | None:
    """The level after ``value``, from ``prev`` (None, warn or critical). An
    episode never steps down from critical, and ends only below ``clear``."""
    critical = levels.get("critical")
    if prev is None:
        if critical is not None and value >= critical:
            return "critical"
        return "warn" if value >= levels["on"] else None
    if value < levels["clear"]:
        return None
    if critical is not None and value >= critical:
        return "critical"
    return prev


def _read(path: Path) -> str | None:
    try:
        return path.read_text()
    except OSError:
        return None


def _disk(paths: list[Path]) -> dict | None:
    """The fullest filesystem holding any of ``paths``, each counted once."""
    best: dict | None = None
    seen: set[int] = set()
    for p in paths:
        try:
            dev = os.stat(p).st_dev
            if dev in seen:
                continue
            seen.add(dev)
            d = host.disk(p)
            st = os.statvfs(p)
        except OSError:
            continue
        if d is None:
            continue
        free = round(st.f_bavail * st.f_frsize / host.GB, 1)
        if best is None or d["pct"] > best["pct"]:
            best = {"pct": d["pct"], "free_gb": free, "total_gb": d["total_gb"]}
    return best


def _memory(proc: Path) -> dict | None:
    text = _read(proc / "meminfo")
    m = None if text is None else host.memory(text)
    if m is None:
        return None
    pressure = _read(proc / "pressure" / "memory")
    p = None if pressure is None else psi(pressure)
    full = (p or {}).get("full", {}).get("avg60")
    return {
        "pct": m["pct"],
        "avail_gb": round(m["total_gb"] - m["used_gb"], 1),
        "total_gb": m["total_gb"],
        "psi_full": full,
    }


def _cpu(proc: Path) -> dict | None:
    pressure = _read(proc / "pressure" / "cpu")
    pct = cpu_pct(
        None if pressure is None else psi(pressure),
        _read(proc / "loadavg"),
        os.cpu_count() or 1,
    )
    return None if pct is None else {"pct": pct}


def read_signals(paths: list[Path], proc: Path = Path("/proc")) -> dict:
    """One reading of every host signal; a kind that cannot be read is None."""
    return {"disk": _disk(paths), "memory": _memory(proc), "cpu": _cpu(proc)}
