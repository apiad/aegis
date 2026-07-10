from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from aegis.locks.persistence import PersistedClaimLog
from aegis.locks.registry import ClaimRegistry
from aegis.locks.resolver import resolve_paths


@dataclass
class _LocksBridge:
    registry: ClaimRegistry
    root_fn: Callable[[], Path]

    def claim(self, handle: str, paths: list[str],
              intent: str = "shared", desc: str = ""):
        prefixes, files = resolve_paths(paths, self.root_fn())
        return self.registry.claim(handle, prefixes, files,
                                   intent=intent, desc=desc)

    def release(self, claim_id: str, handle: str) -> bool:
        return self.registry.release(claim_id, handle)

    def rename(self, old: str, new: str) -> None:
        self.registry.rename(old, new)

    def active(self):
        return self.registry.active()


def make_locks_bridge(*, live_handles: Callable[[], set[str]],
                      root_fn: Callable[[], Path],
                      state_dir: Path | None = None) -> _LocksBridge:
    log = PersistedClaimLog(state_dir) if state_dir is not None else None
    registry = ClaimRegistry(live_handles=live_handles, log=log)
    if log is not None:
        registry.start()
    return _LocksBridge(registry=registry, root_fn=root_fn)
