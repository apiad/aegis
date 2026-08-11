"""The SYSTEM surface — a quick CPU/RAM/disk glance, plus where and when.

The meters are sampled once per app tick (not per pane) and pushed to the
visible pane's StatusBar via ``set_system``. All three figures are
system-wide percentages; ``disk`` reports the filesystem holding the
project root, which is the disk agents actually write into.

The rest — clock, locale, working directory, running build — is static
enough to be read straight off the process at model-assembly time. It has
no place on a one-row status bar, so it lives only in the sidebar's SYSTEM
section, where a terminal's vertical axis is free. Every formatter here is
pure and takes its inputs explicitly: a clock that reads ``now()`` itself
cannot be asserted on.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
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

def format_system_tiers(stats: SystemStats, colors) -> tuple[str, str]:
    """Widest and narrowest forms of the system segment.

    The narrow form drops the labels: three numbers in a fixed order are
    self-explanatory once you have seen the wide form once.
    """

    def val(pct: float) -> str:
        v = f"{pct:.0f}"
        return f"[{colors.working}]{v}[/]" if pct >= HIGH_THRESHOLD else v

    short = f"{val(stats.cpu)}·{val(stats.ram)}·{val(stats.disk)}%"
    return (format_system(stats, colors), short)


def current_locale() -> str:
    """The active locale as ``en_US.UTF-8``, or ``""`` when there is none.

    ``getlocale`` first because it is what Python actually formats with;
    the environment is the fallback for the case where nothing has been set
    and it answers ``(None, None)``.
    """
    import locale
    import os

    lang, encoding = locale.getlocale()
    if lang:
        return f"{lang}.{encoding}" if encoding else lang
    return os.environ.get("LC_ALL") or os.environ.get("LANG") or ""


def format_clock(now: datetime, loc: str, colors) -> tuple[str, ...]:
    """``2026-08-11 11:03 CDT · en_US.UTF-8``, narrowing to the bare time.

    The zone travels with the time rather than with the locale: a session
    driving a host in another zone makes the two answer different
    questions, and the one that makes the timestamp readable is the zone.
    A naive datetime has none, and then ``%Z`` is empty and the row is
    simply shorter.
    """
    stamp = now.strftime("%Y-%m-%d %H:%M")
    zone = now.strftime("%Z")
    wide = f"{stamp} {zone}" if zone else stamp
    tiers = [f"{wide} [{colors.muted}]· {loc}[/]"] if loc else []
    tiers += [wide, stamp, now.strftime("%H:%M")]
    return tuple(dict.fromkeys(tiers))


def format_cwd(path: str | Path, colors) -> tuple[str, ...]:
    """``CWD ~/Workspace/repos/aegis``, narrowing from the head.

    Home collapses to ``~`` and the narrow tiers keep the tail, for one
    reason: in a path you already live in, the prefix is the part you can
    reconstruct and the leaf is the part you are asking about.
    """
    p = Path(path)
    home = Path.home()
    if p == home:
        full = "~"
    elif home in p.parents:
        full = f"~/{p.relative_to(home)}"
    else:
        full = str(p)
    tiers = [full]
    if len(p.parts) > 2:
        tiers.append("…/" + "/".join(p.parts[-2:]))
    if len(p.parts) > 1:
        tiers.append(p.name)
    return tuple(f"[{colors.muted}]CWD[/] {t}"
                 for t in dict.fromkeys(tiers))


def format_build(colors) -> tuple[str, ...]:
    """``aegis 0.21.0+d35b07a`` — the version of the *running* process.

    Read off ``version.BUILD``, which latches at import for exactly this
    reason: under an editable checkout that keeps moving, the sidebar must
    answer "what am I running", not "what is on disk".
    """
    from aegis.version import BUILD

    return tuple(dict.fromkeys((
        f"[{colors.muted}]aegis[/] {BUILD}", BUILD, BUILD.split("+")[0])))
