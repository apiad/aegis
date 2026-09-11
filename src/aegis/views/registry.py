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
    def __init__(self, *, manager, roots: AegisRoots, mcp, **app_kw) -> None:
        # mcp is explicit rather than riding in **app_kw: it is required by
        # every view (app.py:499 binds it, :587 starts it), and burying a
        # required argument in kwargs turns a forgotten one into an
        # AttributeError at mount instead of a TypeError at the call.
        self._mcp = mcp
        self._manager = manager
        self._roots = roots
        self._app_kw = app_kw
        self._views: dict[str, View] = {}

    async def open(self, view_id: str, geometry: tuple[int, int]) -> View:
        existing = self._views.get(view_id)
        if existing is not None:
            return existing
        v = await open_view(view_id, manager=self._manager,
                            geometry=geometry, roots=self._roots,
                            mcp=self._mcp, **self._app_kw)
        self._views[view_id] = v
        return v

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
