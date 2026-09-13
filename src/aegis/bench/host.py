"""How busy the machine is while a benchmark runs.

Timings only compare between runs made on a similarly quiet machine, so
the machine's state travels with the numbers: in the fingerprint at run
start, and at the edges of every measurement window.

The busy share comes from /proc/stat, not the load average. Measured on
zion: at load 7.2 on 8 cores the CPU was 17% busy during a window that
measured quiet-host latency, and two runs at load 10 measured block-stream
p50 28 ms and 141 ms. The load average counts blocked processes and lags
by a minute, so it is kept as context only.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

# Above half the CPU busy with other work, timings stop comparing with a
# quiet run.
BUSY_CPU_PCT = 50.0


def loadavg() -> tuple[float, float, float]:
    """The 1, 5 and 15 minute load averages; zeros where /proc is absent."""
    try:
        parts = Path("/proc/loadavg").read_text().split()
        return float(parts[0]), float(parts[1]), float(parts[2])
    except (OSError, IndexError, ValueError):
        return 0.0, 0.0, 0.0


def cores() -> int:
    return os.cpu_count() or 1


def cpu_times() -> tuple[int, int]:
    """System-wide (idle, total) CPU jiffies since boot, from /proc/stat.

    Idle counts iowait too; the first eight fields are summed because guest
    time is already inside user and nice.
    """
    try:
        fields = Path("/proc/stat").read_text().splitlines()[0].split()[1:]
        values = [int(v) for v in fields]
    except (OSError, IndexError, ValueError):
        return 0, 0
    idle = values[3] + (values[4] if len(values) > 4 else 0)
    return idle, sum(values[:8])


def busy_pct(first: tuple[int, int], last: tuple[int, int]) -> float | None:
    """Percent of CPU time spent on anything but idle between two
    ``cpu_times()`` readings; None when no time passed between them."""
    d_total = last[1] - first[1]
    if d_total <= 0:
        return None
    return round(100 * (1 - (last[0] - first[0]) / d_total), 2)


def sample_busy_pct(seconds: float = 1.0) -> float | None:
    """The busy share over the next ``seconds``."""
    first = cpu_times()
    time.sleep(seconds)
    return busy_pct(first, cpu_times())
