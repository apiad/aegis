"""What the dashboard knows about one session, and about all of them."""

from __future__ import annotations

from dataclasses import dataclass, field

# Kinds whose sessions the substrate closes when their unit of work ends:
# a queue worker in QueueManager._finalize, a workflow subagent by the
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


@dataclass(frozen=True)
class EventLine:
    """One line of the card's activity tail."""

    at: float  # wall-clock epoch seconds, for the HH:MM stamp
    tool: str
    summary: str


@dataclass(frozen=True)
class CardView:
    """One session as the dashboard sees it. Everything a card draws is
    here; the renderer reads no live object."""

    handle: str
    title: str = ""
    state: str = "ready"  # ready | working | error
    agent_slug: str = ""
    host: str = "local"
    repo: str = ""  # "une-tools · main +3 ~2", already formatted
    origin: Origin = field(default_factory=Origin)
    uptime_s: float = 0.0
    turn_s: float = 0.0  # 0 when not in a turn
    cost_usd: float = 0.0
    ctx_pct: float = 0.0
    plan_done: int = 0
    plan_total: int = 0
    plan_current: str = ""
    did: str = ""  # last turn's recap
    doing: str = ""  # mid-turn recap; "" until slice 3
    events: tuple[EventLine, ...] = ()
    claims: int = 0
    monitor: str = ""  # "pytest 60%", "" when none
    spoke_with: tuple[str, ...] = ()  # comms edges, most recent first
    waiting_on: tuple[str, ...] = ()
    tab_index: int = 0  # 1-based; the card is that tab
    ghost_since: float | None = None  # set when an ephemeral session died
    ghost_s: float = 0.0  # how long ago it died; set by build_snapshot


@dataclass(frozen=True)
class RepoCount:
    name: str
    agents: int
    shared: bool = False  # more than one agent in this tree


@dataclass(frozen=True)
class BandView:
    host: str = "local"
    total: int = 0
    yours: int = 0
    ephemeral: int = 0
    by_kind: tuple[tuple[str, int], ...] = ()  # (("queue", 2), ("workflow", 1))
    working: int = 0
    ready: int = 0
    waiting: int = 0
    error: int = 0  # working + ready + waiting + error == total
    ctx_avg: float = 0.0
    ctx_worst: tuple[str, float] | None = None  # (handle, pct)
    cost_live: float = 0.0  # sum over OPEN sessions — not a daily total
    recap_cost: float = 0.0
    recap_calls: int = 0
    queues: tuple[int, int] = (0, 0)  # (running, configured)
    monitors: int = 0
    repos: tuple[RepoCount, ...] = ()
    clock: str = ""


@dataclass(frozen=True)
class FleetSnapshot:
    band: BandView = field(default_factory=BandView)
    cards: tuple[CardView, ...] = ()
