"""RepoTracker — which repos the live agents are writing to.

App-owned, one instance, in the shape ``MonitorManager`` already uses so a
pane's ``on_mount`` has a familiar place to hang a subscription.

Membership is learned from write tools only, and a repo stays for the life
of the session that wrote to it. Both choices are argued in the spec; the
short version is that reads would list every repo an agent merely searched,
and time decay would drop exactly the case the section exists for — the
repo an agent left seven uncommitted files in an hour ago.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from pathlib import Path

from aegis.repos.models import RepoState, RepoView, _Membership
from aegis.repos.probe import (
    Baseline,
    capture_baseline,
    find_repo_root,
    probe_repo,
    read_head_branch,
)

log = logging.getLogger(__name__)

TTL = 5.0


class RepoTracker:
    """Repo membership across every live session, plus their git state.

    The clock is injected rather than read, so the TTL is testable without
    sleeping. The probe is injected for the same reason — but the tests that
    matter still run it against a real repo, because a stubbed probe only
    ever confirms our model of ``git status``.
    """

    def __init__(self, *,
                 clock: Callable[[], float] = time.monotonic,
                 probe: Callable[[Path, Baseline | None], RepoState]
                 = probe_repo,
                 capture: Callable[[Path], Baseline] = capture_baseline,
                 ttl: float = TTL) -> None:
        self._clock = clock
        self._probe = probe
        self._capture = capture
        self.ttl = ttl
        self._repos: dict[tuple[str, Path], _Membership] = {}
        self._states: dict[tuple[str, Path], RepoState] = {}
        self._baselines: dict[tuple[str, Path], Baseline] = {}
        self._probed_at: dict[tuple[str, Path], float] = {}
        self._subs: list[Callable[[], None]] = []

    # --- subscription -------------------------------------------------

    def subscribe(self, cb: Callable[[], None]) -> Callable[[], None]:
        """Register ``cb``; returns an idempotent unsubscribe.

        Callers outlive any one pane, so the handle MUST be released on
        unmount — the same contract ``QueueDigest`` and ``MonitorManager``
        carry, and the same leak if it is dropped.
        """
        self._subs.append(cb)

        def _unsub() -> None:
            if cb in self._subs:
                self._subs.remove(cb)

        return _unsub

    def _notify(self) -> None:
        for cb in list(self._subs):
            try:
                cb()
            except Exception:  # noqa: BLE001 — a bad subscriber must not
                log.exception("repo tracker subscriber raised; continuing")

    # --- membership ---------------------------------------------------

    def record(self, handle: str, path: str | Path, *,
               host: str = "local") -> None:
        """Note that ``handle`` wrote to ``path``.

        Off-host paths are **not** resolved against the local disk: the same
        string names a different tree on another machine, and resolving it
        here would attribute a remote write to a local repo. The repo is
        listed by the path's own basename-bearing parent instead, and never
        probed.
        """
        key = self._key(path, host)
        if key is None:
            return
        if host == "local" and key not in self._baselines:
            # Before the tool runs, and once per repo: this is the anchor the
            # session's line counts are measured from, and re-capturing it on
            # a later write would discard everything already done here.
            self._baselines[key] = self._safe_capture(key[1])
        fresh = key not in self._repos
        mem = self._repos.setdefault(key, _Membership(root=key[1], host=host))
        if handle not in mem.writers:
            mem.writers.append(handle)
            fresh = True
        mem.last_write = self._clock()
        if fresh:
            # The free path: a branch name from .git/HEAD costs one file read
            # and is what the row shows until the first probe lands.
            self._states.setdefault(key, RepoState(
                root=key[1],
                branch=read_head_branch(key[1]) if host == "local" else "",
                stale=True))
            self._notify()

    def drop(self, handle: str) -> None:
        """Forget ``handle`` — its session closed.

        A repo with no live writer left disappears; one another agent still
        holds stays, minus this handle.
        """
        changed = False
        for key, mem in list(self._repos.items()):
            if handle not in mem.writers:
                continue
            mem.writers.remove(handle)
            changed = True
            if not mem.writers:
                del self._repos[key]
                self._states.pop(key, None)
                self._baselines.pop(key, None)
                self._probed_at.pop(key, None)
        if changed:
            self._notify()

    def rename(self, old: str, new: str) -> None:
        """Follow a live session's handle change.

        Same contract as ``ClaimRegistry.rename`` / ``MonitorManager.rename``
        and called from the same place. Without it a renamed agent's rows
        keep naming a handle nobody answers to, and ``drop`` on close misses
        them — a ghost writer holding a repo on the board forever.
        """
        changed = False
        for mem in self._repos.values():
            if old in mem.writers:
                mem.writers[mem.writers.index(old)] = new
                changed = True
        if changed:
            self._notify()

    def _safe_capture(self, root: Path) -> Baseline:
        """A baseline, or an empty one. ``record`` runs on the write event
        itself, and a dashboard must never take a write down with it — an
        empty baseline costs the churn number, not the row."""
        try:
            return self._capture(root)
        except Exception:  # noqa: BLE001
            log.exception("baseline capture raised for %s; continuing", root)
            return Baseline()

    def _key(self, path: str | Path,
             host: str) -> tuple[str, Path] | None:
        if host != "local":
            # No local resolution is possible or honest. The write's own
            # directory stands in for the repo root, which is enough to name
            # a row and is never used to read anything.
            p = Path(path)
            return (host, p.parent if p.suffix else p)
        root = find_repo_root(path)
        return None if root is None else (host, root)

    # --- reading ------------------------------------------------------

    def snapshot(self, for_handle: str = "") -> list[RepoView]:
        """Render rows, most recently written first. Never blocks."""
        views: list[RepoView] = []
        for key, mem in sorted(self._repos.items(),
                               key=lambda kv: -kv[1].last_write):
            writers = list(mem.writers)
            if for_handle in writers:
                # The asking agent leads its own row: the mark says "you are
                # here", and burying the handle behind a peer's makes it read
                # as someone else's repo.
                writers.remove(for_handle)
                writers.insert(0, for_handle)
            views.append(RepoView(
                state=self._states.get(key, RepoState(root=key[1],
                                                      stale=True)),
                writers=tuple(writers),
                mine=for_handle in mem.writers,
                host=mem.host))
        return views

    # --- probing ------------------------------------------------------

    async def refresh(self, *, force: bool = False) -> None:
        """Re-probe every local repo whose state has aged past the TTL.

        Runs the blocking probes in the default executor: ``git status`` over
        a large tree can take a couple of hundred milliseconds, and a paint
        must never wait on one. Callers schedule this only while the sidebar
        is open — closed, no probe runs at all.
        """
        now = self._clock()
        due = [k for k, mem in self._repos.items()
               if mem.host == "local"
               and (force or now - self._probed_at.get(k, 0.0) >= self.ttl)]
        if not due:
            return

        loop = asyncio.get_running_loop()
        results = await asyncio.gather(
            *(loop.run_in_executor(None, self._probe, key[1],
                                   self._baselines.get(key))
              for key in due),
            return_exceptions=True)

        changed = False
        for key, result in zip(due, results, strict=True):
            self._probed_at[key] = now
            if key not in self._repos:      # closed mid-probe
                continue
            if isinstance(result, BaseException):
                log.debug("repo probe raised for %s: %s", key[1], result)
                continue                    # keep the last known state, stale
            if self._states.get(key) != result:
                self._states[key] = result
                changed = True
        if changed:
            self._notify()
