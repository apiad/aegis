"""Filesystem watcher → atomic-swap-or-reject scheduler reload.

Watches ``.aegis.yaml`` and the overlay folders under ``.aegis/``.
File changes are debounced (default 500 ms) and then delivered to a
single ``on_reload`` callback. ``on_reload`` reloads the YAML config
and atomically swaps the running scheduler's schedules table; if the
reload raises (parse error, conflict), the callback is expected to
keep the old state intact.

The watcher itself never crashes — exceptions raised by ``on_reload``
are caught + logged to ``aegis_events.jsonl`` (best-effort).
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Awaitable, Callable

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

logger = logging.getLogger(__name__)


ReloadFn = Callable[[], Awaitable[None] | None]


class _Handler(FileSystemEventHandler):
    def __init__(self, queue: asyncio.Queue,
                 loop: asyncio.AbstractEventLoop) -> None:
        self.queue = queue
        self.loop = loop

    def on_any_event(self, event: FileSystemEvent) -> None:
        src = getattr(event, "src_path", "") or ""
        if not src.endswith((".yaml", ".yml")):
            return
        # Bounce into the loop's queue from the watchdog thread.
        try:
            asyncio.run_coroutine_threadsafe(
                self.queue.put(event), self.loop)
        except RuntimeError:
            # Loop was already closed — drop the event.
            pass


class ReloadWatcher:
    """Filesystem watcher that fires ``on_reload`` after a debounce.

    ``root`` is the directory containing ``.aegis.yaml``. The watcher
    creates the ``.aegis/{agents,queues,schedules}`` overlay folders
    if they don't exist so the observer has stable paths to watch.
    """

    def __init__(
        self, root: Path, *,
        on_reload: ReloadFn,
        debounce_seconds: float = 0.5,
        events_log: Path | None = None,
    ) -> None:
        self.root = Path(root)
        self.on_reload = on_reload
        self.debounce_seconds = debounce_seconds
        self.events_log = events_log
        self._observer: Observer | None = None
        self._queue: asyncio.Queue = asyncio.Queue()
        self._task: asyncio.Task | None = None
        self._stopped = asyncio.Event()

    async def start(self) -> None:
        if self._observer is not None:
            return
        loop = asyncio.get_running_loop()
        handler = _Handler(self._queue, loop)
        self._observer = Observer()
        # Watch the .aegis.yaml file's parent so renames/atomic-writes
        # still produce events (watching the file directly loses
        # rename events).
        self._observer.schedule(handler, str(self.root), recursive=False)
        for section in ("agents", "queues", "schedules"):
            folder = self.root / ".aegis" / section
            folder.mkdir(parents=True, exist_ok=True)
            self._observer.schedule(handler, str(folder), recursive=False)
        self._observer.start()
        self._task = asyncio.create_task(self._drain())

    async def _drain(self) -> None:
        while not self._stopped.is_set():
            try:
                await self._queue.get()
            except asyncio.CancelledError:
                return
            # Collapse any backlog within the debounce window.
            try:
                while True:
                    await asyncio.wait_for(
                        self._queue.get(),
                        timeout=self.debounce_seconds)
            except asyncio.TimeoutError:
                pass
            await self._invoke()

    async def _invoke(self) -> None:
        try:
            res = self.on_reload()
            if asyncio.iscoroutine(res):
                await res
            self._record({"event": "reload_ok"})
        except Exception as e:  # noqa: BLE001
            logger.exception("scheduler reload failed")
            self._record({"event": "reload_failed", "error": repr(e)})

    def _record(self, payload: dict) -> None:
        if self.events_log is None:
            return
        try:
            self.events_log.parent.mkdir(parents=True, exist_ok=True)
            with self.events_log.open("a") as f:
                f.write(json.dumps(payload) + "\n")
        except Exception:  # noqa: BLE001 — event log is best-effort
            pass

    async def stop(self) -> None:
        self._stopped.set()
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=2.0)
            self._observer = None
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None
