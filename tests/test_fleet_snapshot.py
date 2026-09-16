"""Assembly reads live state and nothing from disk.

The fake manager below is shaped like the real one on purpose: it is the
attributes `build_snapshot` actually reaches for, so a rename upstream
breaks this test instead of production."""

from dataclasses import dataclass
from pathlib import Path

from aegis.config import Agent
from aegis.fleet.models import CardView, Origin
from aegis.fleet.snapshot import build_snapshot
from aegis.plan.models import PlanState, PlanTask
from aegis.repos.models import RepoState, RepoView
from aegis.tui.metrics import SessionMetrics


@dataclass
class FakePlace:
    host: str = "local"
    cwd: str = "/home/apiad/Workspace/repos/une-tools"


class FakeSession:
    def __init__(self, handle, state="ready", origin=None, **kw):
        self.handle = handle
        # Carry a real profile so _cost exercises budget.cost.compute rather
        # than only its guard.
        self.agent = Agent(harness="claude-code", model="opus")
        self.title = kw.get("title", "")
        self.agent_slug = kw.get("agent_slug", "opus")
        self.origin = origin or Origin()
        self.place = FakePlace()
        self.state = type("S", (), {"value": state})()
        self.metrics = SessionMetrics(context_window=200_000)
        self.plan = kw.get("plan")
        self.recent_events = ()
        self._last_recap_line = kw.get("did", "")


class FakeManager:
    def __init__(self, sessions):
        self._sessions = list(sessions)
        # The real names — see the table above. None, as on a bare manager.
        self.locks = None
        self.monitor_manager = None
        self.queue_manager = None

    def list_sessions(self):
        return []


def test_an_empty_fleet_is_an_empty_snapshot():
    snap = build_snapshot(FakeManager([]), now=1000.0)
    assert snap.cards == ()
    assert snap.band.total == 0


def test_one_card_per_session_in_tab_order():
    m = FakeManager([FakeSession("alpha"), FakeSession("beta"), FakeSession("gamma")])
    snap = build_snapshot(m, now=1000.0)
    assert [c.handle for c in snap.cards] == ["alpha", "beta", "gamma"]
    assert [c.tab_index for c in snap.cards] == [1, 2, 3]


def test_the_band_counts_yours_against_the_ephemeral():
    m = FakeManager(
        [
            FakeSession("alpha"),
            FakeSession("beta"),
            FakeSession("w1", origin=Origin(kind="queue", by="general")),
            FakeSession("w2", origin=Origin(kind="queue", by="general")),
            FakeSession("w3", origin=Origin(kind="workflow", by="review")),
        ]
    )
    band = build_snapshot(m, now=1000.0).band
    assert (band.total, band.yours, band.ephemeral) == (5, 2, 3)
    assert dict(band.by_kind) == {"queue": 2, "workflow": 1}


def test_states_are_counted_separately():
    m = FakeManager(
        [
            FakeSession("a", state="working"),
            FakeSession("b", state="ready"),
            FakeSession("c", state="ready"),
        ]
    )
    band = build_snapshot(m, now=1000.0).band
    assert (band.working, band.ready) == (1, 2)


def test_the_plan_reaches_the_card():
    tasks = tuple(
        PlanTask(
            key=str(i), subject=f"t{i}", status="completed" if i < 7 else "pending"
        )
        for i in range(10)
    )
    plan = type("P", (), {"snapshot": lambda self, ts: PlanState(tasks=tasks)})()
    m = FakeManager([FakeSession("alpha", plan=plan)])
    card = build_snapshot(m, now=1000.0).cards[0]
    assert (card.plan_done, card.plan_total) == (7, 10)


def test_the_band_states_are_disjoint_and_sum_to_the_total():
    m = FakeManager(
        [
            FakeSession("a", state="working"),
            FakeSession("b", state="ready"),
            FakeSession("c", state="error"),
        ]
    )
    band = build_snapshot(m, now=1000.0).band
    assert band.working + band.ready + band.waiting + band.error == band.total == 3
    assert band.error == 1


def test_assembly_never_touches_the_disk(monkeypatch):
    """A dashboard that reads transcripts is a dashboard that stutters."""
    import builtins

    def boom(*a, **kw):
        raise AssertionError("build_snapshot opened a file")

    monkeypatch.setattr(builtins, "open", boom)
    build_snapshot(FakeManager([FakeSession("alpha")]), now=1000.0)


# --- beyond the brief's six: the rules the table and the guards state ---


class FakeMonitors:
    """`MonitorManager.snapshot(for_handle=...)` scoped by creator."""

    def __init__(self, by_handle):
        self._by = by_handle

    def snapshot(self, *, for_handle=None):
        if for_handle is None:
            return [v for vs in self._by.values() for v in vs]
        return list(self._by.get(for_handle, []))


@dataclass
class FakeMonitorView:
    description: str
    pct: float | None = None


@dataclass
class FakeTask:
    callback: bool
    callback_handle: str | None = None
    enqueued_by: str = ""


class FakeQueues:
    """The two private dicts, in their real shapes: `_pending` maps a queue
    name to a list, `_workers` maps a worker handle to `(task, last_text)`."""

    def __init__(self, pending=(), running=(), configured=1):
        self._pending = {"general": list(pending)}
        self._workers = {f"w{i}": (t, "") for i, t in enumerate(running)}
        self._queues = {f"q{i}": object() for i in range(configured)}


def test_a_ready_session_with_a_live_monitor_is_waiting():
    m = FakeManager([FakeSession("a"), FakeSession("b")])
    m.monitor_manager = FakeMonitors({"a": [FakeMonitorView("pytest", pct=60.0)]})
    snap = build_snapshot(m, now=1000.0)
    assert (snap.band.waiting, snap.band.ready) == (1, 1)
    assert snap.cards[0].monitor == "pytest 60%"
    assert snap.band.monitors == 1


def test_a_ready_session_owed_a_queue_callback_is_waiting():
    """Matched on callback_handle. enqueued_by is a sender tag and would
    never equal a bare handle."""
    m = FakeManager([FakeSession("a"), FakeSession("b"), FakeSession("c")])
    m.queue_manager = FakeQueues(
        pending=[FakeTask(callback=True, callback_handle="a", enqueued_by="agent:a")],
        running=[
            FakeTask(callback=True, callback_handle="b"),
            FakeTask(callback=False, callback_handle="c"),
        ],
        configured=4,
    )
    band = build_snapshot(m, now=1000.0).band
    assert (band.waiting, band.ready) == (2, 1)
    assert band.queues == (2, 4)


def test_a_working_session_with_a_monitor_counts_as_working():
    m = FakeManager([FakeSession("a", state="working")])
    m.monitor_manager = FakeMonitors({"a": [FakeMonitorView("build")]})
    band = build_snapshot(m, now=1000.0).band
    assert (band.working, band.waiting) == (1, 0)


def test_claims_are_counted_per_handle():
    m = FakeManager([FakeSession("a"), FakeSession("b")])
    claim = type("C", (), {})
    c1, c2 = claim(), claim()
    c1.handle, c2.handle = "a", "a"
    m.locks = type("L", (), {"active": lambda self: [c1, c2]})()
    cards = build_snapshot(m, now=1000.0).cards
    assert [c.claims for c in cards] == [2, 0]


def test_turn_seconds_are_zero_between_turns():
    s = FakeSession("a")
    s.metrics.begin_session(100.0)
    card = build_snapshot(FakeManager([s]), now=1000.0).cards[0]
    assert (card.uptime_s, card.turn_s) == (900.0, 0.0)
    s.metrics.start_turn(990.0)
    card = build_snapshot(FakeManager([s]), now=1000.0).cards[0]
    assert card.turn_s == 10.0


def test_context_and_cost_reach_the_card_and_the_band():
    a, b = FakeSession("a"), FakeSession("b")
    a.metrics.last_true_input = 50_000
    b.metrics.last_true_input = 150_000
    b.metrics.c_out = 1_000_000
    snap = build_snapshot(FakeManager([a, b]), now=1000.0)
    assert [c.ctx_pct for c in snap.cards] == [25.0, 75.0]
    assert snap.band.ctx_avg == 50.0
    assert snap.band.ctx_worst == ("b", 75.0)
    assert snap.cards[1].cost_usd > 0.0
    assert snap.band.cost_live == sum(c.cost_usd for c in snap.cards)


def test_a_session_without_a_profile_costs_nothing():
    s = FakeSession("a")
    s.agent = None
    assert build_snapshot(FakeManager([s]), now=1000.0).cards[0].cost_usd == 0.0


def test_the_repo_tracker_is_read_off_a_session():
    root = Path("/home/apiad/Workspace/repos/une-tools")
    view = RepoView(
        state=RepoState(root=root, branch="main", added=3, dirty=2),
        writers=("a", "b"),
    )
    tracker = type("T", (), {"snapshot": lambda self, for_handle="": [view]})()
    a, b, c = FakeSession("a"), FakeSession("b"), FakeSession("c")
    a.repo_tracker = b.repo_tracker = c.repo_tracker = tracker
    snap = build_snapshot(FakeManager([a, b, c]), now=1000.0)
    assert [x.repo for x in snap.cards] == [
        "une-tools · main +3 ~2",
        "une-tools · main +3 ~2",
        "",
    ]
    (row,) = snap.band.repos
    assert (row.name, row.agents, row.shared) == ("une-tools", 2, True)


def test_ghosts_are_drawn_but_not_counted():
    """A ghost is a closed ephemeral session kept on screen briefly. It is
    not part of the live fleet the band counts."""
    ghost = CardView(handle="w9", origin=Origin(kind="queue"), ghost_since=990.0)
    snap = build_snapshot(
        FakeManager([FakeSession("a")]), now=1000.0, ghosts={"w9": (ghost, 990.0)}
    )
    assert [c.handle for c in snap.cards] == ["a", "w9"]
    assert snap.band.total == 1


def test_a_ghost_carries_its_age_so_the_renderer_needs_no_clock():
    """ghost_since and now are both monotonic; the age is computed here, where
    the clock already is, and the renderer only formats it."""
    ghost = CardView(handle="w9", origin=Origin(kind="queue"), ghost_since=977.0)
    snap = build_snapshot(
        FakeManager([FakeSession("a")]), now=1000.0, ghosts={"w9": (ghost, 977.0)}
    )
    assert snap.cards[-1].ghost_s == 23.0


def test_the_band_names_this_machine_even_when_tab_one_is_remote():
    """The first card's host would label the whole fleet with a remote box."""
    import socket

    remote = FakeSession("on-vps")
    remote.place = FakePlace(host="vps")
    band = build_snapshot(
        FakeManager([remote, FakeSession("local-one")]), now=1000.0
    ).band
    assert band.host == socket.gethostname()


def test_a_real_manager_builds_a_snapshot(tmp_path):
    """Every fake above copies the manager's private attributes as they are
    today. This one does not, so renaming `_workers`, `_pending` or `_queues`
    breaks a test instead of the dashboard."""
    from aegis.config.roots import AegisRoots
    from tests.brain import make_brain

    class FakeHarness:
        async def start(self): ...
        async def send(self, t): ...
        async def close(self): ...

        async def events(self):
            if False:
                yield

    mgr = make_brain(
        {"default": Agent(harness="claude-code", model="opus")},
        "default",
        make_session=lambda profile, url, handle: FakeHarness(),
        mcp=None,
        roots=AegisRoots.for_project(tmp_path),
    )
    mgr._sync_spawn("default")
    snap = build_snapshot(mgr, now=1000.0)
    assert len(snap.cards) == 1
    assert snap.band.total == 1
    # A ready session walks the waiting rule, which reads the queue manager.
    assert snap.band.ready + snap.band.waiting == 1
