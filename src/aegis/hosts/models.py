"""Config and resolved-place data for SSH execution hosts. Pure data."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class HostSpec:
    """One entry in the `hosts:` mapping — a machine aegis can run a
    harness on.

    ``ssh`` is handed to the ``ssh`` binary verbatim, so it resolves
    through the user's ``~/.ssh/config``: aliases, ``ProxyCommand``,
    jump hosts and non-standard ports all work without aegis
    reimplementing any of it.
    """
    name: str
    ssh: str
    cwd: str
    ssh_opts: list[str] = field(default_factory=list)
    # Escape hatch: pin the remote forward port instead of letting sshd
    # allocate one and parsing the choice off ssh's stderr.
    remote_mcp_port: int | None = None


@dataclass(frozen=True)
class Place:
    """Where a session's harness process runs. Resolved per spawn,
    never persisted — the same shape as the model/effort overrides."""
    host: str   # "local" or a `hosts:` key
    cwd: str

    @property
    def is_local(self) -> bool:
        return self.host == "local"

    def qualify(self, path: str) -> str:
        """A path string that names its machine. ``/x`` stays ``/x``
        locally and becomes ``vps:/x`` on a remote host, so a reader —
        human or agent — can never mistake one tree for the other."""
        return path if self.is_local else f"{self.host}:{path}"
