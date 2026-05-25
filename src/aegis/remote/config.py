"""Config dataclasses for the remote plane."""
from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlparse


@dataclass(frozen=True)
class RemoteSpec:
    """Outbound remote target — one entry in the `remotes` mapping."""
    url: str
    token: str | None = None

    def __post_init__(self) -> None:
        parsed = urlparse(self.url)
        if not parsed.scheme or not parsed.netloc:
            raise ValueError(
                f"remote url must include scheme + host: {self.url!r}")


@dataclass(frozen=True)
class RemotePlaneSpec:
    """Inbound plane config — single `remote_plane` block."""
    bind: str
    accept_tokens: list[str] = field(default_factory=list)
    accept_from: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        host, _, port = self.bind.rpartition(":")
        if not host or not port.isdigit():
            raise ValueError(
                f"remote_plane.bind must be host:port, got {self.bind!r}")
