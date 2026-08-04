from __future__ import annotations


class HostError(Exception):
    """A remote host could not be prepared or reached."""


class RemoteLinkLost(HostError):
    """The SSH link carrying a session's harness died.

    Distinct from the harness exiting: to the reading side both look like
    stdout EOF, and without this distinction a dead tab sits there
    looking idle.
    """

    def __init__(self, host: str, detail: str) -> None:
        self.host = host
        self.detail = detail
        super().__init__(
            f"link to {host} lost — {detail or 'no diagnostic output'}")
