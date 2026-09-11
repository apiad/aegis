"""``roots`` for the AppBridge fakes.

``build_server`` binds ``bridge.roots`` once at construction, so every fake
that reaches it owes the attribute — even the many that never call a config
tool. Resolved lazily rather than at class-definition time: the autouse
``isolated_project_dir`` fixture chdirs into a per-test directory *after*
the module is imported, and a root captured at import would name the repo.
"""
from __future__ import annotations

from pathlib import Path

from aegis.config.roots import AegisRoots


class StubRoots:
    """Mixin giving an AppBridge fake the roots of the current test dir.

    Settable, so a test that cares which project the bridge is bound to can
    assign a specific ``AegisRoots`` — which is the whole point of the
    isolation tests.
    """

    _roots: AegisRoots | None = None

    @property
    def roots(self) -> AegisRoots:
        if self._roots is None:
            return AegisRoots.for_project(Path.cwd())
        return self._roots

    @roots.setter
    def roots(self, value: AegisRoots) -> None:
        self._roots = value
