"""One view: an AegisApp whose frames go to a sink, at its own geometry.

The app holds the brain by direct Python reference through ``bridge=``
(stage 3), so nothing is serialised between a view and the manager and
there is no protocol that can fall behind. What crosses a view's boundary
is bytes out and key events in — nothing that knows what a session is.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

from aegis.config.roots import AegisRoots
from aegis.tui.app import AegisApp
from aegis.views.driver import view_driver_for
from aegis.views.sink import FrameSink
from aegis.views.state import ViewState, load_view, save_view


@dataclass
class View:
    view_id: str
    app: AegisApp
    state: ViewState
    sink: FrameSink = field(default_factory=FrameSink)
    _task: asyncio.Task | None = None

    @property
    def frames(self) -> list[bytes]:
        """The detached buffer. A property, not a field, so the existing
        in-process assertions (``len(v.frames)``, ``v.frames[:] = …``,
        ``v.frames.clear()``) keep operating on the real list."""
        return self.sink.frames

    async def run(self) -> None:
        """Run the app until it exits. The caller owns the task.

        ``size=`` is not optional. ``run_async`` defaults it to ``None``,
        which reaches the driver as ``size=None`` and sends it to the
        ``COLUMNS``/``ROWS`` fallback (`web_driver.py:52-59`) — the
        process-global this whole stage is about. A view whose geometry
        lives only in its ``ViewState`` and never reaches its driver has a
        write-only field and N views that all render at one size.
        """
        self._task = asyncio.create_task(
            self.app.run_async(size=self.state.geometry))

    async def stop(self) -> None:
        if self._task is not None and not self._task.done():
            self.app.exit()
            with_timeout = asyncio.wait_for(self._task, timeout=10)
            try:
                await with_timeout
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._task.cancel()
        self._task = None

    def repaint(self) -> None:
        """Force the next render cycle to emit a full frame.

        Textual emits *incremental* updates: ``render_update`` returns a
        partial unless the whole screen region is dirty
        (`_compositor.py:1118`), and there is no repaint meta message. So a
        reattaching client would otherwise receive deltas against a screen
        it has never seen.

        ``Screen.refresh()`` with no region already marks the whole screen,
        which is all this needs. Measured on Textual 8.2.6 against a
        quiesced 80x24 view, three ways, byte-for-byte identical -- 4000
        bytes in one frame addressing 25 rows::

            screen.refresh()                             -> 4000 bytes
            app.refresh(repaint=True, layout=True)       -> 4000 bytes
            compositor._dirty_regions.add(...) + refresh -> 4000 bytes
            (nothing)                                    ->    0 bytes

        An earlier draft poked ``compositor._dirty_regions`` first, on the
        measured claim that ``refresh()`` emits nothing. It does not, and no
        test can tell those three apart because there is nothing to tell
        apart -- so this uses the public call rather than coupling to two
        private Textual attributes for no observable gain.
        """
        self.app.screen.refresh()

    def persist(self, state_dir: Path) -> None:
        save_view(state_dir, self.state)


async def open_view(view_id: str, *, manager, geometry: tuple[int, int],
                    roots: AegisRoots, mcp, **app_kw) -> View:
    """Build a view, restoring its persisted state if it has any.

    ``mcp`` is required, not defaulted. The local plane binds and starts it
    unconditionally (`app.py:499`, `:587`), so a ``None`` default turns
    every caller that forgets it into an ``AttributeError`` at mount — and
    in a daemon that is a view that silently never appears.
    """
    sink = FrameSink()
    restored = load_view(roots.state_dir, view_id)
    state = restored or ViewState(view_id=view_id, geometry=geometry)
    # Geometry always comes from this attach, not from the last one: the
    # terminal may have been resized between them.
    state.geometry = geometry

    app = AegisApp(
        agents=app_kw.pop("agents", {}),
        default_agent=app_kw.pop("default_agent", ""),
        make_session=app_kw.pop("make_session", None),
        mcp=mcp,
        bridge=manager,
        driver_class=view_driver_for(sink),
        # The SAME object the View holds, not a copy. The app writes focus
        # into it on every tab change (app.py's _write_snapshot); the View
        # persists it on close. Two objects here would restore state the
        # app never sees and persist state the app never wrote.
        view_state=state,
        # A daemon view detaches on Ctrl+Q; it does not own the brain.
        owns_brain=False,
        **app_kw)
    return View(view_id=view_id, app=app, state=state, sink=sink)
