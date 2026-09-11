"""Boot aegis in-process, at an explicit root, without owning the loop.

The library seam sindri drives. Unlike the CLI entry points this installs no
signal handlers and never calls asyncio.run — the host owns both.
"""
from __future__ import annotations

import asyncio
import signal
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from pathlib import Path

from aegis.config.roots import AegisRoots


@contextmanager
def _keep_host_signals():
    """Give the host back its signal handlers.

    aegis itself installs none — `_serve` is signal-clean, and the handlers
    the CLI wants live in `_run_serve`. But the MCP plane runs on uvicorn,
    and `Server.serve()` captures SIGINT/SIGTERM unconditionally on the
    main thread (`uvicorn/server.py:78`, `:322`) with no config flag to opt
    out of it. So the plane takes them for us, and an embedded aegis must
    hand them straight back: the host owns its own shutdown.

    Putting them back mid-flight is safe because uvicorn restores whatever
    it captured when `serve()` unwinds, and what it captured *is* these —
    so nothing is left dangling when the plane stops.
    """
    handled = (signal.SIGINT, signal.SIGTERM)
    saved = {s: signal.getsignal(s) for s in handled}
    try:
        yield
    finally:
        for sig, handler in saved.items():
            # None means the handler was installed from outside Python and
            # cannot be restored; leaving it alone beats guessing.
            if handler is not None:
                signal.signal(sig, handler)


@dataclass
class EmbeddedAegis:
    manager: object
    queues: object
    roots: AegisRoots
    mcp: object             # the instance's AegisMCP; .server is its FastMCP


@asynccontextmanager
async def embed(root: Path | str, *, harness_cwd: Path | str | None = None):
    """Yield a booted aegis rooted at `root`. Several may coexist."""
    from aegis.cli import _serve, _session_factory, load_boot_config
    from aegis.hosts.registry import HostRegistry
    from aegis.mcp import AegisMCP

    roots = AegisRoots.for_project(Path(root), harness_cwd=harness_cwd)
    cfg = load_boot_config(roots)
    host_registry = HostRegistry(cfg.hosts, state_dir=roots.state_dir,
                                 local_root=str(roots.harness_cwd))
    stop = asyncio.Event()
    holder: dict = {}
    mcp = AegisMCP()

    class _Capture:
        """The no-UI attachment.

        There is no app here, so the manager IS the AppBridge the plane has
        to address — which is exactly what `_serve` already bound. `_serve`
        defers `start()` whenever a UI is attached, because a *front end*
        rebinds the plane to itself first; nothing rebinds here, so this
        attachment owes the plane its start. One `AegisMCP`, bound once in
        `_serve`, started once here. The manager is published only after
        the server is up, so a caller that has been handed an
        `EmbeddedAegis` can call `.mcp.server` without racing it.
        """

        async def run(self, manager) -> None:
            with _keep_host_signals():
                await mcp.start()
            holder["manager"] = manager
            await stop.wait()

    task = asyncio.create_task(_serve(
        roots=roots, agents=cfg.agents, default_agent=cfg.default_agent,
        make_session=_session_factory(str(roots.harness_cwd), host_registry),
        mcp=mcp, stop=stop, queues=cfg.queues,
        schedules=cfg.schedules, remotes=cfg.remotes,
        remote_plane=cfg.remote_plane, hosts=cfg.hosts,
        host_registry=host_registry,
        inline_schedule_names=cfg.inline_schedule_names,
        ui=_Capture()))
    try:
        while "manager" not in holder and not task.done():
            await asyncio.sleep(0.01)
        if task.done():
            task.result()          # re-raise a boot failure
        mgr = holder["manager"]
        yield EmbeddedAegis(manager=mgr, queues=mgr.queue_manager,
                            roots=roots, mcp=mcp)
    finally:
        stop.set()
        await asyncio.wait_for(task, timeout=10)
