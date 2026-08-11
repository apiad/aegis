"""One record per call into the aegis MCP surface."""
from __future__ import annotations

from dataclasses import dataclass

from aegis.comms.descriptors import Target


@dataclass(frozen=True)
class Envelope:
    call_id: str
    ts: str
    #: The calling agent's handle, from the call's ``from_handle`` argument.
    #: Empty when the tool does not take one, or the caller omitted it — the
    #: MCP server is co-resident and shared, so there is no transport
    #: identity to fall back on and guessing would be worse than a gap.
    from_handle: str
    to: Target | None
    family: str
    verb: str
    thread: str
    outcome: str
    duration_ms: int

    def to_record(self) -> dict:
        return {
            "call_id": self.call_id,
            "ts": self.ts,
            "from": self.from_handle,
            "to": ({"kind": self.to.kind, "id": self.to.id}
                   if self.to is not None else None),
            "family": self.family,
            "verb": self.verb,
            "thread": self.thread,
            "outcome": self.outcome,
            "duration_ms": self.duration_ms,
        }
