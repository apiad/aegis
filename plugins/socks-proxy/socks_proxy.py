"""socks-proxy plugin: tunnel harness subprocesses through a SOCKS proxy.

A ``pre_spawn`` hook prepends ``proxychains4 -q -f <conf>`` to the argv
of every harness session, so the spawned ``claude`` / ``gemini`` /
``opencode`` process talks to its API endpoint via the configured SOCKS
proxy. proxychains4 intercepts ``connect()`` via ``LD_PRELOAD`` and
forwards each TCP connection through the proxy list in the conf.

Configuration lives in ``<project>/.aegis/socks-proxy.conf`` — a
proxychains4 conf file written by ``_install.py``. The hook reads it at
spawn time; deleting the conf disables proxying without uninstalling.
The plugin requires ``proxychains4`` on PATH (e.g. ``apt install
proxychains4``); install-time prints a warning if it is missing.
"""
from __future__ import annotations

from pathlib import Path

from aegis.config import find_project_root
from aegis.hooks import PreSpawnContext, PreSpawnResult, hook


CONF_SUBPATH = ".aegis/socks-proxy.conf"


def _conf_path_for(cwd: str) -> Path | None:
    """Resolve ``<project_root>/.aegis/socks-proxy.conf`` if a project root
    exists at or above ``cwd``. Return None when there's no project."""
    root = find_project_root(Path(cwd))
    if root is None:
        return None
    return root / CONF_SUBPATH


@hook("pre_spawn")
async def proxify(ctx: PreSpawnContext) -> PreSpawnResult | None:
    """Prepend ``proxychains4 -q -f <conf>`` when a conf is present."""
    conf = _conf_path_for(ctx.cwd)
    if conf is None or not conf.exists():
        return None
    return PreSpawnResult(
        argv=("proxychains4", "-q", "-f", str(conf), *ctx.argv),
    )
