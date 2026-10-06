"""Every view attached to one brain.

A view outlives its socket: closing one persists its state under its id, so
a reattach restores focus, scroll and drafts. Keyed by client id --
localStorage for browsers, per-tty for terminals -- which is why re-opening
a live id returns the existing view rather than building a second app for
one client.
"""

from __future__ import annotations

from aegis.config.roots import AegisRoots
from aegis.views.view import View, open_view


class ViewRegistry:
    def __init__(
        self, *, manager, roots: AegisRoots, mcp, on_last_quit=None, **app_kw
    ) -> None:
        # What a quitting last client is allowed to do, decided by whoever
        # built this registry rather than by the client asking. `_serve`
        # supplies it only for a daemon a client autostarted, so a daemon a
        # person or systemd started has no callable here and cannot be
        # stopped from any TUI. The protection is the absence of the hook,
        # not a check somebody has to remember to write.
        self._on_last_quit = on_last_quit
        # mcp is explicit rather than riding in **app_kw: it is required by
        # every view (app.py:499 binds it, :587 starts it), and burying a
        # required argument in kwargs turns a forgotten one into an
        # AttributeError at mount instead of a TypeError at the call.
        self._mcp = mcp
        self._manager = manager
        self._roots = roots
        self._app_kw = app_kw
        self._views: dict[str, View] = {}

    async def open(
        self, view_id: str, geometry: tuple[int, int], *, open: str | None = None
    ) -> View:
        existing = self._views.get(view_id)
        if existing is not None:
            if open is not None:
                existing.show(open)
            return existing
        v = await open_view(
            view_id,
            manager=self._manager,
            geometry=geometry,
            roots=self._roots,
            mcp=self._mcp,
            can_stop_daemon=self.would_grant_quit,
            **self._app_kw,
        )
        if open is not None:
            v.show(open)
        self._views[view_id] = v
        return v

    def would_grant_quit(self) -> bool:
        """Whether a quit from the caller's view would stop the daemon.

        Asked BEFORE the view closes, so one attached view is this one and
        two is somebody else. Exists so the TUI can warn about a cost it is
        actually about to incur, rather than about one the daemon would
        refuse anyway.
        """
        return self._on_last_quit is not None and len(self._views) <= 1

    def request_quit(self) -> bool:
        """Grant a quitting client's request to stop the daemon.

        Returns whether anything was done, which is False both when this
        daemon may not be stopped and when another view is still attached.
        Callers must have closed their own view first, so `list()` here is
        the other clients.
        """
        if self._on_last_quit is None or self._views:
            return False
        self._on_last_quit()
        return True

    def get(self, view_id: str) -> View | None:
        return self._views.get(view_id)

    def list(self) -> list[str]:
        return list(self._views)

    async def close(self, view_id: str) -> None:
        v = self._views.pop(view_id, None)
        if v is None:
            return
        v.persist(self._roots.state_dir)
        await v.stop()

    async def close_all(self) -> None:
        for view_id in list(self._views):
            await self.close(view_id)
