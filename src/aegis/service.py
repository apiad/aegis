"""The systemd unit that runs ``aegis serve``: written by ``aegis service install``,
checked by ``aegis doctor``.

``UNIT`` is the one place the recommended settings live. The README's example
is this template rendered for a host with 8 GB (``tests/test_service.py`` fails
when they differ), and the doctor's recommendation for a live unit is the same
template rendered for this host, so the three cannot disagree.

The memory caps follow the host: ``MemoryHigh`` about 70% of its RAM and
``MemoryMax`` about 80%, each rounded to a quarter of a gigabyte (64M below 4 GB,
so the two stay apart), which gives 5.5G and 6.5G on 8 GB. Nothing here calls ``sudo`` or touches ``/etc``:
``install`` writes the unit where the person says and prints the privileged
commands, and the doctor only reads.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import launch
from .config import Finding

UNIT = """\
[Unit]
Description=aegis on {root}
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User={user}
WorkingDirectory={root}
Environment=PATH={path}
ExecStart={aegis} serve --port {port}
# systemd stops the server, and the server ends its agents. A restart
# continues the turns it cut.
KillMode=mixed
Restart=always
RestartSec=3
# Every session, and everything its agent starts, lives in this unit, so these
# lines decide what one runaway session can take down.
# An OOM kill of one child is that child's problem, not a reason to stop the unit.
OOMPolicy=continue
# Throttle the unit before the host runs out, and cap it below the host's RAM,
# so a runaway hits an OOM inside the unit instead of a host-wide one.
MemoryHigh={memory_high}
MemoryMax={memory_max}
# In a host OOM, the kernel picks the server last. aegis raises every agent
# process it starts to oom_score_adj 500, so a runaway session dies first.
OOMScoreAdjust=-500
# Let aegis give each session a cgroup of its own below the unit's, capped by
# session_memory_max, so a runaway session is OOM-killed alone.
Delegate=yes

[Install]
WantedBy=multi-user.target
"""

# The directives that are policy rather than one deployment's choice: the
# doctor compares these, never User, ExecStart or the paths.
POLICY = (
    "KillMode",
    "Restart",
    "RestartSec",
    "OOMPolicy",
    "MemoryHigh",
    "MemoryMax",
    "OOMScoreAdjust",
    "Delegate",
)
MEMORY = ("MemoryHigh", "MemoryMax")
# Where a unit is looked for, and whether systemd runs it as a user unit.
UNIT_DIRS = (
    (Path("/etc/systemd/system"), False),
    (Path.home() / ".config/systemd/user", True),
)

MEMINFO = Path("/proc/meminfo")
GIB = 1024**3
_UNITS = {"": 1, "K": 1024, "M": 1024**2, "G": GIB, "T": 1024**4}


@dataclass(frozen=True)
class Unit:
    user: str
    root: Path
    port: int
    aegis: str
    path: str
    memory_high: str
    memory_max: str


def render(u: Unit) -> str:
    return UNIT.format(**u.__dict__)


def mem_total(meminfo: str) -> int:
    """MemTotal from the text of /proc/meminfo, in bytes."""
    m = re.search(r"^MemTotal:\s+(\d+) kB", meminfo, re.M)
    if not m:
        raise ValueError("no MemTotal in /proc/meminfo")
    return int(m.group(1)) * 1024


def size(n: int) -> str:
    """Bytes as systemd reads them back: 5.5G, 768M."""
    if n >= GIB and n % (GIB // 4) == 0:
        return f"{n / GIB:g}G"
    return f"{n // 1024**2}M"


def caps(total: int) -> tuple[str, str]:
    """MemoryHigh and MemoryMax for a host with ``total`` bytes of RAM."""
    step = GIB // 4 if total >= 4 * GIB else 64 * 1024**2
    high, top = (max(1, round(total * f / step)) * step for f in (0.7, 0.8))
    return size(high), size(top)


def parse_size(value: str, total: int) -> int | None:
    """A memory directive in bytes; None for infinity or what does not parse."""
    v = value.strip()
    if v.endswith("%"):
        return int(total * float(v[:-1]) / 100)
    m = re.fullmatch(r"([\d.]+)\s*([KMGT]?)", v, re.I)
    return int(float(m.group(1)) * _UNITS[m.group(2).upper()]) if m else None


def directives(text: str) -> dict[str, str]:
    """The [Service] settings of a unit and its drop-ins, as ``systemctl cat``
    prints them: the last assignment wins, as it does in systemd."""
    out: dict[str, str] = {}
    section = None
    for line in text.splitlines():
        line = line.strip()
        if not line or line[0] in "#;":
            continue
        if line.startswith("["):
            section = line
        elif section == "[Service]" and "=" in line:
            key, value = line.split("=", 1)
            out[key.strip()] = value.strip()
    return out


def diff(live: str, recommended: str, total: int) -> list[tuple[str, str, str]]:
    """(key, live value, recommended value) for each policy directive that
    differs; a missing one is ``unset``."""
    have, want = directives(live), directives(recommended)
    out = []
    for key in POLICY:
        got = have.get(key, "unset")
        if key in MEMORY and parse_size(got, total) == parse_size(want[key], total):
            continue
        if got != want[key]:
            out.append((key, got, want[key]))
    return out


def units(dirs) -> list[tuple[Path, bool]]:
    """The installed unit files whose ExecStart runs ``aegis serve``; the bool
    says a user unit."""
    found = []
    for d, user in dirs:
        for f in sorted(d.glob("*.service")) if d.is_dir() else []:
            try:
                text = f.read_text()
            except OSError:
                continue
            if re.search(r"^ExecStart=.*\baegis serve\b", text, re.M):
                found.append((f, user))
    return found


def cat(unit: Path, user: bool) -> str:
    """The unit with its drop-ins, from ``systemctl cat``; the file alone when
    systemctl cannot say."""
    cmd = ["systemctl", *(["--user"] if user else []), "cat", unit.name]
    try:
        r = launch.run(cmd, text=True, timeout=5)
        if r.returncode == 0:
            return r.stdout
    except (OSError, subprocess.TimeoutExpired):
        pass
    return unit.read_text()


def check(dirs=None, read=cat) -> list[Finding]:
    """A warning for each policy directive an installed aegis unit sets
    differently from ``aegis service install``; nothing when there is no unit."""
    found = units(UNIT_DIRS if dirs is None else dirs)
    if not found:
        return []
    total = mem_total(MEMINFO.read_text())
    high, top = caps(total)
    want = render(Unit("-", Path("-"), 0, "aegis", "-", high, top))
    out = []
    for f, user in found:
        where = f"service.{f.name}"
        gaps = diff(read(f, user), want, total)
        out += [
            Finding("warn", f"{where}.{k}", f"{got}, recommended {rec}")
            for k, got, rec in gaps
        ]
        if not gaps:
            out.append(
                Finding("ok", where, f"{f} sets what `aegis service install` would")
            )
    return out
