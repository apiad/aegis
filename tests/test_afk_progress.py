"""The two-minute plan mirror."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from aegis.plan.models import PlanSnapshot, PlanState, PlanTask
from aegis.workflows.builtins.afk.progress import format_plan, format_rollup

NOW = 1_000_000.0


def _snap(**kw):
    base = dict(
        done=4,
        total=9,
        current="run the gate",
        current_working_s=720.0,
        updated_at=NOW - 30,
    )
    base.update(kw)
    return PlanSnapshot(**base)


def test_rollup_reads_as_progress() -> None:
    out = format_rollup(_snap(), now=NOW, stall_after_s=1800)
    assert out.startswith("4/9")
    assert "run the gate" in out
    assert "12m" in out


def test_absent_plan_is_itself_a_reading() -> None:
    """A worker ignoring the task-list instruction must be visible on the
    board, not indistinguishable from an idle one."""
    assert format_rollup(None, now=NOW, stall_after_s=1800) == "no plan reported"


def test_rollup_with_no_current_task() -> None:
    out = format_rollup(
        _snap(current=None, current_working_s=None), now=NOW, stall_after_s=1800
    )
    assert out.startswith("4/9")
    assert "run the gate" not in out


def test_stalled_plan_says_so() -> None:
    out = format_rollup(_snap(updated_at=NOW - 2000), now=NOW, stall_after_s=1800)
    assert out.startswith("stalled")
    assert "run the gate" in out


def test_just_inside_the_stall_window_is_not_stalled() -> None:
    out = format_rollup(_snap(updated_at=NOW - 1799), now=NOW, stall_after_s=1800)
    assert not out.startswith("stalled")


def test_stall_is_measured_against_the_iso_string_the_tracker_writes() -> None:
    """`PlanTracker` sets `updated_at` with `datetime.now(UTC).isoformat()`,
    so every real reading is a string. Subtracting one from a `time.time()`
    float raises TypeError, and the float fixtures above would never catch
    it: the rollup must parse the shape production actually produces."""
    now = datetime.now(timezone.utc).timestamp()
    stale = datetime.fromtimestamp(now - 2000, tz=timezone.utc).isoformat()
    fresh = datetime.fromtimestamp(now - 30, tz=timezone.utc).isoformat()

    assert format_rollup(
        _snap(updated_at=stale), now=now, stall_after_s=1800
    ).startswith("stalled")
    assert not format_rollup(
        _snap(updated_at=fresh), now=now, stall_after_s=1800
    ).startswith("stalled")


def test_an_unparseable_timestamp_is_not_reported_as_a_stall() -> None:
    """A clock we cannot read is not evidence the worker stopped. Calling it
    stalled would put a false alarm on the board."""
    out = format_rollup(_snap(updated_at="whenever"), now=NOW, stall_after_s=1800)
    assert not out.startswith("stalled")
    assert out.startswith("4/9")


def test_plan_renders_each_task_with_its_time() -> None:
    state = PlanState(
        tasks=(
            PlanTask("k1", "read AGENTS.md", "completed", working_s=72.0),
            PlanTask(
                "k2",
                "make it pass",
                "in_progress",
                active_form="making it pass",
                working_s=660.0,
            ),
            PlanTask("k3", "run the gate", "pending"),
        )
    )
    out = format_plan(state)
    assert "- [x] read AGENTS.md" in out
    assert "1m12s" in out
    assert "making it pass" in out  # present-continuous while in progress
    assert "- [ ] run the gate" in out


def test_a_task_that_never_ran_is_not_zero() -> None:
    """working_s is None means the task never entered in_progress. Rendering
    that as 0m claims it ran and took no time."""
    state = PlanState(tasks=(PlanTask("k", "later", "pending"),))
    out = format_plan(state)
    assert "0m" not in out and "0s" not in out


def test_empty_plan_renders_the_placeholder() -> None:
    assert format_plan(PlanState(tasks=())).strip() == "_(no plan reported)_"


def test_snapshot_comes_from_the_session_roll_up_not_a_fabrication() -> None:
    """Only the roll-up on SessionInfo carries `updated_at`, and `updated_at`
    is the whole basis of stall detection. A snapshot assembled from
    PlanState with `updated_at=now` is never stale by construction, so the
    stall branch could never be taken and every test of it would be green."""
    from aegis.workflows.builtins.afk.progress import snapshot_for

    stale = _snap(updated_at=NOW - 3000)
    engine = type(
        "E",
        (),
        {
            "list_sessions": lambda self: [
                type("I", (), {"handle": "w1", "plan": stale})()
            ],
        },
    )()
    got = snapshot_for(engine, "w1")
    assert got.updated_at == NOW - 3000
    assert format_rollup(got, now=NOW, stall_after_s=1800).startswith("stalled")


def test_snapshot_for_an_unknown_handle_is_none() -> None:
    from aegis.workflows.builtins.afk.progress import snapshot_for

    engine = type("E", (), {"list_sessions": lambda self: []})()
    assert snapshot_for(engine, "gone") is None


def test_replace_section_leaves_its_siblings_alone() -> None:
    """The progress schedule owns Plan and nothing else. Rewriting the whole
    comment would erase Coordinator and Result, which it cannot rebuild."""
    from aegis.workflows.builtins.afk.render import replace_section

    body = (
        "### Coordinator\n\nstarted it\n\n"
        "### Plan\n\n- [ ] old\n\n"
        "### Result\n\nnot yet\n\n<!-- aegis-afk task=t-1 -->"
    )
    out = replace_section(body, "Plan", "- [x] new")
    assert "started it" in out
    assert "not yet" in out
    assert "<!-- aegis-afk task=t-1 -->" in out
    assert "- [ ] old" not in out
    assert "- [x] new" in out


def test_replace_section_appends_when_the_section_is_absent() -> None:
    from aegis.workflows.builtins.afk.render import replace_section

    out = replace_section("### Coordinator\n\nstarted it", "Plan", "- [ ] a")
    assert "### Plan" in out and "started it" in out


class FakeEngine:
    def __init__(self, tasks, plans) -> None:
        self._tasks, self._plans = tasks, plans
        self.logged: list[str] = []

    def task_status(self, task_id):
        return self._tasks.get(task_id)

    def plan_state(self, handle):
        return self._plans.get(handle)

    def list_sessions(self):
        return []

    async def bash(self, cmd, cwd=None, **kw):
        return {"exit": 0, "stdout": "[]"}

    def log(self, msg):
        self.logged.append(msg)


CFG = {
    "owner": "o",
    "owner_type": "org",
    "project": 3,
    "stall_after_s": 1800,
    "notify_cmd": "",
}

PINNED = (
    "### Coordinator\n\nstarted it\n\n"
    "### Plan\n\n_(no plan reported)_\n\n"
    "### Result\n\nnot yet\n\n<!-- aegis-afk task=t-1 -->"
)


def _wire(monkeypatch, prog, *, progress_value, writes, comments):
    """A one-card board, with every board call recorded instead of made."""
    from aegis.workflows.builtins.afk.board import Card, Schema

    schema = Schema(
        project_id="P",
        field_ids={"Status": "F_s", "Progress": "F_g"},
        option_ids={"Status": {"Running": "o_run"}},
    )
    card = Card(
        item_id="I",
        number=1,
        repo="o/r",
        url="u",
        title="t",
        body="b",
        state="OPEN",
        fields={"Status": "Running", "Progress": progress_value},
    )

    async def fetch_board(run, **kw):
        return schema, [card]

    async def set_field(run, sc, cd, *, field, value):
        writes.append((field, value))

    async def upsert_comment(run, cd, *, body):
        comments.append(body)
        return "edited"

    async def read_marker(engine, cd):
        return {"task": "t-1", "_body": PINNED}

    monkeypatch.setattr(prog, "fetch_board", fetch_board)
    monkeypatch.setattr(prog, "set_field", set_field)
    monkeypatch.setattr(prog, "upsert_comment", upsert_comment)
    monkeypatch.setattr(prog, "read_marker", read_marker)
    return card


@pytest.mark.asyncio
async def test_unchanged_rollup_writes_nothing(monkeypatch) -> None:
    """Five cards at a two-minute cadence is 150 potential writes an hour.
    A write whose value equals what the card already shows is pure cost."""
    from aegis.workflows.builtins.afk import progress as prog

    writes: list = []
    comments: list = []
    _wire(
        monkeypatch,
        prog,
        progress_value="4/9 · run the gate · 12m",
        writes=writes,
        comments=comments,
    )
    monkeypatch.setattr(prog, "snapshot_for", lambda *a, **k: _snap())

    engine = FakeEngine(
        tasks={"t-1": {"status": "running", "worker_handle": "w1"}},
        plans={"w1": PlanState(tasks=())},
    )
    await prog.run_progress(engine, CFG, now=NOW)
    assert writes == []
    assert comments == []
    assert engine.logged == []  # nothing raised on the way to deciding that


@pytest.mark.asyncio
async def test_a_changed_rollup_writes_the_field_and_only_the_plan_section(
    monkeypatch,
) -> None:
    """The counterpart that makes the no-write test mean something: when the
    reading moved, the field is written and the pinned comment's Plan
    section is swapped with its siblings left intact."""
    from aegis.workflows.builtins.afk import progress as prog

    writes: list = []
    comments: list = []
    _wire(
        monkeypatch,
        prog,
        progress_value="1/9 · read AGENTS.md · 20s",
        writes=writes,
        comments=comments,
    )
    monkeypatch.setattr(prog, "snapshot_for", lambda *a, **k: _snap())

    engine = FakeEngine(
        tasks={"t-1": {"status": "running", "worker_handle": "w1"}},
        plans={
            "w1": PlanState(
                tasks=(PlanTask("k", "make it pass", "completed", working_s=660.0),)
            )
        },
    )
    out = await prog.run_progress(engine, CFG, now=NOW)

    assert writes == [("Progress", "4/9 · run the gate · 12m")]
    assert len(comments) == 1
    assert "- [x] make it pass" in comments[0]
    assert "started it" in comments[0]  # Coordinator survived
    assert "not yet" in comments[0]  # Result survived
    assert "<!-- aegis-afk task=t-1 -->" in comments[0]
    assert out == "updated 1 card(s)"
    assert engine.logged == []


@pytest.mark.asyncio
async def test_progress_never_writes_status(monkeypatch) -> None:
    """The two schedules are safe to run together only because they write
    disjoint fields. This test is that guarantee: drive a full tick over a
    card whose reading moved, and assert Progress is the only field the
    module wrote — Status is read to select cards and never touched."""
    from aegis.workflows.builtins.afk import progress as prog

    writes: list = []
    _wire(monkeypatch, prog, progress_value="stale", writes=writes, comments=[])
    monkeypatch.setattr(prog, "snapshot_for", lambda *a, **k: _snap())

    engine = FakeEngine(
        tasks={"t-1": {"status": "running", "worker_handle": "w1"}},
        plans={"w1": PlanState(tasks=())},
    )
    await prog.run_progress(engine, CFG, now=NOW)

    assert [field for field, _ in writes] == ["Progress"]
    assert "Status" not in [field for field, _ in writes]


@pytest.mark.asyncio
async def test_a_card_that_is_not_running_is_left_alone(monkeypatch) -> None:
    """Only Running cards have a worker to mirror. Writing Progress onto a
    Todo or Done card would contradict the reconciler."""
    from aegis.workflows.builtins.afk import progress as prog

    writes: list = []
    card = _wire(monkeypatch, prog, progress_value="stale", writes=writes, comments=[])
    object.__setattr__(card, "fields", {"Status": "Done", "Progress": "stale"})
    monkeypatch.setattr(prog, "snapshot_for", lambda *a, **k: _snap())

    engine = FakeEngine(tasks={}, plans={})
    assert await prog.run_progress(engine, CFG, now=NOW) == "updated 0 card(s)"
    assert writes == []


@pytest.mark.asyncio
async def test_a_failed_board_write_is_logged_not_raised(monkeypatch) -> None:
    """One unwritable card must not stop the rest of the board updating."""
    from aegis.workflows.builtins.afk import progress as prog
    from aegis.workflows.builtins.afk.board import BoardError

    writes: list = []
    _wire(monkeypatch, prog, progress_value="stale", writes=writes, comments=[])

    async def boom(run, sc, cd, *, field, value):
        raise BoardError("board has no field named 'Progress'")

    monkeypatch.setattr(prog, "set_field", boom)
    monkeypatch.setattr(prog, "snapshot_for", lambda *a, **k: _snap())

    engine = FakeEngine(
        tasks={"t-1": {"status": "running", "worker_handle": "w1"}}, plans={}
    )
    assert await prog.run_progress(engine, CFG, now=NOW) == "updated 0 card(s)"
    assert engine.logged and "Progress" in engine.logged[0]
