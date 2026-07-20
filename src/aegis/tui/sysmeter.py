"""System resource meter for the status bar — a quick CPU/RAM/disk glance.

Sampled once per app tick (not per pane) and pushed to the visible pane's
StatusBar via ``set_system``. All three figures are system-wide percentages;
``disk`` reports the filesystem holding the project root, which is the disk
agents actually write into.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# A metric at or above this percentage renders amber to catch the eye.
HIGH_THRESHOLD = 90.0


@dataclass
class SystemStats:
    cpu: float   # system-wide CPU utilisation, 0–100
    ram: float   # virtual-memory utilisation, 0–100
    disk: float  # usage of the project-root filesystem, 0–100


def sample_system(path: str | Path) -> SystemStats:
    """Sample current system utilisation. ``cpu`` is non-blocking — it returns
    the load since the previous call, so the first sample after import reads
    0.0 and later ticks read real values."""
    import psutil

    return SystemStats(
        cpu=float(psutil.cpu_percent(interval=None)),
        ram=float(psutil.virtual_memory().percent),
        disk=float(psutil.disk_usage(str(path)).percent),
    )


def format_system(stats: SystemStats, colors) -> str:
    """Render ``CPU 23% · RAM 38% · DSK 71%`` with amber values past the mark."""

    def seg(label: str, pct: float) -> str:
        val = f"{pct:.0f}%"
        if pct >= HIGH_THRESHOLD:
            val = f"[{colors.working}]{val}[/]"
        return f"[{colors.muted}]{label}[/] {val}"

    return " · ".join((
        seg("CPU", stats.cpu),
        seg("RAM", stats.ram),
        seg("DSK", stats.disk),
    ))
