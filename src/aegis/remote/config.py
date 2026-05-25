"""Config dataclasses for the remote plane."""
from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlparse


@dataclass(frozen=True)
class RemoteSpec:
    """Outbound remote target — one entry in the `remotes` mapping."""
    url: str
    token: str | None = None
    peer_name: str | None = None

    def __post_init__(self) -> None:
        parsed = urlparse(self.url)
        if not parsed.scheme or not parsed.netloc:
            raise ValueError(
                f"remote url must include scheme + host: {self.url!r}")


@dataclass(frozen=True)
class RemotePlaneSpec:
    """Inbound plane config — single `remote_plane` block.

    ``peer_name`` is this serve's identity as known by its peers. It
    populates the ``from_peer`` field of outbound callback POSTs so the
    receiver can look us up in its own ``remotes`` map. Only required
    when this serve also has outbound ``remotes`` configured (i.e.
    might send wire callbacks); receiver-only deployments may leave it
    unset.
    """
    bind: str
    accept_tokens: list[str] = field(default_factory=list)
    accept_from: list[str] = field(default_factory=list)
    peer_name: str | None = None

    def __post_init__(self) -> None:
        host, _, port = self.bind.rpartition(":")
        if not host or not port.isdigit():
            raise ValueError(
                f"remote_plane.bind must be host:port, got {self.bind!r}")
        if self.peer_name is not None and not self.peer_name.strip():
            raise ValueError(
                "remote_plane.peer_name must be a non-empty string")
