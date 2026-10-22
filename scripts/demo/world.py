"""What the demo's aegis reads of the world, scripted: the host's CPU, memory
and disk, each provider's quota, and the version it runs.

The director runs aegis in its own process, as the canary does, so these
readings replace the readers on the live ``App``: the host sampler's
``sample``, the quota's ``snapshot`` and ``aegis.version.running``. Everything
else, sessions, harnesses, monitors, is aegis's own.
"""

from __future__ import annotations

import asyncio
import dataclasses
import random
import threading
import time
from pathlib import Path


@dataclasses.dataclass
class World:
    cpu: float = 18  # percent; each reading wanders a few points around it
    ram: float = 41  # percent of ram_gb
    ram_gb: float = 32
    disk: float = 37  # percent of disk_gb
    disk_gb: float = 512
    # Each provider's windows, in percent. A provider missing is not shown.
    claude: dict[str, float] | None = dataclasses.field(
        default_factory=lambda: {"session": 22, "weekly_all": 38}
    )
    opencode: dict[str, float] | None = None
    version: str = "3.4.0"

    def host(self) -> dict:
        cpu = max(1, min(99, round(self.cpu + random.uniform(-4, 4))))
        ram = {
            "pct": round(self.ram),
            "used_gb": round(self.ram_gb * self.ram / 100, 1),
            "total_gb": self.ram_gb,
        }
        disk = {
            "mount": "/",
            "pct": round(self.disk),
            "used_gb": round(self.disk_gb * self.disk / 100),
            "total_gb": self.disk_gb,
        }
        return {"cpu": cpu, "ram": ram, "disk": disk, "disks": [disk], "net": None}

    def quota(self) -> dict:
        now = time.time()
        rails = [
            ("claude", "Claude", "claude-code", self.claude,
             {"session": ("5 hours", 5 * 3600), "weekly_all": ("week", 7 * 86400)}),
            ("opencode-go", "OpenCode Go", "opencode", self.opencode,
             {"rolling": ("5 hours", 5 * 3600), "weekly": ("week", 7 * 86400)}),
        ]  # fmt: skip
        providers = []
        for name, label, harness, windows, spans in rails:
            if not windows:
                continue
            ws = []
            for kind, pct in windows.items():
                wlabel, span = spans[kind]
                # Halfway through each window, on pace for a little under full.
                starts = round(now - span / 2)
                ws.append({
                    "kind": kind, "label": wlabel, "percent": round(pct),
                    "severity": "normal", "projected": min(99, round(pct * 1.8)),
                    "starts_at": starts, "resets_at": starts + span,
                })  # fmt: skip
            providers.append({
                "name": name, "label": label, "harness": harness, "account": None,
                "state": "ok", "note": "", "read_at": round(now - 20),
                "retry_at": None, "windows": ws,
            })  # fmt: skip
        return {"providers": providers}

    def running(self) -> dict:
        return {"version": self.version, "commit": None, "ref": None, "dev": False}


class Server:
    """aegis on a loop of its own thread, with the world's readings."""

    def __init__(self, root: Path, claude: str, port: int, world: World) -> None:
        self.root, self.claude, self.port, self.world = root, claude, port, world
        self.loop = asyncio.new_event_loop()
        self.app = None
        self.url = f"http://127.0.0.1:{port}/?token=demo"

    def start(self) -> None:
        threading.Thread(target=self.loop.run_forever, daemon=True).start()
        asyncio.run_coroutine_threadsafe(self._boot(), self.loop).result(60)

    async def _boot(self) -> None:
        import uvicorn

        import aegis.version
        import aegis.web
        from aegis.app import App
        from aegis.roots import make_roots
        from aegis.web import build_web

        aegis.version.running = aegis.web.running = self.world.running
        self.app = App(
            make_roots(self.root, None),
            claude_bin=self.claude,
            base_url=f"http://127.0.0.1:{self.port}",
            server_name="studio",
        )
        self.app.host.sample = self.world.host
        self.app.quota.snapshot = lambda: self.world.quota()
        web = build_web(self.app, "demo", {f"127.0.0.1:{self.port}"})
        self.server = uvicorn.Server(
            uvicorn.Config(web, host="127.0.0.1", port=self.port, log_level="warning")
        )
        self.task = asyncio.create_task(self.server.serve())
        while not self.server.started:
            await asyncio.sleep(0.02)

    def call(self, op: str, params: dict) -> dict:
        """An op of aegis's registry, as the browser calls it."""
        return asyncio.run_coroutine_threadsafe(
            self.app.registry.call(op, params), self.loop
        ).result(60)

    def changed(self) -> None:
        """Publish the world now, not at the samplers' next tick."""

        def push() -> None:
            self.app.host.check()
            self.app.quota.check()

        self.loop.call_soon_threadsafe(push)

    def stop(self) -> None:
        async def down() -> None:
            for s in list(self.app.sessions.sessions.values()):
                try:
                    await asyncio.wait_for(s.stop(), 20)
                except Exception:
                    pass
            self.server.should_exit = True
            await asyncio.wait_for(self.task, 30)

        asyncio.run_coroutine_threadsafe(down(), self.loop).result(90)
