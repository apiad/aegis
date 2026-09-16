"""What the dashboard knows about one session, and about all of them."""

from __future__ import annotations

from dataclasses import dataclass

# Kinds whose sessions the substrate closes when their unit of work ends:
# a queue worker at queue/manager.py:719, a workflow subagent by the
# engine, a group member with its group. Derived, never stored — a boolean
# on the session would drift from the behaviour it names.
EPHEMERAL_KINDS = frozenset({"queue", "workflow", "group"})


@dataclass(frozen=True)
class Origin:
    """Who made this agent.

    `spawned_by` records one nullable handle and cannot answer either half
    of that question: four of the seven birth sites write nothing to it,
    and an operator typing `/spawn` in tab A writes exactly what agent A
    calling `aegis_spawn` writes. This sits beside it rather than
    replacing it — `close_guard`, `aegis_close` and the fork guard all
    read `spawned_by` and none of them wants a new type.
    """

    kind: str = "operator"  # operator|agent|queue|workflow|group|schedule|fork
    by: str = ""  # pane handle, agent handle, queue name, workflow name
    detail: str = ""  # task id, workflow run id, group name
    returns_to: str = ""  # where the result goes, for queue callbacks

    @property
    def ephemeral(self) -> bool:
        return self.kind in EPHEMERAL_KINDS
