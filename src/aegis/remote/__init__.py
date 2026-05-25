"""Aegis remote plane: server-to-server enqueue.

See docs/superpowers/specs/2026-05-25-aegis-remote-plane-design.md.
"""
from aegis.remote.client import remote_enqueue
from aegis.remote.config import RemotePlaneSpec, RemoteSpec
from aegis.remote.plane import build_plane

__all__ = [
    "RemoteSpec", "RemotePlaneSpec",
    "remote_enqueue", "build_plane",
]
