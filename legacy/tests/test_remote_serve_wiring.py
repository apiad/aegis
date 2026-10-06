from __future__ import annotations

import asyncio

import pytest

from aegis.config.yaml_loader import AegisConfig
from aegis.remote.config import RemotePlaneSpec


@pytest.mark.asyncio
async def test_serve_starts_remote_plane_when_configured(
        monkeypatch) -> None:
    """When `remote_plane` is configured, `serve` boots an HTTP server.

    Stubs ``run_plane_async`` and asserts that ``_maybe_start_remote_plane``
    invokes it with the configured spec.
    """
    from aegis.remote import plane as plane_mod

    started: list[tuple] = []

    def _fake_run(app, bind):
        started.append((app, bind))

        async def _noop() -> None:
            return None
        return asyncio.create_task(_noop())

    monkeypatch.setattr(plane_mod, "run_plane_async", _fake_run)

    from aegis.cli import _maybe_start_remote_plane

    spec = RemotePlaneSpec(bind="127.0.0.1:8556")
    cfg = AegisConfig(remote_plane=spec)
    qm = object()

    await _maybe_start_remote_plane(cfg, qm)

    assert len(started) == 1
    _app, bind = started[0]
    assert bind == "127.0.0.1:8556"


@pytest.mark.asyncio
async def test_serve_skips_remote_plane_when_unconfigured(
        monkeypatch) -> None:
    from aegis.remote import plane as plane_mod

    started: list = []

    def _fake_run(*a, **k):
        started.append(a)
    monkeypatch.setattr(plane_mod, "run_plane_async", _fake_run)

    from aegis.cli import _maybe_start_remote_plane
    cfg = AegisConfig(remote_plane=None)
    await _maybe_start_remote_plane(cfg, queue_manager=object())
    assert started == []
